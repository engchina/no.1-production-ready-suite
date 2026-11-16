"""補いの検索（要求別の補完・業務ガイドの手順）の根拠を、主の検索と同じ切りと合わせ込みで足す（#5173）。

補いの検索は検索ごとに「検索 → rerank → 下限 → 主の検索の根拠にある候補を除く（#5175）→ ``top_k`` で切る」だけを
行い、切った根拠を検索拡張と同じ規則（並びにある → 印だけ／同じ文書 → 文書の chunk の順／無い → 最後）で主の検索の
根拠へ合わせ、文脈を補う。主の検索の根拠は外さない。検索・rerank は合成のスタブで、DB と LLM を呼ばない。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.models.llm import RerankTextRank
from rag_engine.models.storage import StoredChunk
from rag_engine.retrieval.expansion_merge import EXPANSION_EVIDENCE_REASON, GUIDE_STEP_KEY, QUERY_OVERFLOW_KEY
from rag_engine.retrieval.guide_steps import GuidePlan
from rag_engine.retrieval.scope import RETRIEVAL_SCOPE_KNOWLEDGE_BASE
from rag_engine.retrieval.text_search_tokenizer import TextSearchTokenizationResult

# chunk ごとに違う語の本文（MMR が似た本文を後ろへ回さないように）。
_WORDS = ("赤", "青", "黄", "緑", "白", "黒", "茶", "紫", "灰", "桃")


def _chunk(uid: str, seq: int, document: str) -> Any:
    word = _WORDS[(sum(map(ord, uid)) + seq) % len(_WORDS)]
    stored = StoredChunk(
        chunk_uid=uid,
        chunk_id=uid,
        chunk_level="child",
        chunk_seq=seq,
        parent_chunk_uid="",
        parent_chunk_id="",
        child_chunk_ids=(),
        text=f"{uid} {word}{seq} の合成の本文",
        retrieval_text=f"{uid} {word}{seq} の合成の本文",
        source_run_id="run",
        source_file_name=f"{document}.txt",
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=seq,
        page_end=seq,
        source_seq_ranges=(),
        source_record_refs=(),
        metadata={"chunk_strategy": "fixed", "document_id": document, "chunk_set_id": f"set-{document}"},
    )
    return _stored_chunk_answer_record(stored)


def _documents(**counts: int) -> dict[str, Any]:
    return {f"{doc}{seq}": _chunk(f"{doc}{seq}", seq, doc) for doc, count in counts.items() for seq in range(1, count + 1)}


CHUNKS = _documents(A=6, C=3, D=9, E=2, F=3)
QUESTION = "合成Aの手順と設定"
REQUEST = "対象は合成A。\n(1) 合成の手順を教えてください。\n(2) 合成の設定を教えてください。"


class Harness:
    """検索文ごとの候補と、chunk ごとの rerank の点数を返すスタブ。"""

    def __init__(self, candidates: dict[str, list[str]], scores: dict[str, float]) -> None:
        self.candidates = candidates
        self.scores = scores
        self.searches: list[tuple[str, dict[str, Any]]] = []
        self.reranked: list[list[str]] = []

    def search(self, **kwargs: Any) -> Any:
        query = kwargs["retrieval_queries"][0]
        self.searches.append((query, kwargs))
        names = next((names for key, names in self.candidates.items() if key in query), [])
        children = [CHUNKS[name] for name in names]
        return SimpleNamespace(child_chunks=children, all_chunks=list(CHUNKS.values()), expansion_chunks=[])

    def rerank(self, question: str, documents: list[str], settings: Any, top_n: int | None = None) -> list[RerankTextRank]:
        names = [next(name for name in CHUNKS if document.split("\n")[-1].startswith(f"{name} ")) for document in documents]
        self.reranked.append(names)
        return [RerankTextRank(index, self.scores.get(name, 0.0)) for index, name in enumerate(names)]


def _run(
    monkeypatch: pytest.MonkeyPatch, harness: Harness, *, guide: GuidePlan | None = None, request: str | None = REQUEST, **changes: Any
) -> Any:
    monkeypatch.setattr(a, "search_adb_hybrid_chunks", harness.search)
    monkeypatch.setattr(a, "_stored_chunk_answer_record", lambda record: record)
    monkeypatch.setattr(a, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(a, "rerank_text_with_scores", harness.rerank)
    monkeypatch.setattr(a, "check_adb_hybrid_search_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        a,
        "tokenize_text_search_query_with_trace",
        lambda text, **kwargs: TextSearchTokenizationResult(tokens=("語",), oracle_text_query="{語}", truncated_count=int(text == REQUEST)),
    )
    settings = replace(
        get_settings(environ={}, dotenv_path=None),
        default_rerank_enabled=True,
        rerank_min_relevance_score=0.3,
        section_expansion_enabled=False,
        **changes,
    )
    return a.answer_question_result(
        QUESTION,
        "synthetic-run",
        ["docling"],
        settings,
        chunk_top_k=2,
        chunk_neighbor_count=1,
        retrieval_scope=RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
        generate_answer=False,
        request_question=request,
        guide_plan=guide,
    )


def _evidence(result: Any) -> list[str]:
    return [item["chunk_uid"] for item in result.evidence_items]


SCORES = {"A2": 0.9, "C2": 0.7, "E1": 0.88, "A5": 0.85, "D2": 0.6, "D9": 0.1, "F1": 0.9, "F2": 0.8, "F3": 0.75}


def _step(result: Any, name: str) -> dict[str, str]:
    """工程の記録の欄（ラベル → 値）。同じ名前の工程が複数あれば最初のもの。"""
    step = next(item for item in result.execution_steps if item["name"] == name)
    return {field["label"]: field["value"] for field in step["details"]["fields"]}


MAIN = ["A1", "A2", "A3", "C1", "C2", "C3"]


def _keeps_main_evidence(evidence: list[str]) -> bool:
    """主の検索の根拠（A2・C2 と前後の文脈）が 1 つも外れず、並びの順も同じか。"""
    return [uid for uid in evidence if uid in MAIN] == MAIN


def test_request_supplements_skip_main_evidence_before_the_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness({QUESTION: ["A2", "C2"], "(1)": ["D9", "D2", "A5", "E1", "A2"]}, SCORES)

    result = _run(monkeypatch, harness, request_coverage_max_chunks=3)

    # 要求の検索も rerank にかける（主の検索 1 回 + 要求 1 回）。
    assert [sorted(names) for names in harness.reranked] == [["A2", "C2"], ["A2", "A5", "D2", "D9", "E1"]]
    trace = result.generation_trace["query_overflow"]
    # 上限 3 件（要求 1 件）で切る: 下限 0.3 で D9 を外し、主の検索の根拠にある A2 を切りの前に除き（#5175）、
    # 残りの rerank の上位 3 件（E1・A5・D2）を採る。枠はすべて新しい根拠に使う。
    assert [row["top_k"] for row in trace["requests"]] == [3]
    assert trace["candidate_chunk_ids"] == ["E1", "A5", "D2"]
    assert trace["requests"][0]["new_candidates"] == 3
    # 除いた件数と理由を記録に残す。
    assert trace["requests"][0]["covered_candidates"] == 1
    assert _step(result, "主の検索の根拠と重複する候補の除外")["結果"] == (
        "主の検索の根拠と重複する 1 件を除きました（主の検索の根拠と同じ chunk 1 件）。残りの候補 3 件"
    )
    assert "主の検索の根拠と重複して除いた候補 1 件" in _step(result, "検索上限の要求別補完")["結果"]
    # 主の検索の根拠（A2・C2 と前後の文脈）は外れない。A5 は同じ文書 A の文書の順の位置に入り（前後の文脈 A4・A6 と
    # 一緒に）、別の文書の E1・D2 は前後の文脈と一緒に最後（合わせ込みの規則は #5173 と同じ）。
    assert _evidence(result) == ["A1", "A2", "A3", "A4", "A5", "A6", "C1", "C2", "C3", "E1", "E2", "D1", "D2", "D3"]
    assert _keeps_main_evidence(_evidence(result))
    assert trace["added_chunk_ids"] == ["E1", "D2"]
    children = {child["chunk_uid"]: child for item in result.evidence_items for child in item.get("children") or ()}
    assert children["A2"]["retrieval_role"] == "retrieved_anchor" and children["A2"]["context_reason"] == "top_retrieved_child"
    assert children["E1"]["retrieval_role"] == "retrieved_anchor" and children["E1"]["context_reason"] == EXPANSION_EVIDENCE_REASON
    assert "D9" not in _evidence(result)


def test_request_supplements_fill_the_cut_when_all_top_candidates_are_main_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    # rerank の上位 2 件（A2・C2）がどちらも主の検索の根拠でも、枠 2 件は次の新しい候補（F1・F2）で埋まる。
    harness = Harness({QUESTION: ["A2", "C2"], "(1)": ["F3", "F2", "F1", "C2", "A2"]}, {**SCORES, "A2": 0.99, "C2": 0.98})

    result = _run(monkeypatch, harness, request_coverage_max_chunks=2)

    trace = result.generation_trace["query_overflow"]
    assert trace["candidate_chunk_ids"] == ["F1", "F2"]
    assert trace["requests"][0]["covered_candidates"] == 2
    assert _keeps_main_evidence(_evidence(result))
    assert {"F1", "F2"} <= set(_evidence(result)) and "F3" in _evidence(result)
    assert trace["added_chunk_ids"] == ["F1", "F2"]


def test_guide_step_supplements_use_the_same_cut_and_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    guide = GuidePlan.from_payload("g-1", 1, [{"id": "s1", "title": "合成の工程", "retrieval_hints": ["工程F"]}])
    assert guide is not None
    harness = Harness({"工程F": ["F1", "F2", "F3", "A2"], QUESTION: ["A2", "C2"]}, SCORES)

    result = _run(monkeypatch, harness, guide=guide, request=None, guide_step_chunks_per_step=2)

    step_searches = [kwargs for query, kwargs in harness.searches if "工程F" in query]
    assert step_searches and {kwargs["candidate_limit"] for kwargs in step_searches} == {a._retrieval_candidate_limit(2, None)}
    # 手順の検索も主の検索の根拠にある A2 を切りの前に除き（#5175）、上位 2 件（F1・F2）で切る。F3 は F2 の前後の文脈。
    assert _evidence(result) == ["A1", "A2", "A3", "C1", "C2", "C3", "F1", "F2", "F3"]
    assert _keeps_main_evidence(_evidence(result))
    roles = {child["chunk_uid"]: child for item in result.evidence_items for child in item.get("children") or ()}
    assert roles["F1"]["context_reason"] == EXPANSION_EVIDENCE_REASON and roles["F2"]["context_reason"] == EXPANSION_EVIDENCE_REASON
    guide_trace = result.generation_trace["guide_steps"]
    assert guide_trace["added_chunk_ids"] == ["F1", "F2"]
    search = guide_trace["steps"][0]["searches"][0]
    assert search["candidates"] == 2 and search["covered_candidates"] == 1
    # 除いた A2（主の検索の根拠）も手順の根拠と必須の取得の候補に数える。
    assert guide_trace["steps"][0]["evidence_count"] == 3
    assert "A2" in guide_trace["required_acquisition"]["targets"][0]["candidate_source_ids"]
    assert "主の検索の根拠と重複して除いた候補 1 件" in _step(result, "業務ガイドの手順ごとの検索")["結果"]


def test_request_and_guide_supplements_share_one_exclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """要求別の補完と業務ガイドの手順の検索は、同じ 1 つの除外の判定を使う（#5175）。"""
    guide = GuidePlan.from_payload("g-1", 1, [{"id": "s1", "title": "合成の工程", "retrieval_hints": ["工程F"]}])
    assert guide is not None
    harness = Harness({"工程F": ["F1", "A2"], "(1)": ["E1", "C2"], QUESTION: ["A2", "C2"]}, SCORES)
    calls: list[tuple[str, Any]] = []
    original = a.build_adb_hybrid_answer_context

    def spy(question: str, *args: Any, **kwargs: Any) -> Any:
        calls.append((kwargs.get("rerank_phase", ""), kwargs.get("exclude")))
        return original(question, *args, **kwargs)

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", spy)
    result = _run(monkeypatch, harness, guide=guide)

    excludes = {phase: exclude for phase, exclude in calls}
    assert excludes["main_search"] is None
    assert excludes["missing_requirement"] is not None and excludes["missing_requirement"] is excludes["guide_step"]
    assert result.generation_trace["query_overflow"]["requests"][0]["covered_candidates"] == 1
    assert result.generation_trace["guide_steps"]["steps"][0]["searches"][0]["covered_candidates"] == 1
    assert _keeps_main_evidence(_evidence(result))


def test_supplement_marks_are_kept_on_merged_records() -> None:
    record = CHUNKS["A5"]
    marked = a._with_supplement_mark(record, QUERY_OVERFLOW_KEY, {"request_ids": ["Q1"]})
    marked = a._with_supplement_mark(marked, QUERY_OVERFLOW_KEY, {"request_ids": ["Q2", "Q1"]})
    marked = a._with_supplement_mark(marked, GUIDE_STEP_KEY, {"step_ids": ["s1"]})
    assert marked.metadata[QUERY_OVERFLOW_KEY] == {"request_ids": ["Q1", "Q2"]}
    assert marked.metadata[GUIDE_STEP_KEY] == {"step_ids": ["s1"]}
    assert QUERY_OVERFLOW_KEY not in (record.metadata or {})
