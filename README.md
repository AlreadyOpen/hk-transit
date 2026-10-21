# hk-transit

Hong Kong's franchised bus (KMB/LWB, Citybus), green minibus, MTR Bus, MTR and Light Rail routes and stops, baked daily from public government data
and published for browsers to read in place.

Part of [AlreadyOpen](https://github.com/milleleaf): open, re-runnable pipelines over public data.

| Output | Where | Format |
|---|---|---|
| Stops and routes (search table) | [huggingface.co/datasets/milleleaf/hk-transit](https://huggingface.co/datasets/milleleaf/hk-transit) `search/` | Parquet (ZSTD) + a view-only DuckDB file |
| Bus and minibus route geometry with road level and height, cut into 2 km tiles | [huggingface.co/buckets/milleleaf/hk-transit](https://huggingface.co/buckets/milleleaf/hk-transit) `bus/` | gzipped JSON tiles + `index.json` |
| MTR and Light Rail lines: stations, track, over/underground levels, heights | same bucket, `mtr.json` | JSON |

Both answer CORS and HTTP Range requests, so a web page can read them directly (DuckDB-Wasm for the Parquet, `fetch` +
`DecompressionStream('gzip')` for the tiles).

## Run it

```bash
pip install -r requirements.txt
python dtm.py 835000 820000   # Lands D 5 m DTM: 28.7 MB download, converted once (data/cache/dtm/)
python bus.py                 # routes, stops, road levels, heights -> data/out/gis/hk/bus/  (~40 min the first time: paced downloads)
python mtr.py hk              # MTR lines and stations -> data/out/gis/hk/mtr.json  (193 iB5000 sheets, ~4 GB fetched once)
python search.py              # -> data/out/gis/hk/search/
python publish.py             # -> Hugging Face (needs HF_TOKEN)
```

`HKT_DATA` moves the data folder. The GitHub Action `.github/workflows/bake.yml` runs them all daily.

Before uploading, `publish.py` writes `manifest.json` (sha256 and size of every file, counts, and the sources from
`sources.json`) and stops if a count drops more than 10% against the published manifest (`manifest.py`; a manual run with
"force" overrides a drop checked by hand). `check.py` runs on every push (`.github/workflows/check.yml`): no data, no token.

`certs/` holds one public certificate, the Hongkong Post intermediate that `open.hkmapservice.gov.hk` does not send; `mtr.py`
verifies the iB5000 downloads against certifi's roots plus it (it expires 2032-06-03).

## Sources and terms

Data from the Government of the Hong Kong SAR, via [DATA.GOV.HK](https://data.gov.hk) and the
[CSDI Portal](https://portal.csdi.gov.hk), used under their terms
([DATA.GOV.HK](https://data.gov.hk/en/terms-and-conditions), [CSDI](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC)).
The Government and the relevant organisations own the intellectual property rights in the data:

- Transport Department: Bus Route and Green Minibus Route (CSDI), road network centreline levels (CSDI IRN),
  green minibus routes and fares (DATA.GOV.HK)
- The Kowloon Motor Bus Co. (1933) Ltd / Long Win Bus Co. Ltd: routes, route-stops and stops (DATA.GOV.HK)
- Citybus Ltd: routes, route-stops and stops (DATA.GOV.HK)
- Lands Department: 5 m Digital Terrain Model (DATA.GOV.HK) and iB5000 topographic map sheets, rail lines and station points (CSDI)
- Transport Department: Road Network (2nd Generation) centrelines (DATA.GOV.HK, read from our pinned copy
  [alreadyopen/hk-roads](https://huggingface.co/datasets/milleleaf/hk-roads)), used to route MTR Bus lines
- MTR Corporation Limited: MTR lines and stations, Light Rail routes and stops, MTR Bus and Feeder Bus stops (DATA.GOV.HK)

The outputs are derived from that data (stops snapped to route lines, road levels and heights added, cut into tiles).
**MTR Bus lines are our own routing** along TD's road network through each route's stops in order (no route line is
published for them): an estimate, not MTR's official route geometry. They are
provided as is, with no warranty, and are not endorsed by the Government or the operators.

Code: Apache-2.0.
