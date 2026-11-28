"""回答の記録の検索の段に、ベクトルの実際の読み方と一覧の寄与の出所を残す（#4198）。

範囲の行を距離順に読んだ経路（``exact_scope``）は Oracle の融合（WRRF）を通らないので、
``score_source`` を ``oracle_wrrf`` にしない。原生索引の経路（``native_index``）は ``oracle_wrrf``。
行は合成。外部 I/O なし。
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.clients.oracle import NativeSearchRows, OracleClient, SearchHits
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from tests.test_search_db_roundtrips import _GenAi, _hit, _SearchOracle


def _retrieval(method: str) -> dict[str, object]:
    return {
        "method": method,
        "scope_vector_chunks": 12,
        "exact_max_chunks": 50,
        "requested": 5,
        "expected": 5,
        "returned": 5,
        "shortage": False,
    }


class _MethodOracle(_SearchOracle):
    """混合検索の 1 件を、指定したベクトルの読み方の記録と一緒に返す Oracle の代役。"""

    def __init__(self, method: str) -> None:
        super().__init__()
        self.method = method

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        hit = _hit("c-0", 1.0, mode=mode.value)
        hit.metadata["native_rrf_contribution"] = 1 / 61
        hits = SearchHits([hit])
        hits.vector_retrieval = _retrieval(self.method)
        return hits


@pytest.mark.parametrize(
    ("method", "score_source", "oracle_fusion"),
    [("exact_scope", "method_ranks", False), ("native_index", "oracle_wrrf", True)],
    ids=["exact_scope", "native_index"],
)
async def test_candidate_fusion_records_the_actual_vector_reading(
    method: str, score_source: str, oracle_fusion: bool
) -> None:
    state = _SearchState()
    engine = AnswerEngine(
        Settings(), oracle=cast(Any, _MethodOracle(method)), genai=cast(Any, _GenAi())
    )

    await engine._search(
        SearchRequest(query="合成の質問", filters={"knowledge_base_id": "kb-1"}),
        state,
        retrieval_queries=["合成の質問"],
        expand_references=False,
    )

    stage = next(s for s in state.retrieval_stages if s["stage"] == "candidate_fusion")
    (entry,) = stage["rrf"]["lists"]
    assert entry["channel"] == "hybrid"
    assert entry["vector_retrieval"] == _retrieval(method)
    assert entry["score_source"] == score_source
    assert stage["rrf"]["oracle_internal_fusion"] is oracle_fusion


@pytest.mark.parametrize(
    "method", ["exact_scope", "native_index"], ids=["exact_scope", "native_index"]
)
async def test_native_search_hits_carry_the_vector_reading(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    client = OracleClient(Settings())

    async def native_rows(*args: object, **kwargs: object) -> NativeSearchRows:
        return NativeSearchRows([], {}, _retrieval(method))

    monkeypatch.setattr(client, "_native_rows", native_rows)

    hits = await client._native_search_hits("合成の質問", [0.1], 5, {}, SearchMode.HYBRID)

    assert hits.vector_retrieval == _retrieval(method)
