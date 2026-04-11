"""Async query endpoint: submit a query and get a task_id back immediately (Phase 2+)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from src.agent.graph import run_agent_async
from src.api.query import _persist_history
from src.connectors.registry import ConnectorRegistry
from src.models.request import QueryRequest
from src.models.task import TaskStatus, TaskStatusResponse, TaskSubmitResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["async-query"])


def _get_task_manager():
    """Retrieve the TaskManager singleton from app state."""
    from src.tasks.manager import TaskManager
    # TaskManager is set on app.state during lifespan
    # This function is called at request time, not import time
    return getattr(_get_task_manager, "_instance", None)


@router.post("/query/async", response_model=TaskSubmitResponse)
async def post_query_async(req: QueryRequest, request: Request) -> TaskSubmitResponse:
    """Submit a query asynchronously. Returns a task_id for polling or WebSocket."""
    task_manager = request.app.state.task_manager

    registry = ConnectorRegistry.get_instance()
    if not registry.is_initialized:
        raise HTTPException(status_code=503, detail="connector registry not initialized")

    source_name = req.source or registry.default_name()
    if not source_name:
        raise HTTPException(status_code=400, detail="no `source` given and no default_source configured")

    try:
        connector = registry.get(source_name)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dialect = connector.get_dialect()

    task_id = task_manager.create_task({
        "query": req.query,
        "session_id": req.session_id or "",
        "sql_dialect": dialect,
        "active_source": source_name,
        "user_id": req.user_id or "",
    })

    # Fallback path: used only if astream_events fails to surface the final
    # state. Runs the agent AND persists history.
    async def _run():
        state = await run_agent_async(
            user_query=req.query,
            session_id=req.session_id or "",
            sql_dialect=dialect,
            active_source=source_name,
            user_id=req.user_id or "",
        )
        _persist_history(state)
        return state

    # Happy path: astream_events captures state mid-run, so the manager
    # invokes this persist callback separately.
    def _persist(state: dict):
        _persist_history(state)

    # Schedule execution in the background with progress streaming
    asyncio.create_task(
        task_manager.execute_with_progress(task_id, _run, persist_fn=_persist)
    )

    host = request.headers.get("host", "localhost:8000")
    scheme = "wss" if request.url.scheme == "https" else "ws"

    return TaskSubmitResponse(
        task_id=task_id,
        status=TaskStatus.PENDING,
        ws_url=f"{scheme}://{host}/ws/task/{task_id}",
    )


@router.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str, request: Request) -> TaskStatusResponse:
    """Poll task status (fallback when WebSocket is unavailable)."""
    task_manager = request.app.state.task_manager
    task = task_manager.get_task(task_id)

    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    return TaskStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        current_step=task.get("current_step"),
        progress_pct=task.get("progress_pct", 0),
        created_at=task.get("created_at"),
        completed_at=task.get("completed_at"),
        result=task.get("result") if task["status"] == TaskStatus.SUCCESS else None,
        error=task.get("error"),
    )
