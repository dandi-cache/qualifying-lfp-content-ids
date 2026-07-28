#!/usr/bin/env bash
#
# CI orchestration for the update. Keeps generated results off the code branch and runs the
# processing inside the published container via datalad-containers.
#
#   - `main`        holds only the code (this checkout).
#   - `derivatives` is a persistent DataLad dataset on its own branch, cloned standalone
#                   into scratch. The processing is recorded there with
#                   `datalad containers-run`, so every update carries full provenance (the
#                   command, the input subdataset commit, the output diff, and the container
#                   image digest) and history is retained.
#   - `dist`        is the lightweight, force-recreated publication artifact consumed by
#                   downstream users (see README.md).
#
# The published image is used purely as the runtime environment: the code and the dataset
# are bind-mounted in (the image holds no code), and only the image digest is stored in the
# dataset (a small text file), so it stays annex-free and ghcr holds the bytes.
#
# code/update.py and code/compress.py are the actual code and run in any environment; this
# script is only the CI orchestration around them.
#
# Required environment variables:
#   REPO_URL    Authenticated https remote for this repository (clone/push).
#   WORKSPACE   Path to the `main` checkout that holds the code (this repository).
#   IMAGE       Container image reference to run the processing in.
# Optional:
#   TESTING      Set to "true" to run update.py in testing mode: it processes only a few
#                items and reads/writes derivatives/testing.jsonl, leaving the real cache
#                untouched. Empty/unset means a complete run.
#   LIMIT        Number of new content IDs to process in a complete run (default: 2000).
#                Ignored in testing mode.
#   GITHUB_SHA   Recorded in the provenance message to link results to the code commit.
#   RUNNER_TEMP  Scratch directory for the working clones (default: /tmp).
set -euo pipefail

: "${REPO_URL:?REPO_URL must be set}"
: "${WORKSPACE:?WORKSPACE must be set}"
: "${IMAGE:?IMAGE must be set}"
TESTING="${TESTING:-}"
LIMIT="${LIMIT:-2000}"
GITHUB_SHA="${GITHUB_SHA:-unknown}"

# Only pass --testing when requested, so a normal run processes the full cache.
TESTING_ARG=""
if [ "${TESTING}" = "true" ]; then
  TESTING_ARG="--testing"
fi

BOT_NAME="github-actions[bot]"
BOT_EMAIL="github-actions[bot]@users.noreply.github.com"

# Input mode: upstream DataLad datasets, registered as input subdatasets and pinned via
# `--input` in the provenance of every run.
#   - content-id-to-usage-dandiset-path maps each content ID to the dandiset(s)/path(s) it is
#     used under, which is what this cache needs to resolve an asset and check its acquisition
#     ElectricalSeries rates.
#   - content-id-to-valid-nwb-file marks which content IDs are already known to open as valid
#     NWB files, so this cache skips (for now) any content ID known to be invalid rather than
#     spending a network round trip streaming an asset that would only fail.
INPUT_SUBDATASET_URLS=(
  "https://github.com/dandi-cache/content-id-to-usage-dandiset-path.git"
  "https://github.com/dandi-cache/content-id-to-valid-nwb-file.git"
)
INPUT_SUBDATASET_PATHS=(
  "sourcedata/content-id-to-usage-dandiset-path"
  "sourcedata/content-id-to-valid-nwb-file"
)
INPUT_SUBDATASET_BRANCHES=(
  "derivatives"
  "derivatives"
)

DS="${RUNNER_TEMP:-/tmp}/derivatives-dataset"
DISTDIR="${RUNNER_TEMP:-/tmp}/dist-publish"

# datalad (with the container extension) from the project environment.
datalad() { uv run --project "${WORKSPACE}/envs" datalad "$@"; }

git config --global user.name "${BOT_NAME}"
git config --global user.email "${BOT_EMAIL}"

# The `derivatives` dataset is a standalone clone (not a git worktree): datalad writes the
# input subdataset's config into `.git/config`, which is a file -- not a directory -- in a
# worktree, so subdataset registration fails there.
rm -rf "${DS}" "${DISTDIR}"

# Reuse the persistent `derivatives` dataset branch, or bootstrap a new one.
if git ls-remote --heads "${REPO_URL}" derivatives | grep -q refs/heads/derivatives; then
  echo "Reusing the existing 'derivatives' dataset branch."
  git clone --branch derivatives --single-branch "${REPO_URL}" "${DS}"
  for input_subdataset_path in "${INPUT_SUBDATASET_PATHS[@]}"; do
    git -C "${DS}" submodule update --init "${input_subdataset_path}"
  done
else
  echo "Bootstrapping a new 'derivatives' DataLad dataset."
  datalad create --no-annex "${DS}"
  for i in "${!INPUT_SUBDATASET_URLS[@]}"; do
    input_subdataset_url="${INPUT_SUBDATASET_URLS[$i]}"
    input_subdataset_path="${INPUT_SUBDATASET_PATHS[$i]}"
    input_subdataset_branch="${INPUT_SUBDATASET_BRANCHES[$i]}"
    datalad clone -d "${DS}" "${input_subdataset_url}" "${DS}/${input_subdataset_path}"
    # Track the input dataset's published-data branch (its default branch holds only code),
    # and record that branch in `.gitmodules` so `submodule update --remote` follows it.
    git -C "${DS}/${input_subdataset_path}" fetch origin "${input_subdataset_branch}"
    git -C "${DS}/${input_subdataset_path}" checkout -B "${input_subdataset_branch}" "origin/${input_subdataset_branch}"
    git -C "${DS}" config -f .gitmodules "submodule.${input_subdataset_path}.branch" "${input_subdataset_branch}"
  done
  datalad save -d "${DS}" -m "Initialize derivatives dataset"
fi

# Establish the dataset as the working directory for every operation that follows. All
# subsequent dataset paths are dataset-relative from here, so a `datalad save`/`status`
# argument can never resolve against WORKSPACE (the code checkout) and silently fall outside
# the dataset. This is the only `cd` in the script.
cd "${DS}"

git config user.name "${BOT_NAME}"
git config user.email "${BOT_EMAIL}"
mkdir -p derivatives

# Carry the study-level BIDS dataset_description.json (kept on the code branch) onto the
# derivatives dataset so the published dataset is self-describing. The save uses a
# dataset-relative path now that the dataset is the working directory; no `|| true` mask, so
# a genuine save failure fails the run loudly (`datalad save` already exits 0 when there is
# nothing to save).
cp "${WORKSPACE}/dataset_description.json" dataset_description.json
datalad save -m "Update dataset_description.json" dataset_description.json

# Advance each input subdataset to its latest commit and record the pointer. `-d .` is
# required here: without it, `datalad save` resolves the target dataset by walking up from
# the given path, and since that path is itself a subdataset mount point, it silently targets
# the (clean, nothing-to-save) subdataset instead of registering the new commit in the
# superdataset -- exiting 0 without saving anything.
for input_subdataset_path in "${INPUT_SUBDATASET_PATHS[@]}"; do
  git submodule update --init --remote "${input_subdataset_path}"
  datalad save -d . -m "Update input subdataset ${input_subdataset_path} to latest" "${input_subdataset_path}"
done

# Pin the published image digest and register it as a container. Only the digest is stored
# (a small text file), so the dataset stays annex-free; ghcr holds the image bytes.
docker pull "${IMAGE}"
DIGEST=$(docker inspect --format '{{index .RepoDigests 0}}' "${IMAGE}")
mkdir -p .datalad/environments/pipeline
printf '%s\n' "${DIGEST}" > .datalad/environments/pipeline/image
# The {img}/{cmd} placeholders and the $-expansions are interpolated by datalad at run time,
# not by this shell, so they are intentionally left unexpanded here.
# shellcheck disable=SC2016
datalad containers-add pipeline --update \
  --image .datalad/environments/pipeline/image \
  --call-fmt 'docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/tmp -w /tmp -v "$WORKSPACE/code":/code:ro "$(cat {img})" {cmd}'
datalad save -m "Pin runtime container image to ${DIGEST}" .datalad

# Fail fast if the dataset is not clean before the recorded run. `containers-run` requires a
# clean tree to detect the command's changes and otherwise aborts with a generic "clean
# dataset required" error; surfacing the offending paths here is far easier to diagnose.
# The JSON renderer is required: the default renderer prints "nothing to save, working tree
# clean" on a clean dataset, and the JSON one also emits records for clean paths, so filter
# to the non-clean states.
DATASET_STATUS=$(datalad -f json status | jq -r 'select(.state != "clean") | "\(.state): \(.path)"')
if [ -n "${DATASET_STATUS}" ]; then
  echo "ERROR: derivatives dataset is not clean before containers-run." >&2
  echo "Offending paths:" >&2
  echo "${DATASET_STATUS}" >&2
  exit 1
fi

# Run the processing inside the published image. The image provides only the environment;
# the code and the dataset are bind-mounted in (see the call format). `--explicit` keeps
# datalad from clearing the outputs first, which is required when the outputs are also prior
# state (input) of the next incremental run.
#
# Each input subdataset is pinned via its own `--input` so the provenance records the exact
# commit of every source this run read from.
RUN_INPUT_ARGS=()
for input_subdataset_path in "${INPUT_SUBDATASET_PATHS[@]}"; do
  RUN_INPUT_ARGS+=(--input "${input_subdataset_path}")
done
datalad containers-run -n pipeline --explicit \
  "${RUN_INPUT_ARGS[@]}" \
  --output derivatives \
  -m "Update qualifying-lfp-content-ids (code @ ${GITHUB_SHA}; image ${DIGEST})" \
  "python /code/update.py --base-directory /tmp --limit ${LIMIT} ${TESTING_ARG}"

# Publish the full results to the `derivatives` branch.
git -C "${DS}" push "${REPO_URL}" HEAD:derivatives

# Build and force-publish the consumer-facing `dist` artifact from a fresh repo. Only the
# real cache is published; a testing.jsonl(.gz) left by a testing run never reaches
# consumers (the guard below only matters when a testing run precedes the first ever
# complete run).
uv run --project "${WORKSPACE}/envs" python "${WORKSPACE}/code/compress.py" --base-directory "${DS}"
mkdir -p "${DISTDIR}/derivatives"
if [ -f "${DS}/derivatives/qualifying_lfp_content_ids.jsonl.gz" ]; then
  cp "${DS}/derivatives/qualifying_lfp_content_ids.jsonl.gz" "${DISTDIR}/derivatives/"
fi
cp "${WORKSPACE}/dataset_description.json" "${DISTDIR}/dataset_description.json"
git -C "${DISTDIR}" init -q -b dist
git -C "${DISTDIR}" config user.name "${BOT_NAME}"
git -C "${DISTDIR}" config user.email "${BOT_EMAIL}"
git -C "${DISTDIR}" add dataset_description.json derivatives
git -C "${DISTDIR}" commit -q -m "Publish qualifying-lfp-content-ids"
git -C "${DISTDIR}" push -f "${REPO_URL}" dist:dist
