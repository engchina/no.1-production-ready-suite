"""固定キーの部分失敗で、有効な主張を失わず局所結果を未検証に保つ。"""

import json

import pytest
from audit_fixtures import legacy_audit_to_wire_fixture
from test_citation_audit_boundary import OPEN, QUESTION, evidence
from test_guide_contracts import contract

from rag_engine.config import get_settings
from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.audit_wire import decode_audit_wire, is_audit_wire
from rag_engine.models.llm import GroundedAudit, GroundedCitation, GroundedDraft, GroundedItem


def run(*, request_case="valid", omit_requirement=False, retry_case=None):
    spans = [evidence()]
    calls = []
    contracts = [contract("open", OPEN, "設定を保存します。")]

    def parser(system, prompt, settings, schema, **kwargs):
        calls.append(schema)
        if schema is generation_draft_wire_model():
            return GroundedDraft(
                items=[
                    GroundedItem(kind="operation", request_id="Q1", text=OPEN, citations=[GroundedCitation(evidence_id="E1", lines="1")])
                ],
                summary="",
                confidence="low",
            )
        assert is_audit_wire(schema)
        inputs = json.loads(prompt)
        assert inputs["confirmed_premises"] == [{"question": "操作条件", "answer": "条件A"}]
        assert schema.expected_request_ids == ("Q1",)
        assert schema.expected_requirement_ids == ("open:0", "open:1")
        if retry_case and len(calls) == 3:
            if retry_case == "failure":
                raise RuntimeError("合成の再監査失敗")
            empty = GroundedAudit(reviews=[], summary_supported=False, goal_alignment="partial")
            return decode_audit_wire(json.dumps(legacy_audit_to_wire_fixture(empty, schema)), schema)
        canonical = GroundedAudit(
            reviews=[{"index": 0, "support": "supported", "applicability": "matched", "reason": ""}],
            summary_supported=True,
            goal_alignment="partial",
            request_reviews=[{"request_id": "Q1", "status": "partial", "reason": "保存は未説明"}],
            guide_requirement_reviews=[
                {"requirement_id": "open:0", "answer_state": "explained", "answer_quote": OPEN, "item_indexes": [0], "reason": ""},
                {"requirement_id": "open:1", "answer_state": "not_mentioned", "answer_quote": "", "item_indexes": [], "reason": "未説明"},
            ],
        )
        wire = legacy_audit_to_wire_fixture(canonical, schema)
        if retry_case:
            wire["guide_requirement_reviews"]["open:0"]["answer_quote"] = "回答にはない文"
        if omit_requirement:
            wire["guide_requirement_reviews"].pop("open:1")
        if request_case == "missing":
            wire["request_reviews"].clear()
        elif request_case == "invalid":
            wire["request_reviews"]["Q1"]["status"] = "invalid-status"
        elif request_case == "unknown":
            wire["request_reviews"]["Q999"] = dict(wire["request_reviews"].pop("Q1"))
        raw = json.dumps(wire, ensure_ascii=False)
        if request_case == "duplicate":
            row = json.dumps(wire["request_reviews"]["Q1"], ensure_ascii=False)
            raw = raw.replace('"request_reviews": {"Q1": ' + row + "}", '"request_reviews": {"Q1": ' + row + ', "Q1": ' + row + "}")
        return decode_audit_wire(raw, schema)

    result = grounded.run_round(
        QUESTION,
        spans,
        get_settings(environ={}, dotenv_path=None),
        prompt="合成の入力",
        image_paths=(),
        provider_id=None,
        parse_text=parser,
        parse_images=parser,
        audit_mode="detailed",
        guide_steps=[("open", "画面を開いて保存する", [spans[0]["source_id"]])],
        guide_step_contracts=contracts,
        confirmed_premises=(("操作条件", "条件A"),),
    )
    assert len(calls) == (3 if retry_case else 2) and result.audit_calls == (2 if retry_case else 1)
    entry = next(row for row in result.checked if row.item.text == OPEN)
    assert entry.published and entry.confirmed and not entry.removed
    return result


def test_missing_guide_observation_preserves_valid_results_without_reauditing():
    result = run(omit_requirement=True)
    assert result.review_execution == "partial"
    assert result.audit.request_reviews[0].status == "partial"
    assert result.timing["audit_contract"]["requirements"] == {"open:0": "valid", "open:1": "missing"}
    assert result.guide_steps[0]["status"] != "explained"
    assert result.guide_steps[0]["requirements"][1]["status"] == "unknown"


@pytest.mark.parametrize("case", ["missing", "invalid", "unknown", "duplicate"])
def test_bad_request_binding_stays_unverified_and_preserves_the_other_review(case):
    result = run(request_case=case)
    assert result.review_execution == "partial"
    assert not result.addressed
    assert len(result.unreviewed_requests) == 1
    row = result.unreviewed_requests[0]
    assert row["request_id"] == "Q1" and row["status"] == "unknown"
    assert row["evidence_status"] == "unknown" and not row["evidence_ids"]
    assert row["review_execution"] == ("error" if case in {"invalid", "duplicate"} else "not_run")
    assert result.audit.reviews[0].support == "supported"
    assert result.audit.guide_requirement_reviews[0].requirement_id == "open:0"
    dimension = result.trace()["validation_dimensions"]["answer_completeness"]
    assert dimension["execution"] == "partial"
    if case == "duplicate":
        assert dimension["contract_validation"]["duplicate_request_ids"] == ["Q1"]
    elif case == "invalid":
        assert dimension["contract_validation"]["invalid_request_result_ids"] == ["Q1"]


def test_complete_wire_is_recorded_separately_from_partial_business_coverage():
    result = run()
    assert result.review_execution == "completed"
    assert result.timing["audit_contract"]["strict_wire_validation"] == "passed"
    assert result.audit.request_reviews[0].status == "partial"
    assert result.guide_steps[0]["status"] != "explained"


@pytest.mark.parametrize("case", ["failure", "empty"])
def test_failed_conditional_retry_keeps_valid_first_reviews_and_discards_bad_observation(case):
    result = run(retry_case=case)
    assert result.review_execution == ("error" if case == "failure" else "partial")
    assert result.audit.reviews[0].support == "supported"
    assert result.audit.request_reviews[0].status == "partial"
    assert [row.requirement_id for row in result.audit.guide_requirement_reviews] == ["open:1"]
    assert result.guide_steps[0]["requirements"][0]["status"] == "unknown"
