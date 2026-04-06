"""LangGraph state machine for the Elytra agent (PRD §5.2).

Topology
--------
::

    classify_intent
        ├── clarification ─────────────► format_clarification ──► END
        └── other intents ─► retrieve_schema ─► generate_sql
                                                     │
                                                     ▼
                                                execute_sql
                                                     │
                                ┌────────────────────┴────────────────────┐
                                │ success                                  │ failure
                                ▼                                          ▼
                          format_result                              retry_count < MAX?
                                │                                  ┌────────┴────────┐
                                ▼                                yes               no
                               END                                  │                 │
                                                                    ▼                 ▼
                                                            self_correction      format_error
                                                                    │                 │
                                                                    └─► generate_sql  ▼
                                                                                    END

The retry loop is bounded by ``settings.max_retry_count``. The
``self_correction`` node only manages bookkeeping (push history, bump
counter); the actual rewrite happens back in ``generate_sql`` because we want
the prompt-switching logic in exactly one place.
"""

from __future__ import annotations

import time
from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.agent.nodes.intent_classifier import classify_intent_node
from src.agent.nodes.result_formatter import (
    format_clarification_node,
    format_error_node,
    format_result_node,
)
from src.agent.nodes.schema_retrieval import retrieve_schema_node
from src.agent.nodes.self_correction import self_correction_node
from src.agent.nodes.sql_executor import execute_sql_node
from src.agent.nodes.sql_generator import generate_sql_node
from src.config import settings
from src.models.state import AgentState, make_initial_state


# ----- Conditional edge functions ------------------------------------------


def _route_after_intent(state: AgentState) -> Literal["clarify", "continue"]:
    return "clarify" if state.get("intent") == "clarification" else "continue"


def _route_after_execute(
    state: AgentState,
) -> Literal["success", "retry", "give_up"]:
    if state.get("execution_success"):
        return "success"
    if state.get("retry_count", 0) < settings.max_retry_count:
        return "retry"
    return "give_up"


# ----- Graph builder --------------------------------------------------------


def build_agent_graph():
    """Construct and compile the LangGraph state machine."""
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("retrieve_schema", retrieve_schema_node)
    graph.add_node("generate_sql", generate_sql_node)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("self_correction", self_correction_node)
    graph.add_node("format_result", format_result_node)
    graph.add_node("format_error", format_error_node)
    graph.add_node("format_clarification", format_clarification_node)

    graph.add_edge(START, "classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        _route_after_intent,
        {
            "clarify": "format_clarification",
            "continue": "retrieve_schema",
        },
    )

    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")

    graph.add_conditional_edges(
        "execute_sql",
        _route_after_execute,
        {
            "success": "format_result",
            "retry": "self_correction",
            "give_up": "format_error",
        },
    )

    graph.add_edge("self_correction", "generate_sql")
    graph.add_edge("format_result", END)
    graph.add_edge("format_error", END)
    graph.add_edge("format_clarification", END)

    return graph.compile()


# Build once at import time so callers don't pay the cost on every request.
agent_graph = build_agent_graph()


# ----- Convenience runner ---------------------------------------------------


def run_agent(
    user_query: str,
    session_id: str = "",
    sql_dialect: str = "postgresql",
) -> AgentState:
    """Run the full pipeline end-to-end and return the final state.

    Latency is measured here (not inside any node) so it always reflects the
    total wall-clock time of the request.
    """
    initial = make_initial_state(
        user_query=user_query,
        session_id=session_id,
        sql_dialect=sql_dialect,  # type: ignore[arg-type]
    )
    t0 = time.perf_counter()
    final_state = agent_graph.invoke(initial)
    final_state["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    return final_state  # type: ignore[return-value]
