# syntax=docker/dockerfile:1

# The pinned runtime environment for this cache.
#
# Everything shared comes from the base image: Debian, git-annex, the virtual environment, the
# `dandi_cache_utils` library that `code/update.py` imports, and the vendored `update_pipeline.sh`
# that CI extracts and runs on the runner.
#
# The `:nwb` tag, because this cache streams remote NWB files to read their acquisition sampling rates. That tag carries remfile and the DANDI API; `spikeinterface` is this cache's own, declared in `envs/pyproject.toml` and installed into the image below.
#
# The image holds only the environment, not this cache's code. The code is mounted at run time, so
# a single image serves any revision of it.
FROM ghcr.io/dandi-cache/dandi-cache-utils:nwb

# The source label associates the image on the GitHub Container Registry with this repository.
LABEL org.opencontainers.image.source="https://github.com/dandi-cache/qualifying-lfp-content-ids"
LABEL org.opencontainers.image.description="The pinned runtime environment for the DANDI Cache qualifying-lfp-content-ids update pipeline."

# This cache's own processing dependencies, installed whether or not it has any: an empty
# `dependencies` list resolves to an empty install, so the image's contents follow from
# `envs/pyproject.toml` alone rather than from a per-cache decision that has to agree with it.
#
# pip cannot read [project.dependencies] without building the project, so install the (virtual)
# project from a throwaway build directory.
COPY envs/pyproject.toml /tmp/build/pyproject.toml
RUN pip install /tmp/build \
    && rm -rf /tmp/build

CMD ["python", "--version"]
