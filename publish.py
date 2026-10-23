"""Publish the bakes to Hugging Face (needs HF_TOKEN with write access to the alreadyopen org):
  dataset milleleaf/hk-transit  search/{stops,routes}.parquet + search.duckdb, and the card (hf/README.md); one commit a run
  bucket  milleleaf/hk-transit  bus/: the gzipped 2 km tiles + index.json, synced (tiles no longer baked are deleted);
                                  mtr.json: every MTR line with its stations, track, levels and heights
                                  manifest.json: sha256 of every file, counts, sources (also in the dataset)
Nothing is uploaded unless the publish gate passes (manifest.py: no count drops > 10% against the published manifest).
Both are public and read in place by browsers (CORS + HTTP Range), e.g. by DuckDB-Wasm and MapLibre.
Usage: python3 publish.py     (after bus.py, mtr.py hk and search.py)"""
import glob, json, os, tempfile
import duckdb
from huggingface_hub import HfApi, batch_bucket_files, create_bucket, sync_bucket
import manifest, sources

REPO = 'milleleaf/hk-transit'

def dataset_views(db):
    """a view-only DuckDB whose views read the dataset's own Parquet over hf://, so it works from any folder (needs the parquet published first)"""
    v = duckdb.connect(db)
    for name in ('stops', 'routes'):
        v.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('hf://datasets/{REPO}/search/{name}.parquet')")
    v.close()

def main():
    hk = os.path.join(sources.OUT, 'gis', 'hk'); built = json.load(open(os.path.join(hk, 'bus', 'index.json')))['built']
    files = [os.path.relpath(p, hk) for d in ('bus', 'search') for p in glob.glob(os.path.join(hk, d, '*'))] + ['mtr.json']
    idx, mtr = json.load(open(os.path.join(hk, 'bus', 'index.json'))), json.load(open(os.path.join(hk, 'mtr.json')))
    n = lambda f: duckdb.sql(f"SELECT count(*) FROM '{os.path.join(hk, 'search', f)}'").fetchone()[0]
    m = manifest.manifest(hk, files, {'bus_routes': idx['routes'], 'bus_tiles': len(idx['tiles']), 'mtr_lines': len(mtr['lines']),
                                      'mtr_stations': sum(len(x['stations']) for x in mtr['lines']), 'stops': n('stops.parquet'),
                                      'routes': n('routes.parquet')}, built)
    manifest.gate(m, f'https://huggingface.co/buckets/{REPO}/resolve/manifest.json')
    mf = os.path.join(hk, 'manifest.json'); json.dump(m, open(mf, 'w'), indent=1)
    api = HfApi()
    api.create_repo(REPO, repo_type='dataset', exist_ok=True)
    sd = os.path.join(hk, 'search')
    assert sorted(os.listdir(sd)) == ['routes.parquet', 'search.duckdb', 'stops.parquet'], f'unexpected files in {sd}: {os.listdir(sd)}'
    api.upload_folder(repo_id=REPO, repo_type='dataset', folder_path=sd, path_in_repo='search', allow_patterns=['*.parquet'],
                      commit_message=f'search: bake of {built}')
    with tempfile.TemporaryDirectory() as t:     # the local search.duckdb names build paths (the GIS page); the dataset's names hf://
        db = os.path.join(t, 'search.duckdb'); dataset_views(db)
        api.upload_file(path_or_fileobj=db, path_in_repo='search/search.duckdb', repo_id=REPO, repo_type='dataset',
                        commit_message=f'search.duckdb: views over hf://datasets/{REPO}/search/')
    api.upload_file(path_or_fileobj=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hf', 'README.md'),
                    path_in_repo='README.md', repo_id=REPO, repo_type='dataset', commit_message='card')
    api.upload_file(path_or_fileobj=mf, path_in_repo='manifest.json', repo_id=REPO, repo_type='dataset', commit_message='manifest')
    create_bucket(REPO, private=False, exist_ok=True)
    sync_bucket(os.path.join(hk, 'bus'), f'hf://buckets/{REPO}/bus', delete=True, quiet=True)
    batch_bucket_files(REPO, add=[(os.path.join(hk, 'mtr.json'), 'mtr.json'), (mf, 'manifest.json')])   # the manifest last: the gate reads it
    print(json.dumps({'dataset': f'https://huggingface.co/datasets/{REPO}', 'bucket': f'https://huggingface.co/buckets/{REPO}', 'built': built, 'counts': m['counts']}))

if __name__ == '__main__':
    main()
