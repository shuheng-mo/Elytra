"""SQL generation node.

This node is invoked twice in the worst case:

1. First pass — uses ``build_sql_generation_prompt`` with the few-shot
   template appropriate to the classified intent.
2. Retry pass (after self-correction) — the self-correction node will have
   set ``state['generated_sql']`` to the failed attempt and pushed the error
   into ``correction_history``. This node detects that and switches to the
   self-correction prompt instead.

It also picks the model via :func:`route_model` and writes
``model_used`` / ``complexity_score`` into the state on every pass so the
router can upgrade to the strong model on subsequent retries.
"""

from __future__ import annotations

import logging
import re

from src.agent.llm import chat_complete
from src.agent.nodes.schema_retrieval import render_schemas_for_prompt
from src.agent.prompts.self_correction import build_self_correction_prompt
from src.agent.prompts.sql_generation import build_sql_generation_prompt
from src.models.state import AgentState
from src.router.model_router import estimate_complexity, route_model

logger = logging.getLogger(__name__)


_CODE_FENCE_RE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE)


def _strip_sql(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = _CODE_FENCE_RE.sub("", text)
    return text.strip().rstrip(";").strip()


def generate_sql_node(state: AgentState) -> dict:
    """LangGraph node: writes ``generated_sql``, ``model_used``, ``complexity_score``."""
    intent = state.get("intent", "aggregation")
    retry_count = state.get("retry_count", 0)
    schemas = state.get("retrieved_schemas", [])
    schemas_text = render_schemas_for_prompt(schemas)

    model = route_model(intent, schemas, retry_count=retry_count)
    complexity = estimate_complexity(intent, schemas)

    # Choose between fresh generation and self-correction
    history = state.get("correction_history", [])
    if retry_count > 0 and history:
        last = history[-1]
        messages = build_self_correction_prompt(
            user_query=state["user_query"],
            retrieved_schemas=schemas_text,
            failed_sql=last.get("sql", state.get("generated_sql", "")),
            error_message=last.get("error", state.get("execution_error", "")),
            history=history[:-1],
        )
    else:
        messages = build_sql_generation_prompt(
            user_query=state["user_query"],
            retrieved_schemas=schemas_text,
            intent=intent,
        )

    try:
        result = chat_complete(model, messages, temperature=0.0)
        sql = _strip_sql(result.content)
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM SQL generation failed: %s", exc)
        return {
            "generated_sql": "",
            "execution_error": f"SQL generation failed: {exc}",
            "execution_success": False,
            "model_used": model,
            "complexity_score": complexity,
        }

    return {
        "generated_sql": sql,
        "model_used": model,
        "complexity_score": complexity,
        "token_count": state.get("token_count", 0) + result.token_count,
    }
