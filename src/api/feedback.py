"""POST /api/feedback — user thumbs-up / thumbs-down on a past query.

Request body (FeedbackRequest):
    history_id: the query_history row id returned by POST /api/query
    feedback_type: "positive" | "negative"
    detail: optional free-text explanation ("why this is wrong")

The endpoint looks up the history row, embeds the user_query with the
current embedder, and writes a row to query_feedback. The agent's
retrieve_experience node will pick it up on future similar queries.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.db.connection import get_cursor
from src.evolution.feedback_store import FeedbackStore
from src.retrieval.embedder import Embedder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["feedback"])


class FeedbackRequest(BaseModel):
    history_id: int = Field(..., ge=1)
    feedback_type: str = Field(..., pattern=r"^(positive|negative)$")
    detail: Optional[str] = Field(None, max_length=1000)


class FeedbackResponse(BaseModel):
    success: bool
    feedback_id: Optional[int] = None
    message: str = ""


@lru_cache(maxsize=1)
def _embedder() -> Embedder:
    return Embedder()


@lru_cache(maxsize=1)
def _store() -> FeedbackStore:
    return FeedbackStore()


@router.post("/feedback", response_model=FeedbackResponse)
def post_feedback(req: FeedbackRequest) -> FeedbackResponse:
    # 1. Pull the history row so we can copy user_query / generated_sql /
    # source_name / intent into query_feedback. Denormalized on purpose:
    # retrieve_experience is a hot path and can't afford a JOIN.
    try:
        with get_cursor(dict_rows=True) as cur:
            cur.execute(
                """
                SELECT user_query, generated_sql, source_name, intent
                FROM query_history
                WHERE id = %s
                """,
                (req.history_id,),
            )
            row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"history lookup failed: {exc}"
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=404, detail=f"history_id {req.history_id} not found"
        )
    if not row.get("generated_sql"):
        raise HTTPException(
            status_code=400,
            detail="cannot submit feedback on a query that did not produce SQL",
        )

    # 2. Embed the user_query so we can cosine-search on it later
    try:
        embedding = _embedder().embed(row["user_query"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"embed failed: {exc}"
        ) from exc

    # 3. Persist
    feedback_id = _store().save_feedback(
        history_id=req.history_id,
        feedback_type=req.feedback_type,
        feedback_detail=req.detail,
        user_query=row["user_query"],
        generated_sql=row["generated_sql"],
        source_name=row.get("source_name") or "",
        intent=row.get("intent") or "",
        embedding=embedding,
    )

    if feedback_id is None:
        return FeedbackResponse(
            success=False,
            message="feedback could not be persisted (check server logs)",
        )

    return FeedbackResponse(
        success=True,
        feedback_id=feedback_id,
        message="Feedback saved. This will improve future queries.",
    )
