"""Which NWB files hold an acquisition ElectricalSeries fast enough to be worth spike sorting.

A file qualifies when at least one ElectricalSeries in its acquisition submodule samples above
10 kHz. Lower-rate series, LFP among them, are what this threshold exists to exclude.

Only files `content-id-to-valid-nwb-file` has already confirmed open are streamed, so nothing here
spends a round trip on an asset known to fail.

Everything shared -- the argument parsing, the logging, the batch cap, the stage-routed and
size-capped error logs, the incremental frontier, the output paths and testing mode -- comes from
`dandi_cache_utils`, which the runtime image carries.
"""

import dandi_cache_utils as dandi_cache
import spikeinterface.extractors

#: The side output: which content IDs failed to be assessed, rather than failing to qualify.
#: They are excluded from later runs, since a failure here repeats rather than resolves.
ERROR_IDS = "error_ids.jsonl"

# Resolving an asset through the DANDI API and streaming it through SpikeInterface fail for
# unrelated reasons, and a week of failures is only triageable when each kind has its own log.
STAGES = {
    "retrieving asset information from the DANDI API": "dandi_api_errors.txt",
    "reading the ElectricalSeries rates": "spikeinterface_errors.txt",
}

#: Below this, a series is LFP or similar rather than something to spike sort.
RATE_THRESHOLD_HZ = 10_000


def file_qualifies(url: str, /) -> bool:
    """Whether any acquisition ElectricalSeries in the file samples above the rate threshold."""
    for electrical_series_path in dandi_cache.nwb.electrical_series_paths(url):
        extractor = spikeinterface.extractors.NwbRecordingExtractor(
            file_path=url, stream_mode="remfile", electrical_series_path=electrical_series_path
        )
        if extractor.get_sampling_frequency() > RATE_THRESHOLD_HZ:
            return True
    return False


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset()
    validity = dataset.read_input("content-id-to-valid-nwb-file")
    locations = dataset.read_input("content-id-to-nwb-file")

    assessed = dataset.read_output_lookup()
    error_ids = dandi_cache.read_ids(dataset.output_file_path(ERROR_IDS))

    # A content ID whose assessment has already failed is left out rather than retried: the
    # failures here are properties of the file, so a retry spends the same stream to fail again.
    candidates = [
        content_id for content_id in locations if content_id not in error_ids and validity.get(content_id) is True
    ]

    resolver = dandi_cache.api.AssetResolver()

    def assess(content_id, item) -> bool:
        dandiset_id, path = dandi_cache.api.split_location(locations[content_id])
        # Reported with any failure, so an error log names the asset rather than only its content ID.
        item.context.update({"dandiset ID": dandiset_id, "path": path})

        item.stage = "retrieving asset information from the DANDI API"
        url = resolver.content_url(dandiset_id, path)
        item.context["URL"] = url

        item.stage = "reading the ElectricalSeries rates"
        return file_qualifies(url)

    dandi_cache.run_incremental_update(
        dataset,
        candidates=candidates,
        process=assess,
        recorded=assessed,
        limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
        # A failed assessment is not recorded as a `false`: this cache's `false` means the file
        # does not qualify, and conflating the two would publish a judgement never made.
        # `error_ids` is what keeps the item out of later runs instead.
        on_failure=dandi_cache.SKIP,
        on_error=lambda content_id, _item: error_ids.add(content_id),
        on_write=lambda: dandi_cache.write_ids(dataset.output_file_path(ERROR_IDS), error_ids),
        stages=STAGES,
        describe=lambda qualifies: "qualifies" if qualifies else "does not qualify",
        checkpoint_every=50,
    )


if __name__ == "__main__":
    main()
