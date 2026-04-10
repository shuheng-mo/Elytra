"""YAML-driven role-based permission filter (Phase 2+).

Sits between schema retrieval and SQL generation in the LangGraph pipeline.
Filters out tables/columns the current user's role cannot access and enforces
row-count limits on generated SQL.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PermissionContext:
    role: str
    allowed_tables: list[str]
    denied_columns: dict[str, list[str]]
    max_result_rows: int


class PermissionFilter:
    """Load ``config/permissions.yaml`` and provide filtering helpers."""

    def __init__(self, config_path: str | Path = "config/permissions.yaml"):
        self._config: dict[str, Any] = {}
        try:
            with open(config_path) as f:
                self._config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning(
                "permissions config not found at %s — all access allowed", config_path
            )

    # ------------------------------------------------------------------
    # Context resolution
    # ------------------------------------------------------------------

    def get_context(self, user_id: str | None = None) -> PermissionContext:
        """Resolve *user_id* to a ``PermissionContext``."""
        role_name = self._config.get("default_role", "analyst")

        if user_id:
            user_entry = self._config.get("users", {}).get(user_id)
            if user_entry and isinstance(user_entry, dict):
                role_name = user_entry.get("role", role_name)

        roles = self._config.get("roles", {})
        role = roles.get(role_name, {})

        return PermissionContext(
            role=role_name,
            allowed_tables=role.get("allowed_tables", ["*"]),
            denied_columns=role.get("denied_columns") or {},
            max_result_rows=role.get("max_result_rows", 1000),
        )

    # ------------------------------------------------------------------
    # Schema filtering
    # ------------------------------------------------------------------

    def filter_schemas(
        self,
        schemas: list[dict[str, Any]],
        context: PermissionContext,
    ) -> tuple[list[dict[str, Any]], int]:
        """Filter retrieved schemas by permission context.

        Returns ``(filtered_schemas, tables_removed_count)``.
        """
        filtered: list[dict[str, Any]] = []
        removed = 0

        for schema in schemas:
            table_name = schema.get("table") or schema.get("table_name", "")
            if not self._table_allowed(table_name, context.allowed_tables):
                removed += 1
                continue

            # Strip denied columns (copy to avoid mutating the original)
            if table_name in context.denied_columns:
                denied = set(context.denied_columns[table_name])
                schema = {**schema}
                if "columns" in schema and isinstance(schema["columns"], list):
                    schema["columns"] = [
                        c for c in schema["columns"]
                        if (c.get("name") or c.get("column_name", "")) not in denied
                    ]
            filtered.append(schema)

        return filtered, removed

    # ------------------------------------------------------------------
    # Row-limit enforcement
    # ------------------------------------------------------------------

    def enforce_row_limit(self, sql: str, context: PermissionContext) -> str:
        """Ensure the SQL respects the role's ``max_result_rows``."""
        max_rows = context.max_result_rows
        upper = sql.upper()

        # Already has a LIMIT — clamp it
        match = re.search(r"\bLIMIT\s+(\d+)", upper)
        if match:
            existing = int(match.group(1))
            if existing > max_rows:
                start, end = match.span(1)
                sql = sql[:start] + str(max_rows) + sql[end:]
            return sql

        # No LIMIT — append one
        sql = sql.rstrip().rstrip(";")
        return f"{sql} LIMIT {max_rows};"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _table_allowed(table_name: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            if pattern == "*":
                return True
            if pattern.endswith("*"):
                prefix = pattern[:-1]
                if table_name.startswith(prefix):
                    return True
            elif pattern == table_name:
                return True
        return False
