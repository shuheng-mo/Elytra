"""GET /api/schema — return a data source's schema, grouped by warehouse layer.

Phase 2 makes this endpoint multi-source aware. The optional ``?source=``
query param picks which connector to introspect; if omitted, the registry's
default source is used. SYSTEM-layer tables are filtered out so they never
appear in the public schema response.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.connectors.registry import ConnectorRegistry
from src.models.response import ColumnDescriptor, SchemaResponse, TableDescriptor
from src.retrieval.schema_loader import SchemaLoader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["schema"])

# SYSTEM-layer tables (query_history, schema_embeddings) are infrastructure
# and should not appear in the public schema response.
_HIDDEN_LAYERS = {"SYSTEM"}


@router.get("/schema", response_model=SchemaResponse)
async def get_schema(
    source: Optional[str] = Query(None, description="data source name; defaults to registry default"),
) -> SchemaResponse:
    registry = ConnectorRegistry.get_instance()
    if not registry.is_initialized:
        raise HTTPException(status_code=503, detail="connector registry not initialized")

    source_name = source or registry.default_name()
    if not source_name:
        raise HTTPException(
            status_code=400,
            detail="no `source` given and no default_source configured",
        )

    try:
        connector = registry.get(source_name)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Resolve overlay path from the YAML config block
    overlay_path = None
    for cfg in registry.raw_configs():
        if cfg.get("name") == source_name:
            overlay_path = cfg.get("overlay")
            break

    try:
        tables = await SchemaLoader.load_from_connector(
            connector, overlay_path, reload=False
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("schema load failed for %s", source_name)
        raise HTTPException(
            status_code=500,
            detail=f"failed to load schema for {source_name}: {exc}",
        ) from exc

    grouped: dict[str, list[TableDescriptor]] = defaultdict(list)
    for tbl in tables:
        if tbl.layer in _HIDDEN_LAYERS:
            continue
        layer_key = tbl.layer or "OTHER"
        grouped[layer_key].append(
            TableDescriptor(
                table=tbl.name,
                chinese_name=tbl.chinese_name,
                description=tbl.description,
                layer=layer_key,
                columns=[
                    ColumnDescriptor(
                        name=c.name,
                        type=c.type,
                        chinese_name=c.chinese_name,
                        description=c.description,
                        is_primary_key=c.is_primary_key,
                        enum_values=list(c.enum_values),
                    )
                    for c in tbl.columns
                ],
                common_queries=list(tbl.common_queries),
            )
        )

    return SchemaResponse(layers=dict(grouped))
