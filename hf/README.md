---
pretty_name: Hong Kong bus, minibus and MTR routes and stops
license: other
license_name: hk-government-open-data
license_link: https://data.gov.hk/en/terms-and-conditions
tags:
  - geospatial
  - transport
  - hong-kong
  - bus
  - mtr
size_categories:
  - 10K<n<100K
configs:
  - config_name: stops
    data_files: search/stops.parquet
  - config_name: routes
    data_files: search/routes.parquet
---

# Hong Kong bus, minibus and MTR routes and stops

Every franchised bus (KMB/LWB, Citybus) and green minibus stop and MTR station in Hong Kong with the routes or lines serving it,
and every route direction and MTR line, rebuilt daily by [AlreadyOpen/hk-transit](https://github.com/AlreadyOpen/hk-transit).

- `search/stops.parquet`: `id, name_en, name_tc, co, lon, lat, routes` (one row per stop)
- `search/routes.parquet`: `key, co, route, bound, dest, stops, lon, lat` (one row per route direction; lon/lat = middle of its stops)
- `search/search.duckdb`: a view-only DuckDB file over the two Parquet files

A copy is mirrored to [Kaggle](https://www.kaggle.com/datasets/helenkwok/hk-transit) after every bake; Kaggle stores the gzipped
`bus/` tiles decompressed (`.json`), so their names and sha256 differ from `manifest.json`.

The geometry itself is in the bucket [alreadyopen/hk-transit](https://huggingface.co/buckets/alreadyopen/hk-transit): bus and
minibus routes sampled every 20 m with road level and height in 2 km gzipped JSON tiles (`bus/`), and every MTR line with its
stations, track and over/underground levels (`mtr.json`).

```python
import duckdb
duckdb.sql("SELECT co, count(*) FROM 'hf://datasets/alreadyopen/hk-transit/search/stops.parquet' GROUP BY co")
```

## Sources and terms

Data from the Government of the Hong Kong SAR via DATA.GOV.HK and the CSDI Portal, used under the
[DATA.GOV.HK terms](https://data.gov.hk/en/terms-and-conditions) and the [CSDI terms](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC).
The Government and the relevant organisations (Transport Department, KMB/LWB, Citybus, Lands Department, MTR Corporation) own the
intellectual property rights in the data. This dataset is derived from it, provided as is with no warranty, and not
endorsed by the Government or the operators. Full source list: the GitHub repository's README.
