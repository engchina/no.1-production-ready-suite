"""出力データの項目の裏付けを、同じ成果物の定義と原文引用で確かめる。"""

import pytest

from rag_engine.evaluation.answer_validation import ValidationClaimCheck, _enforce_support
from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedAudit, GroundedItem, GroundedItemReview

QUOTE = "ファイル A の列は区分と年度です。"


@pytest.mark.parametrize(
    "quote,matches,supported",
    [
        ("", True, False),
        ("", None, False),
        (QUOTE, False, False),
        (QUOTE, None, False),
        (QUOTE, True, True),
    ],
)
def test_both_audits_require_the_definition_and_output_identity(quote, matches, supported):
    fields = {"definition_quote": quote, "output_artifact_matches": matches}
    review = GroundedItemReview(index=0, support="supported", applicability="matched", reason="合成の判定", output_fields=fields)
    assert (review.support == "supported") is supported
    claim = ValidationClaimCheck(
        answer_quote="ファイル A に区分列があります。",
        answer_passage_id="A1",
        status="supported",
        source_id="c1",
        evidence_id="E1",
        evidence_quote=QUOTE,
        reason="合成の判定",
        output_fields=fields,
    )
    result = _enforce_support(
        [claim], [{"id": "A1", "text": claim.answer_quote}], {"E1": {"id": "c1", "text": QUOTE}}, {"c1": QUOTE}, [QUOTE]
    )
    assert (result[0].status == "supported") is supported
    if not supported:
        assert result[0].audit_status == "supported"
        assert result[0].support_check == "output_field_basis_unconfirmed"


@pytest.mark.parametrize("carried", [False, True])
def test_screen_filter_is_not_a_definition_even_for_a_carried_item(carried):
    quote = "画面 A の抽出条件: 区分"
    entry = grounded.CheckedItem(
        GroundedItem(kind="operation", text="出力データ A の区分列で絞り込みます。", quote=quote, evidence_id="E1"),
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
                "output_fields": {"definition_quote": "", "output_artifact_matches": True},
            }
        ],
        summary_supported=True,
        goal_alignment="aligned",
    )
    grounded.apply_audit([entry], audit)
    assert any(mark["code"] == "unsupported" for mark in entry.marks)
    assert not entry.removed


def test_invented_definition_is_not_supported_by_either_audit():
    fields = {"definition_quote": "ファイル A に年度列があります。", "output_artifact_matches": True}
    quote = "抽出条件は年度です。"
    item = GroundedItem(kind="operation", text="ファイル A の年度列で絞ります。", quote=quote, evidence_id="E1")
    entry = grounded.CheckedItem(item, {"text": quote, "evidence_id": "E1"})
    audit = GroundedAudit(
        reviews=[{"index": 0, "support": "supported", "applicability": "matched", "reason": "合成", "output_fields": fields}],
        summary_supported=True,
        goal_alignment="aligned",
    )
    grounded.apply_audit([entry], audit)
    assert entry.marks
    claim = ValidationClaimCheck(
        answer_quote=item.text, answer_passage_id="A1", status="supported", source_id="c1", reason="合成", output_fields=fields
    )
    result = _enforce_support([claim], [{"id": "A1", "text": item.text}], {}, {"c1": quote}, [quote])
    assert result[0].status == "unsupported"


def test_confirmed_export_operation_is_retained_and_unsupported_summary_is_detected():
    quote = "一覧 A を選びファイル出力を実行します。"
    entry = grounded.CheckedItem(
        GroundedItem(kind="operation", text=quote, quote=quote, evidence_id="E1"), {"text": quote, "evidence_id": "E1"}
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
