"""要求ごとの根拠の覆域と、根拠の無い要求だけの再検索（#1279）。外部 I/O と LLM は使わない。"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence
from rag_engine.retrieval.request_coverage import (
    CoverageBudget,
    RequestTarget,
    TargetedRetrieval,
    assess_targets,
    coverage_targets,
    evidence_texts,
    re_retrieval_skip_reason,
    request_hints,
    run_targeted_retrievals,
    select_new_parents,
    targeted_queries,
)
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext, RuntimeRule, RuntimeTerm


_VOCABULARY = re.compile("請求書|請求|再発行|受注|登録|方法|業務|種別|追加|担当|インボイス|承認|取消|締め|語[0-9]")


def split(text: str) -> list[str]:
    """決定的な分かち書きのスタブ（語彙の一致だけ）。"""
    return _VOCABULARY.findall(text)


def record(uid: str, text: str, level: str = "parent") -> AnswerRecord:
    return AnswerRecord(id=uid, engine="docling", engine_label="Docling", page=1, seq_no=1, category="Chunk",
                        text=text, source="manual.pdf", source_run_id="source", chunk_id=uid,
                        chunk_uid=f"run:{uid}", chunk_level=level)


def parent(uid: str, text: str, child_text: str = "") -> ContextParentEvidence:
    children = (ContextChildEvidence(record(f"{uid}c", child_text, "child"), "retrieved_anchor", "top"),) if child_text else ()
    return ContextParentEvidence(record=record(uid, text), role="synthesis_parent", reason="top", children=children)


def context(*parents: ContextParentEvidence) -> AnswerContext:
    return AnswerContext(records=[p.record for p in parents], text="x", evidence_tree=tuple(parents))


QUESTION = "受注の登録方法は？請求書の再発行方法は？"


def rows_for(question: str, ctx: AnswerContext) -> tuple[list[RequestTarget], list[dict[str, Any]]]:
    targets = coverage_targets(question, split)
    return targets, assess_targets(targets, evidence_texts(ctx))


def test_coverage_matrix_marks_each_request_against_evidence() -> None:
    ctx = context(parent("p1", "受注入力画面", "受注番号を入力し、登録ボタンを押します。"))

    targets, rows = rows_for(QUESTION, ctx)

    assert [t.id for t in targets] == ["Q1", "Q2"]
    # 問い方の語（方法）は数えない。
    assert "方法" not in targets[0].terms
    by_id = {row["id"]: row for row in rows}
    assert by_id["Q1"]["status"] == "supported"
    # 子の本文も根拠として数える。
    assert by_id["Q1"]["evidence_ids"] == ["run:p1"]
    assert by_id["Q2"]["status"] == "missing"
    assert by_id["Q2"]["evidence_ids"] == []


def test_background_sentence_is_not_assessed() -> None:
    targets = coverage_targets("先月から担当が変わった。受注の登録方法を教えてください。", split)

    assert [t.kind for t in targets] == ["request"]


def test_parallel_targets_are_assessed_separately() -> None:
    # 「AとBを追加」は対象ごとに判定する（親の文は一方だけで足りてしまう）。
    targets = coverage_targets("業務と種別を追加したい。", split)

    assert {t.text for t in targets} >= {"業務の追加", "種別の追加"}


def test_weak_status_between_thresholds() -> None:
    ctx = context(parent("p1", "請求一覧の表示"))
    targets = [RequestTarget("Q1", "request", "x", ("請求", "再発行", "締め"))]

    rows = assess_targets(targets, evidence_texts(ctx))

    assert rows[0]["status"] == "weak"
    assert rows[0]["best_ratio"] == pytest.approx(0.333, abs=0.01)


@pytest.mark.parametrize(
    ("statuses", "budget", "reason"),
    [
        (["supported", "supported"], CoverageBudget(), "all_covered"),
        (["supported", "weak"], CoverageBudget(), "all_covered"),
        (["missing"], CoverageBudget(), "single_request"),
        (["missing", "unassessed"], CoverageBudget(), "single_request"),
        (["supported", "missing"], CoverageBudget(enabled=False), "disabled"),
        (["supported", "missing"], CoverageBudget(max_queries=0), "no_budget"),
        (["supported", "missing"], CoverageBudget(max_chunks=0), "no_budget"),
        (["supported", "missing"], CoverageBudget(), ""),
    ],
    ids=["covered", "weak-is-not-missing", "single", "single-assessed", "disabled", "no-queries", "no-chunks", "run"],
)
def test_re_retrieval_skip_reason(statuses: list[str], budget: CoverageBudget, reason: str) -> None:
    rows = [{"id": f"Q{i}", "status": status} for i, status in enumerate(statuses, 1)]

    assert re_retrieval_skip_reason(rows, budget) == reason


def _missing(n: int) -> tuple[list[RequestTarget], list[dict[str, Any]]]:
    targets = [RequestTarget(f"Q{i}", "request", f"要求{i}", (f"語{i}",)) for i in range(1, n + 1)]
    return targets, [{"id": t.id, "status": "missing"} for t in targets]


def test_targeted_retrieval_respects_max_queries() -> None:
    targets, rows = _missing(3)
    calls: list[str] = []

    def retrieve(text: str, queries: tuple[str, ...]) -> AnswerContext:
        calls.append(text)
        return context(parent(f"new-{text}", text))

    results, stop = run_targeted_retrievals(rows, targets, budget=CoverageBudget(max_queries=2), retrieve=retrieve,
                                            tokenize=split)

    assert calls == ["要求1", "要求2"]
    assert [r.request_id for r in results] == ["Q1", "Q2"]
    assert stop == "max_queries"


def test_targeted_retrieval_stops_at_deadline() -> None:
    targets, rows = _missing(3)
    now = [0.0]
    calls: list[str] = []

    def retrieve(text: str, queries: tuple[str, ...]) -> AnswerContext:
        calls.append(text)
        now[0] += 6.0  # 1 回の検索に 6 秒かかる
        return context()

    results, stop = run_targeted_retrievals(rows, targets, budget=CoverageBudget(max_queries=5, deadline_seconds=10),
                                            retrieve=retrieve, tokenize=split, clock=lambda: now[0])

    # 2 回目は 6 秒の時点で始まり（期限内）、3 回目は 12 秒の時点なので始めない。
    assert calls == ["要求1", "要求2"]
    assert [r.elapsed_ms for r in results] == [6000, 6000]
    assert stop == "deadline"


def test_targeted_retrieval_failure_is_recorded_and_continues() -> None:
    targets, rows = _missing(2)

    def retrieve(text: str, queries: tuple[str, ...]) -> AnswerContext:
        if text == "要求1":
            raise RuntimeError("search down")
        return context(parent("new", "語2"))

    results, stop = run_targeted_retrievals(rows, targets, budget=CoverageBudget(), retrieve=retrieve, tokenize=split)

    assert results[0].error.startswith("RuntimeError")
    assert results[0].parents == ()
    assert results[1].parents and not results[1].error
    assert stop == ""


def test_select_new_parents_round_robin_skips_existing_and_caps() -> None:
    first = TargetedRetrieval("Q1", ("a",), (parent("old", "x"), parent("a1", "x"), parent("a2", "x"), parent("a3", "x")))
    second = TargetedRetrieval("Q2", ("b",), (parent("b1", "x"), parent("a1", "x")))

    selected = select_new_parents([first, second], {"run:old"}, max_chunks=3)

    assert [(rid, p.record.chunk_uid) for rid, p in selected] == [("Q1", "run:a1"), ("Q2", "run:b1"), ("Q1", "run:a2")]


def test_hints_come_from_aliases_and_guide_triggers_sharing_request_terms() -> None:
    target = RequestTarget("Q2", "request", "請求書の再発行方法は？", ("請求書", "再発行"))
    knowledge = RuntimeKnowledgeContext(
        original_question=QUESTION, expanded_question=QUESTION, path=None,  # type: ignore[arg-type]
        matched_terms=(RuntimeTerm(term="請求書", aliases=("インボイス",)), RuntimeTerm(term="受注", aliases=("オーダー",))),
        matched_rules=(RuntimeRule(rule_id="g", title="t", triggers=("再発行 承認", "受注 取消")),),
    )

    hints = request_hints(target, knowledge, split)

    assert hints == ["インボイス", "再発行 承認"]
    assert targeted_queries(target, hints) == ("請求書の再発行方法は？", "請求書の再発行方法は？ インボイス 再発行 承認")
    assert targeted_queries(target, []) == ("請求書の再発行方法は？",)


def _settings(**changes: Any) -> Any:
    return replace(get_settings(environ={}, dotenv_path=None), **changes)


def _patch_search(monkeypatch: pytest.MonkeyPatch, results: dict[str, AnswerContext]) -> list[tuple[str, tuple]]:
    calls: list[tuple[str, tuple]] = []

    def fake_build(question: str, run_id: Any, engines: Any, settings: Any, **kwargs: Any) -> AnswerContext:
        # 文書の選択と画面目録（LLM）は使わない。
        assert settings.document_selection_enabled is False and settings.screen_linking_enabled is False
        calls.append((question, tuple(kwargs["retrieval_queries"])))
        return results.get(question, context())

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", fake_build)
    monkeypatch.setattr(a, "_coverage_tokenizer", lambda settings: split)
    return calls


def _cover(ctx: AnswerContext, settings: Any, question: str = QUESTION) -> tuple[AnswerContext, dict[str, Any]]:
    return a._cover_missing_requests(
        question, ctx, settings, run_id="run", preferred_engines=["docling"], top_k=8, neighbor_child_count=0,
        rerank_enabled=False, inquiry_conditions=None, retrieval_scope="knowledge_base", classification_filter=None,
        runtime_knowledge=None)


def test_cover_missing_requests_adds_evidence_and_records_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_search(monkeypatch, {"請求書の再発行方法は？": context(
        parent("inv", "請求書の再発行は、請求一覧で再発行ボタンを押します。"))})
    ctx = context(parent("p1", "受注番号を入力し、登録ボタンを押します。"))

    merged, trace = _cover(ctx, _settings())

    assert calls == [("請求書の再発行方法は？", ("請求書の再発行方法は？",))]
    # 既存の根拠を先に残し、足した根拠を後ろに置く。
    assert [r.chunk_uid for r in merged.records] == ["run:p1", "run:inv"]
    assert merged.evidence_tree[-1].reason == "request_coverage"
    assert trace["llm_calls"] == 0
    assert trace["counts"] == {"supported": 1, "weak": 0, "missing": 1, "unassessed": 0}
    assert trace["counts_after"] == {"supported": 2, "weak": 0, "missing": 0, "unassessed": 0}
    retrieval = trace["re_retrieval"]
    assert retrieval["applied"] is True
    assert retrieval["added_chunk_ids"] == ["run:inv"]
    assert retrieval["queries"][0]["request_id"] == "Q2"
    assert retrieval["queries"][0]["added_chunk_ids"] == ["run:inv"]
    q2 = next(row for row in trace["requests"] if row["id"] == "Q2")
    assert (q2["status"], q2["status_after"]) == ("missing", "supported")


def test_cover_missing_requests_does_not_search_when_all_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_search(monkeypatch, {})
    ctx = context(parent("p1", "受注番号を入力し、登録ボタンを押します。"),
                  parent("p2", "請求書の再発行は、請求一覧で再発行ボタンを押します。"))

    merged, trace = _cover(ctx, _settings())

    assert calls == []
    assert merged is ctx
    assert trace["re_retrieval"] == {"applied": False, "skipped": "all_covered"}


def test_cover_missing_requests_respects_disabled_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_search(monkeypatch, {})
    ctx = context(parent("p1", "受注番号を入力し、登録ボタンを押します。"))

    merged, trace = _cover(ctx, _settings(request_coverage_retrieval_enabled=False))

    assert calls == []
    assert merged is ctx
    assert trace["re_retrieval"]["skipped"] == "disabled"
    assert {row["id"]: row["status"] for row in trace["requests"]} == {"Q1": "supported", "Q2": "missing"}


def test_cover_missing_requests_caps_added_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_search(monkeypatch, {"請求書の再発行方法は？": context(
        *(parent(f"inv{i}", "請求書の再発行") for i in range(5)))})
    ctx = context(parent("p1", "受注番号を入力し、登録ボタンを押します。"))

    merged, trace = _cover(ctx, _settings(request_coverage_max_chunks=2))

    assert [r.chunk_uid for r in merged.records] == ["run:p1", "run:inv0", "run:inv1"]
    assert trace["re_retrieval"]["added_chunk_ids"] == ["run:inv0", "run:inv1"]


def test_cover_missing_requests_no_new_evidence_keeps_context(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context(parent("p1", "受注番号を入力し、登録ボタンを押します。"))
    _patch_search(monkeypatch, {"請求書の再発行方法は？": ctx})

    merged, trace = _cover(ctx, _settings())

    assert merged is ctx
    assert trace["re_retrieval"]["applied"] is False
    assert trace["re_retrieval"]["added_chunk_ids"] == []
    assert "counts_after" not in trace


def test_engine_settings_default_and_env() -> None:
    default = get_settings(environ={}, dotenv_path=None)
    custom = get_settings(environ={"RAG_ENGINE_REQUEST_COVERAGE_RETRIEVAL": "0",
                                   "RAG_ENGINE_REQUEST_COVERAGE_MAX_QUERIES": "1",
                                   "RAG_ENGINE_REQUEST_COVERAGE_MAX_CHUNKS": "3",
                                   "RAG_ENGINE_REQUEST_COVERAGE_DEADLINE_SECONDS": "2.5"}, dotenv_path=None)

    assert default.request_coverage_retrieval_enabled is True
    assert (default.request_coverage_max_queries, default.request_coverage_max_chunks) == (2, 4)
    assert default.request_coverage_deadline_seconds == 10.0
    assert custom.request_coverage_retrieval_enabled is False
    assert (custom.request_coverage_max_queries, custom.request_coverage_max_chunks) == (1, 3)
    assert custom.request_coverage_deadline_seconds == 2.5
