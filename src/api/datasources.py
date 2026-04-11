"""Data source management endpoints.

* ``GET /api/datasources`` — list all registered connectors
* ``GET /api/datasources/types`` — dialect form schemas for the dynamic UI
* ``POST /api/datasources`` — create and hot-add a new connector
* ``DELETE /api/datasources/{name}`` — remove a user-managed connector

The POST path validates the config, tests the live connection, adds the
connector to the in-process registry, persists it to the gitignored user
layer (``config/datasources.local.yaml``), and optionally triggers an
async schema-embedding bootstrap so the agent can immediately query it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from src.connectors.factory import DIALECT_SCHEMAS, list_dialect_schemas
from src.connectors.registry import ConnectorRegistry
from src.models.request import CreateDataSourceRequest
from src.models.response import (
    CreateDataSourceResponse,
    DataSourceDescriptor,
    DataSourcesResponse,
    DialectFieldDescriptor,
    DialectSchema,
    DialectSchemasResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["datasources"])


# ---------------------------------------------------------------------------
# GET /api/datasources  — list all connectors
# ---------------------------------------------------------------------------


async def _describe(connector, default: str | None, registry: ConnectorRegistry) -> DataSourceDescriptor:
    connected = False
    table_count: int | None = None
    try:
        connected = await connector.test_connection()
    except Exception as exc:  # noqa: BLE001
        logger.warning("test_connection failed for %s: %s", connector.name, exc)

    if connected:
        try:
            tables = await connector.get_tables()
            table_count = len(tables)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_tables failed for %s: %s", connector.name, exc)

    return DataSourceDescriptor(
        name=connector.name,
        dialect=connector.get_dialect(),
        description=connector.description,
        connected=connected,
        table_count=table_count,
        is_default=(connector.name == default),
        user_managed=registry.is_user_managed(connector.name),
    )


@router.get("/datasources", response_model=DataSourcesResponse)
async def list_datasources() -> DataSourcesResponse:
    registry = ConnectorRegistry.get_instance()
    default = registry.default_name()

    descriptors: list[DataSourceDescriptor] = []
    for connector in registry.list_connectors():
        descriptors.append(await _describe(connector, default, registry))

    return DataSourcesResponse(datasources=descriptors, default=default)


# ---------------------------------------------------------------------------
# GET /api/datasources/types  — dialect form schemas
# ---------------------------------------------------------------------------


@router.get("/datasources/types", response_model=DialectSchemasResponse)
async def get_dialect_types() -> DialectSchemasResponse:
    """Return the field schemas used by the frontend to build a dynamic form."""
    schemas: list[DialectSchema] = []
    for entry in list_dialect_schemas():
        fields = [
            DialectFieldDescriptor(**f) for f in entry["fields"]
        ]
        schemas.append(DialectSchema(
            dialect=entry["dialect"],
            label=entry["label"],
            description=entry.get("description", ""),
            fields=fields,
        ))
    return DialectSchemasResponse(dialects=schemas)


# ---------------------------------------------------------------------------
# POST /api/datasources  — create + hot-add
# ---------------------------------------------------------------------------


def _validate_connection_fields(dialect: str, connection: dict) -> None:
    """Validate required fields are present and drop unknown keys."""
    schema = DIALECT_SCHEMAS.get(dialect)
    if schema is None:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported dialect: {dialect!r}. "
                   f"Supported: {list(DIALECT_SCHEMAS.keys())}",
        )
    required = [f["key"] for f in schema["fields"] if f.get("required")]
    missing = [k for k in required if not connection.get(k) and connection.get(k) != 0]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"missing required connection fields for {dialect}: {missing}",
        )


@router.post(
    "/datasources",
    response_model=CreateDataSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_datasource(req: CreateDataSourceRequest) -> CreateDataSourceResponse:
    registry = ConnectorRegistry.get_instance()
    if not registry.is_initialized:
        raise HTTPException(status_code=503, detail="connector registry not initialized")

    _validate_connection_fields(req.dialect, req.connection)

    # Build the YAML-equivalent config block.
    config = {
        "name": req.name,
        "dialect": req.dialect,
        "description": req.description,
        "connection": req.connection,
    }

    # Hot-add: instantiate + connect + test.
    try:
        connector = await registry.add_connector(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Schema embedding bootstrap (optional).
    indexing_status = "skipped"
    indexing_error: str | None = None
    if req.run_bootstrap:
        try:
            from src.retrieval import bootstrap as _bootstrap
            await _bootstrap.run_async(only_source=req.name)
            indexing_status = "success"
        except Exception as exc:  # noqa: BLE001
            logger.warning("bootstrap for new source %s failed: %s", req.name, exc)
            indexing_status = "failed"
            indexing_error = str(exc)
            # Do NOT roll back: the connector is valid, user can retry bootstrap
            # manually via `python -m src.retrieval.bootstrap --source <name>`

    descriptor = await _describe(connector, registry.default_name(), registry)
    return CreateDataSourceResponse(
        success=True,
        datasource=descriptor,
        indexing_status=indexing_status,
        indexing_error=indexing_error,
    )


# ---------------------------------------------------------------------------
# DELETE /api/datasources/{name}
# ---------------------------------------------------------------------------


@router.delete("/datasources/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_datasource(name: str) -> None:
    """Delete a connector.

    For user-managed entries this is permanent (removed from the local YAML).
    For primary entries this is runtime-only — they come back on restart.
    """
    registry = ConnectorRegistry.get_instance()
    try:
        await registry.remove_connector(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
