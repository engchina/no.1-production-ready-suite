"""出力データの項目の裏付けを、同じ成果物の定義と原文引用で確かめる。"""

import pytest

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedAudit, GroundedCitation, GroundedItem

QUOTE = "ファイル A の列は区分と年度です。"


@pytest.mark.parametrize("carried", [False, True])
def test_screen_filter_is_not_a_definition_even_for_a_carried_item(carried):
    quote = "画面 A の抽出条件: 区分"
    entry = grounded.CheckedItem(
        GroundedItem(
            kind="operation",
            text="出力データ A の区分列で絞り込みます。",
            citations=[GroundedCitation(quote=quote, evidence_id="E1")],
        ),
        {"text": quote, "evidence_id": "E1"},
        carried=carried,
    )
    audit = GroundedAudit(
        reviews=[
            {
                "index": 0,
                "support": "supported",
                "applicability": "matched",
                "reason": "画面項目",
                "output_fields": {"asserts_output_fields": True, "definition_quote": "", "output_artifact_matches": True},
            }
        ],
        summary_supported=True,
        goal_alignment="aligned",
    )
    grounded.apply_audit([entry], audit)
    assert any(mark["code"] == "unsupported" for mark in entry.marks)
    assert not entry.removed


def test_confirmed_export_operation_is_retained_and_unsupported_summary_is_detected():
    quote = "一覧 A を選びファイル出力を実行します。"
    entry = grounded.CheckedItem(
        GroundedItem(kind="operation", text=quote, citations=[GroundedCitation(quote=quote, evidence_id="E1")]),
        {"text": quote, "evidence_id": "E1"},
    )
    audit = GroundedAudit(
        reviews=[{"index": 0, "support": "supported", "applicability": "matched", "reason": "出力操作", "output_fields": None}],
        summary_supported=True,
        goal_alignment="aligned",
    )
    grounded.apply_audit([entry], audit)
    assert not entry.marks and not entry.removed
    bad = "出力ファイルの年度列で絞ります。"
    assert grounded.summary_asserts_denied(bad, [bad], [quote])
    assert not grounded.summary_asserts_denied(quote, [bad], [quote])


