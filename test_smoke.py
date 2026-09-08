import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_taxonomy_ids_unique():
    s=json.loads((ROOT/'config/scenes.json').read_text())['scenes']; ids=[x['sceneId'] for x in s]; assert len(ids)==len(set(ids)) and len(ids)>50
def test_runtime_json_valid():
    for n in ['products_raw.json','products_auto.json','product_metrics.json','scan_status.json']: json.loads((ROOT/'data'/n).read_text())
