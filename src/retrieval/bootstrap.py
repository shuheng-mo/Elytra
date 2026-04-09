"""One-shot bootstrap for the multi-source schema_embeddings index.

Run this:
    * On first deployment, after ``init.sql`` has created an empty
      ``schema_embeddings`` table.
    * Whenever you switch ``EMBEDDING_MODEL`` to one with a different vector
      dim (e.g. ``text-embedding-3-small``=1536 → ``text-embedding-3-large``=
      3072), since pgvector columns are dim-typed.
    * Whenever a data source is added, removed, or has its schema changed.

What it does:
    1. Loads ``config/datasources.yaml`` via ``ConnectorRegistry``.
    2. ``Embedder().bootstrap_table()`` — DROP and re-CREATE
       ``schema_embeddings`` with the current vector dim plus the
       ``source_name`` column.  (Skipped when ``--source <name>`` is given.)
    3. For each source (or just one, when ``--source`` is set):
       - introspects the live connector → ``TableMeta``
       - merges the per-source overlay YAML → ``TableInfo``
       - skips SYSTEM-layer tables
       - calls ``Embedder.index_tables(...)`` with the source name as a tag

Skips the SYSTEM layer (``query_history``, ``schema_embeddings`` itself).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.config import settings
from src.connectors.overlay import enrich_with_overlay
from src.connectors.registry import ConnectorRegistry
from src.retrieval.embedder import Embedder

logger = logging.getLogger("elytra.bootstrap")

# Layers we never want to embed — these are infra tables, not user-facing data.
_SKIP_LAYERS = {"SYSTEM"}


async def _index_one_source(
    embedder: Embedder,
    source_cfg: dict,
    *,
    dry_run: bool,
) -> int:
    name = source_cfg.get("name", "unnamed")
    overlay_path = source_cfg.get("overlay")

    registry = ConnectorRegistry.get_instance()
    try:
        connector = registry.get(name)
    except KeyError as exc:
        logger.warning("source %s not in registry: %s", name, exc)
        return 0

    if not connector.is_connected:
        try:
            await connector.connect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("connect to %s failed: %s", name, exc)
            return 0

    try:
        metas = await connector.get_tables()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_tables for %s failed: %s", name, exc)
        return 0

    tables = enrich_with_overlay(metas, overlay_path)
    indexable = [t for t in tables if t.layer not in _SKIP_LAYERS]

    logger.info(
        "%s: %d tables introspected, %d indexable (skip %s)",
        name,
        len(tables),
        len(indexable),
        sorted(_SKIP_LAYERS),
    )

    if dry_run:
        for t in indexable:
            logger.info("would index: %s.%s [%s]", name, t.name, t.layer)
        return 0

    return embedder.index_tables(indexable, source_name=name)


async def run_async(*, dry_run: bool = False, only_source: str | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    registry = ConnectorRegistry.get_instance()
    if not registry.is_initialized:
        await registry.init_from_yaml(settings.datasources_yaml_path)

    embedder = Embedder()
    logger.info(
        "embedder ready — backend=%s model=%s dim=%d",
        embedder.backend,
        embedder.model,
        embedder.dim,
    )

    # Single-source mode skips the table re-create, so existing rows for
    # other sources stay intact.
    if not only_source and not dry_run:
        logger.info("recreating schema_embeddings table…")
        embedder.bootstrap_table()

    total = 0
    for source_cfg in registry.raw_configs():
        name = source_cfg.get("name")
        if only_source and name != only_source:
            continue
        n = await _index_one_source(embedder, source_cfg, dry_run=dry_run)
        total += n

    logger.info("done — %d rows written across all sources", total)
    return total


def run(*, dry_run: bool = False, only_source: str | None = None) -> int:
    return asyncio.run(run_async(dry_run=dry_run, only_source=only_source))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list which tables would be indexed without touching the DB or LLM",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="only re-index this single data source (skips DROP/CREATE)",
    )
    args = parser.parse_args()

    try:
        run(dry_run=args.dry_run, only_source=args.source)
    except Exception as exc:  # noqa: BLE001
        logger.error("bootstrap failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
