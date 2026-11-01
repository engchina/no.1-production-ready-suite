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
                "score": 0.8,
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
    assert len([sql for sql, _ in client.calls if "DBMS_HYBRID_VECTOR.SEARCH" in sql]) == 4


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
