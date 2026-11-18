"""融合順位の下位にある必要な原文が、rerank の判断を受けて回答まで届くことを確かめる。

#4114・#5443。

合成の資料だけを使う。質問と異なる書き方で開始日を持つ原文を、取得件数の上限（100）の
下位に置く。
同じ文書の別の chunk は候補の上位にある（「同じ文書が候補にある」と「必要な原文が候補にある」は
別）。
取得した候補は融合の順位で切らずにすべて rerank へ進む（#5443）。
"""

import json
import re
from typing import Any, cast

import pytest
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import GroundedDraft

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm

QUESTION = "合成制度の義務者が変わったのはいつから？"
NEEDED_TEXT = "この要綱は、合成契約の成立が2031年4月1日以降であるものに適用します。"
NEEDED_DATE = "2031年4月1日"
NEEDED_ID = "doc-needed:96"
NEEDED_RANK = 96
# 同じ文書の別の chunk の融合順位。
SIBLING_RANKS = (5, 60, 70, 80, 90)
TOTAL = 150
# 検索経路ごとの取得件数（RAG_RETRIEVAL_QUERY_LIMIT の上限）。
QUERY_LIMIT = 100


def _chunk(chunk_id: str, document_id: str, text: str, seq: int) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=document_id,
        chunk_id=chunk_id,
        text=text,
        score=0.9,
        file_name=f"{document_id}.pdf",
        metadata={
            "chunk_group_id": f"group-{seq}",
            "chunk_strategy": "fixed",
            "engine_search_text": f"Source file: {document_id}.pdf\nChild text: {text}",
            "engine_chunk_seq": seq,
            "page_start": seq,
            "page_end": seq,
            "engine_metadata_json": json.dumps(
                {
                    "schema_version": 4,
                    "active": True,
                    "atomic": False,
                    "content_hash": f"h{seq}",
                    "classification": {},
                    "source_categories": ["Text"],
                    "section_path": ["合成の章"],
                    "section_path_sources": ["section_header"],
                }
            ),
            "source_record_refs_json": json.dumps(
                [{"record_id": f"r-{document_id}-{seq}", "page": seq, "seq_no": seq}]
            ),
        },
    )


def _ranked_chunks(total: int = TOTAL) -> list[RetrievedChunk]:
    """融合順位の順の合成の候補。"""
    rows: list[RetrievedChunk] = []
    for rank in range(1, total + 1):
        if rank == NEEDED_RANK:
            rows.append(_chunk(NEEDED_ID, "doc-needed", NEEDED_TEXT, rank))
        elif rank in SIBLING_RANKS:
            rows.append(
                _chunk(f"doc-needed:{rank}", "doc-needed", f"合成の別の条 {rank} です。", rank)
            )
        else:
            rows.append(
                _chunk(f"doc-other:{rank}", "doc-other", f"合成の別資料 {rank} です。", rank)
            )
    return rows


class _RankedOracle(FakeOracle):
    def __init__(self, rows: list[RetrievedChunk]) -> None:
        super().__init__()
        self.chunks = rows

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        return self.chunks[:top_k]

    async def context_group_siblings(
        self,
        anchors: list[RetrievedChunk],
        *,
        max_chunks_per_group: int,
        neighbor_child_count: int | None = None,
    ) -> list[RetrievedChunk]:
        return []


class _ScoredGenAi(FakeGenAi):
    """必要な原文にだけ指定の点数を返し、ほかは最低点の下にする。"""

    def __init__(self, needed_score: float) -> None:
        self.needed_score = needed_score
        self.document_counts: list[int] = []

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        self.document_counts.append(len(documents))
        scores = [
            (index, self.needed_score if NEEDED_TEXT in document else 0.1)
            for index, document in enumerate(documents)
        ]
        return sorted(scores, key=lambda item: -item[1])[:top_n]


def _settings(**overrides: Any) -> Settings:
    return Settings(
        **{
            "oci_compartment_id": "ocid1.compartment.oc1..example",
            "rag_query_strategy": "simple_retrieval",
            "rag_answer_flow": "standard_rag",
            "rag_retrieval_query_limit": QUERY_LIMIT,
            **overrides,
        }
    )


def _llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
    """渡された根拠に必要な原文があるときだけ、その原文を引いて答える（無ければ推測しない）。"""
    if getattr(schema, "_uses_full_answer", False):
        selected = [
            uid
            for uid, text in re.findall(r"(?m)^\d+\| \[([A-Z][0-9]+\.[0-9]+)\] (.*)$", prompt)
            if NEEDED_TEXT in text
        ]
        return cast(Any, schema).model_validate(
            {
                "answer_body": NEEDED_TEXT + "".join(f"[{name}]" for name in selected)
                if selected
                else "合成の資料が不足しています。",
                "response_kind": "answered" if selected else "insufficient_evidence",
                "confidence": "high" if selected else "low",
            }
        )
    if not issubclass(schema, (GroundedDraft, generation_draft_wire_model())):
        return _fake_llm(system, prompt, settings, schema, **options)
    match = re.search(r"\[(E[0-9]+)\].*?" + re.escape(NEEDED_TEXT), prompt, re.DOTALL)
    items = (
        [
            {
                "kind": "rule",
                "text": NEEDED_TEXT,
                "citations": [{"evidence_id": match.group(1), "quote": NEEDED_TEXT}],
            }
        ]
        if match
        else []
    )
    return GroundedDraft.model_validate(
        {
            "summary": NEEDED_DATE if match else "",
            "summary_item_indexes": list(range(len(items))),
            "items": items,
            "confidence": "high" if match else "low",
        }
    )


async def _fusion_stage(settings: Settings, rows: list[RetrievedChunk]) -> Any:
    state = _SearchState()
    await AnswerEngine(
        settings,
        oracle=cast(Any, _RankedOracle(rows)),
        genai=cast(Any, FakeGenAi()),
    )._search(
        SearchRequest(query=QUESTION),
        state,
        retrieval_queries=[QUESTION],
        expand_references=False,
        defer_context=True,
    )
    return next(stage for stage in state.retrieval_stages if stage["stage"] == "candidate_fusion")


async def test_all_fetched_candidates_reach_rerank_without_a_fusion_cut() -> None:
    """取得した候補は融合の順位で切らない。取得の数は検索経路ごとの取得件数だけで決まる（#5443）。"""
    stage = await _fusion_stage(_settings(), _ranked_chunks())

    assert len(stage["selected_ids"]) == QUERY_LIMIT
    assert NEEDED_ID in stage["selected_ids"]
    assert "excluded" not in stage
    assert any(item.startswith("doc-needed:") for item in stage["selected_ids"])


async def test_answer_uses_low_fusion_rank_source_chosen_by_rerank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """融合順位が低くても rerank の点数が高い原文は、回答の根拠と引用に届く。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _llm)
    genai = _ScoredGenAi(needed_score=0.9)
    outcome = await AnswerEngine(
        _settings(),
        oracle=cast(Any, _RankedOracle(_ranked_chunks())),
        genai=cast(Any, genai),
    ).run(SearchRequest(query=QUESTION, generate_answer=True))

    assert NEEDED_ID in {citation.chunk_id for citation in outcome.citations}
    assert NEEDED_DATE in (outcome.answer or "")
    assert max(genai.document_counts) <= QUERY_LIMIT


async def test_threshold_is_lowered_once_when_no_candidate_reaches_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """資料の候補が 1 件も下限 0.3 に届かないとき、下限を 0.5 倍（0.15）にして 1 回だけ判断し直す。

    #5443。
    """
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _llm)
    outcome = await AnswerEngine(
        _settings(),
        oracle=cast(Any, _RankedOracle(_ranked_chunks())),
        genai=cast(Any, _ScoredGenAi(needed_score=0.2)),
    ).run(SearchRequest(query=QUESTION, generate_answer=True))

    assert NEEDED_ID in {citation.chunk_id for citation in outcome.citations}
    stages = outcome.diagnostics["retrieval_stages"]
    threshold = next(stage for stage in stages if stage["stage"] == "rerank_threshold")
    assert threshold["relaxed"] is True
    assert (threshold["configured_minimum"], threshold["minimum"]) == (0.3, 0.15)
    decision = next(item for item in threshold["decisions"] if item["candidate_id"] == NEEDED_ID)
    assert decision["status"] == "kept"
    # 0.1 の候補は下げた下限にも届かない。
    assert {item["status"] for item in threshold["decisions"]} == {"kept", "excluded"}


async def test_threshold_is_not_lowered_when_the_ratio_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _llm)
    outcome = await AnswerEngine(
        _settings(rag_rerank_min_relevance_fallback_ratio=0.0),
        oracle=cast(Any, _RankedOracle(_ranked_chunks())),
        genai=cast(Any, _ScoredGenAi(needed_score=0.2)),
    ).run(SearchRequest(query=QUESTION, generate_answer=True))

    assert outcome.diagnostics["outcome"] == "insufficient_evidence"
    threshold = next(
        stage
        for stage in outcome.diagnostics["retrieval_stages"]
        if stage["stage"] == "rerank_threshold"
    )
    assert threshold["relaxed"] is False
    assert threshold["minimum"] == 0.3


async def test_source_below_the_lowered_threshold_is_reported_as_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """下げた下限（0.15）にも届かない原文は渡さない。日付を推測せず、資料不足を明示する。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _llm)
    outcome = await AnswerEngine(
        _settings(),
        oracle=cast(Any, _RankedOracle(_ranked_chunks())),
        genai=cast(Any, _ScoredGenAi(needed_score=0.12)),
    ).run(SearchRequest(query=QUESTION, generate_answer=True))

    assert outcome.diagnostics["outcome"] == "insufficient_evidence"
    assert NEEDED_DATE not in (outcome.answer or "")
    assert NEEDED_ID not in {citation.chunk_id for citation in outcome.citations}
    stages = outcome.diagnostics["retrieval_stages"]
    threshold = next(stage for stage in stages if stage["stage"] == "rerank_threshold")
    decision = next(item for item in threshold["decisions"] if item["candidate_id"] == NEEDED_ID)
    assert decision["status"] == "excluded"
    assert threshold["minimum"] == 0.15
    fusion = next(stage for stage in stages if stage["stage"] == "candidate_fusion")
    assert NEEDED_ID in fusion["selected_ids"]
