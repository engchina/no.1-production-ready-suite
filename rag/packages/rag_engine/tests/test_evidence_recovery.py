"""引用の ID・範囲の検証と、本文の内部根拠ラベルの除去。"""

import json

import pytest
from test_grounded_answer import FakeModel, audit, context, draft, item, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import _grounded_spans, synthesize_grounded_answer
from rag_engine.models.llm import GroundedDraft

QUESTION = "一覧から対象を送信する方法"
ORIGINAL = "一覧画面を開き、対象の行を選択して送信ボタンを押します。"
PARAPHRASE = "一覧画面で対象の行を選択し、送信ボタンを押します。"


def _spans():
    return _grounded_spans(QUESTION, context(record(0, ORIGINAL, "一覧画面", page=4, source="手順書 A.pdf")), (), None)


def _run(*, support="supported", mode="standard", suffix="", evidence_id="E1", complete_guide=False):
    spans = _spans()
    invalid = item(evidence_id, PARAPHRASE + suffix, "対象を決めて提出してください。")
    invalid["citations"][0]["lines"] = "999999"
    generated = draft(invalid, summary="操作の説明（E1）")
    response = audit((0, support, "matched", ""), requests=[("Q1", "addressed", "")])
    if mode == "standard":
        response.pop("unused_evidence_ids")
    if complete_guide:
        response["guide_requirement_reviews"] = [
            {
                "requirement_id": "step-a:0",
                "answer_state": "explained",
                "item_indexes": [0],
                "answer_quote": PARAPHRASE,
                "reason": "本文で操作を説明",
            }
        ]
    model = FakeModel([generated], [response])
    result = grounded.run_round(
        QUESTION,
        spans,
        get_settings(),
        prompt="合成の質問",
        image_paths=[],
        provider_id=None,
        parse_text=model,
        parse_images=model,
        audit_mode=mode,
        guide_steps=[("step-a", "対象を送信", [spans[0]["source_id"]])],
        guide_step_contracts=[{"step_id": "step-a", "requirements": [{"id": "step-a:0", "kind": "completion", "text": "対象を送信"}]}]
        if complete_guide
        else (),
    )
    return result, model, spans


@pytest.mark.parametrize("suffix", ["（E1）", "(E1)", "[E1]", "【E1】", "（E1、E2）"])
def test_inline_labels_are_not_published(suffix):
    spans = _spans()
    generated = GroundedDraft.model_validate(draft(item("E1", PARAPHRASE + suffix, ORIGINAL)))
    checked, _ = grounded.verify(QUESTION, generated, spans)
    assert checked[0].item.text == PARAPHRASE
    assert checked[0].span is not None


def test_a_real_document_code_is_not_an_internal_label():
    spans = [{"text": "エラーコード E6（E6）は接続エラーです。", "evidence_id": "a", "label": "E1"}]
    assert grounded._clean_evidence_labels("エラーコード E6（E6）を確認します。（E1）", spans) == "エラーコード E6（E6）を確認します。"
    assert grounded._clean_evidence_labels("コード E60 を確認します。E6", [{"text": "コード E60"}]) == "コード E60 を確認します。"

