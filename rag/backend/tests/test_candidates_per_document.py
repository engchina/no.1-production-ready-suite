"""検索経路ごとの候補に、1 つの文書が占める数の上限を掛ける(#5854)。

合成の候補だけを使う。chunk の多い文書 A が検索の上位を占め、別の文書の chunk が取得件数の外に
ある並びで、上限なし(既定)は今と同じ候補、上限ありは A の数が上限を超えず、空いた枠が次の順位の
別の文書の chunk で埋まることを、原生方式・個別 SQL 方式の両方で確かめる。
"""

from collections import Counter
from typing import Any, cast

import pytest

from app.clients.oracle import SearchHits
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.candidate_cap import (
    CANDIDATE_FETCH_FACTOR,
    candidate_fetch_limit,
    cap_candidates_per_document,
    summarize_candidate_caps,
)
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest

QUERY = "合成の質問"
QUERY_LIMIT = 10


def _chunk(chunk_id: str, document_id: str, rank: int, mode: SearchMode) -> RetrievedChunk:
    channel = "vector" if mode == SearchMode.VECTOR else "keyword"
    metadata: dict[str, Any] = {"retrieval_mode": mode.value, "chunk_index": rank}
    if mode != SearchMode.HYBRID:
        metadata[f"{channel}_rank"] = rank
    return RetrievedChunk(
        document_id=document_id,
        chunk_id=chunk_id,
        text=f"合成の本文 {chunk_id}",
        score=1.0 - rank / 1000,
        file_name=f"{document_id}.pdf",
        metadata=metadata,
    )


def _ranking(mode: SearchMode, total: int) -> list[RetrievedChunk]:
    """上位 30 件は文書 A、その後は文書 B〜F が順に並ぶ合成の順位。"""
    return [
        _chunk(f"{_document(rank)}:{rank}", _document(rank), rank, mode)
        for rank in range(1, total + 1)
    ]


def _document(rank: int) -> str:
    return "doc-a" if rank <= 30 else f"doc-{'bcdef'[rank % 5]}"


class _RankingOracle:
    """検索経路ごとに合成の順位を ``top_k`` 件まで返す。``search_many`` の件数を記録する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, tuple[SearchMode, ...]]] = []

    async def search_many(
        self,
        searches: list[tuple[str, list[float], SearchMode]],
        top_k: int,
        filters: dict[str, str] | None = None,
        reuse: Any = None,
    ) -> list[list[RetrievedChunk]]:
        self.calls.append((top_k, tuple(mode for _, _, mode in searches)))
        results: list[list[RetrievedChunk]] = []
        for _query, _embedding, mode in searches:
            hits = SearchHits(_ranking(mode, top_k))
            results.append(hits)
        return results

    async def context_group_siblings(self, anchors: list[RetrievedChunk], **_: Any) -> list[Any]:
        return []


class _GenAi:
    async def embed(
        self, texts: list[str], *, input_type: str = "SEARCH_DOCUMENT"
    ) -> list[list[float]]:
        return [[1.0] for _ in texts]


def _settings(backend: str, per_document: int) -> Settings:
    return Settings(
        oci_compartment_id="ocid1.compartment.oc1..example",
        rag_retrieval_query_limit=QUERY_LIMIT,
        rag_retrieval_max_candidates_per_document=per_document,
        rag_hybrid_search_backend=backend,
    )


async def _fusion(
    backend: str, per_document: int, **search: Any
) -> tuple[dict[str, Any], _RankingOracle]:
    oracle = _RankingOracle()
    state = _SearchState()
    await AnswerEngine(
        _settings(backend, per_document),
        oracle=cast(Any, oracle),
        genai=cast(Any, _GenAi()),
    )._search(
        SearchRequest(query=QUERY, filters={"knowledge_base_id": "kb-1"}),
        state,
        retrieval_queries=[QUERY],
        defer_context=True,
        **search,
    )
    stage = next(item for item in state.retrieval_stages if item["stage"] == "candidate_fusion")
    return stage, oracle


BACKENDS = [
    pytest.param("oracle_hybrid_index", (SearchMode.HYBRID,), id="native"),
    pytest.param("oracle_separate_sql", (SearchMode.VECTOR, SearchMode.KEYWORD), id="separate"),
]


@pytest.mark.parametrize(("backend", "modes"), BACKENDS)
async def test_without_a_cap_the_candidates_are_unchanged(
    backend: str, modes: tuple[SearchMode, ...]
) -> None:
    """既定(0 = 上限なし)は取得件数だけを取り、候補は検索の上位のまま(今と同じ)。"""
    stage, oracle = await _fusion(backend, 0)

    assert oracle.calls == [(QUERY_LIMIT, modes)]
    assert stage["selected_ids"] == [f"doc-a:{rank}" for rank in range(1, QUERY_LIMIT + 1)]
    assert "candidates_per_document" not in stage


@pytest.mark.parametrize(("backend", "modes"), BACKENDS)
async def test_cap_fills_freed_slots_with_the_next_documents(
    backend: str, modes: tuple[SearchMode, ...]
) -> None:
    """上限ありは多めに取り、1 文書の候補は上限まで、空いた枠は次の順位の別の文書で埋める。"""
    stage, oracle = await _fusion(backend, 3)

    assert oracle.calls == [(QUERY_LIMIT * CANDIDATE_FETCH_FACTOR, modes)]
    selected = stage["selected_ids"]
    assert len(selected) == QUERY_LIMIT
    counts = Counter(chunk_id.split(":")[0] for chunk_id in selected)
    assert counts["doc-a"] == 3
    tail = [f"{_document(rank)}:{rank}" for rank in range(31, 38)]
    # 並びは検索の順位のまま(残った chunk の順位を詰めない)。
    assert selected == ["doc-a:1", "doc-a:2", "doc-a:3", *tail]
    record = stage["candidates_per_document"]
    assert record["max_per_document"] == 3
    assert record["query_limit"] == QUERY_LIMIT
    assert record["fetch_limit"] == QUERY_LIMIT * CANDIDATE_FETCH_FACTOR
    assert record["excluded_count"] == 7
    assert record["excluded_by_document"] == {"doc-a": 7}
    assert record["added_count"] == 7
    assert record["added_by_document"] == dict(
        Counter(chunk_id.split(":")[0] for chunk_id in tail).most_common()
    )
    assert [item["channel"] for item in record["lists"]] == [mode.value for mode in modes]
    assert all(item["backfilled_count"] == 0 for item in record["lists"])
    assert all(item["kept"] == QUERY_LIMIT for item in record["lists"])
    assert stage["query_results"] == [
        {"query_index": index, "count": QUERY_LIMIT} for index in range(len(modes))
    ]


async def test_page_range_search_is_not_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """頁に絞った検索(業務ガイドの手順など)は範囲が文書を選んでいるので、上限を掛けない。"""
    import app.rag.answer_engine as engine_module

    monkeypatch.setattr(engine_module, "_with_page_ranges", lambda filters, ranges: dict(filters))
    stage, oracle = await _fusion(
        "oracle_hybrid_index",
        3,
        page_ranges=[{"document_id": "doc-a", "page_start": 1, "page_end": 9}],
    )

    assert oracle.calls == [(QUERY_LIMIT, (SearchMode.HYBRID,))]
    assert "candidates_per_document" not in stage


@pytest.mark.parametrize(
    ("limit", "per_document", "expected"),
    [(100, 0, 100), (100, 100, 100), (100, 150, 100), (100, 20, 400), (200, 1, 800)],
    ids=["none", "equal", "above", "cap", "max"],
)
def test_fetch_limit(limit: int, per_document: int, expected: int) -> None:
    assert candidate_fetch_limit(limit, per_document) == expected


def test_cap_backfills_when_other_documents_run_out() -> None:
    """別の文書で埋めきれないときは、外した chunk を元の順位で戻して件数を保つ。"""
    hits = SearchHits(
        [_chunk(f"doc-a:{rank}", "doc-a", rank, SearchMode.HYBRID) for rank in range(1, 9)]
        + [_chunk("doc-b:9", "doc-b", 9, SearchMode.HYBRID)]
    )
    hits.status = "completed"

    capped, record = cap_candidates_per_document(hits, max_per_document=2, limit=5)

    assert isinstance(capped, SearchHits)
    assert capped.status == "completed"
    # doc-a の 1・2 と doc-b、残りの 2 枠は doc-a の 3・4 を元の順位で戻す。
    assert [hit.chunk_id for hit in capped] == [
        "doc-a:1",
        "doc-a:2",
        "doc-a:3",
        "doc-a:4",
        "doc-b:9",
    ]
    assert record["backfilled_count"] == 2
    assert record["excluded"] == {"doc-a:5": "doc-a"}
    assert record["added"] == {"doc-b:9": "doc-b"}


def test_cap_keeps_failed_list_state() -> None:
    """失敗・空の一覧の状態(検索の失敗の記録)は写しに残る。"""
    hits = SearchHits()
    hits.status = "failed"
    hits.error = "RuntimeError"

    capped, record = cap_candidates_per_document(hits, max_per_document=2, limit=5)

    assert list(capped) == []
    assert (capped.status, capped.error) == ("failed", "RuntimeError")
    assert record["kept"] == 0


def test_summary_counts_only_candidates_missing_from_every_list() -> None:
    """ある経路で外れても別の経路に残った chunk は融合に入るので、外した数に数えない。"""
    first = [_chunk(f"doc-a:{rank}", "doc-a", rank, SearchMode.VECTOR) for rank in (1, 2, 3)]
    first.append(_chunk("doc-b:4", "doc-b", 4, SearchMode.VECTOR))
    second = [
        _chunk("doc-a:3", "doc-a", 1, SearchMode.KEYWORD),
        _chunk("doc-c:5", "doc-c", 2, SearchMode.KEYWORD),
    ]
    capped = [
        cap_candidates_per_document(hits, max_per_document=2, limit=3) for hits in (first, second)
    ]

    summary = summarize_candidate_caps(
        max_per_document=2,
        query_limit=3,
        fetch_limit=12,
        lists=[(0, "vector", capped[0][1]), (0, "keyword", capped[1][1])],
        capped_hits=[hits for hits, _ in capped],
    )

    assert [hit.chunk_id for hit in capped[0][0]] == ["doc-a:1", "doc-a:2", "doc-b:4"]
    assert summary["lists"][0]["excluded_by_document"] == {"doc-a": 1}
    # doc-a:3 は全文の経路に残るので、融合の候補から外れていない。
    assert (summary["excluded_count"], summary["excluded_ids"]) == (0, [])
    assert summary["added_by_document"] == {"doc-b": 1}
