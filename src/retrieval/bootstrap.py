"""One-shot bootstrap for the schema_embeddings index.

Run this:
    * On first deployment, after `init.sql` has created an empty
      ``schema_embeddings`` table.
    * Whenever you switch ``EMBEDDING_MODEL`` to one with a different vector
      dim (e.g. ``text-embedding-3-small``=1536 → ``text-embedding-3-large``=
      3072), since pgvector columns are dim-typed.

What it does:
    1. ``Embedder().bootstrap_table()``  — DROP and re-CREATE
       ``schema_embeddings`` with the current embedder's vector dim (and the
       HNSW index over ``embedding``).
    2. ``Embedder().index_tables(...)``  — call the embedding API once per
       table, write rows to ``schema_embeddings``.

Skips the SYSTEM layer (``query_history``, ``schema_embeddings`` itself).
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.retrieval.embedder import Embedder
from src.retrieval.schema_loader import SchemaLoader

logger = logging.getLogger("elytra.bootstrap")

# Layers we never want to embed — these are infra tables, not user-facing data.
_SKIP_LAYERS = {"SYSTEM"}


def run(*, dry_run: bool = False) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    loader = SchemaLoader()
    all_tables = loader.load()
    indexable = [t for t in all_tables if t.layer not in _SKIP_LAYERS]

    logger.info(
        "loaded %d tables from data dictionary (%d after skipping %s)",
        len(all_tables),
        len(indexable),
        sorted(_SKIP_LAYERS),
    )

    embedder = Embedder()
    logger.info(
        "embedder ready — backend=%s model=%s dim=%d",
        embedder.backend,
        embedder.model,
        embedder.dim,
    )

    if dry_run:
        for t in indexable:
            logger.info("would index: %s [%s]", t.name, t.layer)
        return 0

    logger.info("recreating schema_embeddings table…")
    embedder.bootstrap_table()

    logger.info("indexing %d tables…", len(indexable))
    n = embedder.index_tables(indexable)
    logger.info("done — %d rows written to schema_embeddings", n)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list which tables would be indexed without touching the DB or LLM",
    )
    args = parser.parse_args()

    try:
        run(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        logger.error("bootstrap failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
