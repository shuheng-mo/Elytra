"""WebSocket endpoint for real-time task progress (Phase 2+)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/task/{task_id}")
async def ws_task(websocket: WebSocket, task_id: str) -> None:
    """Subscribe to real-time progress events for an async task."""
    task_manager = websocket.app.state.task_manager
    task = task_manager.get_task(task_id)

    if task is None:
        await websocket.close(code=4004, reason=f"task {task_id} not found")
        return

    await websocket.accept()
    queue = task_manager.subscribe(task_id)

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") == "complete":
                break
    except WebSocketDisconnect:
        logger.debug("ws client disconnected for task %s", task_id)
    except Exception:
        logger.exception("ws error for task %s", task_id)
    finally:
        task_manager.unsubscribe(task_id, queue)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
