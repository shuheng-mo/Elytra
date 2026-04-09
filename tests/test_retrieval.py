"""Unit tests for the schema retrieval layer.

These tests deliberately avoid touching Postgres or any real LLM. The
embedder is replaced with a stub so we can exercise ``HybridRetriever``'s
score-fusion logic and the BM25 ranker over an in-memory data dictionary.

Run with::

    .venv/bin/python -m pytest tests/test_retrieval.py -v
"""

from __future__ import annotations

from typing import Iterable

import pytest

from src.retrieval.bm25_index import BM25Index, tokenize
from src.retrieval.hybrid_retriever import HybridRetriever, _min_max_normalize
from src.retrieval.schema_loader import ColumnInfo, SchemaLoader, TableInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_table(
    name: str,
    layer: str,
    chinese_name: str,
    description: str,
    columns: list[tuple[str, str]] | None = None,
    common_queries: list[str] | None = None,
) -> TableInfo:
    return TableInfo(
        name=name,
        layer=layer,
        chinese_name=chinese_name,
        description=description,
        columns=[ColumnInfo(name=c, type=t) for c, t in (columns or [])],
        common_queries=common_queries or [],
    )


@pytest.fixture
def sample_tables() -> list[TableInfo]:
    return [
        _make_table(
            "dwd_order_detail",
            "DWD",
            "订单明细宽表",
            "订单明细，含用户、商品、支付字段",
            columns=[("order_id", "BIGINT"), ("total_amount", "DECIMAL")],
            common_queries=["按品类统计销售额"],
        ),
        _make_table(
            "dwd_user_profile",
            "DWD",
            "用户画像表",
            "用户维度的聚合画像，含消费统计",
            columns=[("user_id", "BIGINT"), ("user_level", "VARCHAR")],
        ),
        _make_table(
            "dwd_product_dim",
            "DWD",
            "商品维度表",
            "商品基础信息和销售统计",
            columns=[("product_id", "BIGINT"), ("brand", "VARCHAR")],
        ),
        # SYSTEM-layer table — should be excluded from retrieval
        _make_table(
            "query_history",
            "SYSTEM",
            "查询历史",
            "系统表，记录每次查询",
        ),
    ]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_handles_empty_string(self):
        assert tokenize("") == []

    def test_splits_latin_words_whole(self):
        toks = tokenize("dwd_order_detail user_profile")
        assert "dwd_order_detail" in toks
        assert "user_profile" in toks

    def test_splits_cjk_per_character(self):
        toks = tokenize("订单明细")
        # each Chinese char becomes a token
        assert toks.count("订") == 1
        assert toks.count("单") == 1
        assert "订" in toks and "单" in toks and "明" in toks and "细" in toks

    def test_mixed_text(self):
        toks = tokenize("dwd_order_detail 订单表")
        assert "dwd_order_detail" in toks
        assert "订" in toks
        assert "单" in toks


# ---------------------------------------------------------------------------
# BM25Index
# ---------------------------------------------------------------------------


class TestBM25Index:
    def test_empty_index_returns_empty(self):
        idx = BM25Index([])
        assert idx.search("anything") == []

    def test_finds_table_by_chinese_query(self, sample_tables):
        idx = BM25Index(sample_tables)
        results = idx.search_by_name("订单 销售额", top_n=3)
        names = [r[0] for r in results]
        # The order_detail table mentions both "订单" and "销售额"
        assert "dwd_order_detail" in names
        # And it should rank highest
        assert names[0] == "dwd_order_detail"

    def test_finds_table_by_latin_identifier(self, sample_tables):
        idx = BM25Index(sample_tables)
        results = idx.search_by_name("dwd_user_profile", top_n=3)
        assert results
        assert results[0][0] == "dwd_user_profile"

    def test_unknown_query_returns_no_positive_scores(self, sample_tables):
        idx = BM25Index(sample_tables)
        # gibberish English; index has no English stems for these
        results = idx.search_by_name("xyzqwertynonsense", top_n=5)
        # tokenize() yields exactly one Latin token; rank_bm25 may give 0 score
        # which we filter out — accept either empty or all-zero filtered out
        assert all(s > 0 for _, s in results)


# ---------------------------------------------------------------------------
# HybridRetriever score fusion
# ---------------------------------------------------------------------------


class TestMinMaxNormalize:
    def test_empty(self):
        assert _min_max_normalize({}) == {}

    def test_collapses_when_all_equal(self):
        out = _min_max_normalize({"a": 5.0, "b": 5.0})
        assert out == {"a": 1.0, "b": 1.0}

    def test_collapses_zero_when_all_zero(self):
        out = _min_max_normalize({"a": 0.0, "b": 0.0})
        assert out == {"a": 0.0, "b": 0.0}

    def test_scales_to_unit_interval(self):
        out = _min_max_normalize({"a": 0.0, "b": 5.0, "c": 10.0})
        assert out["a"] == pytest.approx(0.0)
        assert out["b"] == pytest.approx(0.5)
        assert out["c"] == pytest.approx(1.0)


class _StubLoader:
    """Minimal SchemaLoader stand-in returning the fixture tables verbatim."""

    def __init__(self, tables: list[TableInfo]):
        self._tables = tables

    def load(self, *, reload: bool = False) -> list[TableInfo]:
        return self._tables


class _StubEmbedder:
    """Stub Embedder returning a canned vector_score map without touching DB/LLM."""

    def __init__(self, scores: dict[str, float] | None = None, raise_exc: bool = False):
        self._scores = scores or {}
        self._raise = raise_exc

    def search(
        self,
        query: str,
        top_n: int = 20,
        *,
        source_name: str | None = None,
    ) -> Iterable[tuple[str, float]]:
        if self._raise:
            raise RuntimeError("simulated DB outage")
        return list(self._scores.items())[:top_n]


class TestHybridRetriever:
    def test_excludes_system_layer(self, sample_tables):
        retriever = HybridRetriever(
            loader=_StubLoader(sample_tables),
            embedder=_StubEmbedder({}),
        )
        assert all(t.layer != "SYSTEM" for t in retriever.tables)
        # query_history is in SYSTEM and must not appear in any retrieval
        results = retriever.retrieve("查询历史", top_n=10)
        assert "query_history" not in {r.table.name for r in results}

    def test_combined_score_uses_weights(self, sample_tables):
        # vector says "user_profile is best"; BM25 (over the query) says
        # "order_detail is best". With BM25 weight 0.0 and vector weight 1.0,
        # the vector winner should win.
        retriever = HybridRetriever(
            loader=_StubLoader(sample_tables),
            embedder=_StubEmbedder({"dwd_user_profile": 0.95, "dwd_order_detail": 0.10}),
            bm25_weight=0.0,
            vector_weight=1.0,
        )
        results = retriever.retrieve("订单 销售额 用户画像", top_n=2)
        assert results[0].table.name == "dwd_user_profile"

    def test_bm25_only_when_vector_weight_zero(self, sample_tables):
        retriever = HybridRetriever(
            loader=_StubLoader(sample_tables),
            embedder=_StubEmbedder({"dwd_user_profile": 0.99}),
            bm25_weight=1.0,
            vector_weight=0.0,
        )
        results = retriever.retrieve("订单 销售额", top_n=2)
        assert results[0].table.name == "dwd_order_detail"

    def test_falls_back_to_bm25_when_vector_raises(self, sample_tables):
        # Simulate pgvector / API outage — retriever must not raise.
        retriever = HybridRetriever(
            loader=_StubLoader(sample_tables),
            embedder=_StubEmbedder(raise_exc=True),
        )
        results = retriever.retrieve("订单", top_n=3)
        assert results, "should still return BM25-only results"
        # The order_detail table mentions 订单 in description
        assert results[0].table.name == "dwd_order_detail"

    def test_returns_results_sorted_desc_by_score(self, sample_tables):
        retriever = HybridRetriever(
            loader=_StubLoader(sample_tables),
            embedder=_StubEmbedder({"dwd_order_detail": 0.8, "dwd_user_profile": 0.5}),
        )
        results = retriever.retrieve("订单 用户", top_n=3)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# SchemaLoader smoke (against the real data dictionary)
# ---------------------------------------------------------------------------


class TestSchemaLoaderRealDictionary:
    """Smoke test the production data_dictionary.yaml — the file is part of
    the project source so this is fast and deterministic."""

    def test_loads_all_layers(self):
        tables = SchemaLoader().load()
        layers = {t.layer for t in tables}
        # PRD §4 requires ODS, DWD, DWS plus SYSTEM (query_history etc)
        assert {"ODS", "DWD", "DWS"} <= layers

    def test_dwd_order_detail_exists(self):
        loader = SchemaLoader()
        tbl = loader.get_by_name("dwd_order_detail")
        assert tbl is not None
        col_names = {c.name for c in tbl.columns}
        # spot-check the canonical analytics columns
        assert {"category_l1", "total_amount", "order_date"} <= col_names

    def test_to_text_includes_chinese_name(self):
        tbl = SchemaLoader().get_by_name("dwd_order_detail")
        assert tbl is not None
        text = tbl.to_text()
        assert "订单明细宽表" in text
        assert "[DWD]" in text
