"""FastAPI app entrypoint for Elytra (Phase 1).

Run locally:

    uvicorn src.main:app --reload --port 8000

The Streamlit frontend (Step 6) will hit this on the same host. CORS is wide
open during Phase 1 so the dev frontend on a different port can talk to it;
Phase 2's hardening pass should restrict it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.history import router as history_router
from src.api.query import router as query_router
from src.api.schema import router as schema_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)

app = FastAPI(
    title="Elytra",
    description="LLM-powered NL→SQL data analysis backend",
    version="0.1.0",
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
app.include_router(schema_router)
app.include_router(history_router)
