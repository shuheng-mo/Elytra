"""Intent classification node.

Calls the cheap LLM with the prompt from
``src.agent.prompts.intent_classification`` and writes the parsed intent +
clarification question (if any) into ``AgentState``.

Falls back to a deterministic keyword-based classifier when the LLM is
unavailable so the rest of the graph can still progress in tests.
"""

from __future__ import annotations

import json
import logging
import re

from src.agent.llm import chat_complete
from src.agent.prompts.intent_classification import (
    INTENT_CLASSIFICATION_PROMPT,
    INTENT_LABELS,
)
from src.config import settings
from src.models.state import AgentState

logger = logging.getLogger(__name__)


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


def _heuristic_intent(query: str) -> str:
    """Last-ditch keyword-based classifier used when the LLM is unreachable."""
    q = query.lower()
    if any(kw in q for kw in ("分析", "趋势分析", "为什么", "原因", "留存")):
        return "exploration"
    if any(kw in q for kw in ("和", "对比", "join", "关联", "相比")):
        return "multi_join"
    if any(kw in q for kw in ("总", "平均", "求和", "合计", "数量", "占比", "比例", "排行", "top", "最高", "最低", "趋势")):
        return "aggregation"
    return "simple_query"


def classify_intent_node(state: AgentState) -> dict:
    """LangGraph node: writes ``intent`` (and ``clarification_question``)."""
    user_query = state["user_query"]
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
            "token_count": state.get("token_count", 0) + result.token_count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("intent LLM classification failed (%s); using heuristic.", exc)
        return {
            "intent": _heuristic_intent(user_query),
            "clarification_question": None,
        }
