"""LangGraph node: filter retrieved schemas by user role (Phase 2+).

Inserted between ``retrieve_schema`` and ``generate_sql``. If the user has
no permission to access any of the retrieved tables, the node short-circuits
the pipeline by setting intent to ``clarification``.
"""

from __future__ import annotations

import logging

from src.auth.permission import PermissionFilter
from src.config import settings
from src.models.state import AgentState

logger = logging.getLogger(__name__)

_filter: PermissionFilter | None = None


def _get_filter() -> PermissionFilter:
    global _filter  # noqa: PLW0603
    if _filter is None:
        _filter = PermissionFilter(settings.permissions_yaml_path)
    return _filter


def filter_by_permission_node(state: AgentState) -> dict:
    """Filter ``retrieved_schemas`` according to the user's role."""
    pf = _get_filter()
    user_id = state.get("user_id") or None
    ctx = pf.get_context(user_id)

    schemas = state.get("retrieved_schemas") or []
    filtered, removed = pf.filter_schemas(schemas, ctx)

    if not filtered and schemas:
        # All tables were filtered out — deny the query
        logger.info(
            "permission filter removed all %d tables for role=%s user=%s",
            len(schemas), ctx.role, user_id,
        )
        return {
            "retrieved_schemas": [],
            "user_role": ctx.role,
            "intent": "clarification",
            "clarification_question": (
                f"您的角色（{ctx.role}）没有权限访问相关数据表，请联系管理员。"
            ),
        }

    if removed:
        logger.info(
            "permission filter removed %d/%d tables for role=%s",
            removed, len(schemas), ctx.role,
        )

    return {
        "retrieved_schemas": filtered,
        "user_role": ctx.role,
    }
