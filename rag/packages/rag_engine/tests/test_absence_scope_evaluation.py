"""未記載報告だけの追加検査・範囲証明・失敗時の未評価を検証する。"""

from copy import deepcopy
from unittest.mock import patch

import pytest
from judge_stub import judge

from rag_engine.config import get_settings
from rag_engine.evaluation.answer_eval import (
    AbsenceScopeCheck,
    AbsenceScopeOutput,
    AnswerEvaluationOutput,
    ClaimCheck,
    evaluate_answer_payload,
)
from rag_engine.generation.provided_scope import scope_digest

REPORT = "提供された資料では項目Cの値を確認できません。"


def payload(*, second="項目Bの値は8です。", answer=REPORT):
    items = [
        dict(id="doc:0", source="synthetic-0.txt", text="項目Aの値は12です。"),
        dict(id="doc:1", source="synthetic-1.txt", text=second),
    ]
    return dict(
        question="項目Cの値を教えてください。",
        answer_text=answer + "\n根拠：synthetic-0.txt p.1",
        allow_without_standard=True,
        evidence_items=items,
        cited_evidence=[dict(evidence_id="doc:0", source="synthetic-0.txt", page=1)],
        provided_evidence_scope=dict(
            status="complete",
            provenance="captured_generation_input",
            scope="final_generation_evidence",
            evidence_items=deepcopy(items),
            sha256=scope_digest(items),
        ),
    )


def evaluate(value, *, kind="absence_report", response="absent", failure=None, max_input=None):
    calls = []
    default = judge(
        AnswerEvaluationOutput(
            claim_checks=[
                ClaimCheck(
                    answer_quote=value["answer_text"].splitlines()[0],
                    status="unsupported",
                    source_id="",
                    evidence_quote="",
                    reason="段落の判定",
                    claim_kind=kind,
                )
            ]
        ),
        calls=calls,
    )

    def parse(system, prompt, settings, schema, **kwargs):
        import json

        if schema is not AbsenceScopeOutput:
            return default(system, prompt, settings, schema, **kwargs)
        inputs = json.loads(prompt)
        calls.append((schema, inputs))
        if failure:
            raise failure
        return AbsenceScopeOutput(
            checks=[
                AbsenceScopeCheck(
                    answer_passage_id=item["id"],
                    reason="全送達範囲を比較した結果",
                    status=response,
                    evidence_ids=["doc:1"] if response == "present" else [],
                )
                for item in inputs["passages"]
            ]
        )

    settings = get_settings()
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        if max_input:
            with patch("rag_engine.evaluation.answer_eval.evaluation_input_budget", return_value=max_input):
                result = evaluate_answer_payload(value, settings)
        else:
            result = evaluate_answer_payload(value, settings)
    return result, [inputs for schema, inputs in calls if schema is AbsenceScopeOutput]


def test_absence_is_supported_by_full_scope_without_fabricating_a_quote():
    result, calls = evaluate(payload())
    assert result["status"] == "completed"
    claim = result["claim_checks"][0]
    assert claim["status"] == "supported"
    assert claim["evidence_quote"] == claim["source_id"] == ""
    assert claim["scope_check"]["evidence_count"] == 2
    assert claim["reference_check"] == "provided_scope_verified"
    assert len(calls) == 1 and len(calls[0]["evidence_items"]) == 2
    assert result["judge_diagnostics"]["absence_scope_calls"] == 1


def test_report_is_contradicted_by_uncited_but_sent_evidence():
    result, calls = evaluate(payload(second="項目Cの値は20です。"), response="present")
    claim = result["claim_checks"][0]
    assert claim["status"] == "contradicted"
    assert claim["source_id"] == "doc:1"
    assert "20" in claim["evidence_quote"]
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["missing", "partial", "digest", "duplicate", "provenance", "scope"])
def test_unproven_scope_is_unassessed_without_extra_call(change):
    value = payload()
    scope = value["provided_evidence_scope"]
    if change == "missing":
        del value["provided_evidence_scope"]
    elif change == "partial":
        scope["status"] = "partial"
    elif change == "digest":
        scope["evidence_items"].pop()
    elif change == "duplicate":
        scope["evidence_items"][1]["id"] = "doc:0"
        scope["sha256"] = scope_digest(scope["evidence_items"])
    else:
        scope[change] = "unknown"
    result, calls = evaluate(value)
    assert result["claim_checks"][0]["status"] == "unassessed"
    assert calls == []


def test_all_reports_share_one_additional_request():
    value = payload()
    value["answer_text"] += "\n資料では項目Dも確認できません。\n根拠：synthetic-0.txt p.1"
    # 既存の段落判定の応答だけを固定し、分類の意味を文字列規則で推定しない。
    import json

    from rag_engine.evaluation.answer_eval import ClaimJudgement

    calls = []

    def parse(system, prompt, settings, schema, **kwargs):
        inputs = json.loads(prompt)
        if schema is ClaimJudgement:
            return ClaimJudgement(reason="未記載報告", claim_kind="absence_report", status="unsupported")
        assert schema is AbsenceScopeOutput
        calls.append(inputs)
        return AbsenceScopeOutput(
            checks=[
                AbsenceScopeCheck(answer_passage_id=p["id"], reason="未記載", status="absent", evidence_ids=[]) for p in inputs["passages"]
            ]
        )

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = evaluate_answer_payload(value, get_settings())
    assert len(calls) == 1 and len(calls[0]["passages"]) == 2
    assert all(c["status"] == "supported" for c in result["claim_checks"])


@pytest.mark.parametrize("answer", ["項目Aの値は12です。", "項目Cという機能は存在しません。"])
def test_ordinary_claim_and_global_nonexistence_do_not_expand_scope(answer):
    result, calls = evaluate(payload(answer=answer), kind="ordinary")
    assert result["claim_checks"][0]["status"] == "unsupported"
    assert calls == []


@pytest.mark.parametrize("error", [RuntimeError("private"), ValueError("invalid output")])
def test_scope_check_failure_is_unassessed(error):
    result, calls = evaluate(payload(), failure=error)
    assert result["status"] == "completed"
    assert result["claim_checks"][0]["status"] == "unassessed"
    assert len(calls) == 1
    assert "private" not in str(result)


def test_complete_scope_over_budget_is_not_truncated():
    value = payload()
    value["provided_evidence_scope"]["evidence_items"][1]["text"] = "長い原文" * 10000
    value["provided_evidence_scope"]["sha256"] = scope_digest(value["provided_evidence_scope"]["evidence_items"])
    result, calls = evaluate(value, max_input=48000)
    assert result["claim_checks"][0]["status"] == "unassessed"
    assert result["judge_diagnostics"]["absence_scope_failure"] == "input_too_large"
    assert calls == []


@pytest.mark.parametrize("defect", ["duplicate", "missing", "unknown_reference", "duplicate_reference", "extra_id"])
def test_invalid_output_ids_only_withhold_affected_report(defect):
    import json

    from rag_engine.evaluation.answer_eval import ClaimJudgement

    value = payload()
    value["answer_text"] += "\n資料では項目Dも確認できません。\n根拠：synthetic-0.txt p.1"

    def parse(system, prompt, settings, schema, **kwargs):
        inputs = json.loads(prompt)
        if schema is ClaimJudgement:
            return ClaimJudgement(reason="未記載報告", claim_kind="absence_report", status="unsupported")
        assert schema is AbsenceScopeOutput
        first, second = [
            AbsenceScopeCheck(answer_passage_id=p["id"], reason="全範囲で未記載", status="absent", evidence_ids=[])
            for p in inputs["passages"]
        ]
        checks = [first, second]
        if defect == "duplicate":
            checks.append(first)
        elif defect == "missing":
            checks = [second]
        elif defect in {"unknown_reference", "duplicate_reference"}:
            checks[0] = first.model_copy(
                update=dict(status="present", evidence_ids=["unknown"] if defect == "unknown_reference" else ["doc:0", "doc:0"])
            )
        elif defect == "extra_id":
            checks.append(first.model_copy(update=dict(answer_passage_id="not-requested")))
        return AbsenceScopeOutput(checks=checks)

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = evaluate_answer_payload(value, get_settings())
    first, second = result["claim_checks"]
    assert second["status"] == "supported"
    assert first["status"] == ("supported" if defect == "extra_id" else "unassessed")
    assert result["judge_diagnostics"]["absence_scope_calls"] == 1


def test_claim_classification_is_required_and_has_executed_instructions():
    from rag_engine.evaluation.answer_eval import CLAIM_PROMPT, ClaimJudgement

    assert "claim_kind" in ClaimJudgement.model_json_schema()["required"]
    assert all(value in CLAIM_PROMPT for value in ("claim_kind", "absence_report", "ordinary"))
