"""The constants of this repository's SpikeInterface helpers, in one place."""

#: Below this, a series is LFP or similar rather than something to spike sort.
RATE_THRESHOLD_HZ = 10_000

#: Where an NWB file keeps what an instrument recorded, as opposed to what was derived from it.
ACQUISITION_PREFIX = "acquisition/"
