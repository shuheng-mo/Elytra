"""GET /api/schema — return the data dictionary grouped by warehouse layer."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter

from src.models.response import ColumnDescriptor, SchemaResponse, TableDescriptor
from src.retrieval.schema_loader import SchemaLoader

router = APIRouter(prefix="/api", tags=["schema"])

# SYSTEM-layer tables (query_history, schema_embeddings) are infrastructure
# and should not appear in the public schema response.
_HIDDEN_LAYERS = {"SYSTEM"}


@router.get("/schema", response_model=SchemaResponse)
def get_schema() -> SchemaResponse:
    loader = SchemaLoader()
    grouped: dict[str, list[TableDescriptor]] = defaultdict(list)

    for tbl in loader.load():
        if tbl.layer in _HIDDEN_LAYERS:
            continue
        grouped[tbl.layer].append(
            TableDescriptor(
                table=tbl.name,
                chinese_name=tbl.chinese_name,
                description=tbl.description,
                layer=tbl.layer,
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
