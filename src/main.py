"""FastAPI app entrypoint for Elytra.

Run locally:

    uvicorn src.main:app --reload --port 8000

The Streamlit frontend hits this on the same host. CORS is wide open during
Phase 1/2 so the dev frontend on a different port can talk to it; production
hardening should restrict it.

Lifecycle:
    * startup → ConnectorRegistry.init_from_yaml(settings.datasources_yaml_path)
    * shutdown → ConnectorRegistry.disconnect_all()
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.audit import router as audit_router
from src.api.datasources import router as datasources_router
from src.api.history import router as history_router
from src.api.query import router as query_router
from src.api.query_async import router as async_query_router
from src.api.schema import router as schema_router
from src.api.ws import router as ws_router
from src.tasks.manager import TaskManager
from src.config import settings
from src.connectors.registry import ConnectorRegistry
from src.retrieval.schema_loader import SchemaLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

logger = logging.getLogger("elytra.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize the connector registry + warm the schema cache on startup.

    Schema preload is important: ``retrieve_schema_node`` is a sync LangGraph
    node, so it can't ``await`` a connector's ``get_tables()`` at request
    time. We populate ``SchemaLoader._source_cache`` here so the node only
    ever hits the cache.
    """
    registry = ConnectorRegistry.get_instance()
    try:
        await registry.init_from_yaml(settings.datasources_yaml_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("connector registry init failed: %s", exc)
        # Still let the app come up so /healthz works and operators can debug.

    # Initialize the async task manager.
    _app.state.task_manager = TaskManager(
        max_concurrent=settings.max_concurrent_tasks,
    )
    logger.info("task manager initialized (max_concurrent=%d)", settings.max_concurrent_tasks)

    # Warm the schema cache for every source that came up successfully.
    for cfg in registry.raw_configs():
        name = cfg.get("name")
        if not name:
            continue
        try:
            connector = registry.get(name)
            if not connector.is_connected:
                continue
            await SchemaLoader.load_from_connector(connector, cfg.get("overlay"))
            logger.info("schema cache warmed for %s", name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("schema preload failed for %s: %s", name, exc)

    yield
    try:
        await registry.disconnect_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("registry shutdown error: %s", exc)


app = FastAPI(
    title="Elytra",
    description="LLM-powered NL→SQL data analysis backend",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(query_router)
app.include_router(async_query_router)
app.include_router(schema_router)
app.include_router(history_router)
app.include_router(datasources_router)
app.include_router(audit_router)
app.include_router(ws_router)
