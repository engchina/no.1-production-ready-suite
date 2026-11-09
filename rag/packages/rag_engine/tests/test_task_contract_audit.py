"""目的逸脱、監査漏れ、原文ID、修正回数と出典保存の反例を検証する。"""

import json
from unittest.mock import patch

from judge_stub import judge
from test_evidence_first_answering import PARSE, evaluation_case

from rag_engine.evaluation.answer_eval import _evidence_fragments, evaluate_answer_payload
from rag_engine.generation.answering import (
    AnswerRecord,
)
from rag_engine.retrieval.evidence_selection import evidence_spans
from rag_engine.retrieval.task_contract import filter_queries


def record(uid, text="入口を押すと編集画面が開く。保存ボタンで確定する。", source="guide.pdf", metadata=None):
    return AnswerRecord(
        id="local",
        chunk_id="local",
        chunk_uid=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source=source,
        metadata=dict(metadata or {}),
    )


def test_duplicate_parse_is_consolidated_without_merging_different_versions():
    same_document = {"document_id": "doc-a"}
    a = record("run-a:1", metadata=same_document)
    b = record("run-b:1", metadata=same_document)
    changed = record("run-c:1", "取消ボタンで戻る。", metadata=same_document)
    spans = evidence_spans("保存操作", [a, b, changed])
    assert len(spans) == 2
    assert spans[0]["source_aliases"] == ["run-a:1", "run-b:1"]
    assert spans[0]["evidence_id"] != spans[1]["evidence_id"]
    assert a.text[spans[0]["start"] : spans[0]["end"]] == spans[0]["text"]
    assert len(evidence_spans("保存操作", [record("x", source=""), record("y", source="")])) == 2
    # 文書の識別子が無い根拠は、表示名が同じでも同じ文書と確かめられないのでまとめない（#2603）。
    assert len(evidence_spans("保存操作", [record("run-a:1"), record("run-b:1")])) == 2


def test_uncited_passage_is_unassessed_without_the_model():
    data, scope, output = evaluation_case("data_confirmation")
    data["answer_text"] += "\n\n主档を変更する。"
    with patch(PARSE, side_effect=judge(output)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed" and result["missing_audit_passage_ids"] == []
    uncited = next(claim for claim in result["claim_checks"] if claim["answer_quote"] == "主档を変更する。")
    assert (uncited["status"], uncited["reference_check"]) == ("unassessed", "uncited")


def test_evaluation_binds_source_text_by_stable_id():
    data, scope, output = evaluation_case("supported")
    span = list(_evidence_fragments(data["evidence_items"]))[0]
    output.claim_checks[0].evidence_id = span["evidence_id"]
    with patch(PARSE, side_effect=judge(output)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["claim_checks"][0]["evidence_quote"] == data["evidence_items"][0]["text"]
    assert result["claim_checks"][0]["status"] == "supported"


def test_missing_coverage_can_cite_refusal_without_getting_credit():
    from rag_engine.evaluation.answer_eval import CoverageCheck

    data, scope, output = evaluation_case("supported")
    output.coverage_checks = [CoverageCheck(requirement_index=1, status="missing", answer_quote="", answer_passage_id="A1")]
    with patch(PARSE, side_effect=judge(output)):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed" and result["requirement_coverage"] == 0
    assert result["coverage_checks"][0]["answer_passage_id"] == "A1"


def test_extra_coverage_item_is_retried_with_explicit_fixed_ids_and_feedback():
    from rag_engine.evaluation.answer_eval import CoverageJudgement, CoverageOutput

    data, scope, valid = evaluation_case("supported")
    fake = judge(valid)
    calls = []

    def parse(system, prompt, settings, schema, **kwargs):
        if schema is not CoverageOutput:
            return fake(system, prompt, settings, schema, **kwargs)
        inputs = json.loads(prompt)
        calls.append(inputs)
        assert [item["requirement_index"] for item in inputs["requirements"]] == [1]
        if len(calls) == 1:
            extra = CoverageJudgement(requirement_index=2, reason="理由", status="missing")
            return CoverageOutput(coverage_checks=[*fake(system, prompt, settings, schema).coverage_checks, extra])
        assert "（1）" in inputs["validation_feedback"]
        return fake(system, prompt, settings, schema)

    with patch(PARSE, side_effect=parse):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed" and len(result["coverage_checks"]) == 1
    assert len(calls) == 2 and len(result["standard_answer_scope"]["requirements"]) == 1


def test_filter_queries_rejects_only_business_ids_not_in_the_question():
    question = "申請を変更する方法を教えて"
    accepted, rejected = filter_queries(question, ["申請の変更手順", "XY99 の変更手順"])
    assert accepted == ("申請の変更手順",)
    assert rejected == ({"query": "XY99 の変更手順", "reason": "原質問・明示別名にない業務識別子"},)
    assert filter_queries(question, ["XY99 の変更手順"], grounded_text="XY99")[0] == ("XY99 の変更手順",)
