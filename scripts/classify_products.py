#!/usr/bin/env python3
"""
Product Hunt -> AI classification + lightweight product research.

Design goals:
1. Use rules as a cheap first-pass AI filter.
2. Use GPT-5.6 Luna only for likely AI products.
3. Do NOT call the LLM again for products already researched by the LLM.
4. Preserve manual fields.
5. Fall back safely if the LLM/API fails.
6. Keep API credentials only in environment variables / GitHub Secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG = ROOT / "config"

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna").strip()
API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip()

# Safety valve: maximum number of NEW LLM calls in one GitHub Actions run.
MAX_LLM_CALLS = int(os.environ.get("MAX_LLM_CALLS", "50"))

# Used only for cost estimation in logs.
INPUT_PRICE_RMB_PER_M = float(os.environ.get("INPUT_PRICE_RMB_PER_M", "1.35"))
OUTPUT_PRICE_RMB_PER_M = float(os.environ.get("OUTPUT_PRICE_RMB_PER_M", "8.11"))


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def dump(path: Path, obj):
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def getv(p, *names, default=None):
    for n in names:
        if n in p and p[n] is not None:
            return p[n]
    return default


def stable_id(source_id):
    return 100_000_000 + int(
        hashlib.sha1(str(source_id).encode()).hexdigest()[:8], 16
    ) % 1_000_000_000


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
    return " ".join(
        [
            str(getv(p, "name", default="") or ""),
            str(getv(p, "tagline", default="") or ""),
            str(getv(p, "description", default="") or ""),
            " ".join(normalized_topics(p)),
        ]
    ).lower()


# Deliberately broad: this stage is only a CHEAP pre-filter.
AI_WORDS = re.compile(
    r"\b("
    r"ai|artificial intelligence|llm|large language model|gpt|"
    r"copilot|agent|agents|agentic|generative|genai|"
    r"machine learning|ml model|neural|"
    r"voice ai|speech ai|image generation|video generation|"
    r"chatbot|assistant|reasoning model|vision model|"
    r"ai-powered|powered by ai|ai native"
    r")\b",
    re.I,
)

AI_TOPIC_HINTS = (
    "artificial intelligence",
    "ai",
    "llm",
    "chatbot",
    "ai agents",
    "generative ai",
    "machine learning",
    "developer tools",
)

SCENE_HINTS = {
    "health": [
        "health", "medical", "doctor", "skin", "fitness", "nutrition",
        "sleep", "pregnancy", "period", "pet health"
    ],
    "education": [
        "learn", "study", "education", "tutor", "language", "exam", "course"
    ],
    "work": [
        "productivity", "meeting", "notes", "research", "spreadsheet",
        "presentation", "workflow", "email", "calendar"
    ],
    "career": ["job", "resume", "interview", "career", "recruit"],
    "finance": ["finance", "invest", "stock", "budget", "tax", "insurance"],
    "travel": ["travel", "trip", "hotel", "flight", "itinerary"],
    "shopping": [
        "shopping", "commerce", "product discovery", "fashion",
        "beauty", "checkout"
    ],
    "content": [
        "creator", "video", "image", "music", "writing",
        "content", "social media", "design"
    ],
    "relationship": ["dating", "relationship", "friend", "social"],
    "home": ["home", "interior", "house", "family", "parenting"],
}


def is_ai_candidate(p):
    """
    Cheap first-pass filter.
    False means: do not spend API money on this product.
    """
    text = text_of(p)
    topics = " ".join(normalized_topics(p)).lower()

    if AI_WORDS.search(text):
        return True

    if any(hint in topics for hint in AI_TOPIC_HINTS):
        return True

    return False


def rule_classify(p, scenes):
    """
    Free fallback classifier.
    Also used for non-AI products and API failure.
    """
    if not scenes:
        raise RuntimeError("config/scenes.json contains no scenes")

    text = text_of(p)
    is_ai = is_ai_candidate(p)

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

        if any(k in text for k in SCENE_HINTS["health"]) and (
            "健康" in d or "医疗" in d or "养宠" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["education"]) and (
            "学习" in d or "教育" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["work"]) and (
            "工作" in d or "生产力" in d or "办公" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["career"]) and (
            "求职" in d or "职业" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["finance"]) and (
            "金融" in d or "财富" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["travel"]) and (
            "旅行" in d or "旅游" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["shopping"]) and (
            "商品" in d or "消费" in d or "购物" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["content"]) and (
            "创作" in d or "内容" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["relationship"]) and (
            "情感" in d or "社交" in d
        ):
            score += 3

        if any(k in text for k in SCENE_HINTS["home"]) and (
            "家庭" in d or "家居" in d or "养娃" in d
        ):
            score += 2

        if score > best_score:
            best = s
            best_score = score

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
        "whyInteresting": (
            "自动规则初筛；尚未完成AI研究。"
            if is_ai
            else "规则判断为非AI产品。"
        ),
        "pattern": "",
        "inspiration": "",
        "autoResearchPriority": priority,
        "priorityReason": "",
        "businessModel": "",
        "newSceneCandidate": False,
        "suggestedNewScene": "",
        "newSceneReason": "",
        "researchModel": "",
        "researchStatus": "rules",
    }


def build_client():
    from openai import OpenAI

    kwargs = {"api_key": API_KEY}

    # If your company provides an OpenAI-compatible gateway,
    # save it as GitHub Secret OPENAI_BASE_URL.
    if BASE_URL:
        kwargs["base_url"] = BASE_URL

    return OpenAI(**kwargs)


def product_payload(p):
    """
    Only send fields needed for product understanding.
    Do NOT send local/manual/private dashboard annotations.
    """
    return {
        "name": getv(p, "name", default="") or "",
        "tagline": getv(p, "tagline", default="") or "",
        "description": getv(p, "description", default="") or "",
        "topics": normalized_topics(p),
        "votes": int(getv(p, "votesCount", "votes_count", default=0) or 0),
        "comments": int(
            getv(p, "commentsCount", "comments_count", default=0) or 0
        ),
        "website": getv(
            p, "websiteUrl", "website_url", default=""
        ) or "",
        "productHuntUrl": getv(
            p, "producthuntUrl", "source_url", default=""
        ) or "",
        "launchDate": getv(
            p, "createdAt", "created_at", "launch_date", default=""
        ) or "",
    }


def build_scene_payload(scenes):
    return [
        {
            "sceneId": s["sceneId"],
            "domain": s.get("domain", ""),
            "scene": s.get("scene", ""),
            "need": s.get("need", ""),
        }
        for s in scenes
    ]


SYSTEM_PROMPT = """
你是一名负责2C通用AI助手/AI Chatbot的资深产品策略研究员。

你的任务不是给产品写宣传文案，而是帮助产品团队发现：
1. 新出现或正在变强的消费者需求；
2. AI原生产品交互范式；
3. Agent、Memory、多模态、长期任务等新能力；
4. 能迁移到通用AI Assistant的产品机制；
5. 有较强用户热度、产品验证或商业化潜力的产品。

判断原则：

A. isAI
只有当AI/机器学习/生成式模型是产品核心能力、核心体验或关键价值来源时，
才判断为 true。
只是普通软件偶尔附带AI功能，不要轻易判断为高价值AI产品。

B. sceneId
从给定场景库中选择“用户核心需求”最匹配的一个场景。
不要仅根据产品技术能力分类，要根据消费者真正使用它完成什么任务来分类。

C. autoResearchPriority
1 = 基本无需研究：普通套壳、同质化严重、需求弱或和2C AI助手关系很远
2 = 一般：产品合理，但缺乏明显新意
3 = 值得关注：需求明确、产品有一定差异化
4 = 建议研究：有明显AI-native机制、需求趋势或迁移价值
5 = 强烈建议研究：代表新范式/新需求/强用户信号，对通用AI助手有明显启发

不要因为Product Hunt votes高就直接给4或5分。
优先级应综合考虑：
- 用户需求是否真实
- AI是否带来明显体验跃迁
- 产品机制是否有新意
- 是否适合迁移到通用AI助手
- 是否存在产品/用户验证信号

D. pattern
抽象产品机制，而不是重复产品功能。
例如：
Camera as Input + Longitudinal Memory
Ambient Agent
Voice-first Companion
Generative UI
Personal Context + Proactive Action
Agentic Commerce

E. inspiration
回答：
“如果我们是一个2C通用AI Chatbot/Assistant团队，这个产品最值得借鉴什么？”

F. newSceneCandidate
只有现有场景确实难以覆盖、且它代表一个值得长期追踪的新消费者需求时才设为true。

要求：
- 中文简洁
- 不夸大
- 不编造Product Hunt资料中没有的信息
- 不知道商业模式就写“待核实”
- 输出必须是一个JSON对象
"""


def llm_classify(p, scenes):
    client = build_client()

    payload = product_payload(p)
    compact_scenes = build_scene_payload(scenes)

    prompt = f"""
请研究下面这个Product Hunt产品。

【产品】
{json.dumps(payload, ensure_ascii=False)}

【可选场景库】
{json.dumps(compact_scenes, ensure_ascii=False)}

请只返回JSON，字段必须如下：

{{
  "isAI": true,
  "sceneId": 123,
  "sceneConfidence": 0.90,
  "productCore": "一句中文，清楚说明产品是什么、为谁解决什么问题",
  "whyInteresting": "一句中文，说明为什么值得或不值得关注",
  "pattern": "一个简洁的产品机制/AI-native pattern",
  "inspiration": "一句中文，说明对2C通用AI助手的启发",
  "autoResearchPriority": 1,
  "priorityReason": "一句中文，解释为什么是这个优先级",
  "businessModel": "已知则简述，否则写待核实",
  "newSceneCandidate": false,
  "suggestedNewScene": "",
  "newSceneReason": ""
}}

约束：
- sceneId必须来自可选场景库。
- autoResearchPriority只能是1到5的整数。
- sceneConfidence为0到1。
- 每个文字字段尽量控制在60个中文字以内。
"""

    input_tokens = 0
    output_tokens = 0

    # First try Responses API.
    try:
        response = client.responses.create(
            model=MODEL,
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        txt = response.output_text.strip()

        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = int(
                getattr(usage, "input_tokens", 0) or 0
            )
            output_tokens = int(
                getattr(usage, "output_tokens", 0) or 0
            )

    except Exception as responses_error:
        # Some company OpenAI-compatible gateways support Chat Completions
        # but not the newer Responses API. Fall back automatically.
        print(
            f"Responses API unavailable, trying Chat Completions: "
            f"{type(responses_error).__name__}"
        )

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={"type": "json_object"},
        )

        txt = response.choices[0].message.content.strip()

        usage = getattr(response, "usage", None)
        if usage:
            input_tokens = int(
                getattr(usage, "prompt_tokens", 0) or 0
            )
            output_tokens = int(
                getattr(usage, "completion_tokens", 0) or 0
            )

    txt = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        txt,
        flags=re.S,
    )

    out = json.loads(txt)

    allowed = {s["sceneId"] for s in scenes}

    if out.get("sceneId") not in allowed:
        raise ValueError(
            f"LLM returned unknown sceneId: {out.get('sceneId')}"
        )

    priority = int(out.get("autoResearchPriority", 2))
    if priority < 1 or priority > 5:
        raise ValueError(
            f"Invalid autoResearchPriority: {priority}"
        )

    confidence = float(out.get("sceneConfidence", 0))
    confidence = min(1.0, max(0.0, confidence))

    result = {
        "isAI": bool(out.get("isAI", True)),
        "sceneId": out["sceneId"],
        "sceneConfidence": round(confidence, 2),
        "productCore": str(out.get("productCore", "")).strip(),
        "whyInteresting": str(out.get("whyInteresting", "")).strip(),
        "pattern": str(out.get("pattern", "")).strip(),
        "inspiration": str(out.get("inspiration", "")).strip(),
        "autoResearchPriority": priority,
        "priorityReason": str(out.get("priorityReason", "")).strip(),
        "businessModel": str(
            out.get("businessModel", "待核实")
        ).strip() or "待核实",
        "newSceneCandidate": bool(
            out.get("newSceneCandidate", False)
        ),
        "suggestedNewScene": str(
            out.get("suggestedNewScene", "")
        ).strip(),
        "newSceneReason": str(
            out.get("newSceneReason", "")
        ).strip(),
        "researchModel": MODEL,
        "researchStatus": "llm",
        "_inputTokens": input_tokens,
        "_outputTokens": output_tokens,
    }

    return result


def classification_from_previous(prev):
    """
    Reconstruct classification/research result from a product already
    researched by the LLM so it costs zero additional API calls.
    """
    return {
        "isAI": prev.get("isAI", False),
        "sceneId": prev.get("sceneId"),
        "sceneConfidence": prev.get("sceneConfidence", 0),
        "productCore": prev.get("intro", ""),
        "whyInteresting": prev.get("reason", ""),
        "pattern": prev.get("pattern", ""),
        "inspiration": prev.get("inspiration", ""),
        "autoResearchPriority": prev.get(
            "autoResearchPriority",
            prev.get("researchPriority", 2),
        ),
        "priorityReason": prev.get("priorityReason", ""),
        "businessModel": prev.get("biz", "待核实"),
        "newSceneCandidate": prev.get(
            "newSceneCandidate", False
        ),
        "suggestedNewScene": prev.get(
            "suggestedNewScene", ""
        ),
        "newSceneReason": prev.get(
            "newSceneReason", ""
        ),
        "researchModel": prev.get("researchModel", MODEL),
        "researchStatus": "llm",
    }


def to_dashboard(p, c):
    source_id = str(
        getv(
            p,
            "sourceProductId",
            "source_product_id",
            default="",
        )
        or ""
    )

    if not source_id:
        raise ValueError("missing source product id")

    source_url = getv(
        p,
        "producthuntUrl",
        "source_url",
        default="",
    ) or ""

    website_url = getv(
        p,
        "websiteUrl",
        "website_url",
        default="",
    ) or ""

    created_at = getv(
        p,
        "createdAt",
        "created_at",
        "launch_date",
        default="",
    ) or ""

    captured_at = getv(
        p,
        "lastCapturedAt",
        "last_updated_at",
        "captured_at",
        default="",
    ) or ""

    votes = int(
        getv(
            p,
            "votesCount",
            "votes_count",
            default=0,
        )
        or 0
    )

    comments = int(
        getv(
            p,
            "commentsCount",
            "comments_count",
            default=0,
        )
        or 0
    )

    rating = getv(
        p,
        "reviewsRating",
        "reviews_rating",
        default=0,
    ) or 0

    thumb = getv(
        p,
        "thumbnailUrl",
        "thumbnail_url",
        default="",
    ) or ""

    return {
        "id": stable_id(source_id),
        "sourceProductId": source_id,
        "source": "Product Hunt · Auto",
        "captureSource": "Product Hunt",
        "name": getv(p, "name", default="") or "",
        "sceneId": c["sceneId"],
        "intro": c.get("productCore")
        or getv(p, "tagline", default="")
        or "",
        "biz": c.get("businessModel") or "待核实",
        "website": source_url or website_url,
        "demo": website_url or source_url,
        "feedback": (
            f"PH: {votes} votes · "
            f"{comments} comments · rating {rating}"
        ),
        "reason": c.get("whyInteresting", ""),
        "launchDate": str(created_at)[:10],
        "pattern": c.get("pattern", ""),
        "inspiration": c.get("inspiration", ""),
        "signal": f"{votes} votes · {comments} comments",
        "captureDate": str(captured_at)[:10],
        "foundedDate": "待核实",

        # Manual fields
        "starred": False,
        "teamNote": "",
        "researchPriority": c.get(
            "autoResearchPriority", 2
        ),
        "researchWhy": c.get(
            "priorityReason",
            c.get("whyInteresting", ""),
        ),

        # Automatic research fields
        "autoResearchPriority": c.get(
            "autoResearchPriority", 2
        ),
        "priorityReason": c.get("priorityReason", ""),
        "researchModel": c.get("researchModel", ""),
        "researchStatus": c.get(
            "researchStatus", "rules"
        ),
        "autoClassified": True,
        "isAI": c.get("isAI", False),
        "sceneConfidence": c.get(
            "sceneConfidence", 0
        ),
        "newSceneCandidate": c.get(
            "newSceneCandidate", False
        ),
        "suggestedNewScene": c.get(
            "suggestedNewScene", ""
        ),
        "newSceneReason": c.get(
            "newSceneReason", ""
        ),

        # PH metrics
        "thumbnailUrl": thumb,
        "phVotes": votes,
        "phComments": comments,
        "phRating": rating,
    }


def main():
    raw = load(DATA / "products_raw.json", [])
    taxonomy = load(CONFIG / "scenes.json", {})
    scenes = taxonomy.get("scenes", [])

    if not scenes:
        raise RuntimeError(
            "config/scenes.json contains no scenes"
        )

    old = load(DATA / "products_auto.json", [])

    old_by = {
        str(x.get("sourceProductId")): x
        for x in old
        if x.get("sourceProductId")
    }

    use_llm = bool(API_KEY)

    out = []
    errors = []

    llm_calls = 0
    reused_llm = 0
    rule_only = 0
    skipped_by_limit = 0

    total_input_tokens = 0
    total_output_tokens = 0

    for p in raw:
        source_id = str(
            getv(
                p,
                "sourceProductId",
                "source_product_id",
                default="",
            )
            or ""
        )

        prev = old_by.get(source_id, {})

        try:
            # ---------------------------------------------------------
            # CASE 1:
            # Already researched by an LLM -> REUSE, zero API cost.
            # PH metrics will still be refreshed below.
            # ---------------------------------------------------------
            if (
                prev
                and prev.get("researchStatus") == "llm"
                and prev.get("researchModel")
            ):
                c = classification_from_previous(prev)
                reused_llm += 1

            # ---------------------------------------------------------
            # CASE 2:
            # Not even an AI candidate -> free rules only.
            # ---------------------------------------------------------
            elif not is_ai_candidate(p):
                c = rule_classify(p, scenes)
                rule_only += 1

            # ---------------------------------------------------------
            # CASE 3:
            # AI candidate + API available + under safety limit.
            # Run GPT-5.6 Luna once.
            # ---------------------------------------------------------
            elif use_llm and llm_calls < MAX_LLM_CALLS:
                print(
                    f"[LLM {llm_calls + 1}/{MAX_LLM_CALLS}] "
                    f"{getv(p, 'name', default='unknown')}"
                )

                c = llm_classify(p, scenes)

                total_input_tokens += int(
                    c.pop("_inputTokens", 0) or 0
                )
                total_output_tokens += int(
                    c.pop("_outputTokens", 0) or 0
                )

                llm_calls += 1

                # Small pause makes company gateways/rate limits safer.
                time.sleep(0.15)

            # ---------------------------------------------------------
            # CASE 4:
            # AI candidate but no API key / max calls reached.
            # Keep it with rules and research next run.
            # ---------------------------------------------------------
            else:
                c = rule_classify(p, scenes)

                if use_llm:
                    skipped_by_limit += 1

        except Exception as e:
            errors.append(
                f"{getv(p, 'name', default='unknown')}: "
                f"{type(e).__name__}: {e}"
            )

            # IMPORTANT:
            # If API fails and we already had an old record,
            # do not destroy existing useful data.
            if (
                prev
                and prev.get("researchStatus") == "llm"
            ):
                c = classification_from_previous(prev)
            else:
                c = rule_classify(p, scenes)

        try:
            item = to_dashboard(p, c)

        except Exception as e:
            errors.append(
                f"{getv(p, 'name', default='unknown')}: "
                f"{type(e).__name__}: {e}"
            )
            continue

        # Preserve manual fields.
        # Automatic sync must never overwrite these.
        for key in (
            "starred",
            "teamNote",
            "researchPriority",
            "researchWhy",
        ):
            if key in prev and prev.get(key) not in (
                None,
                "",
                False,
            ):
                item[key] = prev[key]

        out.append(item)

    out.sort(
        key=lambda x: (
            x.get("launchDate", ""),
            x.get("phVotes", 0),
        ),
        reverse=True,
    )

    dump(DATA / "products_auto.json", out)

    estimated_cost_rmb = (
        total_input_tokens
        / 1_000_000
        * INPUT_PRICE_RMB_PER_M
        + total_output_tokens
        / 1_000_000
        * OUTPUT_PRICE_RMB_PER_M
    )

    status = load(DATA / "scan_status.json", {})

    status.update(
        {
            "classifiedAt": datetime.now(
                timezone.utc
            ).isoformat(),
            "classifier": (
                f"llm:{MODEL}" if use_llm else "rules"
            ),
            "llmModel": MODEL if use_llm else "",
            "totalAutoProducts": len(out),
            "aiProducts": sum(
                1 for p in out if p.get("isAI")
            ),
            "llmCallsThisRun": llm_calls,
            "llmReusedThisRun": reused_llm,
            "ruleOnlyThisRun": rule_only,
            "llmSkippedByLimit": skipped_by_limit,
            "llmInputTokensThisRun": total_input_tokens,
            "llmOutputTokensThisRun": total_output_tokens,
            "estimatedLlmCostRmbThisRun": round(
                estimated_cost_rmb, 4
            ),
            "classificationErrors": errors[:30],
        }
    )

    dump(DATA / "scan_status.json", status)

    print("")
    print("========== CLASSIFICATION SUMMARY ==========")
    print(f"Model: {MODEL if use_llm else 'rules only'}")
    print(f"Total products: {len(out)}")
    print(
        "AI products: "
        f"{sum(1 for p in out if p.get('isAI'))}"
    )
    print(f"New LLM calls: {llm_calls}")
    print(f"Reused old LLM research: {reused_llm}")
    print(f"Rules only: {rule_only}")
    print(
        f"Skipped because MAX_LLM_CALLS reached: "
        f"{skipped_by_limit}"
    )
    print(
        f"Input tokens this run: "
        f"{total_input_tokens}"
    )
    print(
        f"Output tokens this run: "
        f"{total_output_tokens}"
    )
    print(
        f"Estimated cost this run: "
        f"¥{estimated_cost_rmb:.4f}"
    )
    print(f"Errors: {len(errors)}")
    print("============================================")


if __name__ == "__main__":
    main()
