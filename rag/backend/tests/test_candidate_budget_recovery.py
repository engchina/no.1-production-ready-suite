"""融合順位の下位にある必要な原文が、rerank の判断を受けて回答まで届くことを確かめる（#4114）。

合成の資料だけを使う。質問と異なる書き方で開始日を持つ原文を、融合の 396 位に置く。同じ文書の別の
chunk は候補の上位にある（「同じ文書が候補にある」と「必要な原文が候補にある」は別）。
"""

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from rag_engine.generation.answer_records import _retrieval_candidate_limit
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import GroundedDraft

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState, build_engine_settings
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm

QUESTION = "合成制度の義務者が変わったのはいつから？"
NEEDED_TEXT = "この要綱は、合成契約の成立が2031年4月1日以降であるものに適用します。"
NEEDED_DATE = "2031年4月1日"
NEEDED_ID = "doc-needed:396"
NEEDED_RANK = 396
# 同じ文書の別の chunk の融合順位。上位と、多様化の文書ごとの枠（5 件）を先に使う位置に置く。
SIBLING_RANKS = (5, 160, 170, 180, 190, 200)
TOTAL = 450


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
            # 融合順位の深い候補を rerank まで届ける広い枠を明示する（既定の枠では落ちる）。
            "rag_retrieval_query_limit": 600,
            "rag_retrieval_candidate_limit": 1000,
            "rag_rerank_candidate_limit": 0,
            **overrides,
        }
    )


def _llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
    """渡された根拠に必要な原文があるときだけ、その原文を引いて答える（無ければ推測しない）。"""
    if getattr(schema, "_uses_full_answer", False):
        selected = [
            uid
            for uid, text in re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\] (.*)$", prompt)
            if NEEDED_TEXT in text
        ]
        return cast(Any, schema).model_validate(
            {
                "answer_body": NEEDED_TEXT + "[C1]" if selected else "合成の資料が不足しています。",
                "response_kind": "answered" if selected else "insufficient_evidence",
                "confidence": "high" if selected else "low",
                "citations": [{"unit_ids": selected}] if selected else [],
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


async def _fusion_stage(
    settings: Settings, candidate_limit: int, rows: list[RetrievedChunk]
) -> Any:
    state = _SearchState()
    await AnswerEngine(
        settings,
        oracle=cast(Any, _RankedOracle(rows)),
        genai=cast(Any, FakeGenAi()),
    )._search(
        SearchRequest(query=QUESTION),
        state,
        retrieval_queries=[QUESTION],
        candidate_limit=candidate_limit,
        expand_references=False,
        defer_context=True,
    )
    return next(stage for stage in state.retrieval_stages if stage["stage"] == "candidate_fusion")


async def test_needed_source_below_old_budget_is_dropped_while_its_document_stays() -> None:
    """従来の上限では、同じ文書の候補は残るのに必要な原文が融合順位で落ちる（脱落の再現）。"""
    stage = await _fusion_stage(_settings(), 150, _ranked_chunks())

    assert stage["fused_ids"][NEEDED_RANK - 1] == NEEDED_ID
    assert NEEDED_ID not in stage["selected_ids"]
    assert {"id": NEEDED_ID, "reason": "fusion_candidate_budget"} in stage["excluded"]
    # 診断は chunk の単位で残り、同じ文書が候補にあることと区別できる。
    assert any(item.startswith("doc-needed:") for item in stage["selected_ids"])


async def test_wide_budget_gives_the_needed_source_to_rerank(tmp_path: Path) -> None:
    settings = _settings()
    limit = _retrieval_candidate_limit(50, build_engine_settings(settings, output_dir=tmp_path))
    stage = await _fusion_stage(settings, limit, _ranked_chunks())

    assert limit == settings.rag_retrieval_candidate_limit == 1000
    assert NEEDED_ID in stage["selected_ids"]
    assert stage["excluded"] == []


async def test_candidate_budget_stays_an_upper_limit() -> None:
    """無制限にしない: 上限を超えた候補は理由を残して外す。"""
    stage = await _fusion_stage(_settings(rag_candidate_diversity_depth=0), 200, _ranked_chunks())

    assert stage["base_limit"] == 200
    assert len(stage["selected_ids"]) == 200
    assert len(stage["excluded"]) == TOTAL - 200
    assert {item["reason"] for item in stage["excluded"]} == {"fusion_candidate_budget"}


async def test_answer_uses_low_fusion_rank_source_chosen_by_rerank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """融合順位が低くても rerank の点数が高い原文は、回答の根拠と引用に届く（修正前後の比較）。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _llm)
    before_genai = _ScoredGenAi(needed_score=0.9)
    before = await AnswerEngine(
        _settings(rag_retrieval_candidate_limit=150),
        oracle=cast(Any, _RankedOracle(_ranked_chunks())),
        genai=cast(Any, before_genai),
    ).run(SearchRequest(query=QUESTION, generate_answer=True))
    after_genai = _ScoredGenAi(needed_score=0.9)
    after = await AnswerEngine(
        _settings(),
        oracle=cast(Any, _RankedOracle(_ranked_chunks())),
        genai=cast(Any, after_genai),
    ).run(SearchRequest(query=QUESTION, generate_answer=True))

    assert NEEDED_ID not in {citation.chunk_id for citation in before.citations}
    assert NEEDED_DATE not in (before.answer or "")
    assert NEEDED_ID in {citation.chunk_id for citation in after.citations}
    assert NEEDED_DATE in (after.answer or "")
    # 根拠の再現と引き換えの費用は rerank の入力の件数。呼び出しの回数は変わらない。
    assert max(before_genai.document_counts) < max(after_genai.document_counts) <= TOTAL
    assert len(before_genai.document_counts) == len(after_genai.document_counts)


async def test_source_below_minimum_score_is_reported_as_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """下げた合格点でも全部落ちる原文は渡さない。日付を推測せず、資料不足を明示する。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _llm)
    outcome = await AnswerEngine(
        _settings(),
        oracle=cast(Any, _RankedOracle(_ranked_chunks())),
        # 全部が不合格なら合格点を 0.7 倍にしてやり直すので、下げた合格点より下の点数にする。
        genai=cast(Any, _ScoredGenAi(needed_score=0.12)),
    ).run(SearchRequest(query=QUESTION, generate_answer=True))

    assert outcome.diagnostics["outcome"] == "insufficient_evidence"
    assert NEEDED_DATE not in (outcome.answer or "")
    assert NEEDED_ID not in {citation.chunk_id for citation in outcome.citations}
    stages = outcome.diagnostics["retrieval_stages"]
    threshold = next(stage for stage in stages if stage["stage"] == "rerank_threshold")
    # 候補に入って最低点で外れたことが、融合の予算で落ちたことと区別して残る。
    decision = next(item for item in threshold["decisions"] if item["candidate_id"] == NEEDED_ID)
    assert decision["status"] == "excluded"
    assert threshold["relaxed_from"] is not None
    assert threshold["minimum"] < threshold["relaxed_from"]
    fusion = next(stage for stage in stages if stage["stage"] == "candidate_fusion")
    assert NEEDED_ID in fusion["selected_ids"]
