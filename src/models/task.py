"""Pydantic models for async task tracking (Phase 2+)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    ws_url: str = ""


class TaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    current_step: Optional[str] = None
    progress_pct: int = 0
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
