"""Elytra connector layer.

Pluggable, async data source connectors. Every analytics query the agent runs
flows through one of these. New engines plug in by implementing
``DataSourceConnector`` (~100 lines) and adding a YAML entry.
"""

from src.connectors.base import (
    ColumnMeta,
    DataSourceConnector,
    QueryResult,
    TableMeta,
    _is_select_only,
    coerce_row,
)

__all__ = [
    "ColumnMeta",
    "DataSourceConnector",
    "QueryResult",
    "TableMeta",
    "_is_select_only",
    "coerce_row",
]
