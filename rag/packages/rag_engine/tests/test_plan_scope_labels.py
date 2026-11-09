"""根拠計画の evidence_scope が照合した根拠を、evidence_ids に無くても計画の根拠にする（#3827）。

schema の evidence_ids は「direct の根拠と、原文に関係が明記された前提・後続操作の根拠」で、evidence_scope の照合と
同じ集合を指す。保存した実応答と同じ形（計画だけを立て直した計画の evidence_ids に根拠でない ID だけが入り、
evidence_scope は引いた根拠を direct と照合した）を匿名の文で作る。unrelated と食い違う判断は今までどおり許可しない。
"""

from rag_engine.generation.grounded import _outside_request_plan, _request_plan_errors, _unknown_plan_label_errors, verify
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
    return _outside_request_plan(item, span, draft, SPANS)


def unassessed(draft):
    unsettled = [*_request_plan_errors(draft, SPANS, unknown_labels=False), *_plan_undecided_errors(draft, SPANS)]
    checked, dropped = verify("設定の手順は？", draft, SPANS, plan_unsettled=unsettled)
    marked = {entry.item.text for entry in checked if any(mark["code"] == "unassessed" for mark in entry.marks)}
    kept = {entry.item.text for entry in checked if not entry.added}
    return unsettled, kept, marked, dropped


def test_a_linked_label_needs_the_relation_quote_the_code_cut_from_its_range():
    found = plan_draft(["E1"], [scope("E1"), scope("E3", "linked", AFTER)], [cited(AFTER, "E3")])
    assert not outside(found, "E3")
    empty = plan_draft(["E1"], [scope("E1"), scope("E3", "linked")], [cited(AFTER, "E3")])
    assert outside(empty, "E3")


def test_conflicting_scope_decisions_still_do_not_authorize_a_label():
    for relations in [("direct", "unrelated"), ("direct", "linked")]:
        scopes = [scope("E1"), *(scope("E2", relation, AFTER if relation == "linked" else "") for relation in relations)]
        for evidence_ids in (["E1"], ["E1", "E2"]):
            draft = plan_draft(evidence_ids, scopes, [cited(STEPS, "E2")])
            assert outside(draft, "E2"), (relations, evidence_ids)


def test_a_scope_row_that_names_no_sent_evidence_authorizes_nothing():
    draft = plan_draft(["E1"], [scope("E1"), scope(GUIDE_IDS[0])], [cited(STEPS, "E2")])
    assert outside(draft, "E2") and outside(draft, "E3")
    assert not outside(draft, "E1")


def test_the_unknown_label_errors_do_not_point_to_guide_coverage():
    draft = plan_draft(GUIDE_IDS, [scope("E1")], [cited(OPEN, "E1")])
    for error in _unknown_plan_label_errors(draft, SPANS, scope_plan=True):
        assert "は Evidence の ID ではありません。" in error and "guide_coverage" not in error
    assert not any("guide_coverage" in error for error in _unknown_plan_label_errors(draft, SPANS))

