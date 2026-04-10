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
                        await self._notify(task_id, {
                            "type": "progress", "step": step_label, "pct": pct,
                        })

                    if kind == "on_chain_end" and name == "LangGraph":
                        output = event.get("data", {}).get("output", {})
                        if output:
                            final_state = output

                if not final_state:
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
                await asyncio.sleep(0.1)
                self._subscribers.pop(task_id, None)
