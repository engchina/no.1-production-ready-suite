"""語の一致と回答可能性を分離し、生成後の不足だけを共有予算で再検索する。"""

from dataclasses import replace
from typing import Any

import pytest
from test_grounded_answer import FakeModel, draft, ids, item, observed_audit
from test_request_coverage import _patch_search, context, parent

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering as a
from rag_engine.generation.answer_models import QueryExpansionResult
from rag_engine.retrieval.extra_retrieval import ExtraRetrievalBudget
from rag_engine.retrieval.request_coverage import CoverageUsage, RequestTarget, request_rows

QUESTION = "対象Aの設定を変更する方法は？"
OLD_TEXT = "対象Aの設定を変更する機能の概要。手順の記載はありません。"
NEW_TEXT = "対象Aの設定画面を開き、項目を選択して保存ボタンを押します。"


def settings(**kwargs: Any) -> Any:
    kwargs.setdefault("request_coverage_retrieval_enabled", True)
    return replace(get_settings(environ={}, dotenv_path=None), **kwargs)


def cover(ctx, config, *, usage=None, unanswered=None, extra=None, question=QUESTION):
    return a._cover_missing_requests(
        question,
        ctx,
        config,
        run_id="run",
        preferred_engines=["docling"],
        top_k=8,
        neighbor_child_count=0,
        rerank_enabled=False,
        retrieval_scope="knowledge_base",
        classification_filter=None,
        runtime_knowledge=None,
        searched_queries=("別の初回検索文",),
        usage=usage,
        unanswered_requests=unanswered,
        extra_budget=extra,
    )


def test_high_word_overlap_does_not_assert_answerability():
    target = RequestTarget("Q1", "request", "要求", tuple(f"語{i}" for i in range(8)))
    row = request_rows([target])[0]
    assert row["status"] == "unassessed" and "best_ratio" not in row
    assert row["assessment"] == "not_performed" and row["answerability"] == "unassessed"


def _synthesize(monkeypatch, first, recover, **changes):
    old = context(parent("old", OLD_TEXT))
    merged = context(parent("old", OLD_TEXT), parent("new", NEW_TEXT))
    eid = ids(merged, QUESTION)[NEW_TEXT]
    model = FakeModel([first, draft(item(eid, NEW_TEXT, NEW_TEXT, request_id="Q1"), summary="資料に記載の操作です。")], [])
    unused = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected I/O"))  # noqa: E731
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = a.synthesize_grounded_answer(
            QUESTION,
            old,
            settings(**{"answer_audit_mode": "none", **changes}),
            image_prompt_mode="text_only",
            recover_requests=recover,
        )
    return result, model


def gap():
    return draft({"kind": "gap", "text": "操作の根拠がありません。", "request_id": "Q1", "citations": []}, summary="根拠が不足しています。")


def _hypothesis_expansion(monkeypatch, generated, effective_strategy="hyde"):
    """質問の拡張の代役。呼ばれた (文, 方式) を記録する。"""
    asked = []

    def fake(question, strategy, config, **kwargs):
        asked.append((question, strategy))
        assert a.SIMPLE_RETRIEVAL_STRATEGY not in kwargs["allowed_strategies"]
        kwargs["on_model_call"]()
        return QueryExpansionResult(
            original_question=question,
            selected_strategy=strategy,
            effective_strategy=effective_strategy,
            generated_queries=tuple(generated),
        )

    monkeypatch.setattr(a, "build_query_expansion", fake)
    return asked


def _cover_same_query(ctx, **kwargs):
    # 最初の検索の文が要求の文と同じ（プログラムでは違う検索文を作れない）場合。
    return a._cover_missing_requests(
        QUESTION,
        ctx,
        settings(),
        run_id="run",
        preferred_engines=["docling"],
        top_k=8,
        neighbor_child_count=0,
        rerank_enabled=False,
        retrieval_scope="knowledge_base",
        classification_filter=None,
        runtime_knowledge=None,
        searched_queries=(QUESTION,),
        unanswered_requests=[{"request_id": "Q1", "status": "missing"}],
        **kwargs,
    )


def test_focused_query_preserves_explicit_target_context():
    from rag_engine.retrieval.request_coverage import CoverageBudget, run_targeted_retrievals

    question = "対象Bについて、設定を変更する方法は？"
    target = RequestTarget("Q1", "request", "設定を変更する方法は？", ("設定", "変更"), query=question, context_terms=("対象b",))
    rows = [{"id": "Q1", "status": "supported", "answer_status": "missing"}]
    calls = []
    run_targeted_retrievals(
        rows,
        [target],
        budget=CoverageBudget(),
        retrieve=lambda text, queries: calls.append((text, queries)),
        tokenize=lambda text: [],
        searched_queries=("別の初回検索文",),
    )
    assert calls == [(question, (question,))]


def test_same_query_is_shared_between_distinct_request_ids():
    from rag_engine.retrieval.request_coverage import CoverageBudget, run_targeted_retrievals

    calls = []
    targets = [RequestTarget("Q1", "request", "対象の作成方法は？", ()), RequestTarget("Q2", "request", "対象の作成方法は？", ())]
    rows = [{"id": target.id, "status": "unassessed", "answer_status": "missing"} for target in targets]

    def retrieve(text, queries):
        calls.append(queries)
        return context(parent("shared", NEW_TEXT))

    results, _ = run_targeted_retrievals(rows, targets, budget=CoverageBudget(), retrieve=retrieve, tokenize=lambda text: ())
    assert len(calls) == 1
    assert [result.request_id for result in results] == ["Q1", "Q2"]
    assert [result.reused for result in results] == [False, True]
    assert results[0].parents == results[1].parents


def test_equal_token_sets_preserve_distinct_original_request_ids(monkeypatch):
    from rag_engine.retrieval.request_coverage import coverage_targets

    targets = coverage_targets("設定を作成する方法は？設定を削除する方法は？", lambda text: ["設定"])
    assert [target.id for target in targets] == ["Q1"]
    assert "作成" in targets[0].text and "削除" in targets[0].text


def _routing_candidates():
    return tuple(value for value, _ in a.QUERY_STRATEGIES if value in a._ROUTABLE_QUERY_STRATEGIES and value != a.SIMPLE_RETRIEVAL_STRATEGY)

