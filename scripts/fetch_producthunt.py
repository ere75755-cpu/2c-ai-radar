#!/usr/bin/env python3
"""Fetch recent Product Hunt launches via official GraphQL API and keep history.

Required env: PRODUCTHUNT_TOKEN
Optional env: PH_LOOKBACK_DAYS (default 3), PH_MAX_PAGES (default 10)
"""
from __future__ import annotations
import json, os, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
API = "https://api.producthunt.com/v2/api/graphql"
TOKEN = os.environ.get("PRODUCTHUNT_TOKEN", "").strip()
LOOKBACK = int(os.environ.get("PH_LOOKBACK_DAYS", "3"))
MAX_PAGES = int(os.environ.get("PH_MAX_PAGES", "10"))
PAGE_SIZE = 50

QUERY = r"""
query RecentPosts($after: String, $postedAfter: DateTime!, $postedBefore: DateTime!, $first: Int!) {
  posts(first: $first, after: $after, postedAfter: $postedAfter, postedBefore: $postedBefore, order: NEWEST) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id name slug tagline description url website createdAt featuredAt votesCount commentsCount reviewsRating
        thumbnail { url }
        topics(first: 20) { edges { node { id name slug } } }
        makers { id name username }
      }
    }
  }
}
"""

def load(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default

def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def gql(variables):
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "2C-AI-Product-Radar/1.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Product Hunt HTTP {e.code}: {detail[:1000]}") from e
    if payload.get("errors"):
        raise RuntimeError("Product Hunt GraphQL error: " + json.dumps(payload["errors"], ensure_ascii=False))
    return payload["data"]["posts"]

def normalize(n):
    topics = [e["node"] for e in (n.get("topics") or {}).get("edges", [])]
    thumb = n.get("thumbnail") or {}
    return {
        "source": "Product Hunt", "sourceProductId": str(n["id"]), "name": n.get("name") or "",
        "slug": n.get("slug") or "", "tagline": n.get("tagline") or "", "description": n.get("description") or "",
        "producthuntUrl": n.get("url") or "", "websiteUrl": n.get("website") or "",
        "thumbnailUrl": thumb.get("url") or "", "createdAt": n.get("createdAt"), "featuredAt": n.get("featuredAt"),
        "votesCount": n.get("votesCount") or 0, "commentsCount": n.get("commentsCount") or 0,
        "reviewsRating": n.get("reviewsRating") or 0, "topics": topics, "makers": n.get("makers") or []
    }

def main():
    if not TOKEN:
        print("ERROR: PRODUCTHUNT_TOKEN is not configured.", file=sys.stderr); return 2
    now = datetime.now(timezone.utc)
    after_dt = now - timedelta(days=LOOKBACK)
    variables = {"after": None, "postedAfter": after_dt.isoformat().replace("+00:00","Z"),
                 "postedBefore": now.isoformat().replace("+00:00","Z"), "first": PAGE_SIZE}
    fetched=[]
    for _ in range(MAX_PAGES):
        page = gql(variables)
        fetched += [normalize(e["node"]) for e in page.get("edges", [])]
        pi = page.get("pageInfo") or {}
        if not pi.get("hasNextPage"): break
        variables["after"] = pi.get("endCursor")
    raw_path = DATA / "products_raw.json"
    existing = load(raw_path, [])
    by_id = {str(x.get("sourceProductId")): x for x in existing if x.get("sourceProductId") is not None}
    new_count=0
    for p in fetched:
        key=p["sourceProductId"]
        if key not in by_id:
            p["firstCapturedAt"] = now.isoformat(); new_count += 1
        else:
            p["firstCapturedAt"] = by_id[key].get("firstCapturedAt", now.isoformat())
        p["lastCapturedAt"] = now.isoformat()
        by_id[key] = {**by_id.get(key,{}), **p}
    merged = sorted(by_id.values(), key=lambda x: x.get("createdAt") or "", reverse=True)
    dump(raw_path, merged)

    metrics_path = DATA / "product_metrics.json"
    metrics = load(metrics_path, [])
    today = now.date().isoformat()
    metric_by_key={(str(m.get("sourceProductId")), m.get("date")):m for m in metrics}
    for p in fetched:
        metric_by_key[(p["sourceProductId"],today)]={
            "source":"Product Hunt","sourceProductId":p["sourceProductId"],"name":p["name"],"date":today,
            "capturedAt":now.isoformat(),"votesCount":p["votesCount"],"commentsCount":p["commentsCount"],
            "reviewsRating":p["reviewsRating"]
        }
    dump(metrics_path, sorted(metric_by_key.values(), key=lambda x:(x.get("date",""),x.get("name","")), reverse=True))
    status=load(DATA/"scan_status.json",{})
    status.update({"lastRunAt":now.isoformat(),"status":"fetched","source":"Product Hunt","fetchedThisRun":len(fetched),"newProducts":new_count,"errors":[]})
    dump(DATA/"scan_status.json",status)
    print(f"Fetched {len(fetched)} Product Hunt posts; {new_count} new; raw total {len(merged)}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
