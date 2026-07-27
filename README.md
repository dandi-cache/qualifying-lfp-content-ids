# DANDI Cache: `qualifying-lfp-content-ids`

This cache catalogs NWB content IDs (from public Dandisets) that contain at least one
`ElectricalSeries` in `acquisition` with a sampling rate above 10 kHz.

It is derived from the [`content-id-to-usage-dandiset-path`](https://github.com/dandi-cache/content-id-to-usage-dandiset-path)
cache: for each content ID not yet processed, it resolves a usage path to a DANDI asset,
opens the asset remotely, and inspects the sampling rate of any acquisition `ElectricalSeries`
it contains.

Updated frequently.

Primarily for use by developers.



## One-time use

If you only plan to use this cache infrequently or from disparate locations, you can directly download the latest version of the cache as a compressed [JSON Lines](https://jsonlines.org/) file from the `dist` branch:

### Python API (recommended)

```python
import gzip
import json

import requests

url = "https://raw.githubusercontent.com/dandi-cache/qualifying-lfp-content-ids/refs/heads/dist/derivatives/qualifying_lfp_content_ids.jsonl.gz"
response = requests.get(url)
lines = gzip.decompress(data=response.content).decode("utf-8").splitlines()
qualifying_lfp_content_ids = [json.loads(line) for line in lines]
```

### Save to file

```bash
curl https://raw.githubusercontent.com/dandi-cache/qualifying-lfp-content-ids/refs/heads/dist/derivatives/qualifying_lfp_content_ids.jsonl.gz -o qualifying_lfp_content_ids.jsonl.gz
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
