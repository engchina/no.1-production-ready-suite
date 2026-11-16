"""業務ガイドの手順ごとの検索と参照の頁の固定（#2395）。外部 I/O と LLM は使わない（合成の fixture）。"""

from __future__ import annotations

import itertools
from dataclasses import replace
from typing import Any

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.grounded import guide_step_evidence_lines
from rag_engine.retrieval.context_builder import ContextParentEvidence
from rag_engine.retrieval.expansion_merge import EXPANSION_EVIDENCE_REASON, GUIDE_STEP_KEY
from rag_engine.retrieval.extra_retrieval import ExtraRetrievalBudget
from rag_engine.retrieval.guide_steps import (
    GuidePlan,
    StepSearch,
    planned_searches,
    run_step_searches,
    step_queries,
)

QUESTION = "設定 A の変更方法は？"


def record(uid: str, text: str = "本文") -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="manual.pdf",
        source_run_id="source",
        chunk_id=uid,
        chunk_uid=f"run:{uid}",
        chunk_level="parent",
    )


def parent(uid: str, text: str = "本文") -> ContextParentEvidence:
    return ContextParentEvidence(record=record(uid, text), role="synthesis_parent", reason="top")


def context(*parents: ContextParentEvidence) -> AnswerContext:
    return AnswerContext(records=[p.record for p in parents], text="x", evidence_tree=tuple(parents))


def plan(*steps: dict[str, Any]) -> GuidePlan:
    result = GuidePlan.from_payload("g-1", 3, steps)
    assert result is not None
    return result


STEP_A = {"id": "s1", "title": "画面 A で設定を変える", "retrieval_hints": ["画面 A"]}
STEP_B = {
    "id": "s2",
    "title": "画面 B で反映する",
    "retrieval_hints": ["画面 B", "反映"],
    "references": [{"document_id": "doc-b", "title": "文書 B", "page_ranges": [{"page_start": 4, "page_end": 6}]}],
}


def test_plan_from_payload_reads_steps_and_page_ranges() -> None:
    result = plan(STEP_A, STEP_B, {"title": "id の無い手順は読まない"})

    assert [step.step_id for step in result.steps] == ["s1", "s2"]
    assert result.steps[1].page_filters() == [{"document_id": "doc-b", "page_start": 4, "page_end": 6}]
    # 頁の無い参照は文書の全体に絞る。
    whole = plan({"id": "s1", "title": "t", "references": [{"document_id": "doc-c"}]})
    assert whole.steps[0].page_filters() == [{"document_id": "doc-c"}]
    assert GuidePlan.from_payload("g", 1, []) is None


def test_plan_from_payload_reads_sheet_row_ranges_apart_from_pages() -> None:
    """表計算の資料はシートの行で絞る（行の番号を頁にしない。#2663）。"""
    sheets = [{"sheet_name": "シートA", "row_start": 42, "row_end": 45}, {"sheet_name": "シートB"}, {"row_start": 3}]
    result = plan({"id": "s1", "title": "t", "references": [{"document_id": "doc-s", "sheet_ranges": sheets}]})

    assert result.steps[0].page_filters() == [
        {"document_id": "doc-s", "sheet_name": "シートA", "row_start": 42, "row_end": 45},
        {"document_id": "doc-s", "sheet_name": "シートB"},
    ]
    assert result.steps[0].references[0].to_payload()["page_ranges"] == []


def test_step_queries_use_question_and_hints_or_title() -> None:
    steps = plan(STEP_A, {"id": "s3", "title": "題名だけの手順"}).steps

    assert step_queries(QUESTION, steps[0]) == (f"{QUESTION} 画面 A", (f"{QUESTION} 画面 A", "画面 A"))
    # 手がかりが無ければ手順の題名で探す。
    assert step_queries(QUESTION, steps[1])[0] == f"{QUESTION} 題名だけの手順"


def test_planned_searches_give_every_step_one_search_before_second_ones() -> None:
    steps = plan(STEP_B, STEP_A)

    assert [(step.step_id, kind) for step, kind in planned_searches(steps, 10)] == [
        ("s2", "reference"),
        ("s1", "step"),
        ("s2", "step"),
    ]
    # 上限で後ろの手順の検索が、前の手順の 2 回目に押し出されない。
    assert [(step.step_id, kind) for step, kind in planned_searches(steps, 2)] == [("s2", "reference"), ("s1", "step")]


def test_run_step_searches_passes_page_ranges_only_for_reference_search() -> None:
    calls: list[tuple[str, list[dict[str, Any]]]] = []

    def retrieve(text: str, queries: tuple[str, ...], page_ranges: list[dict[str, Any]]) -> AnswerContext:
        calls.append((text, page_ranges))
        return context(parent(f"p{len(calls)}"))

    results, stop = run_step_searches(QUESTION, plan(STEP_A, STEP_B), limit=3, deadline_seconds=10, retrieve=retrieve)

    assert stop == ""
    assert [(r.step_id, r.kind) for r in results] == [("s1", "step"), ("s2", "reference"), ("s2", "step")]
    assert calls[1][1] == [{"document_id": "doc-b", "page_start": 4, "page_end": 6}]
    assert calls[0][1] == [] and calls[2][1] == []
    # 上限で切れたら理由を残す。
    _, stopped = run_step_searches(QUESTION, plan(STEP_A, STEP_B), limit=2, deadline_seconds=10, retrieve=retrieve)
    assert stopped == "max_queries"


def test_run_step_searches_records_errors_and_continues() -> None:
    def retrieve(text: str, queries: tuple[str, ...], page_ranges: list[dict[str, Any]]) -> AnswerContext:
        if "画面 A" in text:
            raise RuntimeError("search failed")
        return context(parent("b1"))

    results, _ = run_step_searches(QUESTION, plan(STEP_A, {"id": "s3", "title": "画面 C"}), limit=4, deadline_seconds=10, retrieve=retrieve)

    # 例外の型の名前だけを残し、本文は残さない（#3259）。
    assert results[0].error == "RuntimeError" and results[0].parents == ()
    assert [p.record.chunk_uid for p in results[1].parents] == ["run:b1"]


def _settings(**changes: Any) -> Any:
    return replace(get_settings(environ={}, dotenv_path=None), **changes)


def _patch_search(monkeypatch: pytest.MonkeyPatch, results: dict[str, AnswerContext]) -> list[tuple[str, list[dict[str, Any]]]]:
    calls: list[tuple[str, list[dict[str, Any]]]] = []
    options: list[dict[str, Any]] = []

    def fake_build(question: str, run_id: Any, engines: Any, settings: Any, **kwargs: Any) -> AnswerContext:
        page_ranges = list(kwargs.get("page_ranges") or [])
        calls.append((question, page_ranges))
        options.append(kwargs)
        key = "reference" if page_ranges else question
        return results.get(key, context())

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", fake_build)
    _patch_search.options = options  # type: ignore[attr-defined]
    return calls


def _search(ctx: AnswerContext, settings: Any, guide: GuidePlan, budget: ExtraRetrievalBudget | None = None, **kwargs: Any) -> Any:
    return a._search_guide_steps(
        QUESTION,
        ctx,
        settings,
        guide,
        extra_budget=budget or ExtraRetrievalBudget.from_settings(settings),
        run_id="run",
        preferred_engines=["docling"],
        neighbor_child_count=1,
        rerank_enabled=False,
        retrieval_scope="knowledge_base",
        classification_filter=None,
        **kwargs,
    )


def _merged(*parents: ContextParentEvidence, added: tuple[str, ...] = ()) -> AnswerContext:
    """主の検索の根拠に手順の根拠を合わせた後の文脈（合わせ込みと文脈の補完は ``fill_answer_context``）。"""
    tree = []
    for item in parents:
        if item.record.chunk_uid in added:
            marked = replace(item.record, metadata={**item.record.metadata, GUIDE_STEP_KEY: {"step_ids": ["s2"]}})
            child = a.ContextChildEvidence(record=marked, role="retrieved_anchor", reason=EXPANSION_EVIDENCE_REASON)
            item = replace(item, record=marked, reason=EXPANSION_EVIDENCE_REASON, children=(child,))
        tree.append(item)
    return AnswerContext(records=[item.record for item in tree], text="x", evidence_tree=tuple(tree))


def _cover(ctx: AnswerContext, settings: Any, guide: GuidePlan, budget: ExtraRetrievalBudget | None = None) -> Any:
    """手順の検索 → 切った根拠を主の検索の根拠の後に合わせる（並びにあるものは印だけ）→ 手順の根拠を決める。

    合わせ込みの規則（同じ文書の位置・最後）と文脈の補完は ``fill_answer_context`` のテスト（test_expansion_merge）で
    確かめる。ここでは文書の無い合成の根拠を使うので、足す根拠は最後に並ぶ。
    """
    results, acquisitions, trace = _search(ctx, settings, guide, budget)
    supplements, _pool = a._guide_step_supplements(results)
    present = {item.record.chunk_uid for item in ctx.evidence_tree}
    added = tuple(item.chunk_uid for item in supplements if item.chunk_uid not in present)
    merged = _merged(
        *ctx.evidence_tree,
        *(ContextParentEvidence(record=item, role="synthesis_parent", reason="top") for item in supplements if item.chunk_uid in added),
        added=added,
    )
    added_chunks = a._merged_supplement_chunks(merged, set(a._context_owner_keys(ctx)))[GUIDE_STEP_KEY]
    trace, sources = a._finish_guide_steps(guide, results, acquisitions, trace, merged, added_chunks)
    return merged, trace, sources


def test_search_guide_steps_cuts_every_search_to_chunks_per_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """手順の検索は検索ごとに rerank・下限の後で上位 ``guide_step_chunks_per_step`` 件に切り、文脈は補わない（#5173）。"""
    calls = _patch_search(
        monkeypatch,
        {
            f"{QUESTION} 画面 A": context(parent("a1")),
            f"{QUESTION} 画面 B 反映": context(parent("b-top")),
            "reference": context(_paged("b-page", "doc-b", 5)),
        },
    )

    results, acquisitions, trace = _search(
        context(parent("a1"), parent("other")), _settings(guide_step_chunks_per_step=3), plan(STEP_A, STEP_B)
    )

    assert [q for q, _ in calls] == [f"{QUESTION} 画面 A", f"{QUESTION} 画面 B 反映", f"{QUESTION} 画面 B 反映"]
    options = _patch_search.options  # type: ignore[attr-defined]
    assert {item["top_k"] for item in options} == {3}
    assert all(item["fill"] is False and "max_records" not in item for item in options)
    assert [(r.step_id, r.kind, r.lane) for r in results] == [
        ("s1", "step", "required"),
        ("s2", "reference", "required"),
        ("s2", "step", "optional"),
    ]
    assert [row["execution"] for row in acquisitions] == ["completed", "completed"]
    assert trace["llm_calls"] == 0 and trace["top_k"] == 3
    assert trace["budget"]["used"] == {"guide_required": 2, "guide_steps": 1}


def test_guide_step_supplements_mark_steps_in_search_order() -> None:
    """手順の検索で切った根拠を検索の順で返し、同じ根拠を切った手順の印をつなぐ（#5173）。"""
    results = [
        StepSearch("s1", "step", "q", ("q",), (parent("a1"), parent("shared")), (record("pool-1"),)),
        StepSearch("s2", "reference", "q", ("q",), (parent("shared"), parent("b1")), (record("pool-2"),), owner_ids=("s2", "s3")),
    ]

    supplements, pool = a._guide_step_supplements(results)

    assert [item.chunk_uid for item in supplements] == ["run:a1", "run:shared", "run:b1"]
    assert supplements[1].metadata[GUIDE_STEP_KEY] == {"step_ids": ["s1", "s2", "s3"], "kinds": ["step", "reference"]}
    assert [item.chunk_uid for item in pool] == ["run:pool-1", "run:pool-2"]


def test_finish_guide_steps_links_step_sources_after_the_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """合わせた後の根拠で、手順ごとの根拠・足した根拠・必須の取得を決める（#2706・#5173）。"""
    _patch_search(
        monkeypatch,
        {
            f"{QUESTION} 画面 A": context(parent("a1")),
            f"{QUESTION} 画面 B 反映": context(parent("b-top")),
            "reference": context(_paged("b-page", "doc-b", 5)),
        },
    )
    guide = plan(STEP_A, STEP_B)
    main = context(parent("a1"), parent("other"))
    results, acquisitions, trace = _search(main, _settings(), guide)
    # 主の検索の根拠（a1・other）を外さず、手順の根拠を同じ規則で合わせた後の文脈。
    merged = _merged(parent("a1"), parent("other"), _paged("b-page", "doc-b", 5), parent("b-top"), added=("run:b-page", "run:b-top"))
    # 新しく足した根拠は合わせ込みの直後に決める（主の検索の根拠にあるものは数えない。#5185）。
    added_chunks = a._merged_supplement_chunks(merged, set(a._context_owner_keys(main)))[GUIDE_STEP_KEY]
    assert added_chunks == ["run:b-page", "run:b-top"]

    trace, sources = a._finish_guide_steps(guide, results, acquisitions, trace, merged, added_chunks)

    assert sources == (("s1", "画面 A で設定を変える", ("run:a1",)), ("s2", "画面 B で反映する", ("run:b-page", "run:b-top")))
    assert trace["added_chunk_ids"] == ["run:b-page", "run:b-top"]
    step_b = trace["steps"][1]
    assert step_b["has_references"] is True and step_b["evidence_count"] == 2
    assert step_b["reference_chunk_count"] == 1
    assert [s["kind"] for s in step_b["searches"]] == ["reference", "step"]
    assert [row["retained_source_ids"] for row in trace["required_acquisition"]["targets"]] == [["run:a1"], ["run:b-page"]]


def _paged(uid: str, document_id: str, page: int) -> ContextParentEvidence:
    rec = replace(record(uid), page=page, page_end=page, metadata={"document_id": document_id})
    return ContextParentEvidence(record=rec, role="synthesis_parent", reason="top")


def test_guide_steps_link_reference_pages_of_steps_cut_by_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """期限で手順 B を検索しなくても、手順 B の参照の頁の根拠（回答の材料にある）を手順 B の根拠にする（#2706）。"""
    step_a = {**STEP_A, "references": [{"document_id": "doc-b", "page_ranges": [{"page_start": 5, "page_end": 5}]}]}
    calls = _patch_search(monkeypatch, {"reference": context(_paged("shared", "doc-b", 5))})
    ctx = context(_paged("shared", "doc-b", 5), _paged("other", "doc-b", 9))
    # 開始・検索の開始・1 回目の期限の確認・1 回目の検索の開始は 0 秒、検索 1 回が 30 秒かかる。
    ticks = itertools.chain([0.0, 0.0, 0.0, 0.0], itertools.repeat(30.0))
    guide = plan(step_a, STEP_B)

    results, acquisitions, trace = _search(
        ctx, _settings(guide_step_deadline_seconds=10.0), guide, ExtraRetrievalBudget.from_settings(_settings()), clock=lambda: next(ticks)
    )
    trace, sources = a._finish_guide_steps(guide, results, acquisitions, trace, ctx)

    # 手順 A の参照の検索 1 回で期限。手順 B は検索していない。
    assert len(calls) == 1
    assert trace["stop_reason"] == "deadline"
    assert trace["unsearched_step_ids"] == []
    assert trace["required_acquisition"]["targets"][0]["execution"] == "reused"
    # 手順 B の参照の頁（文書 B の 4〜6 頁）に当たる材料の根拠は、手順 A と共有する根拠として手順 B にも紐づく。
    assert sources == (("s1", "画面 A で設定を変える", ("run:shared",)), ("s2", "画面 B で反映する", ("run:shared",)))
    assert [step["referenced_count"] for step in trace["steps"]] == [1, 1]
    assert trace["required_acquisition"]["targets"][1]["scope_coverage"] == "unknown"
    assert trace["added_chunk_ids"] == []


def test_search_guide_steps_respects_disabled_and_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_search(monkeypatch, {})
    ctx = context(parent("a1"))

    results, acquisitions, trace = _search(ctx, _settings(guide_step_retrieval_enabled=False), plan(STEP_A))
    assert (results, acquisitions, trace["skipped"], calls) == ([], [], "disabled", [])
    assert a._finish_guide_steps(plan(STEP_A), results, acquisitions, trace, ctx)[1] == (("s1", STEP_A["title"], ()),)

    results, acquisitions, trace = _search(ctx, _settings(extra_retrieval_max_queries=0), plan(STEP_A))
    assert (results, trace["stop_reason"], calls) == ([], "required_budget_exhausted", [])
    trace, _ = a._finish_guide_steps(plan(STEP_A), results, acquisitions, trace, ctx)
    assert trace["required_acquisition"]["incomplete_step_ids"] == ["s1"]


def test_guide_steps_and_request_coverage_share_one_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """手順ごとの検索と根拠の無い要求の再検索は、追加の検索の枠を 1 か所で数える。"""
    _patch_search(monkeypatch, {})
    settings = _settings(extra_retrieval_max_queries=3, guide_step_max_queries=4)
    budget = ExtraRetrievalBudget.from_settings(settings)

    _, _, trace = _search(context(parent("a1")), settings, plan(STEP_A, STEP_B, {"id": "s3", "title": "画面 C"}), budget)

    # 手順の検索は枠の 3 回まで（手順 B の 2 回目の検索は枠の外）。
    assert trace["stop_reason"] == "max_queries"
    assert budget.used == {"guide_required": 3} and budget.remaining == 0
    # 残りの無い枠では、再検索の回数は 0 になる。
    assert budget.allowance(settings.request_coverage_max_queries) == 0


def test_build_context_block_lists_step_evidence_labels() -> None:
    spans = [
        {"label": "E1", "source_id": "run:a1", "function": ("doc", "F"), "text": "a"},
        {"label": "E2", "source_id": "run:b1", "function": ("doc", "F"), "text": "b"},
    ]
    steps = (("s1", "画面 A", ("run:a1",)), ("s2", "画面 B", ("run:b1", "run:zz")), ("s3", "画面 C", ()))

    assert guide_step_evidence_lines(steps, spans) == ["- s1「画面 A」: E1", "- s2「画面 B」: E2", "- s3「画面 C」: 根拠なし"]


def test_engine_settings_default_and_env_for_guide_steps() -> None:
    default = get_settings(environ={}, dotenv_path=None)
    custom = get_settings(
        environ={
            "RAG_ENGINE_GUIDE_STEP_RETRIEVAL": "0",
            "RAG_ENGINE_GUIDE_STEP_MAX_QUERIES": "2",
            "RAG_ENGINE_GUIDE_STEP_CHUNKS_PER_STEP": "3",
            "RAG_ENGINE_GUIDE_STEP_DEADLINE_SECONDS": "2.5",
            "RAG_ENGINE_EXTRA_RETRIEVAL_MAX_QUERIES": "5",
        },
        dotenv_path=None,
    )

    assert (default.guide_step_retrieval_enabled, default.guide_step_max_queries, default.extra_retrieval_max_queries) == (True, 4, 8)
    assert default.guide_step_deadline_seconds == 60.0  # 期限の既定は上限の 60 秒（#2826）
    assert (custom.guide_step_retrieval_enabled, custom.guide_step_max_queries, custom.guide_step_chunks_per_step) == (False, 2, 3)
    assert (custom.guide_step_deadline_seconds, custom.extra_retrieval_max_queries) == (2.5, 5)
    # 参照の頁の根拠の上限は削除した（手順の検索ごとに上位 guide_step_chunks_per_step 件で切る。#5173）。
    assert not hasattr(custom, "guide_reference_max_chunks")
