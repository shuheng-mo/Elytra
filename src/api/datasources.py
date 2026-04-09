"""GET /api/datasources — list configured analytics data sources.

Returns metadata for every connector registered with ConnectorRegistry plus
the current default. ``connected`` is the result of an async ping; sources
that failed to come up at startup will appear with ``connected=false`` so
operators can spot misconfiguration without losing the rest of the registry.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from src.connectors.registry import ConnectorRegistry
from src.models.response import DataSourceDescriptor, DataSourcesResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["datasources"])


@router.get("/datasources", response_model=DataSourcesResponse)
async def list_datasources() -> DataSourcesResponse:
    registry = ConnectorRegistry.get_instance()
    default = registry.default_name()

    descriptors: list[DataSourceDescriptor] = []
    for connector in registry.list_connectors():
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

        descriptors.append(
            DataSourceDescriptor(
                name=connector.name,
                dialect=connector.get_dialect(),
                description=connector.description,
                connected=connected,
                table_count=table_count,
                is_default=(connector.name == default),
            )
        )

    return DataSourcesResponse(datasources=descriptors, default=default)
