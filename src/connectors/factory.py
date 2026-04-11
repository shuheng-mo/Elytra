"""ConnectorFactory — instantiates a connector class for a YAML config block.

The factory is intentionally tiny: dialect → class lookup, then forward the
config dict to the constructor. New engines plug in by importing their class
and adding one entry to ``CONNECTOR_REGISTRY``.

We import each concrete connector lazily inside ``_resolve_class`` so that
optional drivers (asyncpg, duckdb, aiomysql) only get imported when their
engine is actually configured. This lets the test suite import
``src.connectors`` even on machines that haven't installed every driver yet.
"""

from __future__ import annotations

from typing import Any, Type

from src.connectors.base import DataSourceConnector


# ---------------------------------------------------------------------------
# Connection form schemas — drives the dynamic "add datasource" form in the UI.
# Each field entry: {key, label, type, required, placeholder, default, help}
# Types: "string" | "password" | "int" | "path" | "select"
# ---------------------------------------------------------------------------

DIALECT_SCHEMAS: dict[str, dict[str, Any]] = {
    "postgresql": {
        "label": "PostgreSQL",
        "description": "关系型数据库，支持 OLTP + 轻量分析",
        "fields": [
            {"key": "host", "label": "Host", "type": "string",
             "required": True, "default": "localhost",
             "placeholder": "localhost"},
            {"key": "port", "label": "Port", "type": "int",
             "required": True, "default": 5432},
            {"key": "database", "label": "Database", "type": "string",
             "required": True, "placeholder": "postgres"},
            {"key": "user", "label": "User", "type": "string",
             "required": True, "placeholder": "postgres"},
            {"key": "password", "label": "Password", "type": "password",
             "required": False,
             "help": "留空则假设免密访问"},
        ],
    },
    "duckdb": {
        "label": "DuckDB",
        "description": "嵌入式 OLAP 引擎，单文件数据库",
        "fields": [
            {"key": "database_path", "label": "Database Path", "type": "path",
             "required": True, "placeholder": "datasets/mydb.duckdb",
             "help": "相对 backend 进程工作目录的路径；用 ':memory:' 创建内存数据库"},
        ],
    },
    "starrocks": {
        "label": "StarRocks",
        "description": "MPP OLAP 引擎，MySQL 协议兼容",
        "fields": [
            {"key": "host", "label": "Host (FE)", "type": "string",
             "required": True, "default": "localhost"},
            {"key": "port", "label": "Query Port", "type": "int",
             "required": True, "default": 9030,
             "help": "FE 的 query_port，不是 http_port"},
            {"key": "database", "label": "Database", "type": "string",
             "required": False, "placeholder": "default_db"},
            {"key": "user", "label": "User", "type": "string",
             "required": True, "default": "root"},
            {"key": "password", "label": "Password", "type": "password",
             "required": False},
        ],
    },
}


def list_dialect_schemas() -> list[dict[str, Any]]:
    """Return the ordered list of supported dialects with their form schemas."""
    return [
        {"dialect": dialect, **schema}
        for dialect, schema in DIALECT_SCHEMAS.items()
    ]


def _resolve_class(dialect: str) -> Type[DataSourceConnector]:
    """Lazy import to keep optional drivers truly optional."""
    if dialect == "postgresql":
        from src.connectors.postgres_connector import PostgresConnector

        return PostgresConnector
    if dialect == "duckdb":
        from src.connectors.duckdb_connector import DuckDBConnector

        return DuckDBConnector
    if dialect == "starrocks":
        from src.connectors.starrocks_connector import StarRocksConnector

        return StarRocksConnector
    raise ValueError(
        f"Unsupported dialect: {dialect!r}. "
        f"Supported: postgresql / duckdb / starrocks"
    )


class ConnectorFactory:
    """Factory: turn a YAML config block into a live connector instance."""

    @staticmethod
    def create(config: dict) -> DataSourceConnector:
        dialect = config.get("dialect", "postgresql")
        cls = _resolve_class(dialect)
        return cls(config)

    @staticmethod
    def create_all(datasources_config: list[dict]) -> dict[str, DataSourceConnector]:
        """Build a ``{name: connector}`` dict from a list of YAML blocks."""
        result: dict[str, DataSourceConnector] = {}
        for ds in datasources_config:
            name = ds.get("name")
            if not name:
                raise ValueError(f"datasource entry missing 'name': {ds}")
            result[name] = ConnectorFactory.create(ds)
        return result
