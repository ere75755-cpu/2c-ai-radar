#!/usr/bin/env python3
"""
Product Hunt incremental fetcher with low-complexity pagination.

Reads:
  PRODUCTHUNT_TOKEN from environment

Writes:
  data/products_raw.json
  data/product_metrics.json
  data/scan_status.json
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API_URL = "https://api.producthunt.com/v2/api/graphql"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PRODUCTS_RAW = DATA_DIR / "products_raw.json"
PRODUCT_METRICS = DATA_DIR / "product_metrics.json"
SCAN_STATUS = DATA_DIR / "scan_status.json"

PAGE_SIZE = 15
LOOKBACK_DAYS = 3
REQUEST_SLEEP_SECONDS = 0.4


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def gql(query, variables, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "2c-ai-radar/1.0",
    }
    resp = requests.post(
        API_URL,
        headers=headers,
        json={"query": query, "variables": variables},
        timeout=30,
    )

    if resp.status_code >= 400:
        raise RuntimeError(
            f"Product Hunt HTTP {resp.status_code}: {resp.text[:1000]}"
        )

    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(
            "Product Hunt GraphQL error: "
            + json.dumps(payload["errors"], ensure_ascii=False)
        )

    return payload["data"]


QUERY = """
query FetchPosts(
  $after: String
  $postedAfter: DateTime!
  $postedBefore: DateTime!
  $first: Int!
) {
  posts(
    first: $first
    after: $after
    postedAfter: $postedAfter
    postedBefore: $postedBefore
    order: NEWEST
  ) {
    edges {
      cursor
      node {
        id
        name
        tagline
        description
        url
        website
        createdAt
        featuredAt
        votesCount
        commentsCount
        reviewsRating
        thumbnail {
          url
        }
        topics(first: 8) {
          edges {
            node {
              id
              name
              slug
            }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def normalize_post(node, captured_at):
    topics = []
    for edge in ((node.get("topics") or {}).get("edges") or []):
        topic = edge.get("node") or {}
        topics.append(
            {
                "id": topic.get("id"),
                "name": topic.get("name"),
                "slug": topic.get("slug"),
            }
        )

    thumb = node.get("thumbnail") or {}

    return {
        "source": "Product Hunt",
        "source_product_id": str(node.get("id") or ""),
        "name": node.get("name") or "",
        "tagline": node.get("tagline") or "",
        "description": node.get("description") or "",
        "source_url": node.get("url") or "",
        "website_url": node.get("website") or "",
        "thumbnail_url": thumb.get("url") or "",
        "launch_date": node.get("featuredAt") or node.get("createdAt"),
        "created_at": node.get("createdAt"),
        "featured_at": node.get("featuredAt"),
        "votes_count": node.get("votesCount"),
        "comments_count": node.get("commentsCount"),
        "reviews_rating": node.get("reviewsRating"),
        "topics": topics,
        "captured_at": captured_at,
        "last_updated_at": captured_at,
    }


def main():
    token = os.environ.get("PRODUCTHUNT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing PRODUCTHUNT_TOKEN environment variable")

    now = datetime.now(timezone.utc)
    posted_after = now - timedelta(days=LOOKBACK_DAYS)
    posted_before = now + timedelta(minutes=5)
    captured_at = now.isoformat()

    existing = load_json(PRODUCTS_RAW, [])
    existing_by_id = {
        str(x.get("source_product_id")): x
        for x in existing
        if x.get("source_product_id")
    }

    metrics = load_json(PRODUCT_METRICS, [])
    seen_metric_keys = {
        (str(x.get("source_product_id")), x.get("date"))
        for x in metrics
    }

    cursor = None
    fetched_count = 0
    page_no = 0

    while True:
        page_no += 1
        variables = {
            "after": cursor,
            "postedAfter": posted_after.isoformat(),
            "postedBefore": posted_before.isoformat(),
            "first": PAGE_SIZE,
        }

        data = gql(QUERY, variables, token)
        conn = data["posts"]
        edges = conn.get("edges") or []

        print(f"Fetched page {page_no}: {len(edges)} posts")

        for edge in edges:
            node = edge.get("node") or {}
            pid = str(node.get("id") or "")
            if not pid:
                continue

            fresh = normalize_post(node, captured_at)

            old = existing_by_id.get(pid)
            if old:
                merged = dict(old)
                merged.update(fresh)
                if old.get("captured_at"):
                    merged["captured_at"] = old["captured_at"]
                existing_by_id[pid] = merged
            else:
                existing_by_id[pid] = fresh

            metric_date = now.date().isoformat()
            metric_key = (pid, metric_date)
            if metric_key not in seen_metric_keys:
                metrics.append(
                    {
                        "source": "Product Hunt",
                        "source_product_id": pid,
                        "date": metric_date,
                        "votes_count": node.get("votesCount"),
                        "comments_count": node.get("commentsCount"),
                        "reviews_rating": node.get("reviewsRating"),
                    }
                )
                seen_metric_keys.add(metric_key)

            fetched_count += 1

        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break

        cursor = page_info.get("endCursor")
        if not cursor:
            break

        time.sleep(REQUEST_SLEEP_SECONDS)

    products_out = sorted(
        existing_by_id.values(),
        key=lambda x: (x.get("launch_date") or "", x.get("name") or ""),
        reverse=True,
    )

    save_json(PRODUCTS_RAW, products_out)
    save_json(PRODUCT_METRICS, metrics)
    save_json(
        SCAN_STATUS,
        {
            "source": "Product Hunt",
            "last_scan_at": captured_at,
            "lookback_days": LOOKBACK_DAYS,
            "page_size": PAGE_SIZE,
            "fetched_this_run": fetched_count,
            "total_products_raw": len(products_out),
            "status": "success",
        },
    )

    print(
        f"Done. fetched_this_run={fetched_count}, "
        f"total_products_raw={len(products_out)}"
    )


if __name__ == "__main__":
    main()
