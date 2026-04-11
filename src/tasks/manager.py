"""In-memory async task manager (Phase 2+).

Wraps the LangGraph agent pipeline in an asyncio-based task queue with
semaphore concurrency control and subscriber-based progress notifications.
Production deployments can swap this for a Redis-backed implementation
with the same interface.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from src.models.task import TaskStatus

logger = logging.getLogger(__name__)

# Map LangGraph node names to human-readable step labels and progress %.
PROGRESS_MAP: dict[str, tuple[str, int]] = {
    "classify_intent": ("classifying_intent", 20),
    "retrieve_schema": ("retrieving_schema", 35),
    "filter_by_permission": ("filtering_permissions", 40),
    "generate_sql": ("generating_sql", 60),
    "execute_sql": ("executing_sql", 80),
    "self_correction": ("self_correcting", 65),
    "format_result": ("formatting_result", 95),
    "format_error": ("formatting_error", 95),
    "format_clarification": ("formatting_clarification", 95),
}


def _extract_step_detail(node_name: str, output: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a short, user-facing description of what the agent did in this node.

    The `output` argument is the merged AgentState after the node finished, as
    reported by LangGraph's ``astream_events`` on_chain_end. We pull only a few
    high-signal fields per node so the frontend can show "what the agent is
    thinking" without flooding the websocket with the full state.
    """
    if not output:
        return None

    if node_name == "classify_intent":
        intent = output.get("intent")
        complexity = output.get("complexity_score")
        if intent:
            info = f"识别为 {intent}"
            if complexity:
                info += f" · 复杂度 {complexity}/5"
            extra: dict[str, Any] = {"intent": intent}
            if complexity:
                extra["complexity_score"] = complexity
            return {"info": info, "extra": extra}

    elif node_name == "retrieve_schema":
        schemas = output.get("retrieved_schemas") or []
        tables = [s.get("table") for s in schemas if s.get("table")]
        if tables:
            head = ", ".join(tables[:3])
            more = f" +{len(tables) - 3}" if len(tables) > 3 else ""
            return {
                "info": f"混合检索命中 {len(tables)} 张表: {head}{more}",
                "extra": {"tables": tables},
            }

    elif node_name == "filter_by_permission":
        role = output.get("user_role") or "default"
        schemas_after = output.get("retrieved_schemas") or []
        return {
            "info": f"角色 {role} · 过滤后保留 {len(schemas_after)} 张表",
            "extra": {"role": role, "tables_remaining": len(schemas_after)},
        }

    elif node_name == "generate_sql":
        model = output.get("model_used") or "—"
        sql = output.get("generated_sql") or ""
        if sql:
            preview = " ".join(sql.split())[:120]
            return {
                "info": f"{model} 生成了 SQL",
                "extra": {"model": model, "sql_preview": preview},
            }

    elif node_name == "execute_sql":
        success = output.get("execution_success")
        row_count = output.get("row_count", 0)
        err = output.get("execution_error")
        if success is True:
            return {
                "info": f"执行成功 · 返回 {row_count} 行",
                "extra": {"row_count": row_count},
            }
        if success is False:
            return {
                "info": "执行失败，准备自修正",
                "extra": {"error": (err or "unknown")[:200]},
            }

    elif node_name == "self_correction":
        retries = output.get("retry_count", 0)
        history = output.get("correction_history") or []
        last_err = history[-1].get("error", "") if history else ""
        return {
            "info": f"第 {retries} 次修正",
            "extra": {"retry_count": retries, "last_error": last_err[:200]},
        }

    elif node_name == "format_result":
        hint = output.get("visualization_hint") or "table"
        ans = output.get("final_answer") or ""
        return {
            "info": f"可视化建议: {hint}",
            "extra": {"visualization_hint": hint, "final_answer_preview": ans[:200]},
        }

    elif node_name == "format_error":
        return {
            "info": "生成错误摘要",
            "extra": {"final_answer_preview": (output.get("final_answer") or "")[:200]},
        }

    elif node_name == "format_clarification":
        q = output.get("clarification_question") or ""
        return {
            "info": "需要用户澄清",
            "extra": {"question": q[:200]},
        }

    return None


class TaskManager:
    """In-memory async task manager with subscriber-based progress push."""

    def __init__(self, max_concurrent: int = 5):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def create_task(self, request_data: dict[str, Any]) -> str:
        """Create a new pending task. Returns the task_id."""
        task_id = uuid.uuid4().hex[:8]
        self._tasks[task_id] = {
            "task_id": task_id,
            "status": TaskStatus.PENDING,
            "current_step": None,
            "progress_pct": 0,
            "created_at": datetime.now(tz=timezone.utc),
            "completed_at": None,
            "request": request_data,
            "result": None,
            "error": None,
        }
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """Register a WebSocket subscriber for task progress events."""
        if task_id not in self._subscribers:
            self._subscribers[task_id] = []
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[task_id].append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        subs = self._subscribers.get(task_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(task_id, None)

    async def _notify(self, task_id: str, event: dict[str, Any]) -> None:
        """Push an event to all subscribers of this task."""
        for queue in self._subscribers.get(task_id, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def execute(
        self,
        task_id: str,
        run_fn: Callable[..., Coroutine],
    ) -> None:
        """Execute a query task asynchronously with progress tracking.

        ``run_fn`` is expected to be a bound call that accepts no extra args
        and returns an ``AgentState`` dict (i.e., a partial application of
        ``run_agent_async``).
        """
        task = self._tasks.get(task_id)
        if task is None:
            return

        async with self._semaphore:
            task["status"] = TaskStatus.RUNNING
            await self._notify(task_id, {
                "type": "status", "status": "running", "step": "starting",
            })

            try:
                final_state = await run_fn()

                task["status"] = TaskStatus.SUCCESS
                task["result"] = final_state
                task["completed_at"] = datetime.now(tz=timezone.utc)
                await self._notify(task_id, {
                    "type": "complete", "status": "success",
                })

            except Exception as exc:
                task["status"] = TaskStatus.FAILED
                task["error"] = str(exc)
                task["completed_at"] = datetime.now(tz=timezone.utc)
                logger.exception("task %s failed", task_id)
                await self._notify(task_id, {
                    "type": "complete", "status": "failed", "error": str(exc),
                })

            finally:
                # Clean up subscribers after a short delay to allow final reads
                await asyncio.sleep(0.1)
                self._subscribers.pop(task_id, None)

    async def execute_with_progress(
        self,
        task_id: str,
        run_fn: Callable[..., Coroutine],
        persist_fn: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Like :meth:`execute`, but streams progress from the LangGraph agent.

        Falls back to :meth:`execute` if ``astream_events`` is unavailable or
        the agent graph cannot be imported.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return

        async with self._semaphore:
            task["status"] = TaskStatus.RUNNING
            await self._notify(task_id, {
                "type": "status", "status": "running", "step": "starting",
            })

            try:
                from src.agent.graph import agent_graph
                from src.models.state import make_initial_state

                req = task["request"]
                initial = make_initial_state(
                    user_query=req.get("query", ""),
                    session_id=req.get("session_id", ""),
                    sql_dialect=req.get("sql_dialect", "postgresql"),
                    active_source=req.get("active_source", ""),
                    user_id=req.get("user_id", ""),
                )

                final_state: dict[str, Any] = {}
                async for event in agent_graph.astream_events(initial, version="v2"):
                    kind = event.get("event")
                    name = event.get("name", "")

                    if kind == "on_chain_end" and name in PROGRESS_MAP:
                        step_label, pct = PROGRESS_MAP[name]
                        task["current_step"] = step_label
                        task["progress_pct"] = pct
                        node_output = event.get("data", {}).get("output") or {}
                        detail = _extract_step_detail(name, node_output)
                        event_payload: dict[str, Any] = {
                            "type": "progress",
                            "step": step_label,
                            "pct": pct,
                        }
                        if detail:
                            event_payload["detail"] = detail
                        await self._notify(task_id, event_payload)

                    if kind == "on_chain_end" and name == "LangGraph":
                        output = event.get("data", {}).get("output", {})
                        if output:
                            final_state = output

                if not final_state:
                    # Fallback: astream_events didn't surface the final state.
                    # run_fn() runs the agent and persists history itself.
                    final_state = await run_fn()
                elif persist_fn is not None:
                    # astream path captured the state but skipped persistence;
                    # call the persist callback directly.
                    try:
                        persist_fn(final_state)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("persist_fn failed for task %s: %s", task_id, exc)

                task["status"] = TaskStatus.SUCCESS
                task["result"] = final_state
                task["completed_at"] = datetime.now(tz=timezone.utc)
                await self._notify(task_id, {
                    "type": "complete", "status": "success",
                })

            except Exception as exc:
                task["status"] = TaskStatus.FAILED
                task["error"] = str(exc)
                task["completed_at"] = datetime.now(tz=timezone.utc)
                logger.exception("task %s failed", task_id)
                await self._notify(task_id, {
                    "type": "complete", "status": "failed", "error": str(exc),
                })

            finally:
                await asyncio.sleep(0.1)
                self._subscribers.pop(task_id, None)
