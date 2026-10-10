# hk-transit

Hong Kong's franchised bus (KMB/LWB, Citybus) and green minibus routes and stops, baked daily from public government data
and published for browsers to read in place.

Part of [AlreadyOpen](https://github.com/AlreadyOpen): open, re-runnable pipelines over public data.

| Output | Where | Format |
|---|---|---|
| Stops and routes (search table) | [huggingface.co/datasets/alreadyopen/hk-transit](https://huggingface.co/datasets/alreadyopen/hk-transit) `search/` | Parquet (ZSTD) + a view-only DuckDB file |
| Route geometry with road level and height, cut into 2 km tiles | [huggingface.co/buckets/alreadyopen/hk-transit](https://huggingface.co/buckets/alreadyopen/hk-transit) `bus/` | gzipped JSON tiles + `index.json` |

Both answer CORS and HTTP Range requests, so a web page can read them directly (DuckDB-Wasm for the Parquet, `fetch` +
`DecompressionStream('gzip')` for the tiles).

## Run it

```bash
pip install -r requirements.txt
python dtm.py 835000 820000   # Lands D 5 m DTM: 28.7 MB download, converted once (data/cache/dtm/)
python bus.py                 # routes, stops, road levels, heights -> data/out/gis/hk/bus/  (~40 min the first time: paced downloads)
python search.py              # -> data/out/gis/hk/search/
python publish.py             # -> Hugging Face (needs HF_TOKEN)
```

`HKT_DATA` moves the data folder. The GitHub Action `.github/workflows/bake.yml` runs all four daily.

## Sources and terms

Data from the Government of the Hong Kong SAR, via [DATA.GOV.HK](https://data.gov.hk) and the
[CSDI Portal](https://portal.csdi.gov.hk), used under their terms
([DATA.GOV.HK](https://data.gov.hk/en/terms-and-conditions), [CSDI](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC)).
The Government and the relevant organisations own the intellectual property rights in the data:

- Transport Department: Bus Route and Green Minibus Route (CSDI), road network centreline levels (CSDI IRN),
  green minibus routes and fares (DATA.GOV.HK)
- The Kowloon Motor Bus Co. (1933) Ltd / Long Win Bus Co. Ltd: routes, route-stops and stops (DATA.GOV.HK)
- Citybus Ltd: routes, route-stops and stops (DATA.GOV.HK)
- Lands Department: 5 m Digital Terrain Model (DATA.GOV.HK)

The outputs are derived from that data (stops snapped to route lines, road levels and heights added, cut into tiles). They are
provided as is, with no warranty, and are not endorsed by the Government or the operators.

Code: Apache-2.0.
