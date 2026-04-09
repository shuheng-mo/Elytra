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

from typing import Type

from src.connectors.base import DataSourceConnector


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
