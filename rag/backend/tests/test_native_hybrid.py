"""原生hybridの範囲・独立チャネル・設定の契約を合成行で検証する。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from rag_engine.adapters.oracle.native_hybrid import NativeHybridUnavailable, native_search_request

from app.clients.oracle import OracleClient, SearchHits
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
        super().__init__(Settings(rag_hybrid_search_backend="oracle_native"))
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
            return [{"chunk_id": "a"}]
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
    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
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
            return [{"chunk_id": f"{index:0100}", "native_scope_id": 1} for index in range(400)] + [
                {"chunk_id": "partial-a", "native_scope_id": 2}
            ]
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
