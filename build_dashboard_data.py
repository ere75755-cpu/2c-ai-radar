#!/usr/bin/env python3
"""Validate runtime JSON and build a single status summary for the dashboard."""
import json
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'
def load(n,d):
    try:return json.loads((DATA/n).read_text(encoding='utf-8'))
    except Exception:return d
def dump(n,o):(DATA/n).write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
products=load('products_auto.json',[]); metrics=load('product_metrics.json',[]); status=load('scan_status.json',{})
status.update({'builtAt':datetime.now(timezone.utc).isoformat(),'totalAutoProducts':len(products),'aiProducts':sum(1 for p in products if p.get('isAI')),'newSceneCandidates':sum(1 for p in products if p.get('newSceneCandidate')),'metricSnapshots':len(metrics)})
dump('scan_status.json',status)
print(json.dumps(status,ensure_ascii=False))
