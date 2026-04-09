"""Schema overlay — enrich introspected ``TableMeta`` with curated YAML metadata.

A connector's ``get_tables()`` returns engine-faithful metadata: table names,
column names, types, NULL flags, primary keys, and (sometimes) raw comments.
What it can't return is the *human-friendly layer* of metadata: Chinese names,
business descriptions, common analytical queries, table relationships.

The overlay file fills in that human layer. Each data source can declare an
``overlay`` path in ``config/datasources.yaml``; we read it here, merge it
on top of the introspected ``TableMeta`` list, and produce ``TableInfo``
objects ready for the existing BM25 / embedding / retrieval stack.

Two YAML structures are accepted, transparently:

1. **Legacy list form** (Phase 1's ``db/data_dictionary.yaml``):
   ```yaml
   tables:
     - name: ods_users
       layer: ODS
       chinese_name: 原始用户表
       description: ...
       columns:
         - name: user_id
           type: BIGINT
           chinese_name: 用户ID
           ...
   ```

2. **Name-keyed dict form** (cleaner for new sources):
   ```yaml
   tables:
     ods_users:
       chinese_name: 原始用户表
       description: ...
       columns:
         user_id:
           chinese_name: 用户ID
           ...
   ```

Merge precedence: overlay wins for ``chinese_name`` / ``description`` /
``common_queries`` / ``relationships`` / ``layer`` / column ``chinese_name``
and ``description``. The connector's introspected ``data_type`` always wins
over any overlay ``type`` field — the engine is the source of truth on types.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from src.connectors.base import TableMeta
from src.retrieval.schema_loader import ColumnInfo, TableInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YAML loading + structural normalization
# ---------------------------------------------------------------------------


def _load_overlay(path: Path) -> dict[str, dict[str, Any]]:
    """Load an overlay YAML and normalize it to ``{table_name: entry_dict}``."""
    if not path.exists():
        logger.info("overlay not found at %s; using empty overlay", path)
        return {}

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tables_node = raw.get("tables")
    if tables_node is None:
        return {}

    if isinstance(tables_node, list):
        # Legacy list-of-dicts form: each entry has a `name` field.
        out: dict[str, dict[str, Any]] = {}
        for entry in tables_node:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            out[name] = entry
        return out

    if isinstance(tables_node, dict):
        # New name-keyed dict form.
        return {k: (v or {}) for k, v in tables_node.items()}

    logger.warning("unrecognized overlay structure in %s; ignoring", path)
    return {}


def _normalize_columns_overlay(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize column overlays to ``{column_name: column_dict}``."""
    cols_node = entry.get("columns")
    if cols_node is None:
        return {}

    if isinstance(cols_node, list):
        out: dict[str, dict[str, Any]] = {}
        for c in cols_node:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not name:
                continue
            out[name] = c
        return out

    if isinstance(cols_node, dict):
        return {k: (v or {}) for k, v in cols_node.items()}

    return {}


# ---------------------------------------------------------------------------
# Merge: TableMeta + overlay → TableInfo
# ---------------------------------------------------------------------------


def _merge_column(meta: Any, overlay: dict[str, Any]) -> ColumnInfo:
    """Build a ``ColumnInfo`` from connector metadata + overlay enrichment."""
    return ColumnInfo(
        name=meta.name,
        # Engine-introspected type wins over any overlay-declared type
        type=str(meta.data_type),
        chinese_name=str(overlay.get("chinese_name", "")),
        description=str(
            overlay.get("description") or (meta.comment or "")
        ),
        is_primary_key=bool(meta.is_primary_key),
        enum_values=list(overlay.get("enum_values") or []),
        business_logic=overlay.get("business_logic"),
    )


def _merge_table(meta: TableMeta, overlay: dict[str, Any]) -> TableInfo:
    """Build a ``TableInfo`` from connector metadata + overlay enrichment."""
    cols_overlay = _normalize_columns_overlay(overlay)
    columns = [_merge_column(c, cols_overlay.get(c.name, {})) for c in meta.columns]

    layer = overlay.get("layer") or meta.layer or ""
    chinese_name = str(overlay.get("chinese_name", "") or "")
    description = str(
        overlay.get("description") or (meta.comment or "") or ""
    )

    return TableInfo(
        name=meta.table_name,
        layer=layer,
        chinese_name=chinese_name,
        description=description,
        columns=columns,
        common_queries=list(overlay.get("common_queries") or []),
        relationships=list(overlay.get("relationships") or []),
        update_frequency=overlay.get("update_frequency"),
        row_count_approx=overlay.get("row_count_approx") or meta.row_count_approx,
    )


def enrich_with_overlay(
    metas: list[TableMeta],
    overlay_path: str | Path | None,
) -> list[TableInfo]:
    """Merge an overlay YAML on top of a list of ``TableMeta``.

    Args:
        metas: introspected metadata from ``connector.get_tables()``.
        overlay_path: path to the overlay YAML, or ``None`` to skip enrichment.

    Returns:
        ``TableInfo`` list ready to feed BM25 / embedding / retrieval.
    """
    overlay_map: dict[str, dict[str, Any]] = {}
    if overlay_path:
        overlay_map = _load_overlay(Path(overlay_path))

    enriched: list[TableInfo] = []
    for meta in metas:
        entry = overlay_map.get(meta.table_name, {})
        enriched.append(_merge_table(meta, entry))
    return enriched
