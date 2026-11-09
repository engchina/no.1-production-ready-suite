"""監査モードの入力範囲と、未校正の意味観察の採否を分離する。

standard と detailed の schema・資料入力は維持するが、意味観察だけで本文を削除・修正しない。
none の決定的な引用検査と条件付き回答の既存是正は維持する。純粋な判定部品は明示した入力で別に検証する。
"""

from __future__ import annotations

import json
from dataclasses import replace

from test_grounded_answer import LIST_TEXT, FakeModel, audit, context, draft, ids, item, marks, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.models.llm import GroundedAudit, GroundedCitation, GroundedItem, GuideRequirementReview

STEP = "一覧表を選択し、処理を選択します。"


def _run(model, ctx, mode, **overrides):
    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    settings = replace(get_settings(), **overrides)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(
            "年次集計の明細一覧表を出力する手順を教えてください", ctx, settings, image_prompt_mode="text_only"
        )


def _fact(value):
    """事実の監査の応答（standard の schema に無い項目を除く）。"""
    return {key: value[key] for key in value if key in GroundedFactAudit.model_fields}


def _audit_inputs(model):
    return [json.loads(prompt) for name, prompt in model.prompts if name in {"GroundedAudit", "GroundedFactAudit"}]


def _drafts(model):
    return [prompt for name, prompt in model.prompts if name == "GroundedDraftWire"]


# ---- 監査なしの型（#2800）と、要件の監査の観察（#2800） ------------------------------------


# ---- 前提を示して回答する（未回答の確認事項。#3069） ------------------------------------

_ASSUME_QUESTION = "利用料の上限はいくらですか？"
_ROW_A = "区分Aの利用料の上限は8000円です。"
_ROW_B = "区分Bの利用料の上限は10000円です。"


def _assume_run(model, *, assume):
    from pathlib import Path

    from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext

    def unused(*a, **k):
        return (_ for _ in ()).throw(AssertionError("unexpected I/O"))

    ctx = context(record(0, _ROW_A, "区分A"), record(1, _ROW_B, "区分B"))
    knowledge = RuntimeKnowledgeContext(
        original_question=_ASSUME_QUESTION,
        expanded_question=_ASSUME_QUESTION,
        path=Path("runtime_knowledge.json"),
        loaded=True,
        assume_unconfirmed=assume,
        unconfirmed_conditions=(
            ({"condition_id": "rule:a", "question": "区分はどちらですか？", "options": ["区分A", "区分B"]},) if assume else ()
        ),
    )
    settings = replace(get_settings())
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = synthesize_grounded_answer(_ASSUME_QUESTION, ctx, settings, runtime_knowledge=knowledge, image_prompt_mode="text_only")
    return result.response, ids(ctx, _ASSUME_QUESTION)


def _gap_only():
    return draft(item("", "区分が分からないため上限を答えられません。", "", kind="gap", request_id="Q1"), summary="")


def test_refusal_without_assumption_is_not_redrafted_in_none_mode():
    model = FakeModel([_gap_only()], [])

    result, _ = _assume_run(model, assume=False)

    assert [name for name, _prompt in model.prompts] == ["GroundedDraftWire"]
    assert result.envelope["outcome"] == "insufficient_evidence"


def test_assumed_answer_with_cited_evidence_is_not_redrafted():
    ctx = context(record(0, _ROW_A, "区分A"), record(1, _ROW_B, "区分B"))
    eid = ids(ctx, _ASSUME_QUESTION)
    model = FakeModel([draft(item(eid[_ROW_A], _ROW_A, _ROW_A, applies="conditional", condition="区分A", request_id="Q1"))], [])

    _assume_run(model, assume=True)

    assert [name for name, _prompt in model.prompts] == ["GroundedDraftWire"]

