"""段落の分け方に依存せず、個別に確認した手順を対応付ける。"""

from rag_engine.evaluation.answer_eval import _evidence_fragments
from rag_engine.evaluation.guide_step_validation import validate_guide_steps
from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedAudit, GroundedCitation, GroundedItem
from rag_engine.retrieval.guide_steps import GuidePlan

FIRST = "条件を入力して一覧を抽出します。"
SECOND = "確認画面で検証結果を出力します。"
COMMON = "最後に実行ボタンを押します。"
SPAN = {"source_id": "shared", "evidence_id": "E1", "text": FIRST + SECOND + COMMON}
STEPS = (("a", "条件を入力して一覧を抽出する", ("shared",)), ("b", "確認画面で検証結果を出力する", ("shared",)))


def entry(text):
    return grounded.CheckedItem(
        GroundedItem(kind="operation", text=text, citations=[GroundedCitation(evidence_id="E1", quote=text)]), span=SPAN
    )


def test_one_paragraph_can_explain_two_independently_matched_steps():
    coverage, representative = grounded.guide_step_assignment(STEPS, [entry(FIRST + SECOND)], [SPAN])
    assert [step["status"] for step in coverage] == ["unknown", "unknown"]
    assert all(step["validation"]["body_coverage"]["execution"] == "not_run" for step in coverage)
    assert [step["assignment"]["assigned_item_indexes"] for step in coverage] == [[0], [0]]
    assert representative == {0: "a"}


def test_shared_common_action_does_not_explain_either_specific_step():
    coverage, _ = grounded.guide_step_assignment(STEPS, [entry(COMMON), entry(COMMON)], [SPAN])
    assert [step["status"] for step in coverage] == ["unknown", "unknown"]
    assert all(step["assignment"]["assigned_item_indexes"] == [] for step in coverage)


def test_each_step_keeps_all_its_matched_paragraphs():
    coverage, _ = grounded.guide_step_assignment(STEPS[:1], [entry(FIRST), entry(FIRST + "続けて条件を確認します。")], [SPAN])
    assert coverage[0]["assignment"]["assigned_item_indexes"] == [0, 1]


def test_independent_requirement_proofs_can_share_one_item():
    contracts = [{"step_id": key, "requirements": [{"id": key + ":0", "text": text}]} for key, text in [("a", FIRST), ("b", SECOND)]]
    audit = GroundedAudit(
        reviews=[],
        summary_supported=True,
        goal_alignment="aligned",
        guide_requirement_reviews=[
            {
                "requirement_id": key + ":0",
                "answer_state": "explained",
                "item_indexes": [0],
                "answer_quote": text,
                "reason": "原文と説明に記載",
            }
            for key, text in [("a", FIRST), ("b", SECOND)]
        ],
    )
    coverage = [{"step_id": key, "status": "unused", "required": True} for key in ["a", "b"]]
    result = grounded.apply_guide_contracts(coverage, [entry(FIRST + SECOND)], contracts, audit)
    assert [step["status"] for step in result] == ["explained", "explained"]


def test_final_validation_uses_all_paragraphs_of_each_step():
    plan = GuidePlan.from_payload("", 1, [{"id": "a", "title": STEPS[0][1], "retrieval_hints": ["条件"]}])
    assert plan is not None
    paragraphs = [FIRST, FIRST + "続けて条件を確認します。"]
    result = validate_guide_steps(
        plan,
        [{"status": "supported", "answer_quote": text, "support_quote": FIRST, "source_id": "shared"} for text in paragraphs],
        [{"id": "shared", "text": SPAN["text"]}],
    )
    assert result["steps"][0]["answer_quote"] == "\n".join(paragraphs)


def test_selected_units_keep_all_matched_paragraphs_without_filling_support_quote(monkeypatch):
    """内部選択の本文だけをguideへ渡し、未選択の後続操作を引用へ補わない。"""
    plan = GuidePlan.from_payload("", 1, [{"id": "a", "title": STEPS[0][1], "retrieval_hints": ["条件"]}])
    assert plan is not None
    paragraphs = [FIRST, FIRST + "続けて条件を確認します。"]
    evidence = [{"id": "shared", "text": SPAN["text"]}]
    eid = next(_evidence_fragments(evidence))["evidence_id"]
    # binderの単体検証ではなく、信頼済み選択rowsを受け取るconsumerの固定fixture。
    selected = {
        index: [
            {
                "unit_id": "U-selected",
                "evidence_id": eid,
                "source_id": "shared",
                "text": FIRST,
                "start": 0,
                "end": len(FIRST),
                "fragment_start": 0,
                "scope": {},
            }
        ]
        for index in range(2)
    }
    captured = []
    original = grounded.guide_step_assignment

    def assign(steps, checked, *args, **kwargs):
        captured.extend(citation.quote for item in checked for citation in item.item.citations)
        return original(steps, checked, *args, **kwargs)

    monkeypatch.setattr(grounded, "guide_step_assignment", assign)
    result = validate_guide_steps(
        plan,
        [
            {"status": "supported", "answer_quote": text, "support_quote": "", "source_id": "shared", "evidence_id": eid}
            for text in paragraphs
        ],
        evidence,
        selected_evidence=selected,
    )
    assert result["steps"][0]["answer_quote"] == "\n".join(paragraphs)
    assert captured == [FIRST, FIRST]
    assert all(SECOND not in quote and COMMON not in quote for quote in captured)


def test_explicit_empty_unit_selection_does_not_reuse_legacy_quote():
    """新wireの未確認選択を旧quote/source fallbackで説明済みにしない。"""
    plan = GuidePlan.from_payload("", 1, [{"id": "a", "title": STEPS[0][1], "retrieval_hints": ["条件"]}])
    assert plan is not None
    result = validate_guide_steps(
        plan,
        [{"status": "supported", "answer_quote": FIRST, "support_quote": FIRST, "source_id": "shared"}],
        [{"id": "shared", "text": SPAN["text"]}],
        selected_evidence={0: []},
    )
    assert result["steps"][0]["answer_quote"] == ""
    assert result["steps"][0]["status"] == "unused"
