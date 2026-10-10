"""Publish the bakes to Hugging Face (needs HF_TOKEN with write access to the alreadyopen org):
  dataset alreadyopen/hk-transit  search/{stops,routes}.parquet + search.duckdb, and the card (hf/README.md); one commit a run
  bucket  alreadyopen/hk-transit  bus/: the gzipped 2 km tiles + index.json, synced (tiles no longer baked are deleted);
                                  mtr.json: every MTR line with its stations, track, levels and heights
Both are public and read in place by browsers (CORS + HTTP Range), e.g. by DuckDB-Wasm and MapLibre.
Usage: python3 publish.py     (after bus.py, mtr.py hk and search.py)"""
import json, os
from huggingface_hub import HfApi, batch_bucket_files, create_bucket, sync_bucket
import sources

REPO = 'alreadyopen/hk-transit'

def main():
    hk = os.path.join(sources.OUT, 'gis', 'hk'); built = json.load(open(os.path.join(hk, 'bus', 'index.json')))['built']
    api = HfApi()
    api.create_repo(REPO, repo_type='dataset', exist_ok=True)
    api.upload_folder(repo_id=REPO, repo_type='dataset', folder_path=os.path.join(hk, 'search'), path_in_repo='search',
                      commit_message=f'search: bake of {built}')
    api.upload_file(path_or_fileobj=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hf', 'README.md'),
                    path_in_repo='README.md', repo_id=REPO, repo_type='dataset', commit_message='card')
    create_bucket(REPO, private=False, exist_ok=True)
    sync_bucket(os.path.join(hk, 'bus'), f'hf://buckets/{REPO}/bus', delete=True, quiet=True)
    batch_bucket_files(REPO, add=[(os.path.join(hk, 'mtr.json'), 'mtr.json')])
    print(json.dumps({'dataset': f'https://huggingface.co/datasets/{REPO}', 'bucket': f'https://huggingface.co/buckets/{REPO}', 'built': built}))

if __name__ == '__main__':
    main()
