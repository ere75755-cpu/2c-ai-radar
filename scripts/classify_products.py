#!/usr/bin/env python3
"""
Classify Product Hunt raw products into the existing 2C scene taxonomy.

Compatible with both:
- old camelCase raw schema
- new snake_case raw schema

Works without OPENAI_API_KEY using conservative rules.
If OPENAI_API_KEY is configured, it can optionally use OpenAI for richer mapping.
"""

from __future__ import annotations
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG = ROOT / "config"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")

def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def getv(p, *names, default=None):
    for n in names:
        if n in p and p[n] is not None:
            return p[n]
    return default

def stable_id(source_id):
    return 100_000_000 + int(
        hashlib.sha1(str(source_id).encode()).hexdigest()[:8], 16
    ) % 1_000_000_000

AI_WORDS = re.compile(
    r"\b(ai|artificial intelligence|llm|gpt|copilot|agent|agents|"
    r"generative|machine learning|voice ai|image generation|"
    r"video generation|chatbot|assistant)\b",
    re.I,
)

SCENE_HINTS = {
    "health": ["health","medical","doctor","skin","fitness","nutrition","sleep","pregnancy","period","pet health"],
    "education": ["learn","study","education","tutor","language","exam","course"],
    "work": ["productivity","meeting","notes","research","spreadsheet","presentation","workflow","email","calendar"],
    "career": ["job","resume","interview","career","recruit"],
    "finance": ["finance","invest","stock","budget","tax","insurance"],
    "travel": ["travel","trip","hotel","flight","itinerary"],
    "shopping": ["shopping","commerce","product discovery","fashion","beauty","checkout"],
    "content": ["creator","video","image","music","writing","content","social media","design"],
    "relationship": ["dating","relationship","friend","social"],
    "home": ["home","interior","house","family","parenting"],
}

def normalized_topics(p):
    topics = getv(p, "topics", default=[]) or []
    out = []
    for t in topics:
        if isinstance(t, dict):
            out.append(str(t.get("name") or ""))
        else:
            out.append(str(t))
    return out

def text_of(p):
    return " ".join([
        str(getv(p, "name", default="") or ""),
        str(getv(p, "tagline", default="") or ""),
        str(getv(p, "description", default="") or ""),
        " ".join(normalized_topics(p)),
    ]).lower()

def rule_classify(p, scenes):
    text = text_of(p)
    topics_text = " ".join(normalized_topics(p)).lower()
    is_ai = bool(AI_WORDS.search(text)) or "artificial intelligence" in topics_text

    best = None
    best_score = -1

    english_words = set(re.findall(r"[a-zA-Z]{3,}", text))

    for s in scenes:
        d = str(s.get("domain", ""))
        scene = str(s.get("scene", ""))
        need = str(s.get("need", ""))
        st = f"{d} {scene} {need}".lower()

        scene_eng = set(re.findall(r"[a-zA-Z]{3,}", st))
        score = len(english_words & scene_eng)

        if any(k in text for k in SCENE_HINTS["health"]) and ("健康" in d or "医疗" in d or "养宠" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["education"]) and ("学习" in d or "教育" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["work"]) and ("工作" in d or "生产力" in d or "办公" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["career"]) and ("求职" in d or "职业" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["finance"]) and ("金融" in d or "财富" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["travel"]) and ("旅行" in d or "旅游" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["shopping"]) and ("商品" in d or "消费" in d or "购物" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["content"]) and ("创作" in d or "内容" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["relationship"]) and ("情感" in d or "社交" in d):
            score += 3
        if any(k in text for k in SCENE_HINTS["home"]) and ("家庭" in d or "家居" in d or "养娃" in d):
            score += 2

        if score > best_score:
            best, best_score = s, score

    if not scenes:
        raise RuntimeError("config/scenes.json contains no scenes")
    if best is None:
        best = scenes[0]

    confidence = min(0.82, 0.30 + 0.08 * max(best_score, 0))
    votes = int(getv(p, "votesCount", "votes_count", default=0) or 0)

    priority = 1
    if is_ai:
        priority = 4 if votes >= 500 else 3 if votes >= 200 else 2

    return {
        "isAI": is_ai,
        "sceneId": best["sceneId"],
        "sceneConfidence": round(confidence, 2),
        "productCore": getv(p, "tagline", default="") or "",
        "whyInteresting": "自动初筛：基于 Product Hunt 描述、Topics 与热度；建议人工复核。",
        "pattern": "待人工/LLM归纳",
        "inspiration": "待人工/LLM归纳",
        "autoResearchPriority": priority,
        "newSceneCandidate": False,
        "suggestedNewScene": "",
        "newSceneReason": "",
    }

def llm_classify(p, scenes):
    from openai import OpenAI

    client = OpenAI()
    compact = [
        {
            "sceneId": s["sceneId"],
            "domain": s["domain"],
            "scene": s["scene"],
            "need": s.get("need", ""),
        }
        for s in scenes
    ]

    prompt = f"""你是2C AI产品研究员。判断 Product Hunt 产品是否与AI相关，并且只能从给定场景库中选择最匹配sceneId。
若现有场景明显无法覆盖且这是有价值的新2C需求，可把 newSceneCandidate 设为 true，但仍需给出最接近的 sceneId。
输出严格JSON，不要markdown。字段：isAI(boolean), sceneId(integer), sceneConfidence(0-1), productCore(中文1句话), whyInteresting(中文1句话), pattern(英文/中文短标签), inspiration(中文1句话), autoResearchPriority(1-5), newSceneCandidate(boolean), suggestedNewScene(string), newSceneReason(string)。
产品：{json.dumps(p,ensure_ascii=False)}
场景库：{json.dumps(compact,ensure_ascii=False)}"""

    r = client.responses.create(model=MODEL, input=prompt)
    txt = r.output_text.strip()
    txt = re.sub(r'^```(?:json)?\s*|\s*```$', '', txt, flags=re.S)
    out = json.loads(txt)

    allowed = {s["sceneId"] for s in scenes}
    if out.get("sceneId") not in allowed:
        raise ValueError("LLM returned unknown sceneId")
    return out

def to_dashboard(p, c):
    source_id = str(getv(p, "sourceProductId", "source_product_id", default="") or "")
    if not source_id:
        raise ValueError("missing source product id")

    source_url = getv(p, "producthuntUrl", "source_url", default="") or ""
    website_url = getv(p, "websiteUrl", "website_url", default="") or ""
    created_at = getv(p, "createdAt", "created_at", "launch_date", default="") or ""
    captured_at = getv(p, "lastCapturedAt", "last_updated_at", "captured_at", default="") or ""
    votes = int(getv(p, "votesCount", "votes_count", default=0) or 0)
    comments = int(getv(p, "commentsCount", "comments_count", default=0) or 0)
    rating = getv(p, "reviewsRating", "reviews_rating", default=0) or 0
    thumb = getv(p, "thumbnailUrl", "thumbnail_url", default="") or ""

    return {
        "id": stable_id(source_id),
        "sourceProductId": source_id,
        "source": "Product Hunt · Auto",
        "captureSource": "Product Hunt",
        "name": getv(p, "name", default="") or "",
        "sceneId": c["sceneId"],
        "intro": c.get("productCore") or getv(p, "tagline", default="") or "",
        "biz": "待核实",
        "website": source_url or website_url,
        "demo": website_url or source_url,
        "feedback": f"PH: {votes} votes · {comments} comments · rating {rating}",
        "reason": c.get("whyInteresting", ""),
        "launchDate": str(created_at)[:10],
        "pattern": c.get("pattern", ""),
        "inspiration": c.get("inspiration", ""),
        "signal": f"{votes} votes · {comments} comments",
        "captureDate": str(captured_at)[:10],
        "foundedDate": "待核实",
        "starred": False,
        "teamNote": "",
        "researchPriority": c.get("autoResearchPriority", 2),
        "researchWhy": c.get("whyInteresting", ""),
        "autoResearchPriority": c.get("autoResearchPriority", 2),
        "autoClassified": True,
        "isAI": c.get("isAI", False),
        "sceneConfidence": c.get("sceneConfidence", 0),
        "newSceneCandidate": c.get("newSceneCandidate", False),
        "suggestedNewScene": c.get("suggestedNewScene", ""),
        "newSceneReason": c.get("newSceneReason", ""),
        "thumbnailUrl": thumb,
        "phVotes": votes,
        "phComments": comments,
        "phRating": rating,
    }

def main():
    raw = load(DATA / "products_raw.json", [])
    taxonomy = load(CONFIG / "scenes.json", {})
    scenes = taxonomy.get("scenes", [])
    old = load(DATA / "products_auto.json", [])
    old_by = {str(x.get("sourceProductId")): x for x in old if x.get("sourceProductId")}

    use_llm = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    out = []
    errors = []

    for p in raw:
        try:
            c = llm_classify(p, scenes) if use_llm else rule_classify(p, scenes)
        except Exception as e:
            errors.append(f"{getv(p,'name',default='unknown')}: {e}")
            c = rule_classify(p, scenes)

        try:
            item = to_dashboard(p, c)
        except Exception as e:
            errors.append(f"{getv(p,'name',default='unknown')}: {e}")
            continue

        prev = old_by.get(str(item.get("sourceProductId")), {})
        # Preserve manual fields if products_auto.json was ever hand-edited.
        for key in ("starred", "teamNote", "researchPriority", "researchWhy"):
            if key in prev and prev.get(key) not in (None, "", False):
                item[key] = prev[key]

        out.append(item)

    out.sort(
        key=lambda x: (x.get("launchDate", ""), x.get("phVotes", 0)),
        reverse=True,
    )
    dump(DATA / "products_auto.json", out)

    status = load(DATA / "scan_status.json", {})
    status.update({
        "classifiedAt": datetime.now(timezone.utc).isoformat(),
        "classifier": "openai" if use_llm else "rules",
        "totalAutoProducts": len(out),
        "aiProducts": sum(1 for p in out if p.get("isAI")),
        "classificationErrors": errors[:30],
    })
    dump(DATA / "scan_status.json", status)

    print(
        f"Classified {len(out)} products via "
        f"{'OpenAI '+MODEL if use_llm else 'rules'}; "
        f"AI products={sum(1 for p in out if p.get('isAI'))}; "
        f"errors={len(errors)}"
    )

if __name__ == "__main__":
    main()
