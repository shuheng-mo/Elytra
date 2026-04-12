"""LangGraph state machine for the Elytra agent (PRD §5.2).

Topology
--------
::

    classify_intent
        ├── clarification ─────────────► format_clarification ──► END
        └── other intents ─► retrieve_schema ─► filter_by_permission ─► generate_sql
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

import asyncio
import functools
import inspect
import logging
import time
from typing import Callable, Literal

from langgraph.graph import END, START, StateGraph

from src.agent.nodes.chart_generator import generate_chart_node
from src.agent.nodes.intent_classifier import classify_intent_node
from src.agent.nodes.permission_filter import filter_by_permission_node
from src.agent.nodes.resolve_context import resolve_context_node
from src.agent.nodes.result_formatter import (
    format_clarification_node,
    format_error_node,
    format_result_node,
)
from src.agent.nodes.retrieve_experience import retrieve_experience_node
from src.agent.nodes.save_experience import save_experience_node
from src.agent.nodes.schema_retrieval import retrieve_schema_node
from src.agent.nodes.self_correction import self_correction_node
from src.agent.nodes.sql_executor import execute_sql_node
from src.agent.nodes.sql_generator import generate_sql_node
from src.agent.nodes.summarize_conversation import (
    MIN_TURNS_FOR_SUMMARY,
    summarize_conversation_node,
)
from src.config import settings
from src.models.state import AgentState, make_initial_state
from src.observability.sanitizer import SanitizerAction, sanitize_user_query

logger_graph = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-node timing wrapper
# ---------------------------------------------------------------------------


def _timed(name: str, fn: Callable) -> Callable:
    """Wrap a sync or async node function with perf_counter instrumentation."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(state: AgentState) -> dict:
            t0 = time.perf_counter()
            result = await fn(state)
            elapsed = (time.perf_counter() - t0) * 1000
            timings = dict(state.get("node_timings") or {})
            # Append suffix for retried nodes (e.g. generate_sql_2)
            key = name
            if key in timings:
                i = 2
                while f"{name}_{i}" in timings:
                    i += 1
                key = f"{name}_{i}"
            timings[key] = round(elapsed, 1)
            result["node_timings"] = timings
            logger_graph.info("[timing] %s = %.0f ms", key, elapsed)
            return result
        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(state: AgentState) -> dict:
        t0 = time.perf_counter()
        result = fn(state)
        elapsed = (time.perf_counter() - t0) * 1000
        timings = dict(state.get("node_timings") or {})
        key = name
        if key in timings:
            i = 2
            while f"{name}_{i}" in timings:
                i += 1
            key = f"{name}_{i}"
        timings[key] = round(elapsed, 1)
        result["node_timings"] = timings
        logger_graph.info("[timing] %s = %.0f ms", key, elapsed)
        return result
    return sync_wrapper


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


def _route_after_format(
    state: AgentState,
) -> Literal["save", "skip"]:
    """Should we fire save_experience before moving to generate_chart?

    Only if the query ran successfully AND went through at least one
    self-correction cycle. A first-try success has nothing to learn from
    and writing it would pollute the experience pool.
    """
    if state.get("execution_success") and state.get("retry_count", 0) > 0:
        return "save"
    return "skip"


def _route_before_end(
    state: AgentState,
) -> Literal["summarize", "skip"]:
    """Should we compress the conversation before returning?

    Only when the session has enough prior successful turns that a
    summary would actually help subsequent queries. The threshold
    matches ``MIN_TURNS_FOR_SUMMARY`` in summarize_conversation.
    """
    if not state.get("execution_success"):
        return "skip"
    if not state.get("session_id"):
        return "skip"
    # The current turn plus N-1 prior turns from conversation_history
    # = MIN_TURNS_FOR_SUMMARY. conversation_history was populated by
    # resolve_context at the top of this run, so it already reflects
    # what was in query_history when the query started.
    prior = len(state.get("conversation_history") or [])
    if prior + 1 >= MIN_TURNS_FOR_SUMMARY:
        return "summarize"
    return "skip"


# ----- Graph builder --------------------------------------------------------


def build_agent_graph():
    """Construct and compile the LangGraph state machine."""
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", _timed("classify_intent", classify_intent_node))
    graph.add_node("resolve_context", _timed("resolve_context", resolve_context_node))
    graph.add_node("retrieve_schema", _timed("retrieve_schema", retrieve_schema_node))
    graph.add_node("filter_by_permission", _timed("filter_by_permission", filter_by_permission_node))
    graph.add_node("retrieve_experience", _timed("retrieve_experience", retrieve_experience_node))
    graph.add_node("generate_sql", _timed("generate_sql", generate_sql_node))
    graph.add_node("execute_sql", _timed("execute_sql", execute_sql_node))
    graph.add_node("self_correction", _timed("self_correction", self_correction_node))
    graph.add_node("format_result", _timed("format_result", format_result_node))
    graph.add_node("save_experience", _timed("save_experience", save_experience_node))
    graph.add_node("generate_chart", _timed("generate_chart", generate_chart_node))
    graph.add_node("summarize_conversation", _timed("summarize_conversation", summarize_conversation_node))
    graph.add_node("format_error", _timed("format_error", format_error_node))
    graph.add_node("format_clarification", _timed("format_clarification", format_clarification_node))

    graph.add_edge(START, "classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        _route_after_intent,
        {
            "clarify": "format_clarification",
            "continue": "resolve_context",
        },
    )

    # v0.5.0: resolve_context runs before schema retrieval so downstream
    # nodes (including retrieve_schema's BM25 + vector search) can see the
    # conversation summary if one exists.
    graph.add_edge("resolve_context", "retrieve_schema")
    graph.add_edge("retrieve_schema", "filter_by_permission")
    graph.add_edge("filter_by_permission", "retrieve_experience")
    graph.add_edge("retrieve_experience", "generate_sql")
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

    # v0.5.0: conditional fork — only visit save_experience when the run
    # actually self-corrected. First-try successes skip directly to chart.
    graph.add_conditional_edges(
        "format_result",
        _route_after_format,
        {
            "save": "save_experience",
            "skip": "generate_chart",
        },
    )
    graph.add_edge("save_experience", "generate_chart")

    # v0.5.0: conditional fork at the end — summarize conversation if the
    # session has accumulated enough turns. First-turn / short sessions
    # skip this to save tokens.
    graph.add_conditional_edges(
        "generate_chart",
        _route_before_end,
        {
            "summarize": "summarize_conversation",
            "skip": END,
        },
    )
    graph.add_edge("summarize_conversation", END)

    graph.add_edge("format_error", END)
    graph.add_edge("format_clarification", END)

    return graph.compile()


# Build once at import time so callers don't pay the cost on every request.
agent_graph = build_agent_graph()


# ----- Convenience runner ---------------------------------------------------


async def run_agent_async(
    user_query: str,
    session_id: str = "",
    sql_dialect: str = "postgresql",
    active_source: str = "",
    user_id: str = "",
) -> AgentState:
    """Run the full pipeline end-to-end and return the final state.

    Async because the SQL execution node now talks to async connectors. Other
    nodes are still sync; LangGraph handles the mix transparently via
    ``ainvoke``.

    Latency is measured here (not inside any node) so it always reflects the
    total wall-clock time of the request.

    Input is sanitized before the graph is invoked. A ``REJECT`` verdict
    short-circuits the request with a ``prompt_injection`` error state so
    the rest of the pipeline (retrieval, generation, execution) never sees
    the hostile input.
    """
    sanitized = sanitize_user_query(user_query)

    initial = make_initial_state(
        user_query=user_query,
        session_id=session_id,
        sql_dialect=sql_dialect,  # type: ignore[arg-type]
        active_source=active_source,
        user_id=user_id,
    )
    initial["sanitized_query"] = sanitized.cleaned
    initial["sanitizer_violations"] = list(sanitized.violations)

    if sanitized.action == SanitizerAction.REJECT:
        logger_graph.warning(
            "sanitizer rejected query: violations=%s", sanitized.violations
        )
        initial["execution_success"] = False
        initial["execution_error"] = (
            f"Query rejected by input sanitizer: {', '.join(sanitized.violations)}"
        )
        initial["final_answer"] = "输入未通过安全检查，请重新描述你的问题。"
        initial["intent"] = "simple_query"
        return initial  # type: ignore[return-value]

    if sanitized.action == SanitizerAction.WARN:
        logger_graph.info(
            "sanitizer warn: violations=%s", sanitized.violations
        )

    t0 = time.perf_counter()
    final_state = await agent_graph.ainvoke(initial)
    final_state["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    # Propagate sanitizer metadata out of the graph
    final_state["sanitizer_violations"] = list(sanitized.violations)
    return final_state  # type: ignore[return-value]


def run_agent(
    user_query: str,
    session_id: str = "",
    sql_dialect: str = "postgresql",
    active_source: str = "",
    user_id: str = "",
) -> AgentState:
    """Sync wrapper around :func:`run_agent_async`.

    Kept for tests, CLI scripts, and any non-async caller. Production code
    served by FastAPI should call :func:`run_agent_async` directly.
    """
    return asyncio.run(
        run_agent_async(
            user_query=user_query,
            session_id=session_id,
            sql_dialect=sql_dialect,
            active_source=active_source,
            user_id=user_id,
        )
    )
