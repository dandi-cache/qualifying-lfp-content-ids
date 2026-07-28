import argparse
import itertools
import json
import pathlib
import traceback

import dandi.dandiapi
import spikeinterface.extractors

# The only qualification requirement: at least one ElectricalSeries in the acquisition
# submodule with a sampling rate above this threshold.
_MIN_RATE_HZ = 10_000

_CACHE_FILE_NAME = "qualifying_lfp_content_ids.jsonl"
_TESTING_FILE_NAME = "testing.jsonl"
_TESTING_LIMIT = 10


def _is_nwb_file(path: str) -> bool:
    """Whether a path points to an NWB asset, which ends in `.nwb` (HDF5) or `.nwb.zarr` (Zarr)."""
    suffixes = pathlib.Path(path).suffixes
    return suffixes[-2:] == [".nwb", ".zarr"] or suffixes[-1:] == [".nwb"]


def _load_ids(file_path: pathlib.Path) -> set:
    """Load a set of IDs from a JSONL file, returning an empty set if the file does not exist."""
    if not file_path.exists():
        return set()

    with file_path.open(mode="r") as file_stream:
        return {json.loads(line) for line in file_stream if line.strip()}


def _load_results(file_path: pathlib.Path) -> dict:
    """Load a mapping of content ID to qualification result from a JSONL file of `[id, bool]` pairs."""
    if not file_path.exists():
        return {}

    with file_path.open(mode="r") as file_stream:
        return {
            content_id: qualifies
            for content_id, qualifies in (json.loads(line) for line in file_stream if line.strip())
        }


def _load_record_map(file_path: pathlib.Path) -> dict:
    """Load a mapping from a JSONL file of one `{content_id: value}` object per line."""
    if not file_path.exists():
        return {}

    records: dict = {}
    with file_path.open(mode="r") as file_stream:
        for line in file_stream:
            if line.strip():
                records.update(json.loads(line))
    return records


# The `derivatives` dataset is a persistent DataLad dataset: this log accumulates across every
# run forever (entries are never otherwise removed), and GitHub hard-rejects any single blob over
# 100 MB. Keep the log comfortably under that limit by dropping the oldest entries once it grows
# past this cap.
_MAX_LOG_FILE_SIZE_BYTES = 80_000_000


def _log_error(log_file_path: pathlib.Path, message: str) -> None:
    """Append a single error report to the given error log, separated by a blank line.

    If the file has grown past `_MAX_LOG_FILE_SIZE_BYTES`, the oldest entries are dropped first so
    the file never approaches GitHub's 100 MB per-file limit.
    """
    with log_file_path.open(mode="a") as file_stream:
        file_stream.write(f"{message}\n\n")

    if log_file_path.stat().st_size <= _MAX_LOG_FILE_SIZE_BYTES:
        return

    with log_file_path.open(mode="rb") as file_stream:
        file_stream.seek(-_MAX_LOG_FILE_SIZE_BYTES, 2)
        tail = file_stream.read()

    # Realign to the start of the next whole entry so no partial entry is kept.
    next_entry_offset = tail.find(b"\n\n")
    if next_entry_offset != -1:
        tail = tail[next_entry_offset + 2 :]

    with log_file_path.open(mode="wb") as file_stream:
        file_stream.write(tail)


def _nwb_file_qualifies(s3_url: str) -> bool:
    """Whether the NWB file has at least one acquisition ElectricalSeries above the rate threshold."""
    electrical_series_paths = spikeinterface.extractors.NwbRecordingExtractor.fetch_available_electrical_series_paths(
        file_path=s3_url, stream_mode="remfile"
    )
    acquisition_series_paths = [
        electrical_series_path
        for electrical_series_path in electrical_series_paths
        if electrical_series_path.startswith("acquisition/")
    ]

    for electrical_series_path in acquisition_series_paths:
        extractor = spikeinterface.extractors.NwbRecordingExtractor(
            file_path=s3_url, stream_mode="remfile", electrical_series_path=electrical_series_path
        )
        if extractor.get_sampling_frequency() > _MIN_RATE_HZ:
            return True

    return False


def _run(base_directory: pathlib.Path, testing: bool, limit: int | None) -> None:
    submodule_file_path = (
        base_directory
        / "sourcedata"
        / "content-id-to-usage-dandiset-path"
        / "derivatives"
        / "content_id_to_usage_dandiset_path.jsonl"
    )
    content_id_to_dandiset_path = {}
    with submodule_file_path.open(mode="r") as file_stream:
        for line in file_stream:
            if line.strip():
                content_id_to_dandiset_path.update(json.loads(line))

    # Cross-referenced to skip content IDs already known not to open as a valid NWB file, so
    # this cache never spends a network round trip streaming an asset that would only fail.
    valid_nwb_file_path = (
        base_directory
        / "sourcedata"
        / "content-id-to-valid-nwb-file"
        / "derivatives"
        / "content_id_to_valid_nwb_file.jsonl"
    )
    content_id_to_valid_nwb_file = _load_record_map(file_path=valid_nwb_file_path)

    derivatives_directory = base_directory / "derivatives"
    derivatives_directory.mkdir(parents=True, exist_ok=True)
    logs_directory = base_directory / "logs"
    logs_directory.mkdir(parents=True, exist_ok=True)

    # Testing runs read and write their own designated files, so the real cache and its
    # bookkeeping are never touched.
    cache_file_name = _TESTING_FILE_NAME if testing else _CACHE_FILE_NAME
    error_ids_file_name = "testing_error_ids.jsonl" if testing else "error_ids.jsonl"
    error_log_file_name = "testing_errors.txt" if testing else "errors.txt"

    output_file_path = derivatives_directory / cache_file_name
    error_ids_file_path = derivatives_directory / error_ids_file_name
    error_log_file_path = logs_directory / error_log_file_name

    results = _load_results(output_file_path)
    error_ids = _load_ids(error_ids_file_path)

    content_ids_to_process = {
        content_id
        for content_id, dandiset_path in content_id_to_dandiset_path.items()
        if content_id not in results
        and content_id not in error_ids
        and _is_nwb_file(next(iter(dandiset_path.values())))
        and content_id_to_valid_nwb_file.get(content_id) is True
    }

    run_limit = _TESTING_LIMIT if testing else limit
    content_ids_to_process = set(itertools.islice(content_ids_to_process, run_limit))

    client = dandi.dandiapi.DandiAPIClient()  # Run tokenless to ensure only public dandisets are accessed
    for content_id in content_ids_to_process:
        dandiset_id = first_path = s3_url = None
        try:
            dandiset_id, first_path = next(iter(content_id_to_dandiset_path[content_id].items()))
            dandiset = client.get_dandiset(dandiset_id=dandiset_id)
            asset = dandiset.get_asset_by_path(path=first_path)
            s3_url = asset.get_content_url(follow_redirects=1, strip_query=True)
            qualifies = _nwb_file_qualifies(s3_url=s3_url)
        except Exception as exception:
            _log_error(
                log_file_path=error_log_file_path,
                message=(
                    f"Error while processing `{content_id=}` "
                    f"(dandiset ID {dandiset_id}, path {first_path}, URL {s3_url})!\n\n"
                    f"{type(exception)}:{str(exception)}\n\n"
                    f"{traceback.format_exc()}"
                ),
            )
            error_ids.add(content_id)
            continue

        results[content_id] = qualifies

    with output_file_path.open(mode="w") as file_stream:
        file_stream.writelines(f"{json.dumps([content_id, results[content_id]])}\n" for content_id in sorted(results))
    with error_ids_file_path.open(mode="w") as file_stream:
        file_stream.writelines(f"{json.dumps(content_id)}\n" for content_id in sorted(error_ids))


if __name__ == "__main__":
    default_base_directory = pathlib.Path(__file__).parent.parent

    parser = argparse.ArgumentParser(description="Update the qualifying-lfp-content-ids DANDI cache.")
    parser.add_argument(
        "--base-directory",
        type=pathlib.Path,
        default=default_base_directory,
        help=(
            "The directory containing the `sourcedata`, `derivatives`, and `logs` directories. "
            "`sourcedata` must hold both the `content-id-to-usage-dandiset-path` and "
            "`content-id-to-valid-nwb-file` datasets. "
            "Set to the mounted dataset path when run inside the pipeline container; "
            "defaults to the repository root."
        ),
    )
    parser.add_argument(
        "--testing",
        action="store_true",
        help=(
            f"Run in testing mode: process only the first {_TESTING_LIMIT} items and write "
            f"`derivatives/{_TESTING_FILE_NAME}` instead of the real cache, leaving it "
            "untouched. Omit for a complete update."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2_000,
        help="The number of new content IDs to process in this run. Ignored in testing mode.",
    )
    args = parser.parse_args()

    _run(base_directory=args.base_directory, testing=args.testing, limit=args.limit)
