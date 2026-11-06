"""未送達単位・重複選択の再審と、旧コピー診断の読取を分ける。"""

import json
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_eval import _evidence_fragments
from rag_engine.evaluation.answer_validation import (
    REFERENCE_REAUDIT_ADOPTED,
    REFERENCE_REAUDIT_FEEDBACK,
    REFERENCE_REAUDIT_REJECTED,
    ClaimAuditOutput,
    ValidationClaimCheck,
    _bind_validation,
    _enforce_support,
    validate_answer_claims,
)
from rag_engine.resources.deadline import Deadline, bind_deadline

TEXT = "項目Aの値は42です。\n項目Bの値は84です。"
EVIDENCE = [{"id": "synthetic-chunk-a", "source": "合成資料A", "text": TEXT}]
ANSWER = "項目Aの値は42です。【証拠 ID synthetic-chunk-a】\n\n項目Bの値は84です。【証拠 ID synthetic-chunk-a】"
CITATIONS = [{"start": 0, "end": len(ANSWER), "chunk_ids": ["synthetic-chunk-a"]}]


def run(first, second=None, *, deadline=None, answer=ANSWER, citations=CITATIONS):
    calls = []

    def parse(system, inputs, settings, schema, provider_id=None):
        data = json.loads(inputs)
        assert "passage_citations" not in data
        assert "passage_citations" not in system
        calls.append({"system": system, "inputs": data, "schema": schema.model_json_schema()})
        is_reaudit = str(data.get("validation_feedback") or "").startswith(REFERENCE_REAUDIT_FEEDBACK)
        rows = second if is_reaudit else first
        assert rows is not None, "想定外の再審"
        if isinstance(rows, Exception):
            raise rows
        if len(calls) == 1 and deadline is not None:
            deadline.expire()
        selected = next(e["units"] for e in data["evidence_items"] if e["id"] == EVIDENCE[0]["id"])
        claims = []
        for passage in data["answer_passages"]:
            status, choice = rows[passage["id"]]
            unit_ids = [selected[0 if passage["id"] == "A1" else 1]["unit_id"]]
            if choice == "invalid":
                unit_ids = ["U-not-delivered"]
            elif choice == "duplicate":
                unit_ids *= 2
            elif choice == "empty":
                unit_ids = []
            claims.append(
                {
                    "answer_quote": "",
                    "answer_passage_id": passage["id"],
                    "evidence_unit_ids": unit_ids,
                    "claim_target": "合成項目",
                    "evidence_target": "合成項目",
                    "applicability": "matched",
                    "output_fields": None,
                    "status": status,
                    "reason": "固定した合成判定です。",
                }
            )
        return schema.model_validate({"claim_checks": claims})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse), bind_deadline(deadline):
        result = validate_answer_claims("項目の値を教えてください。", answer, EVIDENCE, None, caller_citations=citations)
    return result, calls


@pytest.mark.parametrize("invalid", ["invalid", "duplicate", "empty"])
def test_only_reference_failures_are_reaudited_once_and_valid_units_are_adopted(invalid):
    result, calls = run({"A1": ("supported", invalid), "A3": ("supported", "valid")}, {"A1": ("supported", "valid")})
    assert len(calls) == 2 and result["counts"] == {"supported": 2}
    assert [p["id"] for p in calls[1]["inputs"]["answer_passages"]] == ["A1"]
    assert result["reference_reaudit"]["adopted_passage_ids"] == ["A1"]
    claims = result["claim_checks"]
    assert claims[0]["reference_reaudit"] == REFERENCE_REAUDIT_ADOPTED
    assert claims[1]["reference_reaudit"] == ""
    assert all(c["support_quote"] == "" for c in claims)
    assert [r["text"] for r in result["_selected_evidence"][0]] == ["項目Aの値は42です。"]


@pytest.mark.parametrize(
    "status,choice",
    [("supported", "invalid"), ("unsupported", "valid"), ("contradicted", "valid"), ("not_a_claim", "valid"), ("figure_only", "invalid")],
)
def test_reaudit_that_repeats_error_or_does_not_support_keeps_first_diagnostic(status, choice):
    result, calls = run({"A1": ("supported", "invalid"), "A3": ("supported", "valid")}, {"A1": (status, choice)})
    assert len(calls) == 2
    assert [c["status"] for c in result["claim_checks"]] == ["citation_error", "supported"]
    assert result["claim_checks"][0]["reference_reaudit"] == REFERENCE_REAUDIT_REJECTED
    assert result["reference_reaudit"]["adopted_passage_ids"] == []
    assert result["_selected_evidence"][0] == []


@pytest.mark.parametrize("error", [ValueError("合成契約違反"), RuntimeError("合成通信失敗")])
def test_reaudit_failure_preserves_first_result_without_new_retry(error):
    result, calls = run({"A1": ("supported", "invalid"), "A3": ("supported", "valid")}, error)
    assert len(calls) == 2
    assert result["reference_reaudit"]["status"] == "failed"
    assert result["claim_checks"][0]["status"] == "citation_error"
    assert result["claim_checks"][0]["reference_reaudit"] == REFERENCE_REAUDIT_REJECTED


@pytest.mark.parametrize("status", ["supported", "unsupported", "contradicted", "data_confirmation"])
def test_non_reference_failures_are_not_reaudited(status):
    result, calls = run({"A1": (status, "valid"), "A3": ("supported", "valid")})
    assert len(calls) == 1 and result["reference_reaudit"]["status"] == "not_required"
    assert result["claim_checks"][0]["status"] == status


def test_invalid_unit_cannot_be_recovered_from_answer_written_chunk_id():
    answer = "項目Aの値は42です。【証拠 ID synthetic-chunk-a】"
    result, calls = run({"A1": ("supported", "invalid")}, {"A1": ("supported", "invalid")}, answer=answer)
    assert len(calls) == 2
    assert result["claim_checks"][0]["status"] == "citation_error"
    assert result["claim_checks"][0]["source_id"] == ""


def test_reaudit_is_not_called_after_deadline():
    result, calls = run({"A1": ("supported", "invalid"), "A3": ("supported", "valid")}, deadline=Deadline(600))
    assert len(calls) == 1
    assert result["reference_reaudit"]["status"] == "deadline_exceeded"


def test_reference_diagnostics_and_copy_fields_are_absent_from_actual_wire_schema():
    _result, calls = run({"A1": ("supported", "valid"), "A3": ("supported", "valid")})
    encoded = json.dumps(calls[0]["schema"])
    for key in ["audit_evidence_id", "reference_reaudit", "support_quote", "evidence_quote", "unit_selection"]:
        assert key not in encoded
    assert "evidence_unit_ids" in encoded


@pytest.mark.parametrize(
    "quote,status", [("（引用は未記録）", "supported"), ("項目Aの値は42です。（page_start: 1）", "supported"), ("値99。", "citation_error")]
)
def test_legacy_saved_copy_diagnostic_is_still_checked_without_new_wire_compatibility(quote, status):
    fragments = list(_evidence_fragments(EVIDENCE))
    catalog = {r["evidence_id"]: r for r in fragments}
    output = ClaimAuditOutput(
        claim_checks=[
            ValidationClaimCheck(
                answer_quote="項目Aの値は42です。",
                answer_passage_id="A1",
                evidence_id=fragments[0]["evidence_id"],
                evidence_quote=quote,
                source_id="",
                status="supported",
                applicability="matched",
                reason="保存済み診断の合成値です。",
            )
        ]
    )
    bound = _bind_validation(output, catalog, [{"id": "A1", "text": "項目Aの値は42です。"}])
    claims = _enforce_support(bound.claim_checks, [], catalog, {"synthetic-chunk-a": TEXT}, [])
    assert claims[0].status == status and claims[0].support_quote == quote
