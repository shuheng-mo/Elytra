"""Intent classification node.

Uses a deterministic keyword-based classifier as the default path for
speed (~0ms vs ~10s LLM call). The LLM-based classifier is retained as
an opt-in via ``INTENT_CLASSIFIER=llm`` for deployments that need
higher accuracy on ambiguous queries.
"""

from __future__ import annotations

import json
import logging
import os
import re

from src.models.state import AgentState

logger = logging.getLogger(__name__)

# Whether to use LLM for intent classification.
# Default "heuristic" saves ~10s per query.
_USE_LLM = os.getenv("INTENT_CLASSIFIER", "heuristic").lower() == "llm"


def _heuristic_intent(query: str) -> str:
    """Keyword + regex classifier — covers the four intents with high recall."""
    q = query.lower()
    # exploration: analytical / causal questions
    if any(kw in q for kw in ("分析", "趋势分析", "为什么", "原因", "留存", "归因",
                                "insight", "探索", "分布", "异常")):
        return "exploration"
    # multi_join: explicit cross-table / comparison signals
    if any(kw in q for kw in ("和", "对比", "join", "关联", "相比", "合并",
                                "跨", "结合", "匹配")):
        return "multi_join"
    # aggregation: any aggregate function or ranking keyword
    if any(kw in q for kw in ("总", "平均", "求和", "合计", "数量", "占比", "比例",
                                "排行", "top", "最高", "最低", "趋势", "环比",
                                "同比", "增长率", "统计", "汇总", "多少",
                                "count", "sum", "avg", "max", "min")):
        return "aggregation"
    return "simple_query"


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise


def _llm_classify(user_query: str, token_count: int) -> dict:
    """LLM-based classifier (opt-in via INTENT_CLASSIFIER=llm)."""
    from src.agent.llm import chat_complete
    from src.agent.prompts.intent_classification import (
        INTENT_CLASSIFICATION_PROMPT,
        INTENT_LABELS,
    )
    from src.config import settings

    prompt = INTENT_CLASSIFICATION_PROMPT.format(user_query=user_query)
    messages = [{"role": "user", "content": prompt}]
    try:
        result = chat_complete(settings.default_cheap_model, messages, temperature=0.0)
        data = _extract_json(result.content)
        intent = data.get("intent", "").strip()
        if intent not in INTENT_LABELS:
            raise ValueError(f"unknown intent: {intent!r}")
        clarification = (data.get("clarification_question") or "").strip() or None
        return {
            "intent": intent,
            "clarification_question": clarification,
            "token_count": token_count + result.token_count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("intent LLM classification failed (%s); falling back to heuristic.", exc)
        return {
            "intent": _heuristic_intent(user_query),
            "clarification_question": None,
        }


def classify_intent_node(state: AgentState) -> dict:
    """LangGraph node: writes ``intent`` (and ``clarification_question``)."""
    user_query = state["user_query"]

    if _USE_LLM:
        return _llm_classify(user_query, state.get("token_count", 0))

    intent = _heuristic_intent(user_query)
    return {
        "intent": intent,
        "clarification_question": None,
    }
