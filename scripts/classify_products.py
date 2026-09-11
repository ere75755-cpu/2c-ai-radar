#!/usr/bin/env python3

"""
2C AI Radar - Local AI Research Pipeline V2

Run on a machine that can access the company LiteLLM gateway.

What V2 changes:
1. Separates automatic research priority from manual overrides.
2. Adds consumer / 2C relevance.
3. Prevents B2B developer infra from dominating the 2C opportunity radar.
4. Only proposes a new scene when the unmet scene is genuinely 2C-relevant.
5. Uses researchVersion to invalidate old LLM research when the rubric changes.
6. Reuses already completed V2 research to avoid repeated API cost.
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
# LiteLLM configuration
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


# Change this string whenever the research rubric materially changes.
# Old LLM results with a different version will be researched again.
RESEARCH_VERSION = os.environ.get(
    "RESEARCH_VERSION",
    "v2-consumer-fit-20260911",
).strip()


# ============================================================
# Helpers
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
        ) + "\n",
        encoding="utf-8",
    )


def getv(obj, *names, default=None):
    for name in names:
        if (
            name in obj
            and obj[name] is not None
        ):
            return obj[name]

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


def normalized_topics(product):
    topics = getv(
        product,
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


def text_of(product):
    return " ".join(
        [
            str(
                getv(
                    product,
                    "name",
                    default="",
                )
                or ""
            ),
            str(
                getv(
                    product,
                    "tagline",
                    default="",
                )
                or ""
            ),
            str(
                getv(
                    product,
                    "description",
                    default="",
                )
                or ""
            ),
            " ".join(
                normalized_topics(
                    product
                )
            ),
        ]
    ).lower()


# ============================================================
# Cheap AI pre-filter
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


def is_ai_candidate(product):
    text = text_of(product)

    topics = " ".join(
        normalized_topics(product)
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
# Rule fallback
# ============================================================

def rule_classify(product, scenes):
    if not scenes:
        raise RuntimeError(
            "config/scenes.json contains no scenes"
        )

    is_ai = is_ai_candidate(
        product
    )

    best_scene = scenes[0]

    votes = int(
        getv(
            product,
            "votesCount",
            "votes_count",
            default=0,
        )
        or 0
    )

    if not is_ai:
        priority = 1
        consumer_relevance = 1
    else:
        if votes >= 500:
            priority = 3
        elif votes >= 200:
            priority = 2
        else:
            priority = 2

        consumer_relevance = 2

    return {
        "isAI": is_ai,

        "sceneId": best_scene[
            "sceneId"
        ],

        "sceneConfidence": 0.2,

        "productCore": (
            getv(
                product,
                "tagline",
                default="",
            )
            or ""
        ),

        "whyInteresting": (
            "AI候选产品，尚未完成LLM研究。"
            if is_ai
            else
            "规则判断为非AI产品。"
        ),

        "pattern": "",
        "inspiration": "",

        "productType": (
            "unknown"
            if is_ai
            else "non-ai"
        ),

        "consumerRelevance": (
            consumer_relevance
        ),

        "consumerRelevanceReason": (
            "尚未完成LLM研究。"
            if is_ai
            else "非核心AI产品。"
        ),

        "autoResearchPriority": (
            priority
        ),

        "priorityReason": (
            "规则初筛，等待LLM研究。"
            if is_ai
            else "非核心AI产品。"
        ),

        "businessModel": "待核实",

        "newSceneCandidate": False,
        "suggestedNewScene": "",
        "newSceneReason": "",

        "researchModel": "",
        "researchStatus": "rules",
        "researchVersion": "",
    }


# ============================================================
# LiteLLM
# ============================================================

def build_client():
    from openai import OpenAI

    if not API_KEY:
        raise RuntimeError(
            "LITELLM_API_KEY is not configured"
        )

    if not BASE_URL:
        raise RuntimeError(
            "LITELLM_BASE_URL is not configured"
        )

    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL.rstrip("/") + "/",
        timeout=60.0,
        max_retries=1,
    )


# ============================================================
# Public data sent to LLM
# ============================================================

def product_payload(product):
    return {
        "name": (
            getv(
                product,
                "name",
                default="",
            )
            or ""
        ),

        "tagline": (
            getv(
                product,
                "tagline",
                default="",
            )
            or ""
        ),

        "description": (
            getv(
                product,
                "description",
                default="",
            )
            or ""
        ),

        "topics": normalized_topics(
            product
        ),

        "votes": int(
            getv(
                product,
                "votesCount",
                "votes_count",
                default=0,
            )
            or 0
        ),

        "comments": int(
            getv(
                product,
                "commentsCount",
                "comments_count",
                default=0,
            )
            or 0
        ),

        "website": (
            getv(
                product,
                "websiteUrl",
                "website_url",
                default="",
            )
            or ""
        ),

        "productHuntUrl": (
            getv(
                product,
                "producthuntUrl",
                "source_url",
                default="",
            )
            or ""
        ),

        "launchDate": (
            getv(
                product,
                "createdAt",
                "created_at",
                "launch_date",
                default="",
            )
            or ""
        ),
    }


def scene_payload(scenes):
    result = []

    for scene in scenes:
        result.append(
            {
                "sceneId": scene[
                    "sceneId"
                ],
                "domain": scene.get(
                    "domain",
                    "",
                ),
                "scene": scene.get(
                    "scene",
                    "",
                ),
                "need": scene.get(
                    "need",
                    "",
                ),
            }
        )

    return result


# ============================================================
# Prompt
# ============================================================

SYSTEM_PROMPT = """
你是一名负责2C通用AI助手 / AI Chatbot的资深产品策略研究员。

我们的目标不是收集所有AI产品，而是发现：
1. 消费者新需求
2. 2C AI产品的新交互
3. Personal Agent、Memory、多模态、长期任务等新范式
4. 可以迁移到通用AI Assistant的产品机制
5. 有真实用户信号或商业化潜力的产品

你必须区分：
“技术上很AI-native”
和
“对2C通用AI助手产品策略真正有价值”。

====================

【isAI】

只有AI、机器学习或生成式模型是产品核心体验、
核心能力或关键价值来源时，才为true。

普通软件只是加AI按钮，不算高价值AI产品。

====================

【productType】

只能选：

consumer
prosumer
b2b
developer
mixed
non-ai
unknown

含义：

consumer：
主要面向普通个人消费者。

prosumer：
主要面向创作者、自由职业者、专业个人用户。

b2b：
主要面向企业团队、业务部门。

developer：
主要面向开发者、AI工程师、技术团队。

mixed：
同时有明显个人与企业用户。

====================

【consumerRelevance】

1-5分。

5：
直接是高价值2C产品，
或验证了非常重要的新消费者需求。

4：
明显面向个人用户，
且AI体验或需求机制值得通用助手借鉴。

3：
不一定是纯2C，
但产品机制对通用AI助手具有较强迁移价值。

2：
主要是B2B / Developer工具，
只有局部机制值得参考。

1：
与2C通用AI助手几乎无关。

注意：
AI技术先进 ≠ consumerRelevance高。

====================

【sceneId】

按照用户真正想完成什么任务分类，
不要按底层技术分类。

如果产品主要是B2B / Developer工具，
但没有合适的2C场景，
可以选择最接近的已有场景，
同时降低sceneConfidence。

不要为了B2B开发工具强行创造新的2C场景。

====================

【newSceneCandidate】

只有同时满足以下条件才可以为true：

1. consumerRelevance >= 4
2. 产品代表真实的消费者需求
3. 现有场景库确实缺失这个需求

例如：

AI Agent监控平台
Agent评测工具
LLM开发基础设施

不应该因为场景库没有对应分类，
就新增2C场景。

====================

【autoResearchPriority】

这是“我们2C通用AI助手团队是否值得进一步研究”的优先级。

只能为1-5。

1：
基本无需研究。
非AI、普通套壳、弱需求，
或与2C方向明显无关。

2：
一般。
产品合理，但2C价值或新意有限。

3：
值得关注。
需求明确、机制有一定新意，
或虽然不是2C产品但存在明显迁移价值。

4：
建议重点研究。
具有明显AI-native机制、
新消费者需求、新交互、
Personal Agent / Memory / Multimodal /
长期任务等战略价值。

5：
强烈建议研究。
代表新产品范式、新消费者需求，
有强用户信号，
或可能影响通用AI Assistant路线。

一般情况下：

consumerRelevance <= 2 的产品
autoResearchPriority不应高于3。

纯Developer / B2B工具，
除非存在极强战略迁移价值，
通常不应给4或5。

Product Hunt votes高，
不能单独构成高分理由。

====================

【pattern】

抽象产品机制，
不要重复产品功能描述。

例如：

Camera as Input + Longitudinal Memory

Ambient Agent

Voice-first Companion

Generative UI

Personal Context + Proactive Action

Agentic Commerce

Continuous Monitoring + Proactive Alert

Cross-App Task Execution + Human Approval

====================

【inspiration】

回答：

如果我们是一个2C通用AI Chatbot / Assistant团队，
这个产品最值得借鉴什么？

如果启发很弱，
直接说“直接启发有限”。

====================

【输出原则】

中文简洁。
不要夸大。
不要编造。
无法确认的信息写“待核实”。

只输出JSON。
"""


# ============================================================
# LLM research
# ============================================================

def llm_classify(
    client,
    product,
    scenes,
):
    public_product = (
        product_payload(
            product
        )
    )

    public_scenes = (
        scene_payload(
            scenes
        )
    )

    user_prompt = f"""
请分析以下Product Hunt产品。

【产品】

{json.dumps(
    public_product,
    ensure_ascii=False
)}

【2C场景库】

{json.dumps(
    public_scenes,
    ensure_ascii=False
)}

只返回JSON：

{{
  "isAI": true,

  "productType": "consumer",

  "consumerRelevance": 4,

  "consumerRelevanceReason": "一句话解释为什么与2C通用AI助手相关或不相关",

  "sceneId": 123,

  "sceneConfidence": 0.90,

  "productCore": "一句话说明产品是什么、给谁用、解决什么需求",

  "whyInteresting": "一句话说明为什么值得或不值得关注",

  "pattern": "抽象的AI-native产品机制",

  "inspiration": "一句话说明对2C通用AI助手的启发",

  "autoResearchPriority": 4,

  "priorityReason": "一句话解释为什么值得这个研究优先级",

  "businessModel": "已知则简述，否则待核实",

  "newSceneCandidate": false,

  "suggestedNewScene": "",

  "newSceneReason": ""
}}

要求：

1.
sceneId必须来自场景库。

2.
consumerRelevance只能为1-5整数。

3.
autoResearchPriority只能为1-5整数。

4.
sceneConfidence必须为0-1。

5.
如果consumerRelevance <= 2，
原则上autoResearchPriority <= 3。

6.
只有consumerRelevance >= 4时，
newSceneCandidate才允许为true。

7.
开发者工具、AI infra、Agent评测、
模型监控等产品，
不能因为技术先进就给高2C研究优先级。

8.
每个文字字段尽量控制在60个中文字以内。

9.
不要输出Markdown。
"""

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

    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text,
        flags=re.S,
    )

    raw = json.loads(
        text
    )

    allowed_scene_ids = {
        scene["sceneId"]
        for scene in scenes
    }

    scene_id = raw.get(
        "sceneId"
    )

    if (
        scene_id
        not in allowed_scene_ids
    ):
        raise ValueError(
            f"Unknown sceneId: "
            f"{scene_id}"
        )

    consumer_relevance = int(
        raw.get(
            "consumerRelevance",
            2,
        )
    )

    consumer_relevance = max(
        1,
        min(
            5,
            consumer_relevance,
        ),
    )

    priority = int(
        raw.get(
            "autoResearchPriority",
            2,
        )
    )

    priority = max(
        1,
        min(
            5,
            priority,
        ),
    )

    # Hard safety rule:
    # low consumer relevance should not dominate
    # the 2C research radar.
    if (
        consumer_relevance <= 2
        and priority > 3
    ):
        priority = 3

    confidence = float(
        raw.get(
            "sceneConfidence",
            0,
        )
    )

    confidence = max(
        0,
        min(
            1,
            confidence,
        ),
    )

    product_type = str(
        raw.get(
            "productType",
            "unknown",
        )
    ).strip().lower()

    allowed_product_types = {
        "consumer",
        "prosumer",
        "b2b",
        "developer",
        "mixed",
        "non-ai",
        "unknown",
    }

    if (
        product_type
        not in allowed_product_types
    ):
        product_type = "unknown"

    new_scene_candidate = bool(
        raw.get(
            "newSceneCandidate",
            False,
        )
    )

    # Do not let B2B / dev infra generate
    # fake 2C scene proposals.
    if consumer_relevance < 4:
        new_scene_candidate = False

    usage = getattr(
        response,
        "usage",
        None,
    )

    input_tokens = 0
    output_tokens = 0

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

    return {
        "isAI": bool(
            raw.get(
                "isAI",
                True,
            )
        ),

        "productType": (
            product_type
        ),

        "consumerRelevance": (
            consumer_relevance
        ),

        "consumerRelevanceReason": (
            str(
                raw.get(
                    "consumerRelevanceReason",
                    "",
                )
            ).strip()
        ),

        "sceneId": (
            scene_id
        ),

        "sceneConfidence": round(
            confidence,
            2,
        ),

        "productCore": str(
            raw.get(
                "productCore",
                "",
            )
        ).strip(),

        "whyInteresting": str(
            raw.get(
                "whyInteresting",
                "",
            )
        ).strip(),

        "pattern": str(
            raw.get(
                "pattern",
                "",
            )
        ).strip(),

        "inspiration": str(
            raw.get(
                "inspiration",
                "",
            )
        ).strip(),

        "autoResearchPriority": (
            priority
        ),

        "priorityReason": str(
            raw.get(
                "priorityReason",
                "",
            )
        ).strip(),

        "businessModel": (
            str(
                raw.get(
                    "businessModel",
                    "待核实",
                )
            ).strip()
            or "待核实"
        ),

        "newSceneCandidate": (
            new_scene_candidate
        ),

        "suggestedNewScene": (
            str(
                raw.get(
                    "suggestedNewScene",
                    "",
                )
            ).strip()
            if new_scene_candidate
            else ""
        ),

        "newSceneReason": (
            str(
                raw.get(
                    "newSceneReason",
                    "",
                )
            ).strip()
            if new_scene_candidate
            else ""
        ),

        "researchModel": MODEL,
        "researchStatus": "llm",
        "researchVersion": (
            RESEARCH_VERSION
        ),

        "_inputTokens": (
            input_tokens
        ),

        "_outputTokens": (
            output_tokens
        ),
    }


# ============================================================
# Reuse previous V2 research
# ============================================================

def classification_from_previous(
    previous,
):
    return {
        "isAI": previous.get(
            "isAI",
            False,
        ),

        "productType": previous.get(
            "productType",
            "unknown",
        ),

        "consumerRelevance": (
            previous.get(
                "consumerRelevance",
                2,
            )
        ),

        "consumerRelevanceReason": (
            previous.get(
                "consumerRelevanceReason",
                "",
            )
        ),

        "sceneId": previous.get(
            "sceneId"
        ),

        "sceneConfidence": (
            previous.get(
                "sceneConfidence",
                0,
            )
        ),

        "productCore": previous.get(
            "intro",
            "",
        ),

        "whyInteresting": previous.get(
            "reason",
            "",
        ),

        "pattern": previous.get(
            "pattern",
            "",
        ),

        "inspiration": previous.get(
            "inspiration",
            "",
        ),

        "autoResearchPriority": (
            previous.get(
                "autoResearchPriority",
                2,
            )
        ),

        "priorityReason": previous.get(
            "priorityReason",
            "",
        ),

        "businessModel": previous.get(
            "biz",
            "待核实",
        ),

        "newSceneCandidate": (
            previous.get(
                "newSceneCandidate",
                False,
            )
        ),

        "suggestedNewScene": (
            previous.get(
                "suggestedNewScene",
                "",
            )
        ),

        "newSceneReason": (
            previous.get(
                "newSceneReason",
                "",
            )
        ),

        "researchModel": previous.get(
            "researchModel",
            MODEL,
        ),

        "researchStatus": "llm",

        "researchVersion": (
            previous.get(
                "researchVersion",
                RESEARCH_VERSION,
            )
        ),
    }


# ============================================================
# Manual override detection
# ============================================================

def has_manual_priority_override(
    previous,
):
    """
    Only preserve a previous researchPriority when it is
    explicitly marked as manual.

    Old rule-generated values such as:
    researchPriority = 2
    researchWhy = "规则初筛..."
    must NOT override new LLM research.
    """

    if not previous:
        return False

    if previous.get(
        "researchPriorityManual"
    ) is True:
        return True

    if (
        previous.get(
            "researchPrioritySource"
        )
        == "manual"
    ):
        return True

    return False


def has_manual_reason_override(
    previous,
):
    if not previous:
        return False

    if previous.get(
        "researchWhyManual"
    ) is True:
        return True

    if (
        previous.get(
            "researchWhySource"
        )
        == "manual"
    ):
        return True

    return False


# ============================================================
# Dashboard schema
# ============================================================

def to_dashboard(
    product,
    classification,
    previous=None,
):
    previous = previous or {}

    source_id = str(
        getv(
            product,
            "sourceProductId",
            "source_product_id",
            default="",
        )
        or ""
    )

    if not source_id:
        raise ValueError(
            "missing sourceProductId"
        )

    votes = int(
        getv(
            product,
            "votesCount",
            "votes_count",
            default=0,
        )
        or 0
    )

    comments = int(
        getv(
            product,
            "commentsCount",
            "comments_count",
            default=0,
        )
        or 0
    )

    rating = (
        getv(
            product,
            "reviewsRating",
            "reviews_rating",
            default=0,
        )
        or 0
    )

    source_url = (
        getv(
            product,
            "producthuntUrl",
            "source_url",
            default="",
        )
        or ""
    )

    website_url = (
        getv(
            product,
            "websiteUrl",
            "website_url",
            default="",
        )
        or ""
    )

    launch_date = str(
        getv(
            product,
            "createdAt",
            "created_at",
            "launch_date",
            default="",
        )
        or ""
    )[:10]

    capture_date = str(
        getv(
            product,
            "lastCapturedAt",
            "last_updated_at",
            "captured_at",
            default="",
        )
        or ""
    )[:10]

    thumbnail = (
        getv(
            product,
            "thumbnailUrl",
            "thumbnail_url",
            default="",
        )
        or ""
    )

    auto_priority = int(
        classification.get(
            "autoResearchPriority",
            2,
        )
        or 2
    )

    auto_reason = (
        classification.get(
            "priorityReason",
            "",
        )
        or classification.get(
            "whyInteresting",
            "",
        )
    )

    # Default dashboard priority = newest automatic research.
    research_priority = (
        auto_priority
    )

    research_why = (
        auto_reason
    )

    research_priority_source = (
        "auto"
    )

    research_why_source = (
        "auto"
    )

    # Only explicit manual override is preserved.
    if has_manual_priority_override(
        previous
    ):
        research_priority = int(
            previous.get(
                "researchPriority",
                auto_priority,
            )
        )

        research_priority_source = (
            "manual"
        )

    if has_manual_reason_override(
        previous
    ):
        research_why = str(
            previous.get(
                "researchWhy",
                auto_reason,
            )
            or ""
        )

        research_why_source = (
            "manual"
        )

    return {
        "id": stable_id(
            source_id
        ),

        "sourceProductId": (
            source_id
        ),

        "source": (
            "Product Hunt · Auto"
        ),

        "captureSource": (
            "Product Hunt"
        ),

        "name": (
            getv(
                product,
                "name",
                default="",
            )
            or ""
        ),

        "sceneId": (
            classification[
                "sceneId"
            ]
        ),

        "intro": (
            classification.get(
                "productCore"
            )
            or getv(
                product,
                "tagline",
                default="",
            )
            or ""
        ),

        "biz": classification.get(
            "businessModel",
            "待核实",
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

        "launchDate": (
            launch_date
        ),

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

        "captureDate": (
            capture_date
        ),

        "foundedDate": (
            previous.get(
                "foundedDate",
                "待核实",
            )
        ),

        # --------------------------------
        # Manual / dashboard fields
        # --------------------------------

        "starred": bool(
            previous.get(
                "starred",
                False,
            )
        ),

        "teamNote": (
            previous.get(
                "teamNote",
                "",
            )
            or ""
        ),

        "researchPriority": (
            research_priority
        ),

        "researchWhy": (
            research_why
        ),

        "researchPrioritySource": (
            research_priority_source
        ),

        "researchWhySource": (
            research_why_source
        ),

        "researchPriorityManual": (
            research_priority_source
            == "manual"
        ),

        "researchWhyManual": (
            research_why_source
            == "manual"
        ),

        # --------------------------------
        # Automatic research
        # --------------------------------

        "autoResearchPriority": (
            auto_priority
        ),

        "priorityReason": (
            classification.get(
                "priorityReason",
                "",
            )
        ),

        "productType": (
            classification.get(
                "productType",
                "unknown",
            )
        ),

        "consumerRelevance": (
            classification.get(
                "consumerRelevance",
                2,
            )
        ),

        "consumerRelevanceReason": (
            classification.get(
                "consumerRelevanceReason",
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

        "researchVersion": (
            classification.get(
                "researchVersion",
                "",
            )
        ),

        "autoClassified": True,

        "isAI": classification.get(
            "isAI",
            False,
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

        # --------------------------------
        # Product Hunt metrics
        # --------------------------------

        "thumbnailUrl": (
            thumbnail
        ),

        "phVotes": (
            votes
        ),

        "phComments": (
            comments
        ),

        "phRating": (
            rating
        ),
    }


# ============================================================
# Main
# ============================================================

def main():
    if not API_KEY:
        raise RuntimeError(
            "Missing local environment variable: "
            "LITELLM_API_KEY"
        )

    if not BASE_URL:
        raise RuntimeError(
            "Missing local environment variable: "
            "LITELLM_BASE_URL"
        )

    raw_products = load(
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

    previous_products = load(
        DATA / "products_auto.json",
        [],
    )

    previous_by_id = {
        str(
            item.get(
                "sourceProductId"
            )
        ): item

        for item in previous_products

        if item.get(
            "sourceProductId"
        )
    }

    client = build_client()

    output = []
    errors = []

    new_llm_calls = 0
    reused_llm = 0
    rules_only = 0
    skipped_by_limit = 0

    total_input_tokens = 0
    total_output_tokens = 0

    for product in raw_products:
        source_id = str(
            getv(
                product,
                "sourceProductId",
                "source_product_id",
                default="",
            )
            or ""
        )

        previous = previous_by_id.get(
            source_id,
            {},
        )

        try:

            # --------------------------------
            # Reuse only CURRENT V2 research.
            # Old V1 results are intentionally
            # researched again.
            # --------------------------------

            if (
                previous
                and previous.get(
                    "researchStatus"
                ) == "llm"
                and previous.get(
                    "researchModel"
                )
                and previous.get(
                    "researchVersion"
                )
                == RESEARCH_VERSION
            ):
                classification = (
                    classification_from_previous(
                        previous
                    )
                )

                reused_llm += 1

            # --------------------------------
            # Clearly non-AI:
            # zero LLM cost.
            # --------------------------------

            elif not is_ai_candidate(
                product
            ):
                classification = (
                    rule_classify(
                        product,
                        scenes,
                    )
                )

                rules_only += 1

            # --------------------------------
            # AI candidate:
            # Luna research.
            # --------------------------------

            elif (
                new_llm_calls
                < MAX_LLM_CALLS
            ):
                product_name = (
                    getv(
                        product,
                        "name",
                        default="unknown",
                    )
                )

                print(
                    f"[LLM "
                    f"{new_llm_calls + 1}"
                    f"/{MAX_LLM_CALLS}] "
                    f"{product_name}"
                )

                classification = (
                    llm_classify(
                        client,
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

                new_llm_calls += 1

                time.sleep(
                    0.15
                )

            # --------------------------------
            # Safety cap reached.
            # --------------------------------

            else:
                classification = (
                    rule_classify(
                        product,
                        scenes,
                    )
                )

                skipped_by_limit += 1

        except Exception as exc:
            product_name = getv(
                product,
                "name",
                default="unknown",
            )

            error_message = (
                f"{product_name}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            print(
                "[ERROR] "
                + error_message
            )

            errors.append(
                error_message
            )

            # Preserve previous current-version
            # LLM research if an API error occurs.
            if (
                previous
                and previous.get(
                    "researchStatus"
                ) == "llm"
                and previous.get(
                    "researchVersion"
                )
                == RESEARCH_VERSION
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

        item = to_dashboard(
            product,
            classification,
            previous=previous,
        )

        output.append(
            item
        )

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
        DATA / "products_auto.json",
        output,
    )

    estimated_cost_rmb = (
        total_input_tokens
        / 1_000_000
        * INPUT_PRICE_RMB_PER_M
        +
        total_output_tokens
        / 1_000_000
        * OUTPUT_PRICE_RMB_PER_M
    )

    ai_count = sum(
        1
        for item in output
        if item.get(
            "isAI"
        )
    )

    consumer_high_count = sum(
        1
        for item in output
        if int(
            item.get(
                "consumerRelevance",
                0,
            )
            or 0
        ) >= 4
    )

    priority_high_count = sum(
        1
        for item in output
        if int(
            item.get(
                "autoResearchPriority",
                0,
            )
            or 0
        ) >= 4
    )

    status = load(
        DATA / "scan_status.json",
        {},
    )

    status.update(
        {
            "classifiedAt": (
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),

            "classifier": (
                f"local-llm:{MODEL}"
            ),

            "llmModel": (
                MODEL
            ),

            "researchVersion": (
                RESEARCH_VERSION
            ),

            "totalAutoProducts": (
                len(output)
            ),

            "aiProducts": (
                ai_count
            ),

            "consumerRelevantProducts": (
                consumer_high_count
            ),

            "highPriorityProducts": (
                priority_high_count
            ),

            "llmCallsThisRun": (
                new_llm_calls
            ),

            "llmReusedThisRun": (
                reused_llm
            ),

            "ruleOnlyThisRun": (
                rules_only
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
        DATA / "scan_status.json",
        status,
    )

    print("")
    print(
        "========== CLASSIFICATION SUMMARY =========="
    )
    print(
        f"Research version: "
        f"{RESEARCH_VERSION}"
    )
    print(
        f"Model: {MODEL}"
    )
    print(
        f"Total products: "
        f"{len(output)}"
    )
    print(
        f"AI products: "
        f"{ai_count}"
    )
    print(
        f"Consumer relevance >= 4: "
        f"{consumer_high_count}"
    )
    print(
        f"Auto priority >= 4: "
        f"{priority_high_count}"
    )
    print(
        f"New LLM calls: "
        f"{new_llm_calls}"
    )
    print(
        f"Reused current V2 LLM research: "
        f"{reused_llm}"
    )
    print(
        f"Rules only: "
        f"{rules_only}"
    )
    print(
        "Skipped because "
        f"MAX_LLM_CALLS reached: "
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
        "============================================"
    )


if __name__ == "__main__":
    main()
