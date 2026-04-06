"""In-memory BM25 index over table-level documents.

The data dictionary mixes English identifiers (e.g. ``dwd_order_detail``) with
Chinese descriptions, so we tokenize Latin identifiers as whole words and CJK
characters individually. This is intentionally simple — no jieba dependency —
and it works well for the relatively short table descriptions we index.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from src.retrieval.schema_loader import TableInfo

_LATIN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """Mixed Latin/CJK tokenizer.

    Latin words are kept whole and lowercased; each Chinese character is one
    token. Digits-only sequences are dropped because they rarely help schema
    matching.
    """
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    tokens.extend(_LATIN_RE.findall(text))
    tokens.extend(_CJK_RE.findall(text))
    return tokens


class BM25Index:
    """Wraps `rank_bm25.BM25Okapi` over a list of `TableInfo` objects."""

    def __init__(self, tables: list[TableInfo]):
        self.tables = tables
        self.documents = [t.to_text() for t in tables]
        self._tokenized = [tokenize(d) for d in self.documents]
        self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None

    def search(self, query: str, top_n: int = 20) -> list[tuple[int, float]]:
        """Return ``[(table_index, score), ...]`` sorted by score desc."""
        if self._bm25 is None:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda kv: kv[1], reverse=True)
        return [(i, float(s)) for i, s in ranked[:top_n] if s > 0]

    def search_by_name(self, query: str, top_n: int = 20) -> list[tuple[str, float]]:
        """Same as :meth:`search` but returns ``(table_name, score)`` tuples."""
        return [(self.tables[i].name, s) for i, s in self.search(query, top_n=top_n)]
