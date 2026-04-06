# Backend image — FastAPI + LangGraph agent
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg2-binary needs libpq at runtime; build-essential is only needed if a
# wheel is missing for the target arch (kept slim by uninstalling afterward).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first so layer is cached when only source changes.
COPY pyproject.toml ./
RUN pip install \
        "fastapi>=0.110" \
        "uvicorn[standard]" \
        "psycopg2-binary" \
        "langchain>=0.3" \
        "langchain-openai" \
        "langchain-anthropic" \
        "langchain-community" \
        "langgraph" \
        "rank-bm25" \
        "pgvector" \
        "openai" \
        "pydantic>=2.0" \
        "pyyaml" \
        "python-dotenv" \
        "httpx"

# Project source
COPY src ./src
COPY db ./db

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
