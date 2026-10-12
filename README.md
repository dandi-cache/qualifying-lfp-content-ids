# DANDI Cache: `qualifying-lfp-content-ids`

This cache catalogs NWB content IDs (from public Dandisets) that contain at least one
`ElectricalSeries` in `acquisition` with a sampling rate above 10 kHz.

It is derived from the [`content-id-to-valid-nwb-file`](https://github.com/dandi-cache/content-id-to-valid-nwb-file)
cache: content IDs already known not to open as a valid NWB file are skipped, so this cache
never spends a network round trip on an asset that would only fail. For each remaining
content ID not yet processed, it resolves the dandiset ID/path of the NWB asset (via
`content-id-to-valid-nwb-file`'s own [`content-id-to-nwb-file`](https://github.com/dandi-cache/content-id-to-nwb-file)
dependency), opens the asset remotely, and inspects the sampling rate of any acquisition
`ElectricalSeries` it contains.

Updated frequently.

Primarily for use by developers.



## One-time use

`dist` publishes each output as a [Zarr](https://zarr.dev/) store: the cache itself is `derivatives/qualifying_lfp_content_ids.zarr/`, with the cache's other outputs beside it as stores of the same kind (`derivatives/error_ids.zarr/`).
A store is one array of [JSON Lines](https://jsonlines.org/) text cut into chunk files, each holding whole lines and gzip-compressed.
It can be read with the standard library alone, or opened remotely with any Zarr library.

### Python API (recommended)

The chunk files are gzip-compressed [JSON Lines](https://jsonlines.org/): list them, then read them in order as you would any JSONL files.

```python
import gzip, json, urllib.request

chunks = "https://api.github.com/repos/dandi-cache/qualifying-lfp-content-ids/contents/derivatives/qualifying_lfp_content_ids.zarr/stream/c?ref=dist"
files = sorted((entry["download_url"] for entry in json.load(urllib.request.urlopen(chunks))), key=lambda url: int(url.rsplit("/", 1)[1]))
qualifying_lfp_content_ids = {key: value for url in files for line in gzip.decompress(urllib.request.urlopen(url).read()).decode().splitlines() if line for key, value in json.loads(line).items()}
```

### Remote Zarr store

Anything that opens Zarr can open the store over HTTP, reading only the chunks it needs.
The group's `zarr.json` carries the array's metadata, so opening it takes one request:

```bash
pip install "zarr>=3" fsspec aiohttp
```

```python
import json

import zarr

url = "https://raw.githubusercontent.com/dandi-cache/qualifying-lfp-content-ids/refs/heads/dist/derivatives/qualifying_lfp_content_ids.zarr"
stream = zarr.open_group(url, mode="r")["stream"]
print(stream.shape, stream.chunks)  # Bytes in all, and bytes per chunk.

# The first chunk, fetched and decompressed on its own: whole lines, each `{"<key>": <value>}`.
first_chunk = stream[: stream.chunks[0]].tobytes().decode("utf-8")
for line in first_chunk.splitlines():
    if line.strip():
        print(json.loads(line))
        break
```

Reading `stream[:]` returns the whole text; to read it in pieces, step through it by `stream.chunks[0]`, as each chunk is fetched whole.

### Save to file

A raw URL cannot be listed, so to fetch every chunk at once clone the `dist` branch, which holds nothing else:

```bash
git clone --branch dist --depth 1 https://github.com/dandi-cache/qualifying-lfp-content-ids.git
```



## Repeated use

If you plan on using this cache regularly, clone the `derivatives` branch of this repository:

```bash
git clone --branch derivatives https://github.com/dandi-cache/qualifying-lfp-content-ids.git
```

Or, if you prefer [DataLad](https://www.datalad.org/):

```bash
datalad clone https://github.com/dandi-cache/qualifying-lfp-content-ids.git --branch derivatives
```

Then set up a CRON on your system to pull the latest version of the cache at your desired frequency.

For example, through `crontab -e`, add:

```bash
0 0 * * * git -C /path/to/qualifying-lfp-content-ids pull
```

This will minimize data overhead by only loading the most recent changes.

Each output is a store on the `derivatives` branch, where it is the whole log, uncompacted, and plain text:

```python
import json, pathlib

chunks = sorted(pathlib.Path("qualifying-lfp-content-ids/derivatives/qualifying_lfp_content_ids.zarr/stream/c").iterdir(), key=lambda path: int(path.name))
qualifying_lfp_content_ids = {key: value for chunk in chunks for line in chunk.read_text().splitlines() if line for key, value in json.loads(line).items()}
```
