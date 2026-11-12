"""モデルの根拠計画は、実在する引用を拒否する権限を持たない。"""

from rag_engine.generation.grounded import _unknown_plan_label_errors, verify
from rag_engine.models.llm import GroundedDraft, GroundedItem

OPEN = "メニューから設定を選び、設定タブの登録画面を開きます。"
STEPS = "(1)設定タブより割当画面を開きます。(2)名前を一覧から選択します。"
AFTER = "割当画面の登録が終わったら、登録画面に戻って保存します。"
GUIDE_IDS = ["step:0", "step:1"]

SPANS = [
    {"evidence_id": "id-open", "label": "E1", "text": OPEN},
    {"evidence_id": "id-steps", "label": "E2", "text": STEPS},
    {"evidence_id": "id-after", "label": "E3", "text": AFTER},
]


def scope(label, relation="direct", quote=""):
    return {"evidence_id": label, "target": "対象", "relation": relation, "link_quote": quote}


def cited(text, *labels):
    return {"kind": "operation", "request_id": "Q1", "text": text, "citations": [{"evidence_id": label, "quote": text} for label in labels]}


def plan_draft(evidence_ids, scopes, items):
    return GroundedDraft.model_validate(
        {
            "request_coverage": [{"request_id": "Q1", "evidence_ids": evidence_ids, "evidence_scope": scopes}],
            "items": items,
            "summary": "設定の手順です。",
            "confidence": "high",
        }
    )


def outside(draft, label):
    span = next(span for span in SPANS if span["label"] == label)
    item = GroundedItem.model_validate(cited(span["text"], label))
    checked, _ = verify("設定の手順", draft.model_copy(update={"items": [item]}), SPANS)
    return not checked


def test_a_real_citation_is_kept_with_or_without_a_predicted_link_quote():
    found = plan_draft(["E1"], [scope("E1"), scope("E3", "linked", AFTER)], [cited(AFTER, "E3")])
    assert not outside(found, "E3")
    empty = plan_draft(["E1"], [scope("E1"), scope("E3", "linked")], [cited(AFTER, "E3")])
    assert not outside(empty, "E3")


def test_conflicting_scope_predictions_do_not_reject_real_citations():
    for relations in [("direct", "unrelated"), ("direct", "linked")]:
        scopes = [scope("E1"), *(scope("E2", relation, AFTER if relation == "linked" else "") for relation in relations)]
        for evidence_ids in (["E1"], ["E1", "E2"]):
            draft = plan_draft(evidence_ids, scopes, [cited(STEPS, "E2")])
            assert not outside(draft, "E2"), (relations, evidence_ids)


def test_unknown_plan_rows_do_not_reject_other_real_citations():
    draft = plan_draft(["E1"], [scope("E1"), scope(GUIDE_IDS[0])], [cited(STEPS, "E2")])
    assert not outside(draft, "E2") and not outside(draft, "E3")
    assert not outside(draft, "E1")


def test_the_unknown_label_errors_do_not_point_to_guide_coverage():
    draft = plan_draft(GUIDE_IDS, [scope("E1")], [cited(OPEN, "E1")])
    for error in _unknown_plan_label_errors(draft, SPANS):
        assert "は Evidence の ID ではありません。" in error and "guide_coverage" not in error
    assert not any("guide_coverage" in error for error in _unknown_plan_label_errors(draft, SPANS))
