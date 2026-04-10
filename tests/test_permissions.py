"""Unit tests for the permission filter (Phase 2+)."""

from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest

from src.auth.permission import PermissionContext, PermissionFilter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "permissions.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _schemas(*names: str, layer: str = "DWD") -> list[dict]:
    return [
        {"table": n, "layer": layer, "columns": [{"name": "id"}, {"name": "cost_amount"}]}
        for n in names
    ]


# ---------------------------------------------------------------------------
# PermissionFilter.get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    def test_known_user_resolves_role(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          admin:
            allowed_tables: ["*"]
            denied_columns: {}
            max_result_rows: 10000
        users:
          alice:
            role: admin
        default_role: admin
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context("alice")
        assert ctx.role == "admin"
        assert ctx.max_result_rows == 10000

    def test_unknown_user_gets_default(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          analyst:
            allowed_tables: ["dws_*"]
            denied_columns: {}
            max_result_rows: 1000
        users: {}
        default_role: analyst
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context("unknown_user")
        assert ctx.role == "analyst"

    def test_none_user_gets_default(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          analyst:
            allowed_tables: ["dws_*"]
            denied_columns: {}
            max_result_rows: 500
        default_role: analyst
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context(None)
        assert ctx.role == "analyst"

    def test_missing_config_file_allows_all(self):
        pf = PermissionFilter("/nonexistent/permissions.yaml")
        ctx = pf.get_context("anyone")
        assert ctx.allowed_tables == ["*"]


# ---------------------------------------------------------------------------
# PermissionFilter.filter_schemas
# ---------------------------------------------------------------------------


class TestFilterSchemas:
    def test_wildcard_allows_all(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          admin:
            allowed_tables: ["*"]
            denied_columns: {}
            max_result_rows: 10000
        default_role: admin
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context()
        schemas = _schemas("ods_users", "dwd_order_detail", "dws_daily_sales")
        filtered, removed = pf.filter_schemas(schemas, ctx)
        assert len(filtered) == 3
        assert removed == 0

    def test_prefix_pattern_filters(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          operator:
            allowed_tables: ["dws_*"]
            denied_columns: {}
            max_result_rows: 500
        default_role: operator
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context()
        schemas = _schemas("ods_users", "dwd_order_detail", "dws_daily_sales")
        # Override layers to match real names
        schemas[2]["table"] = "dws_daily_sales"
        filtered, removed = pf.filter_schemas(schemas, ctx)
        assert len(filtered) == 1
        assert filtered[0]["table"] == "dws_daily_sales"
        assert removed == 2

    def test_exact_match_filters(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          analyst:
            allowed_tables: ["dwd_order_detail", "dws_*"]
            denied_columns: {}
            max_result_rows: 1000
        default_role: analyst
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context()
        schemas = _schemas("dwd_order_detail", "dwd_user_profile", "dws_daily_sales")
        filtered, removed = pf.filter_schemas(schemas, ctx)
        assert len(filtered) == 2
        tables = {s["table"] for s in filtered}
        assert tables == {"dwd_order_detail", "dws_daily_sales"}

    def test_denied_columns_stripped(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          analyst:
            allowed_tables: ["*"]
            denied_columns:
              dwd_order_detail:
                - cost_amount
            max_result_rows: 1000
        default_role: analyst
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context()
        schemas = [
            {
                "table": "dwd_order_detail",
                "columns": [
                    {"name": "id"},
                    {"name": "cost_amount"},
                    {"name": "total_amount"},
                ],
            }
        ]
        filtered, _ = pf.filter_schemas(schemas, ctx)
        col_names = [c["name"] for c in filtered[0]["columns"]]
        assert "cost_amount" not in col_names
        assert "id" in col_names
        assert "total_amount" in col_names

    def test_does_not_mutate_original(self, tmp_path):
        p = _write_yaml(tmp_path, """\
        roles:
          analyst:
            allowed_tables: ["*"]
            denied_columns:
              t1:
                - secret
            max_result_rows: 1000
        default_role: analyst
        """)
        pf = PermissionFilter(p)
        ctx = pf.get_context()
        original = [{"table": "t1", "columns": [{"name": "id"}, {"name": "secret"}]}]
        filtered, _ = pf.filter_schemas(original, ctx)
        # Original should be untouched
        assert len(original[0]["columns"]) == 2
        assert len(filtered[0]["columns"]) == 1


# ---------------------------------------------------------------------------
# PermissionFilter.enforce_row_limit
# ---------------------------------------------------------------------------


class TestEnforceRowLimit:
    def test_adds_limit_when_missing(self):
        ctx = PermissionContext(role="op", allowed_tables=[], denied_columns={}, max_result_rows=500)
        pf = PermissionFilter("/nonexistent")
        sql = "SELECT * FROM t"
        result = pf.enforce_row_limit(sql, ctx)
        assert "LIMIT 500" in result

    def test_clamps_existing_limit(self):
        ctx = PermissionContext(role="op", allowed_tables=[], denied_columns={}, max_result_rows=100)
        pf = PermissionFilter("/nonexistent")
        sql = "SELECT * FROM t LIMIT 9999"
        result = pf.enforce_row_limit(sql, ctx)
        assert "LIMIT 100" in result
        assert "9999" not in result

    def test_keeps_smaller_limit(self):
        ctx = PermissionContext(role="op", allowed_tables=[], denied_columns={}, max_result_rows=1000)
        pf = PermissionFilter("/nonexistent")
        sql = "SELECT * FROM t LIMIT 50"
        result = pf.enforce_row_limit(sql, ctx)
        assert "LIMIT 50" in result

    def test_strips_trailing_semicolon(self):
        ctx = PermissionContext(role="op", allowed_tables=[], denied_columns={}, max_result_rows=500)
        pf = PermissionFilter("/nonexistent")
        sql = "SELECT * FROM t;"
        result = pf.enforce_row_limit(sql, ctx)
        assert "LIMIT 500;" in result


# ---------------------------------------------------------------------------
# _table_allowed
# ---------------------------------------------------------------------------


class TestTableAllowed:
    def test_star_matches_everything(self):
        assert PermissionFilter._table_allowed("anything", ["*"]) is True

    def test_prefix_pattern(self):
        assert PermissionFilter._table_allowed("dws_daily_sales", ["dws_*"]) is True
        assert PermissionFilter._table_allowed("ods_users", ["dws_*"]) is False

    def test_exact_match(self):
        assert PermissionFilter._table_allowed("dwd_order_detail", ["dwd_order_detail"]) is True
        assert PermissionFilter._table_allowed("dwd_user_profile", ["dwd_order_detail"]) is False

    def test_multiple_patterns(self):
        patterns = ["dws_*", "dwd_order_detail"]
        assert PermissionFilter._table_allowed("dws_daily_sales", patterns) is True
        assert PermissionFilter._table_allowed("dwd_order_detail", patterns) is True
        assert PermissionFilter._table_allowed("ods_users", patterns) is False
