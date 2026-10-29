"""本文の最終状態から、未確認・撤回した結論を要約で復活させない。"""

from dataclasses import replace
from unittest.mock import patch

import pytest
from test_grounded_answer import FakeModel, audit, context, draft, ids, item, marks, record, run

from rag_engine.config import get_settings
from rag_engine.generation import grounded

QUESTION = "設定を変更する手順を教えてください。"
TEXT = "追加ボタンがあります。実行ボタンを押します。"
GOOD = "実行ボタンを押します。"
CLAIM = "設定ボタンで一覧を初期化できます。"
PARAPHRASE = "設定機能を使えば一覧の初期化が可能です。"


@pytest.mark.parametrize(
    "quote,support,summary",
    [
        ("存在しない引用", "supported", CLAIM),
        ("存在しない引用", "supported", PARAPHRASE),
        ("追加ボタンがあります。", "unsupported", PARAPHRASE),
        ("追加ボタンがあります。", "contradicted", PARAPHRASE),
        ("追加ボタンがあります。", "unknown", PARAPHRASE),
    ],
)
def test_mixed_unconfirmed_body_does_not_leave_a_definitive_summary(quote, support, summary):
    ctx = context(record(0, TEXT, "合成設定", source="synthetic.pdf"))
    eid = ids(ctx, QUESTION)[TEXT]
    reviews = ((0, support, "matched", ""), (1, "supported", "matched", ""))
    if quote == "存在しない引用":
        reviews = ((0, "supported", "matched", ""),)
    model = FakeModel(
        [draft(item(eid, CLAIM, quote, request_id="Q1"), item(eid, GOOD, GOOD, request_id="Q1"), summary=summary)],
        [audit(*reviews, requests=[("Q1", "addressed")], summary_supported=True)],
    )
    with patch("test_grounded_answer.get_settings", return_value=replace(get_settings(), answer_max_correction_rounds=0)):
        result = run(model, ctx, QUESTION).response
    assert not result.answer_text.startswith(summary)
    assert result.answer_text.startswith(grounded.answer_labels().neutral_summary)
    assert GOOD in result.answer_text
    if support != "contradicted":
        assert CLAIM in result.answer_text
    assert len(model.prompts) == 2


def test_confirmed_exact_summary_is_kept_with_unrelated_unconfirmed_body():
    ctx = context(record(0, TEXT, "合成設定", source="synthetic.pdf"))
    eid = ids(ctx, QUESTION)[TEXT]
    model = FakeModel(
        [draft(item(eid, CLAIM, "追加ボタンがあります。", request_id="Q1"), item(eid, GOOD, GOOD, request_id="Q1"), summary=GOOD)],
        [
            {
                **audit(
                    (0, "unsupported", "matched", ""),
                    (1, "supported", "matched", ""),
                    requests=[("Q1", "addressed")],
                    summary_supported=True,
                ),
                "summary_basis": [GOOD],
            }
        ],
    )
    with patch("test_grounded_answer.get_settings", return_value=replace(get_settings(), answer_max_correction_rounds=0)):
        result = run(model, ctx, QUESTION).response
    assert result.answer_text.startswith(GOOD)
    assert CLAIM in result.answer_text
    assert marks(result)[CLAIM] == ["unsupported"]
    assert result.generation_trace["finalization"]["summary"]["status"] == "kept"
    assert len(model.prompts) == 2
