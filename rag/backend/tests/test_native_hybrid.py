"""原生hybridの範囲・独立チャネル・設定の契約を合成行で検証する。"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from rag_engine.adapters.oracle.native_hybrid import NativeHybridUnavailable, native_search_request

from app.clients.oracle import NativeSearchRows, OracleClient, SearchHits
from app.config import Settings
from app.schemas.search import SearchMode


def test_default_and_reject_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_HYBRID_SEARCH_BACKEND", raising=False)
    assert Settings().rag_hybrid_search_backend == "oracle_native"
    with pytest.raises(ValidationError):
        Settings(rag_hybrid_search_backend="unknown")


def test_native_request_scopes_before_ranking_and_does_not_infer_queries() -> None:
    spec = native_search_request(
        query="比較 A.pdf と B.pdf",
        vector=[1, 0, 0],
        channel="keyword",
        allowed_ids=["a", "a", "b"],
    )
    assert spec["filter_by"]["args"] == ["a", "b"]
    assert spec["text"]["search_text"] == "比較 A.pdf と B.pdf"
    assert spec["text"]["inpath"] == ["$.text"]
    assert "vector" not in spec
    assert spec["return"]["topN"] == 600
    with pytest.raises(ValueError):
        native_search_request(query="合成", vector=[1], channel="vector", allowed_ids=[])
    large = native_search_request(
        query="合成", vector=[1], channel="vector", allowed_ids=[str(i) for i in range(2100)]
    )
    assert [len(v["args"]) for v in large["filter_by"]["args"]] == [1000, 1000, 100]


class NativeOracle(OracleClient):
    def __init__(self, *, fail_keyword: bool = False) -> None:
        # 原生索引の経路の契約を確かめる(範囲の行を距離順に読む経路は下の専用のテスト)。
        super().__init__(
            Settings(
                rag_hybrid_search_backend="oracle_native", rag_native_vector_exact_max_chunks=0
            )
        )
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.fail_keyword = fail_keyword

    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        binds = dict(binds or {})
        self.calls.append((sql, binds))
        if "FROM user_indexes" in sql:
            return [{"index_name": "RAG_CHUNKS_HYBRID_IDX"}]
        if "DBMS_HYBRID_VECTOR.SEARCH" not in sql:
            assert "is_active = 1" in sql
            assert "d.status = 'INDEXED'" in sql
            if "allowed_count" in sql:
                return [{"native_scope_id": None, "allowed_count": 1, "vector_count": 1}]
            return [{"chunk_id": "a", "native_scope_id": None}]
        spec = json.loads(str(binds["native_spec"]))
        assert spec["filter_by"]["args"] == ["a"]
        assert "WHERE" in sql and "is_active = 1" in sql
        if self.fail_keyword and spec["search_fusion"] == "TEXT_ONLY":
            raise RuntimeError("合成の全文検索失敗")
        return [
            {
                "document_id": "doc-a",
                "chunk_id": "a",
                "chunk_text": "合成の本文",
                "metadata_json": {},
                "chunk_index": 0,
                "score": 100 if spec["search_fusion"] == "UNION" else 0.8,
                "native_vector_rank": 1,
                "native_text_rank": 1,
                "native_vector_score": 0 if spec["search_fusion"] == "TEXT_ONLY" else 100,
                "native_text_score": 0 if spec["search_fusion"] == "VECTOR_ONLY" else 80,
                "native_similarity": None if spec["search_fusion"] == "TEXT_ONLY" else 1.0,
                "file_name": "文書 A.txt",
            }
        ]


@pytest.mark.asyncio
async def test_both_entry_points_execute_native_and_never_sudachi() -> None:
    client = NativeOracle()
    vector = [1.0] * 1536
    with patch(
        "app.clients.oracle._oracle_text_query", side_effect=AssertionError("Sudachi呼出し禁止")
    ):
        one = await client.hybrid_search("合成の質問", vector, 4)
        many = await client.search_many(
            [("合成の質問", vector, SearchMode.VECTOR), ("合成の質問", vector, SearchMode.KEYWORD)],
            4,
        )
    assert [v.chunk_id for v in one] == ["a"]
    assert [[v.chunk_id for v in group] for group in many] == [["a"], ["a"]]
    assert len([sql for sql, _ in client.calls if "DBMS_HYBRID_VECTOR.SEARCH" in sql]) == 3


@pytest.mark.asyncio
async def test_channel_failure_is_explicit_without_old_sql_fallback() -> None:
    client = NativeOracle(fail_keyword=True)
    many = await client.search_many(
        [("合成", [1.0] * 1536, SearchMode.VECTOR), ("合成", [], SearchMode.KEYWORD)], 4
    )
    assert len(many[0]) == 1
    assert isinstance(many[1], SearchHits)
    assert many[1].status == "failed"
    assert isinstance(many[1].failure, NativeHybridUnavailable)
    assert not any("CONTAINS(c.search_text" in sql for sql, _ in client.calls)


@pytest.mark.asyncio
async def test_missing_index_fails_before_search_and_never_returns_empty_success() -> None:
    client = NativeOracle()
    with patch.object(client, "_fetch_all", return_value=[]):
        with pytest.raises(NativeHybridUnavailable):
            await client.search_many([("合成", [], SearchMode.KEYWORD)], 4)
        with pytest.raises(NativeHybridUnavailable):
            await client.keyword_search("合成", 4)
        with pytest.raises(NativeHybridUnavailable):
            await client.vector_search([1.0] * 1536, 4)


def test_original_request_runs_both_channels_and_native_wrrf_once() -> None:
    spec = native_search_request(
        query="合成の質問", vector=[1, 0, 0], channel="hybrid", allowed_ids=["a"], limit=4
    )
    assert spec["search_fusion"] == "UNION"
    assert spec["search_scorer"] == "WRRF"
    assert spec["vector"]["search_vector"] == [1, 0, 0]
    assert spec["text"]["search_text"] == "合成の質問"
    assert spec["vector"]["score_weight"] == 10
    assert spec["text"]["score_weight"] == 1
    assert "score" in spec["return"]["values"]


@pytest.mark.asyncio
async def test_native_hybrid_many_is_one_combined_call_per_original() -> None:
    client = NativeOracle()
    await client.search_many([("合成の質問", [1.0] * 1536, SearchMode.HYBRID)], 4)
    specs = [
        json.loads(str(binds["native_spec"]))
        for sql, binds in client.calls
        if "DBMS_HYBRID_VECTOR.SEARCH" in sql
    ]
    assert len(specs) == 1
    assert specs[0]["search_fusion"] == "UNION"


@pytest.mark.asyncio
async def test_native_hybrid_score_keeps_oracle_scale_and_zero_missing_rank() -> None:
    from app.rag.answer_engine import _hit_channels

    client = NativeOracle()
    rows = await client.search_many([("合成", [1.0] * 1536, SearchMode.HYBRID)], 4)
    hit = rows[0][0]
    assert hit.metadata["native_wrrf_score"] == 100
    assert hit.metadata["native_rrf_contribution"] == pytest.approx(1 / 61)
    hit.metadata["keyword_rank"] = 0
    assert _hit_channels(hit, 0) == {"vector:q1"}


def test_numeric_scope_filter_keeps_large_scope_small() -> None:
    spec = native_search_request(
        query="合成",
        vector=[1],
        channel="hybrid",
        allowed_ids=[],
        allowed_scopes=list(range(1, 141)),
    )
    assert spec["filter_by"]["col"] == "NATIVE_SCOPE_ID"
    assert spec["filter_by"]["type"] == "number"
    assert len(json.dumps(spec["filter_by"]).encode()) < 1000
    assert spec["filter_by"]["args"] == list(range(1, 141))


class ScopedNativeOracle(NativeOracle):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        self.reads.append(sql)
        if "allowed_count" in sql:
            return [
                {"native_scope_id": 1, "allowed_count": 400, "vector_count": 400},
                {"native_scope_id": 2, "allowed_count": 1, "vector_count": 1},
            ]
        if "COUNT(*) AS chunk_count" in sql:
            return [
                {"native_scope_id": 1, "chunk_count": 400},
                {"native_scope_id": 2, "chunk_count": 2},
            ]
        if "SELECT c.native_scope_id, c.chunk_index, c.chunk_id" in sql:
            return [
                {"native_scope_id": 2, "chunk_index": 0, "chunk_id": "partial-a"},
                {"native_scope_id": 2, "chunk_index": 1, "chunk_id": "outside-b"},
            ]
        if "SELECT c.chunk_id, c.native_scope_id" in sql:
            assert json.loads(str((binds or {})["partial_scope_ids"])) == [2]
            return [{"chunk_id": "partial-a", "native_scope_id": 2}]
        if "DBMS_HYBRID_VECTOR.SEARCH" in sql:
            self.calls.append((sql, dict(binds or {})))
            spec = json.loads(str((binds or {})["native_spec"]))
            assert spec["filter_by"] == {
                "op": "OR",
                "args": [
                    {"op": "IN", "col": "NATIVE_SCOPE_ID", "type": "number", "args": [1]},
                    {
                        "op": "AND",
                        "args": [
                            {"op": "=", "col": "NATIVE_SCOPE_ID", "type": "number", "args": [2]},
                            {
                                "op": "BETWEEN",
                                "col": "CHUNK_INDEX",
                                "type": "number",
                                "args": [0, 0],
                            },
                        ],
                    },
                ],
            }
            assert "is_active = 1" in sql and "d.status = 'INDEXED'" in sql
            return []
        return await super()._fetch_all(sql, binds)


@pytest.mark.asyncio
async def test_only_complete_authorized_sets_are_compressed() -> None:
    client = ScopedNativeOracle()
    await client._native_rows(
        "hybrid",
        "合成の検索",
        [1.0] * 1536,
        top_k=10,
        filters={"page_number_min": "1", "page_number_max": "1"},
    )
    assert len([sql for sql, _ in client.calls if "DBMS_HYBRID_VECTOR.SEARCH" in sql]) == 1


class CompleteScopeOracle(NativeOracle):
    """全行が許可された chunk set だけの範囲。読んだ SQL と検索の filter を記録する。"""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []
        self.filters: list[object] = []

    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        if "FROM user_indexes" in sql:
            return [{"index_name": "RAG_CHUNKS_HYBRID_IDX"}]
        self.reads.append(sql)
        if "allowed_count" in sql:
            return [{"native_scope_id": 7, "allowed_count": 40000, "vector_count": 40000}]
        if "COUNT(*) AS chunk_count" in sql:
            return [{"native_scope_id": 7, "chunk_count": 40000}]
        if "DBMS_HYBRID_VECTOR.SEARCH" in sql:
            self.filters.append(json.loads(str((binds or {})["native_spec"]))["filter_by"])
            return []
        raise AssertionError("全行が許可された集合で chunk ID を読まない")


@pytest.mark.asyncio
async def test_complete_scope_is_resolved_without_reading_chunk_ids() -> None:
    client = CompleteScopeOracle()

    await client._native_rows("hybrid", "合成の検索", [1.0] * 1536, top_k=10, filters={})

    assert client.filters == [{"op": "IN", "col": "NATIVE_SCOPE_ID", "type": "number", "args": [7]}]


@pytest.mark.asyncio
async def test_search_many_resolves_the_shared_scope_once() -> None:
    """検索文ごとに範囲を解決し直さない。検索は検索文ごとに 1 回のまま(#4011)。"""
    client = CompleteScopeOracle()
    searches = [(f"合成の検索文 {index}", [1.0] * 1536, SearchMode.HYBRID) for index in range(3)]

    await client.search_many(searches, 4)

    assert len([sql for sql in client.reads if "allowed_count" in sql]) == 1
    assert len(client.filters) == 3


@pytest.mark.asyncio
async def test_empty_scope_does_not_search() -> None:
    class EmptyScopeOracle(CompleteScopeOracle):
        async def _fetch_all(
            self, sql: str, binds: Mapping[str, object] | None = None
        ) -> list[dict[str, object]]:
            if "allowed_count" in sql:
                return []
            return await super()._fetch_all(sql, binds)

    client = EmptyScopeOracle()

    assert await client._native_rows("hybrid", "合成", [1.0] * 1536, top_k=4, filters={}) == []
    assert client.filters == []


@pytest.mark.asyncio
async def test_scope_failure_is_reported_per_search_without_empty_success() -> None:
    class FailingScopeOracle(CompleteScopeOracle):
        async def _fetch_all(
            self, sql: str, binds: Mapping[str, object] | None = None
        ) -> list[dict[str, object]]:
            if "allowed_count" in sql:
                raise RuntimeError("合成の範囲の失敗")
            return await super()._fetch_all(sql, binds)

    client = FailingScopeOracle()
    searches = [(f"合成 {index}", [1.0] * 1536, SearchMode.HYBRID) for index in range(2)]

    results = await client.search_many(searches, 4)

    assert [getattr(hits, "status", "") for hits in results] == ["failed", "failed"]
    assert client.filters == []


@pytest.mark.parametrize(
    ("vector_score", "text_score", "similarity", "minimum", "expected"),
    [
        (100.0, 0.0, 1.0, 0.3, (10 / 11 / 61, 0.0)),
        (0.0, 18.0, None, 0.3, (0.0, 1 / 11 / 62)),
        (58.82, 18.0, 0.3, 0.3, (10 / 11 / 61, 1 / 11 / 62)),
        (58.82, 18.0, 0.299, 0.3, (0.0, 1 / 11 / 62)),
        (58.82, 0.0, 0.299, 0.3, (0.0, 0.0)),
        (0.0, 18.0, 0.9, 0.3, (0.0, 1 / 11 / 62)),
        (100.0, 18.0, None, 0.3, (0.0, 1 / 11 / 62)),
    ],
)
def test_native_rank_terms_keep_only_actual_admitted_channel_contributions(
    vector_score: float,
    text_score: float,
    similarity: float | None,
    minimum: float,
    expected: tuple[float, float],
) -> None:
    from rag_engine.adapters.oracle.native_hybrid import native_rank_terms

    terms = native_rank_terms(
        vector_rank=1,
        text_rank=2,
        vector_score=vector_score,
        text_score=text_score,
        similarity=similarity,
        min_similarity=minimum,
        method_ratio=(10, 1),
        k=60,
    )
    assert (terms.vector, terms.text) == pytest.approx(expected)
    assert terms.total == pytest.approx(sum(expected))
    assert bool(terms.vector_rank) == (expected[0] > 0)
    assert bool(terms.text_rank) == (expected[1] > 0)


@pytest.mark.parametrize("ratio", [(0, 1), (1, 0), (2, 3)])
def test_native_rank_terms_respect_zero_weight_and_original_rank_gaps(
    ratio: tuple[int, int],
) -> None:
    from rag_engine.adapters.oracle.native_hybrid import native_rank_terms

    terms = native_rank_terms(
        vector_rank=5,
        text_rank=7,
        vector_score=80,
        text_score=12,
        similarity=0.5,
        min_similarity=0.3,
        method_ratio=ratio,
        k=20,
    )
    assert terms.vector == pytest.approx(ratio[0] / sum(ratio) / 25)
    assert terms.text == pytest.approx(ratio[1] / sum(ratio) / 27)


@pytest.mark.parametrize("channel", ["hybrid", "vector"])
def test_native_threshold_keeps_text_independent_and_uses_true_cosine(channel: str) -> None:
    from app.clients.oracle import _apply_native_search_threshold

    rows = [
        {
            "chunk_id": "vector",
            "score": 99.85,
            "native_vector_score": 100,
            "native_text_score": 0,
            "native_vector_rank": 1,
            "native_text_rank": 4,
            "native_similarity": 0.8,
        },
        {
            "chunk_id": "both-low",
            "score": 90,
            "native_vector_score": 58.82,
            "native_text_score": 18,
            "native_vector_rank": 2,
            "native_text_rank": 1,
            "native_similarity": 0.1,
        },
        {
            "chunk_id": "low",
            "score": 80,
            "native_vector_score": 60,
            "native_text_score": 0,
            "native_vector_rank": 3,
            "native_text_rank": 4,
            "native_similarity": 0.2,
        },
        {
            "chunk_id": "text",
            "score": 70,
            "native_vector_score": 0,
            "native_text_score": 7,
            "native_vector_rank": 4,
            "native_text_rank": 2,
            "native_similarity": None,
        },
    ]
    selected = _apply_native_search_threshold(rows, channel, Settings(rag_min_similarity=0.3))
    assert [row["chunk_id"] for row in selected] == (
        ["vector", "both-low", "text"] if channel == "hybrid" else ["vector"]
    )
    assert selected.threshold["raw_count"] == 4
    assert selected.threshold["retained_count"] == len(selected)
    assert selected.threshold["excluded_count"] == 4 - len(selected)
    assert selected.threshold["rejected_vector_count"] == 2
    assert selected[0]["native_effective_text_rank"] == 0
    if channel == "hybrid":
        assert selected[0]["score"] == pytest.approx(10 / 11 / 61)
        assert selected[1]["score"] == pytest.approx(1 / 11 / 61)
        assert selected[1]["native_effective_vector_rank"] == 0
        assert selected[2]["score"] == pytest.approx(1 / 11 / 62)
        assert selected[0]["native_wrrf_score"] == 99.85
    assert rows[0]["score"] == 99.85


@pytest.mark.asyncio
async def test_standalone_native_vector_uses_same_cosine_metadata_and_one_call() -> None:
    client = NativeOracle()
    hits = await client.vector_search([1.0] * 1536, 4, query="合成の質問")
    assert hits and hits[0].metadata["cosine_similarity"] == 1.0
    calls = [(sql, binds) for sql, binds in client.calls if "DBMS_HYBRID_VECTOR.SEARCH" in sql]
    assert len(calls) == 1
    assert "VECTOR_DISTANCE" in calls[0][0]
    scope_sql = [sql for sql, _ in client.calls if "DBMS_HYBRID_VECTOR.SEARCH" not in sql]
    assert all("VECTOR_DISTANCE" not in sql for sql in scope_sql)


@pytest.mark.asyncio
async def test_vector_filter_does_not_renumber_surviving_original_rank() -> None:
    class GappedNativeOracle(NativeOracle):
        async def _fetch_all(
            self, sql: str, binds: Mapping[str, object] | None = None
        ) -> list[dict[str, object]]:
            rows = await super()._fetch_all(sql, binds)
            if "DBMS_HYBRID_VECTOR.SEARCH" in sql:
                rows[0]["native_vector_rank"] = 5
            return rows

    client = GappedNativeOracle()
    hits = await client.vector_search([1.0] * 1536, 8)
    assert hits[0].metadata["vector_rank"] == 5


def test_backend_threshold_defaults_and_explicit_saved_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAG_MIN_SIMILARITY", raising=False)
    monkeypatch.delenv("RAG_RERANK_MIN_RELEVANCE_SCORE", raising=False)
    default = Settings(_env_file=None)
    assert default.rag_min_similarity == 0.05
    assert default.rag_rerank_min_relevance_score == 0.4
    monkeypatch.setenv("RAG_MIN_SIMILARITY", "0.18")
    monkeypatch.setenv("RAG_RERANK_MIN_RELEVANCE_SCORE", "0.0")
    saved = Settings(_env_file=None)
    assert saved.rag_min_similarity == 0.18
    assert saved.rag_rerank_min_relevance_score == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("present", ["vector", "keyword"])
async def test_native_metadata_omits_unmatched_channel_rank(present: str) -> None:
    class OneChannelOracle(NativeOracle):
        async def _fetch_all(
            self, sql: str, binds: Mapping[str, object] | None = None
        ) -> list[dict[str, object]]:
            rows = await super()._fetch_all(sql, binds)
            if "DBMS_HYBRID_VECTOR.SEARCH" in sql:
                rows[0]["native_vector_score"] = 100 if present == "vector" else 0
                rows[0]["native_text_score"] = 80 if present == "keyword" else 0
            return rows

    client = OneChannelOracle()
    hits = await client.hybrid_search("合成の質問", [1.0] * 1536, 4)
    assert len(hits) == 1
    absent = "keyword" if present == "vector" else "vector"
    assert f"{absent}_rank" not in hits[0].metadata
    assert hits[0].metadata[f"{absent}_contribution"] == 0
    assert hits[0].metadata[f"{present}_rank"] == 1


class ExactScopeOracle(OracleClient):
    """範囲の行を距離順に読む経路(#4134・#4097)。読んだ SQL と検索の要求を記録する。"""

    def __init__(self, *, vector_chunks: int = 3, exact_max_chunks: int = 50000) -> None:
        super().__init__(
            Settings(
                rag_hybrid_search_backend="oracle_native",
                rag_native_vector_exact_max_chunks=exact_max_chunks,
                rag_min_similarity=0.3,
            )
        )
        self.vector_chunks = vector_chunks
        self.exact_sql: list[str] = []
        self.exact_binds: list[dict[str, object]] = []
        self.searches: list[dict[str, Any]] = []
        self.search_rows: list[dict[str, object]] = []
        self.fail_exact = False

    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        if "FROM user_indexes" in sql:
            return [{"index_name": "RAG_CHUNKS_HYBRID_IDX"}]
        if "allowed_count" in sql:
            assert "c.embedding IS NOT NULL" in sql
            return [{"native_scope_id": 7, "allowed_count": 5, "vector_count": self.vector_chunks}]
        if "COUNT(*) AS chunk_count" in sql:
            return [{"native_scope_id": 7, "chunk_count": 5}]
        if "DBMS_HYBRID_VECTOR.SEARCH" in sql:
            spec = json.loads(str((binds or {})["native_spec"]))
            self.searches.append(spec)
            return self.search_rows
        assert "FETCH EXACT FIRST" in sql
        if self.fail_exact:
            raise RuntimeError("合成の距離順の失敗")
        self.exact_sql.append(sql)
        self.exact_binds.append(dict(binds or {}))
        return [
            _exact_row("near", 0.1),
            _exact_row("middle", 0.4),
            _exact_row("far", 0.9),  # 類似度 0.1 は下限 0.3 より低い。
        ]


def _exact_row(chunk_id: str, distance: float) -> dict[str, object]:
    return {
        "document_id": "doc-a",
        "chunk_id": chunk_id,
        "chunk_text": f"合成の本文 {chunk_id}",
        "metadata_json": {},
        "chunk_index": 0,
        "file_name": "文書 A.txt",
        "score": 100 / (1 + distance),
        "native_similarity": 1 - distance,
        "native_vector_score": 100 / (1 + distance),
    }


def _text_row(chunk_id: str, rank: int) -> dict[str, object]:
    return {
        "document_id": "doc-a",
        "chunk_id": chunk_id,
        "chunk_text": f"合成の本文 {chunk_id}",
        "metadata_json": {},
        "chunk_index": 1,
        "file_name": "文書 A.txt",
        "score": 8,
        "native_similarity": None,
        "native_vector_rank": None,
        "native_text_rank": rank,
        "native_vector_score": 0,
        "native_text_score": 8,
    }


@pytest.mark.asyncio
async def test_vector_channel_reads_scope_rows_in_distance_order_without_search() -> None:
    client = ExactScopeOracle()

    rows = await client._native_rows(
        "vector", "合成", [1.0] * 1536, top_k=3, filters={"document_id": "doc-a"}
    )

    assert client.searches == []
    assert [row["chunk_id"] for row in rows] == ["near", "middle"]
    assert [row["native_vector_rank"] for row in rows] == [1, 2]
    assert rows[0]["score"] == pytest.approx(100 / 1.1)
    sql = client.exact_sql[0]
    # 解決済みの範囲で読む行を決め、結果の行に原生の分岐と同じ権限の条件を掛け直す。
    assert json.loads(str(client.exact_binds[0]["exact_scope_ids"])) == [7]
    assert "candidate.native_scope_id IN (" in sql
    assert sql.index("FETCH EXACT FIRST 3 ROWS ONLY") < sql.index(":filter_document_id")
    assert "is_active = 1" in sql and "d.status = 'INDEXED'" in sql
    assert "candidate.embedding IS NOT NULL" in sql
    assert "DR$" not in sql
    assert client.exact_binds[0]["filter_document_id"] == "doc-a"
    assert isinstance(rows, NativeSearchRows)
    assert rows.vector_retrieval == {
        "method": "exact_scope",
        "scope_vector_chunks": 3,
        "exact_max_chunks": 50000,
        "requested": 3,
        "expected": 3,
        "returned": 3,
        "shortage": False,
    }
    assert rows.threshold["rejected_vector_count"] == 1


def test_exact_statement_keeps_partial_ranges_and_individual_ids_in_scope() -> None:
    from app.clients.oracle import _native_exact_vector_statement, _NativeScope

    scope = _NativeScope(
        allowed_ids=["single-a"],
        complete_scopes=[1, 2],
        scope_ranges=[(3, 0, 4)],
        vector_chunks=9,
    )
    sql, binds = _native_exact_vector_statement(where="1 = 1", scope=scope, top_k=5)

    assert json.loads(str(binds["exact_scope_ids"])) == [1, 2]
    assert json.loads(str(binds["exact_scope_ranges"])) == [[3, 0, 4]]
    assert json.loads(str(binds["exact_chunk_ids"])) == ["single-a"]
    assert sql.count("UNION ALL") == 2
    only_ids = _NativeScope(
        allowed_ids=["single-a"], complete_scopes=[], scope_ranges=[], vector_chunks=1
    )
    sql, binds = _native_exact_vector_statement(where="1 = 1", scope=only_ids, top_k=5)
    assert set(binds) == {"exact_chunk_ids"}
    assert "UNION ALL" not in sql
    with pytest.raises(ValueError):
        _native_exact_vector_statement(
            where="1 = 1",
            scope=_NativeScope(
                allowed_ids=[], complete_scopes=[], scope_ranges=[], vector_chunks=0
            ),
            top_k=5,
        )


@pytest.mark.asyncio
async def test_hybrid_channel_ranks_exact_vectors_with_one_text_search() -> None:
    from app.rag.answer_engine import _hit_channels

    client = ExactScopeOracle()
    client.search_rows = [_text_row("middle", 1), _text_row("text-only", 2)]

    results = await client.search_many([("合成", [1.0] * 1536, SearchMode.HYBRID)], 3)

    assert [spec["search_fusion"] for spec in client.searches] == ["TEXT_ONLY"]
    hits = {hit.chunk_id: hit for hit in results[0]}
    # 全文と重なる行が先頭に来る。類似度の下限より低いベクトルだけの行は残らない。
    assert [hit.chunk_id for hit in results[0]] == ["middle", "near", "text-only"]
    assert hits["middle"].metadata["vector_rank"] == 2
    assert hits["middle"].metadata["keyword_rank"] == 1
    assert hits["near"].metadata["vector_rank"] == 1
    assert "keyword_rank" not in hits["near"].metadata
    assert "vector_rank" not in hits["text-only"].metadata
    assert "native_wrrf_score" not in hits["near"].metadata
    assert _hit_channels(hits["near"], 0) == {"vector:q1"}
    assert _hit_channels(hits["middle"], 0) == {"vector:q1", "keyword:oracle_text:q1"}
    assert _hit_channels(hits["text-only"], 0) == {"keyword:oracle_text:q1"}


@pytest.mark.asyncio
async def test_exact_read_does_not_query_when_no_chunk_has_an_embedding() -> None:
    client = ExactScopeOracle(vector_chunks=0)
    client.search_rows = [_text_row("text-only", 1)]

    rows = await client._native_rows("hybrid", "合成", [1.0] * 1536, top_k=3, filters={})

    assert client.exact_sql == []
    assert [row["chunk_id"] for row in rows] == ["text-only"]
    assert isinstance(rows, NativeSearchRows)
    assert rows.vector_retrieval["expected"] == 0
    assert rows.vector_retrieval["shortage"] is False


@pytest.mark.asyncio
async def test_exact_read_failure_is_a_failed_search_not_an_empty_result() -> None:
    client = ExactScopeOracle()
    client.fail_exact = True

    results = await client.search_many([("合成", [1.0] * 1536, SearchMode.VECTOR)], 3)

    assert getattr(results[0], "status", "") == "failed"
    assert isinstance(getattr(results[0], "failure", None), NativeHybridUnavailable)


@pytest.mark.parametrize(
    ("channel", "returned", "shortage"),
    [("vector", 0, True), ("hybrid", 1, True), ("vector", 2, False)],
)
@pytest.mark.asyncio
async def test_native_index_shortage_over_the_limit_is_recorded_without_exact_read(
    channel: str, returned: int, shortage: bool, caplog: pytest.LogCaptureFixture
) -> None:
    client = ExactScopeOracle(vector_chunks=10, exact_max_chunks=5)
    vector_rows = [
        {**_text_row(f"v{i}", 1), "native_vector_rank": i + 1, "native_vector_score": 80}
        for i in range(returned)
    ]
    client.search_rows = (
        [*vector_rows, _text_row("text-only", 2)] if channel == "hybrid" else (vector_rows)
    )

    with caplog.at_level("WARNING", logger="app.clients.oracle"):
        rows = await client._native_rows(channel, "合成", [1.0] * 1536, top_k=2, filters={})

    assert client.exact_sql == []
    assert isinstance(rows, NativeSearchRows)
    assert rows.vector_retrieval["method"] == "native_index"
    assert rows.vector_retrieval["expected"] == 2
    assert rows.vector_retrieval["returned"] == returned
    assert rows.vector_retrieval["shortage"] is shortage
    assert ("候補が不足" in caplog.text) is shortage


@pytest.mark.asyncio
async def test_keyword_channel_never_reads_exact_rows() -> None:
    client = ExactScopeOracle()
    client.search_rows = [_text_row("text-only", 1)]

    rows = await client._native_rows("keyword", "合成", (), top_k=3, filters={})

    assert client.exact_sql == []
    assert [spec["search_fusion"] for spec in client.searches] == ["TEXT_ONLY"]
    assert isinstance(rows, NativeSearchRows) and rows.vector_retrieval == {}


@pytest.mark.asyncio
async def test_vector_search_trace_shows_how_the_vector_branch_was_read() -> None:
    client = ExactScopeOracle()
    details: list[dict[str, Any]] = []

    class _Observation:
        def display(self, *, details: object = None, params: object = None) -> None:
            if isinstance(details, dict):
                captured.append(details)

    captured = details

    @contextmanager
    def observe(*_: object, **__: object) -> Iterator[_Observation]:
        yield _Observation()

    with patch("app.clients.oracle.observe_call", observe):
        await client.search_many([("合成", [1.0] * 1536, SearchMode.VECTOR)], 3)

    fields = {field["label"]: field["value"] for field in details[0]["fields"]}
    assert "範囲の行を距離順に読む" in fields["ベクトルの読み方 1"]
    assert "取得 3 件" in fields["ベクトルの読み方 1"]


def test_exact_chunk_limit_default_and_bounds() -> None:
    assert Settings().rag_native_vector_exact_max_chunks == 50000
    assert Settings(rag_native_vector_exact_max_chunks=0).rag_native_vector_exact_max_chunks == 0
    with pytest.raises(ValidationError):
        Settings(rag_native_vector_exact_max_chunks=-1)
