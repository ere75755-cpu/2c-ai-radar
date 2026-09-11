#!/usr/bin/env python3

"""
Local AI research pipeline for 2C AI Radar.

Run this script on a machine that can access the company LiteLLM gateway.

Pipeline:
Product Hunt raw products
    ->
cheap AI candidate filter
    ->
GPT-5.6 Luna
    ->
scene classification + lightweight product research
    ->
products_auto.json

Important:
- API key is read ONLY from local environment variables.
- Already researched products are reused and are NOT charged again.
- Manual fields are preserved.
- Non-AI candidates do not consume LLM tokens.
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


# Maximum NEW products researched each local run.
MAX_LLM_CALLS = int(
    os.environ.get(
        "MAX_LLM_CALLS",
        "50",
    )
)


# RMB pricing used only for cost estimation.
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

    # Fallback only.
    # Luna normally handles actual scene mapping.
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
        "sceneId": best_scene["sceneId"],
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
# Data sent to LLM
# ============================================================

def product_payload(product):
    """
    Only public Product Hunt information is sent.

    Browser LocalStorage data such as:
    TAM / teamNote / starred / manual score
    is NOT sent to the model.
    """

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
                "sceneId": scene["sceneId"],
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
# Product strategy prompt
# ============================================================

SYSTEM_PROMPT = """
你是一名负责2C通用AI助手 / AI Chatbot的资深产品策略研究员。

目标不是给Product Hunt产品写宣传文案，而是帮助产品团队发现：

1. 新出现或正在增强的消费者需求
2. AI原生产品交互范式
3. Agent、Memory、多模态、长期任务等新能力
4. 可以迁移到通用AI Assistant的产品机制
5. 有明显用户验证、热度或商业化潜力的产品


【isAI】

只有AI、机器学习或生成式模型是产品核心能力、
核心体验或关键价值来源时，才判断为true。

普通软件只是加一个AI按钮，不应因此成为高价值AI产品。


【sceneId】

按照“用户真正想完成什么任务”分类。

不是按照底层技术标签分类。


【autoResearchPriority】

只能为1到5：

1：
基本无需研究。普通套壳、同质化严重、需求较弱。

2：
一般。产品合理，但新意有限。

3：
值得关注。需求明确，有一定产品差异化。

4：
建议研究。有明显AI-native机制、新交互、
新需求趋势或较强迁移价值。

5：
强烈建议研究。代表新范式、新需求，
或对通用AI Assistant具有明显战略启发。

不要因为Product Hunt votes高就直接打高分。


【pattern】

抽象产品机制，而不是重复产品功能。

例如：

Camera as Input + Longitudinal Memory
Ambient Agent
Voice-first Companion
Generative UI
Personal Context + Proactive Action
Agentic Commerce
Continuous Monitoring + Proactive Alert


【inspiration】

回答：

如果我们是一个2C通用AI Chatbot / Assistant团队，
这个产品最值得借鉴什么？


【输出原则】

中文简洁。
不要夸大。
不要编造。
无法确认的信息写“待核实”。
只输出JSON。
"""


# ============================================================
# LLM classification + lightweight research
# ============================================================

def llm_classify(
    client,
    product,
    scenes,
):
    public_product = product_payload(
        product
    )

    public_scenes = scene_payload(
        scenes
    )

    user_prompt = f"""
请分析以下Product Hunt产品。

【产品】

{json.dumps(
    public_product,
    ensure_ascii=False
)}

【场景库】

{json.dumps(
    public_scenes,
    ensure_ascii=False
)}

只返回JSON：

{{
  "isAI": true,
  "sceneId": 123,
  "sceneConfidence": 0.90,

  "productCore": "一句话说明产品是什么以及解决什么需求",

  "whyInteresting": "一句话说明为什么值得或不值得关注",

  "pattern": "抽象的AI-native产品机制",

  "inspiration": "一句话说明对2C通用AI助手的启发",

  "autoResearchPriority": 1,

  "priorityReason": "一句话解释优先级",

  "businessModel": "已知则简述，否则待核实",

  "newSceneCandidate": false,

  "suggestedNewScene": "",

  "newSceneReason": ""
}}

要求：

sceneId必须来自场景库。

autoResearchPriority只能为1-5整数。

sceneConfidence必须为0-1。

每个文字字段尽量控制在60个中文字以内。

不要输出Markdown。
"""

    # Company LiteLLM gateway:
    # use OpenAI-compatible Chat Completions directly.
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

    raw = json.loads(text)

    allowed_scene_ids = {
        scene["sceneId"]
        for scene in scenes
    }

    scene_id = raw.get(
        "sceneId"
    )

    if scene_id not in allowed_scene_ids:
        raise ValueError(
            f"Unknown sceneId: {scene_id}"
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

        "sceneId": scene_id,

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

        "autoResearchPriority": priority,

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

        "newSceneCandidate": bool(
            raw.get(
                "newSceneCandidate",
                False,
            )
        ),

        "suggestedNewScene": str(
            raw.get(
                "suggestedNewScene",
                "",
            )
        ).strip(),

        "newSceneReason": str(
            raw.get(
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
# Reuse previous LLM result
# ============================================================

def classification_from_previous(
    previous,
):
    return {
        "isAI": previous.get(
            "isAI",
            False,
        ),

        "sceneId": previous.get(
            "sceneId"
        ),

        "sceneConfidence": previous.get(
            "sceneConfidence",
            0,
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

        "newSceneCandidate": previous.get(
            "newSceneCandidate",
            False,
        ),

        "suggestedNewScene": previous.get(
            "suggestedNewScene",
            "",
        ),

        "newSceneReason": previous.get(
            "newSceneReason",
            "",
        ),

        "researchModel": previous.get(
            "researchModel",
            MODEL,
        ),

        "researchStatus": "llm",
    }


# ============================================================
# Dashboard product format
# ============================================================

def to_dashboard(
    product,
    classification,
):
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

        "sceneId": classification[
            "sceneId"
        ],

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

        "reason": classification.get(
            "whyInteresting",
            "",
        ),

        "launchDate": (
            launch_date
        ),

        "pattern": classification.get(
            "pattern",
            "",
        ),

        "inspiration": classification.get(
            "inspiration",
            "",
        ),

        "signal": (
            f"{votes} votes · "
            f"{comments} comments"
        ),

        "captureDate": (
            capture_date
        ),

        "foundedDate": "待核实",

        # Manual fields
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

        # Automatic research
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

        # PH metrics
        "thumbnailUrl": thumbnail,
        "phVotes": votes,
        "phComments": comments,
        "phRating": rating,
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

            # Already researched by LLM:
            # ZERO new API cost.
            if (
                previous
                and previous.get(
                    "researchStatus"
                ) == "llm"
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

            # Clearly non-AI:
            # free rule classification.
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

            # AI candidate:
            # send to Luna.
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

            # Safety limit reached.
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

            # Never destroy old LLM research.
            if (
                previous
                and previous.get(
                    "researchStatus"
                ) == "llm"
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
        )

        # Preserve any manual fields already
        # contained in products_auto.json.
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
                f"local-llm:{MODEL}"
            ),

            "llmModel": MODEL,

            "totalAutoProducts": len(
                output
            ),

            "aiProducts": sum(
                1
                for item in output
                if item.get("isAI")
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

    ai_count = sum(
        1
        for item in output
        if item.get("isAI")
    )

    print("")
    print(
        "========== CLASSIFICATION SUMMARY =========="
    )
    print(
        f"Model: {MODEL}"
    )
    print(
        f"Total products: {len(output)}"
    )
    print(
        f"AI products: {ai_count}"
    )
    print(
        f"New LLM calls: {new_llm_calls}"
    )
    print(
        f"Reused old LLM research: {reused_llm}"
    )
    print(
        f"Rules only: {rules_only}"
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
        f"Errors: {len(errors)}"
    )
    print(
        "============================================"
    )


if __name__ == "__main__":
    main()
