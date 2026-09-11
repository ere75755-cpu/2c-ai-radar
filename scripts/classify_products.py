#!/usr/bin/env python3

"""
Product Hunt -> AI classification + lightweight product research.

V1 goals:
1. Use rules as a free first-pass AI filter.
2. Only send likely AI products to GPT-5.6 Luna.
3. Complete classification + lightweight research in one model call.
4. Products already researched by LLM will NOT be charged again.
5. Preserve manual dashboard annotations.
6. Fall back to rule-based results if LiteLLM/API fails.
7. Record token usage and estimated RMB cost in scan_status.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# Paths
# ============================================================

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG = ROOT / "config"


# ============================================================
# LiteLLM / model config
# ============================================================

MODEL = os.environ.get(
    "LITELLM_MODEL",
    "gpt-5.6-luna",
).strip()

API_KEY = os.environ.get(
    "LITELLM_API_KEY",
    "",
).strip()

BASE_URL = os.environ.get(
    "LITELLM_BASE_URL",
    "",
).strip()


# ============================================================
# Safety / cost config
# ============================================================

MAX_LLM_CALLS = int(
    os.environ.get(
        "MAX_LLM_CALLS",
        "50",
    )
)

INPUT_PRICE_RMB_PER_M = float(
    os.environ.get(
        "INPUT_PRICE_RMB_PER_M",
        "1.35",
    )
)

OUTPUT_PRICE_RMB_PER_M = float(
    os.environ.get(
        "OUTPUT_PRICE_RMB_PER_M",
        "8.11",
    )
)


# ============================================================
# Generic helpers
# ============================================================

def load(path: Path, default):
    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return default


def dump(path: Path, obj):
    path.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def getv(p, *names, default=None):
    for name in names:
        if name in p and p[name] is not None:
            return p[name]
    return default


def stable_id(source_id):
    digest = hashlib.sha1(
        str(source_id).encode()
    ).hexdigest()

    return (
        100_000_000
        + int(digest[:8], 16)
        % 1_000_000_000
    )


def normalized_topics(p):
    topics = getv(
        p,
        "topics",
        default=[],
    ) or []

    result = []

    for topic in topics:
        if isinstance(topic, dict):
            result.append(
                str(
                    topic.get("name")
                    or ""
                )
            )
        else:
            result.append(
                str(topic)
            )

    return result


def text_of(p):
    return " ".join(
        [
            str(
                getv(
                    p,
                    "name",
                    default="",
                )
                or ""
            ),
            str(
                getv(
                    p,
                    "tagline",
                    default="",
                )
                or ""
            ),
            str(
                getv(
                    p,
                    "description",
                    default="",
                )
                or ""
            ),
            " ".join(
                normalized_topics(p)
            ),
        ]
    ).lower()


# ============================================================
# Cheap first-pass AI filtering
# ============================================================

AI_WORDS = re.compile(
    r"\b("
    r"ai|"
    r"artificial intelligence|"
    r"llm|"
    r"large language model|"
    r"gpt|"
    r"copilot|"
    r"agent|"
    r"agents|"
    r"agentic|"
    r"generative|"
    r"genai|"
    r"machine learning|"
    r"ml model|"
    r"neural|"
    r"voice ai|"
    r"speech ai|"
    r"image generation|"
    r"video generation|"
    r"chatbot|"
    r"assistant|"
    r"reasoning model|"
    r"vision model|"
    r"ai-powered|"
    r"powered by ai|"
    r"ai native"
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
)


SCENE_HINTS = {
    "health": [
        "health",
        "medical",
        "doctor",
        "skin",
        "fitness",
        "nutrition",
        "sleep",
        "pregnancy",
        "period",
        "pet health",
    ],
    "education": [
        "learn",
        "study",
        "education",
        "tutor",
        "language",
        "exam",
        "course",
    ],
    "work": [
        "productivity",
        "meeting",
        "notes",
        "research",
        "spreadsheet",
        "presentation",
        "workflow",
        "email",
        "calendar",
    ],
    "career": [
        "job",
        "resume",
        "interview",
        "career",
        "recruit",
    ],
    "finance": [
        "finance",
        "invest",
        "stock",
        "budget",
        "tax",
        "insurance",
    ],
    "travel": [
        "travel",
        "trip",
        "hotel",
        "flight",
        "itinerary",
    ],
    "shopping": [
        "shopping",
        "commerce",
        "product discovery",
        "fashion",
        "beauty",
        "checkout",
    ],
    "content": [
        "creator",
        "video",
        "image",
        "music",
        "writing",
        "content",
        "social media",
        "design",
    ],
    "relationship": [
        "dating",
        "relationship",
        "friend",
        "social",
    ],
    "home": [
        "home",
        "interior",
        "house",
        "family",
        "parenting",
    ],
}


def is_ai_candidate(p):
    """
    Free pre-filter.

    False:
    Do not spend LLM cost on this product.

    True:
    Product is worth sending to Luna for a proper judgement.
    """

    text = text_of(p)

    topics = " ".join(
        normalized_topics(p)
    ).lower()

    if AI_WORDS.search(text):
        return True

    if any(
        hint in topics
        for hint in AI_TOPIC_HINTS
    ):
        return True

    return False


# ============================================================
# Free rule-based fallback
# ============================================================

def rule_classify(p, scenes):
    if not scenes:
        raise RuntimeError(
            "config/scenes.json contains no scenes"
        )

    text = text_of(p)
    is_ai = is_ai_candidate(p)

    best = None
    best_score = -1

    english_words = set(
        re.findall(
            r"[a-zA-Z]{3,}",
            text,
        )
    )

    for scene_item in scenes:
        domain = str(
            scene_item.get(
                "domain",
                "",
            )
        )

        scene_name = str(
            scene_item.get(
                "scene",
                "",
            )
        )

        need = str(
            scene_item.get(
                "need",
                "",
            )
        )

        scene_text = (
            f"{domain} "
            f"{scene_name} "
            f"{need}"
        ).lower()

        scene_words = set(
            re.findall(
                r"[a-zA-Z]{3,}",
                scene_text,
            )
        )

        score = len(
            english_words
            & scene_words
        )

        if any(
            k in text
            for k in SCENE_HINTS["health"]
        ) and (
            "健康" in domain
            or "医疗" in domain
            or "养宠" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["education"]
        ) and (
            "学习" in domain
            or "教育" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["work"]
        ) and (
            "工作" in domain
            or "生产力" in domain
            or "办公" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["career"]
        ) and (
            "求职" in domain
            or "职业" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["finance"]
        ) and (
            "金融" in domain
            or "财富" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["travel"]
        ) and (
            "旅行" in domain
            or "旅游" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["shopping"]
        ) and (
            "商品" in domain
            or "消费" in domain
            or "购物" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["content"]
        ) and (
            "创作" in domain
            or "内容" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["relationship"]
        ) and (
            "情感" in domain
            or "社交" in domain
        ):
            score += 3

        if any(
            k in text
            for k in SCENE_HINTS["home"]
        ) and (
            "家庭" in domain
            or "家居" in domain
            or "养娃" in domain
        ):
            score += 2

        if score > best_score:
            best = scene_item
            best_score = score

    if best is None:
        best = scenes[0]

    confidence = min(
        0.82,
        0.30
        + 0.08
        * max(
            best_score,
            0,
        ),
    )

    votes = int(
        getv(
            p,
            "votesCount",
            "votes_count",
            default=0,
        )
        or 0
    )

    priority = 1

    if is_ai:
        if votes >= 500:
            priority = 4
        elif votes >= 200:
            priority = 3
        else:
            priority = 2

    return {
        "isAI": is_ai,
        "sceneId": best["sceneId"],
        "sceneConfidence": round(
            confidence,
            2,
        ),
        "productCore": (
            getv(
                p,
                "tagline",
                default="",
            )
            or ""
        ),
        "whyInteresting": (
            "规则初筛判断为AI候选，"
            "尚未完成LLM研究。"
            if is_ai
            else
            "规则判断为非AI产品。"
        ),
        "pattern": "",
        "inspiration": "",
        "autoResearchPriority": priority,
        "priorityReason": "",
        "businessModel": "待核实",
        "newSceneCandidate": False,
        "suggestedNewScene": "",
        "newSceneReason": "",
        "researchModel": "",
        "researchStatus": "rules",
    }


# ============================================================
# LiteLLM client
# ============================================================

def build_client():
    from openai import OpenAI

    if not API_KEY:
        raise RuntimeError(
            "LITELLM_API_KEY is empty"
        )

    if not BASE_URL:
        raise RuntimeError(
            "LITELLM_BASE_URL is empty"
        )

    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )


# ============================================================
# Product / scene data sent to LLM
# ============================================================

def product_payload(p):
    """
    Only public Product Hunt information is sent to LiteLLM.

    Browser LocalStorage, TAM, teamNote, starred,
    manual research fields etc. are NOT sent.
    """

    return {
        "name": (
            getv(
                p,
                "name",
                default="",
            )
            or ""
        ),
        "tagline": (
            getv(
                p,
                "tagline",
                default="",
            )
            or ""
        ),
        "description": (
            getv(
                p,
                "description",
                default="",
            )
            or ""
        ),
        "topics": normalized_topics(p),
        "votes": int(
            getv(
                p,
                "votesCount",
                "votes_count",
                default=0,
            )
            or 0
        ),
        "comments": int(
            getv(
                p,
                "commentsCount",
                "comments_count",
                default=0,
            )
            or 0
        ),
        "website": (
            getv(
                p,
                "websiteUrl",
                "website_url",
                default="",
            )
            or ""
        ),
        "productHuntUrl": (
            getv(
                p,
                "producthuntUrl",
                "source_url",
                default="",
            )
            or ""
        ),
        "launchDate": (
            getv(
                p,
                "createdAt",
                "created_at",
                "launch_date",
                default="",
            )
            or ""
        ),
    }


def build_scene_payload(scenes):
    result = []

    for scene_item in scenes:
        result.append(
            {
                "sceneId": scene_item["sceneId"],
                "domain": scene_item.get(
                    "domain",
                    "",
                ),
                "scene": scene_item.get(
                    "scene",
                    "",
                ),
                "need": scene_item.get(
                    "need",
                    "",
                ),
            }
        )

    return result


# ============================================================
# LLM prompt
# ============================================================

SYSTEM_PROMPT = """
你是一名负责2C通用AI助手 / AI Chatbot的资深产品策略研究员。

你的目标不是给Product Hunt产品写宣传文案，而是帮助产品团队发现：

1. 新出现或正在增强的消费者需求
2. AI原生交互范式
3. Agent、Memory、多模态、长期任务等新能力
4. 可以迁移到通用AI Assistant的产品机制
5. 有明显用户验证、热度或商业化潜力的产品

【isAI判断】

只有当AI、机器学习或生成式模型是产品核心能力、
核心体验或关键价值来源时，isAI才为true。

如果只是普通软件附带一个AI按钮、AI功能很边缘，
不要因为产品写了AI就判断为重要AI产品。

【sceneId判断】

根据“用户真正想完成什么任务”选择最匹配的场景。

不要只按技术标签分类。
例如一个AI图片产品如果核心任务是帮助用户购物选衣，
应该优先匹配购物消费，而不只是内容创作。

【autoResearchPriority】

只能为1到5。

1：
基本无需研究。
普通套壳、同质化严重、需求较弱，
或和2C通用AI助手关系很远。

2：
一般。
产品成立，但缺乏明显的新产品机制或新需求。

3：
值得关注。
用户需求明确，产品具有一定差异化或增长信号。

4：
建议研究。
存在明显AI-native机制、新交互方式、新需求趋势，
或者对通用AI助手具有较强迁移价值。

5：
强烈建议研究。
代表新的产品范式、新的重要消费者需求、
强烈用户验证，或对通用AI Assistant有明显战略启发。

不要因为Product Hunt votes高就直接打4或5分。

优先级综合考虑：
- 用户需求是否真实
- AI是否带来明显体验跃迁
- 产品机制是否有新意
- 是否适合迁移到通用AI助手
- 是否存在用户或产品验证信号

【pattern】

抽象产品机制，而不是重复产品功能。

好的例子：
Camera as Input + Longitudinal Memory
Ambient Agent
Voice-first Companion
Generative UI
Personal Context + Proactive Action
Agentic Commerce
Continuous Monitoring + Proactive Alert

【inspiration】

回答这个问题：

“如果我们是一个2C通用AI Chatbot / Assistant团队，
这个产品最值得借鉴什么？”

【newSceneCandidate】

只有当现有场景确实难以覆盖，
并且产品代表一个值得长期追踪的新消费者需求时，
才设置为true。

【输出原则】

- 中文简洁
- 不夸大
- 不编造资料中不存在的信息
- 不确定的商业模式写“待核实”
- 每个文字字段尽量控制在60个中文字以内
- 只输出JSON对象
"""


# ============================================================
# LLM call
# ============================================================

def llm_classify(p, scenes):
    client = build_client()

    payload = product_payload(p)

    compact_scenes = build_scene_payload(
        scenes
    )

    user_prompt = f"""
请研究下面这个Product Hunt产品。

【产品公开信息】

{json.dumps(
    payload,
    ensure_ascii=False
)}

【可选场景库】

{json.dumps(
    compact_scenes,
    ensure_ascii=False
)}

请只返回下面结构的JSON：

{{
  "isAI": true,
  "sceneId": 123,
  "sceneConfidence": 0.90,
  "productCore": "一句中文，清楚说明产品是什么、为谁解决什么问题",
  "whyInteresting": "一句中文，说明为什么值得或不值得关注",
  "pattern": "一个简洁的AI-native产品机制",
  "inspiration": "一句中文，说明对2C通用AI助手的启发",
  "autoResearchPriority": 1,
  "priorityReason": "一句中文，解释为什么是这个优先级",
  "businessModel": "已知则简述，否则写待核实",
  "newSceneCandidate": false,
  "suggestedNewScene": "",
  "newSceneReason": ""
}}

强约束：

1. sceneId必须来自可选场景库
2. autoResearchPriority只能为1、2、3、4、5
3. sceneConfidence必须为0到1
4. 不要输出Markdown
5. 不要输出JSON之外的任何内容
"""

    input_tokens = 0
    output_tokens = 0

    # --------------------------------------------------------
    # First try Responses API
    # --------------------------------------------------------

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
                    "content": user_prompt,
                },
            ],
        )

        text = response.output_text.strip()

        usage = getattr(
            response,
            "usage",
            None,
        )

        if usage:
            input_tokens = int(
                getattr(
                    usage,
                    "input_tokens",
                    0,
                )
                or 0
            )

            output_tokens = int(
                getattr(
                    usage,
                    "output_tokens",
                    0,
                )
                or 0
            )

    # --------------------------------------------------------
    # LiteLLM gateways often expose Chat Completions
    # even when Responses API is unavailable.
    # --------------------------------------------------------

    except Exception as responses_error:
        print(
            "Responses API unavailable, "
            "trying Chat Completions: "
            f"{type(responses_error).__name__}: "
            f"{responses_error}"
        )

        response = (
            client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                response_format={
                    "type": "json_object"
                },
            )
        )

        text = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        usage = getattr(
            response,
            "usage",
            None,
        )

        if usage:
            input_tokens = int(
                getattr(
                    usage,
                    "prompt_tokens",
                    0,
                )
                or 0
            )

            output_tokens = int(
                getattr(
                    usage,
                    "completion_tokens",
                    0,
                )
                or 0
            )

    # Remove accidental markdown fences
    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text,
        flags=re.S,
    )

    result_raw = json.loads(text)

    allowed_scene_ids = {
        scene_item["sceneId"]
        for scene_item in scenes
    }

    returned_scene_id = (
        result_raw.get("sceneId")
    )

    if returned_scene_id not in allowed_scene_ids:
        raise ValueError(
            "LLM returned unknown sceneId: "
            f"{returned_scene_id}"
        )

    priority = int(
        result_raw.get(
            "autoResearchPriority",
            2,
        )
    )

    if priority < 1 or priority > 5:
        raise ValueError(
            "Invalid autoResearchPriority: "
            f"{priority}"
        )

    confidence = float(
        result_raw.get(
            "sceneConfidence",
            0,
        )
    )

    confidence = min(
        1.0,
        max(
            0.0,
            confidence,
        ),
    )

    return {
        "isAI": bool(
            result_raw.get(
                "isAI",
                True,
            )
        ),
        "sceneId": returned_scene_id,
        "sceneConfidence": round(
            confidence,
            2,
        ),
        "productCore": str(
            result_raw.get(
                "productCore",
                "",
            )
        ).strip(),
        "whyInteresting": str(
            result_raw.get(
                "whyInteresting",
                "",
            )
        ).strip(),
        "pattern": str(
            result_raw.get(
                "pattern",
                "",
            )
        ).strip(),
        "inspiration": str(
            result_raw.get(
                "inspiration",
                "",
            )
        ).strip(),
        "autoResearchPriority": priority,
        "priorityReason": str(
            result_raw.get(
                "priorityReason",
                "",
            )
        ).strip(),
        "businessModel": (
            str(
                result_raw.get(
                    "businessModel",
                    "待核实",
                )
            ).strip()
            or "待核实"
        ),
        "newSceneCandidate": bool(
            result_raw.get(
                "newSceneCandidate",
                False,
            )
        ),
        "suggestedNewScene": str(
            result_raw.get(
                "suggestedNewScene",
                "",
            )
        ).strip(),
        "newSceneReason": str(
            result_raw.get(
                "newSceneReason",
                "",
            )
        ).strip(),
        "researchModel": MODEL,
        "researchStatus": "llm",
        "_inputTokens": input_tokens,
        "_outputTokens": output_tokens,
    }


# ============================================================
# Reuse previous LLM research
# ============================================================

def classification_from_previous(prev):
    """
    Already researched by LLM.

    Reconstruct the classification from products_auto.json
    without making another paid API call.
    """

    return {
        "isAI": prev.get(
            "isAI",
            False,
        ),
        "sceneId": prev.get(
            "sceneId"
        ),
        "sceneConfidence": prev.get(
            "sceneConfidence",
            0,
        ),
        "productCore": prev.get(
            "intro",
            "",
        ),
        "whyInteresting": prev.get(
            "reason",
            "",
        ),
        "pattern": prev.get(
            "pattern",
            "",
        ),
        "inspiration": prev.get(
            "inspiration",
            "",
        ),
        "autoResearchPriority": prev.get(
            "autoResearchPriority",
            prev.get(
                "researchPriority",
                2,
            ),
        ),
        "priorityReason": prev.get(
            "priorityReason",
            "",
        ),
        "businessModel": prev.get(
            "biz",
            "待核实",
        ),
        "newSceneCandidate": prev.get(
            "newSceneCandidate",
            False,
        ),
        "suggestedNewScene": prev.get(
            "suggestedNewScene",
            "",
        ),
        "newSceneReason": prev.get(
            "newSceneReason",
            "",
        ),
        "researchModel": prev.get(
            "researchModel",
            MODEL,
        ),
        "researchStatus": "llm",
    }


# ============================================================
# Convert to dashboard product schema
# ============================================================

def to_dashboard(p, classification):
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
        raise ValueError(
            "missing source product id"
        )

    source_url = (
        getv(
            p,
            "producthuntUrl",
            "source_url",
            default="",
        )
        or ""
    )

    website_url = (
        getv(
            p,
            "websiteUrl",
            "website_url",
            default="",
        )
        or ""
    )

    created_at = (
        getv(
            p,
            "createdAt",
            "created_at",
            "launch_date",
            default="",
        )
        or ""
    )

    captured_at = (
        getv(
            p,
            "lastCapturedAt",
            "last_updated_at",
            "captured_at",
            default="",
        )
        or ""
    )

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

    rating = (
        getv(
            p,
            "reviewsRating",
            "reviews_rating",
            default=0,
        )
        or 0
    )

    thumbnail = (
        getv(
            p,
            "thumbnailUrl",
            "thumbnail_url",
            default="",
        )
        or ""
    )

    return {
        "id": stable_id(
            source_id
        ),

        "sourceProductId": source_id,

        "source": (
            "Product Hunt · Auto"
        ),

        "captureSource": (
            "Product Hunt"
        ),

        "name": (
            getv(
                p,
                "name",
                default="",
            )
            or ""
        ),

        "sceneId": classification[
            "sceneId"
        ],

        "intro": (
            classification.get(
                "productCore"
            )
            or getv(
                p,
                "tagline",
                default="",
            )
            or ""
        ),

        "biz": (
            classification.get(
                "businessModel"
            )
            or "待核实"
        ),

        "website": (
            source_url
            or website_url
        ),

        "demo": (
            website_url
            or source_url
        ),

        "feedback": (
            f"PH: {votes} votes · "
            f"{comments} comments · "
            f"rating {rating}"
        ),

        "reason": (
            classification.get(
                "whyInteresting",
                "",
            )
        ),

        "launchDate": str(
            created_at
        )[:10],

        "pattern": (
            classification.get(
                "pattern",
                "",
            )
        ),

        "inspiration": (
            classification.get(
                "inspiration",
                "",
            )
        ),

        "signal": (
            f"{votes} votes · "
            f"{comments} comments"
        ),

        "captureDate": str(
            captured_at
        )[:10],

        "foundedDate": "待核实",

        # ----------------------------------------
        # Manual fields
        # These must never be destroyed by sync.
        # ----------------------------------------

        "starred": False,

        "teamNote": "",

        "researchPriority": (
            classification.get(
                "autoResearchPriority",
                2,
            )
        ),

        "researchWhy": (
            classification.get(
                "priorityReason"
            )
            or classification.get(
                "whyInteresting",
                "",
            )
        ),

        # ----------------------------------------
        # Automatic research fields
        # ----------------------------------------

        "autoResearchPriority": (
            classification.get(
                "autoResearchPriority",
                2,
            )
        ),

        "priorityReason": (
            classification.get(
                "priorityReason",
                "",
            )
        ),

        "researchModel": (
            classification.get(
                "researchModel",
                "",
            )
        ),

        "researchStatus": (
            classification.get(
                "researchStatus",
                "rules",
            )
        ),

        "autoClassified": True,

        "isAI": (
            classification.get(
                "isAI",
                False,
            )
        ),

        "sceneConfidence": (
            classification.get(
                "sceneConfidence",
                0,
            )
        ),

        "newSceneCandidate": (
            classification.get(
                "newSceneCandidate",
                False,
            )
        ),

        "suggestedNewScene": (
            classification.get(
                "suggestedNewScene",
                "",
            )
        ),

        "newSceneReason": (
            classification.get(
                "newSceneReason",
                "",
            )
        ),

        # ----------------------------------------
        # Product Hunt metrics
        # ----------------------------------------

        "thumbnailUrl": thumbnail,
        "phVotes": votes,
        "phComments": comments,
        "phRating": rating,
    }


# ============================================================
# Main pipeline
# ============================================================

def main():
    raw = load(
        DATA / "products_raw.json",
        [],
    )

    taxonomy = load(
        CONFIG / "scenes.json",
        {},
    )

    scenes = taxonomy.get(
        "scenes",
        [],
    )

    if not scenes:
        raise RuntimeError(
            "config/scenes.json contains no scenes"
        )

    old_products = load(
        DATA / "products_auto.json",
        [],
    )

    old_by_source_id = {
        str(
            item.get(
                "sourceProductId"
            )
        ): item
        for item in old_products
        if item.get(
            "sourceProductId"
        )
    }

    use_llm = bool(
        API_KEY
        and BASE_URL
    )

    output = []
    errors = []

    llm_calls = 0
    reused_llm = 0
    rule_only = 0
    skipped_by_limit = 0

    total_input_tokens = 0
    total_output_tokens = 0

    for product in raw:
        source_id = str(
            getv(
                product,
                "sourceProductId",
                "source_product_id",
                default="",
            )
            or ""
        )

        previous = (
            old_by_source_id.get(
                source_id,
                {},
            )
        )

        try:

            # ========================================================
            # 1. Already researched by LLM
            # Reuse previous result for zero additional cost.
            # ========================================================

            if (
                previous
                and previous.get(
                    "researchStatus"
                )
                == "llm"
                and previous.get(
                    "researchModel"
                )
            ):

                classification = (
                    classification_from_previous(
                        previous
                    )
                )

                reused_llm += 1

            # ========================================================
            # 2. Clearly not an AI candidate
            # Do not waste money on model call.
            # ========================================================

            elif not is_ai_candidate(
                product
            ):

                classification = (
                    rule_classify(
                        product,
                        scenes,
                    )
                )

                rule_only += 1

            # ========================================================
            # 3. AI candidate + API available
            # Send to Luna.
            # ========================================================

            elif (
                use_llm
                and llm_calls
                < MAX_LLM_CALLS
            ):

                product_name = getv(
                    product,
                    "name",
                    default="unknown",
                )

                print(
                    f"[LLM "
                    f"{llm_calls + 1}"
                    f"/"
                    f"{MAX_LLM_CALLS}] "
                    f"{product_name}"
                )

                classification = (
                    llm_classify(
                        product,
                        scenes,
                    )
                )

                total_input_tokens += int(
                    classification.pop(
                        "_inputTokens",
                        0,
                    )
                    or 0
                )

                total_output_tokens += int(
                    classification.pop(
                        "_outputTokens",
                        0,
                    )
                    or 0
                )

                llm_calls += 1

                # Slight delay to be friendlier to
                # internal LiteLLM rate limits.
                time.sleep(
                    0.15
                )

            # ========================================================
            # 4. API unavailable or safety limit reached
            # Keep rule result temporarily.
            # It can be researched next run.
            # ========================================================

            else:

                classification = (
                    rule_classify(
                        product,
                        scenes,
                    )
                )

                if use_llm:
                    skipped_by_limit += 1

        except Exception as exc:

            product_name = getv(
                product,
                "name",
                default="unknown",
            )

            errors.append(
                f"{product_name}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            # If an old LLM result already exists,
            # never destroy it because of a transient API failure.

            if (
                previous
                and previous.get(
                    "researchStatus"
                )
                == "llm"
            ):

                classification = (
                    classification_from_previous(
                        previous
                    )
                )

            else:

                classification = (
                    rule_classify(
                        product,
                        scenes,
                    )
                )

        try:

            item = to_dashboard(
                product,
                classification,
            )

        except Exception as exc:

            product_name = getv(
                product,
                "name",
                default="unknown",
            )

            errors.append(
                f"{product_name}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            continue

        # ============================================================
        # Preserve manual fields
        # ============================================================

        for key in (
            "starred",
            "teamNote",
            "researchPriority",
            "researchWhy",
        ):

            if (
                key in previous
                and previous.get(key)
                not in (
                    None,
                    "",
                    False,
                )
            ):
                item[key] = previous[key]

        output.append(
            item
        )

    # Newest launches first,
    # then higher-vote products.
    output.sort(
        key=lambda item: (
            item.get(
                "launchDate",
                "",
            ),
            item.get(
                "phVotes",
                0,
            ),
        ),
        reverse=True,
    )

    dump(
        DATA
        / "products_auto.json",
        output,
    )

    # ================================================================
    # Cost estimation
    # ================================================================

    estimated_cost_rmb = (
        total_input_tokens
        / 1_000_000
        * INPUT_PRICE_RMB_PER_M
        +
        total_output_tokens
        / 1_000_000
        * OUTPUT_PRICE_RMB_PER_M
    )

    status = load(
        DATA / "scan_status.json",
        {},
    )

    status.update(
        {
            "classifiedAt": datetime.now(
                timezone.utc
            ).isoformat(),

            "classifier": (
                f"llm:{MODEL}"
                if use_llm
                else "rules"
            ),

            "llmModel": (
                MODEL
                if use_llm
                else ""
            ),

            "totalAutoProducts": len(
                output
            ),

            "aiProducts": sum(
                1
                for item in output
                if item.get(
                    "isAI"
                )
            ),

            "llmCallsThisRun": (
                llm_calls
            ),

            "llmReusedThisRun": (
                reused_llm
            ),

            "ruleOnlyThisRun": (
                rule_only
            ),

            "llmSkippedByLimit": (
                skipped_by_limit
            ),

            "llmInputTokensThisRun": (
                total_input_tokens
            ),

            "llmOutputTokensThisRun": (
                total_output_tokens
            ),

            "estimatedLlmCostRmbThisRun": round(
                estimated_cost_rmb,
                4,
            ),

            "classificationErrors": (
                errors[:30]
            ),
        }
    )

    dump(
        DATA
        / "scan_status.json",
        status,
    )

    # ================================================================
    # GitHub Actions log summary
    # ================================================================

    print("")
    print(
        "========== "
        "CLASSIFICATION SUMMARY "
        "=========="
    )

    print(
        "Model: "
        f"{MODEL if use_llm else 'rules only'}"
    )

    print(
        f"Total products: "
        f"{len(output)}"
    )

    print(
        "AI products: "
        f"{sum(
            1
            for item in output
            if item.get('isAI')
        )}"
    )

    print(
        f"New LLM calls: "
        f"{llm_calls}"
    )

    print(
        f"Reused old LLM research: "
        f"{reused_llm}"
    )

    print(
        f"Rules only: "
        f"{rule_only}"
    )

    print(
        "Skipped because "
        "MAX_LLM_CALLS reached: "
        f"{skipped_by_limit}"
    )

    print(
        "Input tokens this run: "
        f"{total_input_tokens}"
    )

    print(
        "Output tokens this run: "
        f"{total_output_tokens}"
    )

    print(
        "Estimated cost this run: "
        f"¥{estimated_cost_rmb:.4f}"
    )

    print(
        f"Errors: "
        f"{len(errors)}"
    )

    print(
        "================================"
    )


if __name__ == "__main__":
    main()
