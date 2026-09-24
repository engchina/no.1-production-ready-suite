"""回答評価の採点境界・除外範囲・保存失敗分離を検証する。"""
import json
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from docrag.config import get_settings
from docrag.evaluation.answer_eval import (AXES, AnswerEvaluationOutput, AxisScore, evaluate_answer_payload,
                                           MAX_EVALUATION_INPUT_BYTES, _input_bytes, StandardAnswerScope, StandardRequirement,
                                           ClaimCheck, CoverageCheck, _prepare_standard_scope, _evaluate_batch, EvaluationContractError)
from docrag.generation.answering import AnswerQuestionResult, parse_answer_response, AnswerRecord, answer_evidence_items
from docrag.knowledge.answers import save_answer_result


@pytest.fixture(autouse=True)
def fixed_scope():
    """既存の評価・保存テストでは、範囲抽出I/Oを独立した固定入力に置き換える。"""
    with patch("docrag.evaluation.answer_eval._prepare_standard_scope", return_value=scope_output()) as mocked:
        yield mocked


def scope_output():
    return StandardAnswerScope(requirements=[StandardRequirement(standard_answer_quote="ログを確認", requirement="文書にあるログ確認手順")], excluded_case_data=["実際の原因"])


def output(scores=(4, 4, 4, 4), external=True):
    """外部のログによる原因特定を除外した固定の評価結果を返す。"""
    return AnswerEvaluationOutput(
        question_goal="質問への回答", goal_alignment="aligned", goal_reason="原質問への確認案内",
        claim_checks=[ClaimCheck(answer_quote="回答", status="data_confirmation", source_id="", evidence_quote="", reason="未確認データの確認案内")],
        external_data_required=external, external_data_items=["当該実行のログと今回の失敗原因"] if external else [],
        evaluated_content=["文書にあるログ確認手順"],
        coverage_checks=[CoverageCheck(requirement_index=1, status="addressed", answer_quote="回答")], evidence_summary="文書で確認したログ確認手順と出典",
        **{name: AxisScore(score=score, reason="文書の手順との対応を確認") for name, score in zip(AXES, scores)},
    )


def payload():
    return {"question": "今回の取込失敗の原因は？", "standard_answer": "ログを確認して原因を特定する。",
            "answer_text": "回答",
            "evidence_items": [{"id": "c1", "text": "手順本文" * 150, "text_preview": "短縮表示", "source": "manual.pdf"}]}


@pytest.mark.parametrize("scores,total,passed", [((4, 4, 4, 3), 16, True), ((4, 4, 4, 2), 15, False), ((5, 0, 5, 5), 20, True), ((0, 0, 0, 0), 5, False)])
def test_total_threshold_and_full_evidence(scores, total, passed):
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output(scores)) as parse:
        result = evaluate_answer_payload(payload(), object(), provider_id="chosen")
    assert result["total_score"] == total
    assert result["rubric_version"] == 10
    assert result["passed"] is passed
    assert result["external_data_items"] == ["当該実行のログと今回の失敗原因"]
    assert result["evaluated_content"] == ["文書にあるログ確認手順"]
    prompt = json.loads(parse.call_args.args[1])
    assert prompt["evidence_items"][0]["text"] == "手順本文" * 150
    assert parse.call_args.args[3] is AnswerEvaluationOutput
    assert parse.call_args.kwargs["provider_id"] == "chosen"
    assert "全4軸の評価対象として除外" in parse.call_args.args[0]


@pytest.mark.parametrize("value", [-1, 6, 2.5, True, "4"])
def test_invalid_score_is_rejected(value):
    with pytest.raises(ValidationError):
        AxisScore(score=value, reason="理由")


@pytest.mark.parametrize("evidence", [[], [{"id": "c1", "text": "EC列が対象値。CSVを取得して確認する。"}]])
def test_data_only_question_still_scores_confirmation_guidance(evidence, fixed_scope):
    data = {"question": "今回のCSVのEC列は何ですか。", "standard_answer": "CSVを確認してください。",
            "answer_text": "CSVが未提供のため、EC列を確認して提供してください。", "evidence_items": evidence}
    fixed_scope.return_value = StandardAnswerScope(requirements=[StandardRequirement(
        standard_answer_quote="CSVを確認してください", requirement="未確認値を断定せず、CSVのEC列の提供を依頼しているか")], excluded_case_data=["EC列の実値"])
    evaluated = output().model_copy(update={
        "external_data_items": ["今回のCSVのEC列の実値"],
        "evaluated_content": ["未確認値を断定せず、CSVのEC列の提供を依頼しているか"],
        "coverage_checks": [CoverageCheck(requirement_index=1, status="addressed", answer_quote="EC列を確認して提供してください")],
        "claim_checks": [ClaimCheck(answer_quote="EC列を確認して提供してください", status="data_confirmation", source_id="", evidence_quote="", reason="確認案内")],
    })
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=evaluated):
        result = evaluate_answer_payload(data, object())
    assert result["status"] == "completed" and result["total_score"] == 17
    assert result["external_data_required"] is True
    assert set(result["scores"]) == set(AXES)
    assert result["evaluated_content"] == evaluated.evaluated_content


def test_legacy_empty_scope_is_retried_with_same_evidence_and_required_scores():
    invalid = output().model_copy(update={"evaluated_content": [], **dict.fromkeys(AXES)})
    with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=[invalid, output()]) as parse:
        result = evaluate_answer_payload(payload(), object(), provider_id="chosen")
    assert result["status"] == "completed" and result["total_score"] == 17
    assert result["batch_count"] == 1
    assert parse.call_count == 2
    first = json.loads(parse.call_args_list[0].args[1])
    retry = json.loads(parse.call_args_list[1].args[1])
    assert retry.pop("validation_feedback")
    assert retry == first
    schema = parse.call_args.args[3].model_json_schema()
    assert schema["properties"]["evaluated_content"]["minItems"] == 1
    for axis in AXES:
        assert schema["properties"][axis] == {"$ref": "#/$defs/AxisScore"}


def test_repeated_empty_scope_is_error_without_fabricated_or_partial_scores():
    invalid = output().model_copy(update={"evaluated_content": [], **dict.fromkeys(AXES)})
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=invalid) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 2
    assert result["status"] == "error"
    assert result["scores"] is None and result["total_score"] is None and result["passed"] is None


def test_adapter_validation_error_also_retries_once():
    with pytest.raises(ValidationError) as error:
        AnswerEvaluationOutput(**(output().model_dump() | {"accuracy": None}))
    with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=[error.value, output()]) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 2
    assert result["status"] == "completed"


def test_inconsistent_scope_is_rejected():
    for change in ({"external_data_required": False}, {"evaluated_content": []}, {"accuracy": None}, {"external_data_items": [" "]}):
        with pytest.raises(ValidationError):
            AnswerEvaluationOutput(**(output().model_dump() | change))


def test_no_standard_answer_skips_network_and_keeps_external_status():
    with patch("docrag.evaluation.answer_eval.parse_text_response") as parse:
        result = evaluate_answer_payload({"standard_answer": "  ", "external_data_required": True, "external_data_items": ["ログ"]}, object())
    parse.assert_not_called()
    assert result["status"] == "no_standard_answer"
    assert result["external_data_items"] == ["ログ"]
    assert result["passed"] is None


def test_error_does_not_prevent_answer_persistence_or_leak_details():
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp), query_history_enabled=False)
        result = AnswerQuestionResult(answer="回答", answer_text="回答", original_question="質問", question_display="質問",
                                      selected_strategy="simple", effective_strategy="simple", primary_source_run_id="run1")
        with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=RuntimeError("private connection details")):
            saved = save_answer_result(result, settings, standard_answer="標準")
        stored = json.loads((Path(tmp) / "run1" / "answers" / f"{saved.answer_id}.json").read_text())
        assert stored["answer_text"] == "回答"
        assert stored["evaluation"]["status"] == "error"
        assert "private connection details" not in json.dumps(stored)


def test_external_output_and_legacy_compatibility():
    response = parse_answer_response(json.dumps({"answer": "回答", "external_data_required": False, "external_data_items": ["実行ログ"]}))
    assert response.external_data_required is True
    assert response.needs_human_review is True
    assert parse_answer_response('{"answer":"旧回答"}').external_data_required is None


def test_evidence_body_is_not_truncated_for_evaluation():
    record = AnswerRecord(id="c1", engine="docling", engine_label="Docling", page=1, seq_no=1,
                          category="text", text="metadata付き本文", body_text="根拠全文" * 150)
    assert answer_evidence_items([record])[0]["text"] == "根拠全文" * 150


def test_successful_evaluation_is_saved_with_external_fields():
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp), query_history_enabled=False)
        result = AnswerQuestionResult(answer="回答", answer_text="回答", original_question="質問", question_display="質問",
                                      selected_strategy="simple", effective_strategy="simple", primary_source_run_id="run1",
                                      external_data_required=True, external_data_items=("実行ログ",))
        with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output()) as parse:
            saved = save_answer_result(result, settings, standard_answer="標準", evaluation_provider="chosen")
        stored = json.loads((Path(tmp) / "run1" / "answers" / f"{saved.answer_id}.json").read_text())
        assert stored["external_data_items"] == stored["evaluation"]["external_data_items"]
        assert stored["evaluation"]["total_score"] == 17
        assert stored["evaluation"]["passed"] is True
        assert parse.call_args.kwargs["provider_id"] == "chosen"


@pytest.mark.parametrize("external,initial_review", [(True, False), (True, None), (False, True), (False, False)])
def test_evaluation_reconciles_saved_review_flags(external, initial_review):
    from docrag.knowledge.answer_feedback import load_answer_trace_summary
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp), query_history_enabled=False)
        generated = AnswerQuestionResult(answer="回答", original_question="質問", question_display="質問",
            selected_strategy="simple", effective_strategy="simple", primary_source_run_id="run1",
            external_data_required=not external, external_data_items=("旧確認対象",),
            needs_human_review=initial_review, question_type=("操作手順", "外部データ確認が必要"))
        with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output(external=external)):
            saved = save_answer_result(generated, settings, standard_answer="標準")
        assert saved.payload["external_data_required"] is external
        assert saved.payload["external_data_items"] == saved.payload["evaluation"]["external_data_items"]
        assert saved.payload["needs_human_review"] is (True if external else initial_review)
        if external:
            assert "人手確認: 必要" in saved.payload["answer"]
            assert "人手確認: 不要" not in saved.payload["answer"]
        assert ("外部データ確認が必要" in saved.payload["question_type"]) is external
        context = load_answer_trace_summary(settings.output_dir, saved.run_id, saved.answer_id)
        assert context["needs_human_review"] == saved.payload["needs_human_review"]


def long_payload():
    """12個の親と子の全文を送り、末尾の根拠を保持することを検証する。"""
    data = payload()
    data["evidence_items"] = [{"id": f"p{i}", "source": "manual.pdf", "text": "手順条件。" * 1700,
        "children": [{"id": f"c{i}", "source": "manual.pdf", "text": "😀\n\"\\条件" * 1200 + f"末尾{i}"}]} for i in range(12)]
    return data


def test_long_evidence_is_losslessly_batched_with_bounded_previous_state():
    data = long_payload()
    calls = []
    def parse(system, prompt, settings, schema, *, provider_id):
        inputs = json.loads(prompt)
        assert _input_bytes(inputs) <= MAX_EVALUATION_INPUT_BYTES
        assert provider_id == "chosen"
        if calls:
            assert inputs["previous_evaluation"]["evidence_summary"] == f"batch {len(calls)} 確認済み事実と出典"
        calls.append(inputs)
        scores = (4, 4, 4, 4) if inputs["is_final_batch"] else (0, 0, 0, 0)
        return output(scores).model_copy(update={"evidence_summary": f"batch {len(calls)} 確認済み事実と出典"})
    with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = evaluate_answer_payload(data, object(), provider_id="chosen")
    assert result["status"] == "completed"
    assert result["total_score"] == 17 and result["passed"] is True
    assert result["batch_count"] == len(calls) > 1
    assert [call["batch_index"] for call in calls] == list(range(1, len(calls) + 1))
    assert all(not call["is_final_batch"] for call in calls[:-1])
    reconstructed = {}
    for call in calls:
        for fragment in call["evidence_items"]:
            old = reconstructed.get(fragment["id"], "")
            assert len(old) == fragment["fragment_start"]
            reconstructed[fragment["id"]] = old + fragment["text"]
            assert len(reconstructed[fragment["id"]]) == fragment["fragment_end"]
    for parent in data["evidence_items"]:
        for item in [parent, *parent["children"]]:
            assert reconstructed[item["id"]] == item["text"]


def test_mid_batch_failure_does_not_publish_partial_scores():
    partial = output().model_copy(update={"evidence_summary": "確認済みの根拠"})
    with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=[partial, RuntimeError("private")]):
        result = evaluate_answer_payload(long_payload(), object())
    assert result["status"] == "error" and result["batch_count"] == 1
    assert result["total_score"] is None and result["passed"] is None and result["scores"] is None
    assert "private" not in result["message"]


def test_oversized_fixed_input_skips_network_and_explains_scope_reduction():
    with patch("docrag.evaluation.answer_eval.parse_text_response") as parse:
        result = evaluate_answer_payload(payload() | {"standard_answer": "長" * MAX_EVALUATION_INPUT_BYTES}, object())
    parse.assert_not_called()
    assert result["status"] == "input_too_large" and result["passed"] is None
    assert "範囲を絞" in result["message"]


def test_missing_intermediate_summary_is_not_treated_as_complete():
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output().model_copy(update={"evidence_summary": ""})):
        result = evaluate_answer_payload(long_payload(), object())
    assert result["status"] == "error" and result["scores"] is None


def test_large_axis_details_are_not_repeated_in_next_batch():
    oversized = output().model_copy(update={"evidence_summary": "摘要", "evaluated_content": ["評価対象" * 15000]})
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=oversized) as parse:
        result = evaluate_answer_payload(long_payload(), object())
    assert parse.call_count > 1
    assert result["status"] == "completed"
    assert "evaluated_content" not in json.loads(parse.call_args.args[1])["previous_evaluation"]


def test_tight_remaining_budget_splits_fragments_without_losing_unicode():
    from collections import deque
    from docrag.evaluation.answer_eval import _next_batch
    text = "😀\\\"\n" * 500
    item = {"id": "c1", "text": text, "fragment_start": 0, "fragment_end": len(text)}
    empty = {"previous_evaluation": None, "batch_index": 1, "is_final_batch": False, "evidence_items": []}
    limit = _input_bytes(empty) + 500
    pending = deque([item])
    fragments = []
    with patch("docrag.evaluation.answer_eval.MAX_EVALUATION_INPUT_BYTES", limit):
        while pending:
            inputs = _next_batch({}, pending, None, 1)
            assert _input_bytes(inputs) <= limit
            fragments.extend(inputs["evidence_items"])
    assert len(fragments) > 1
    assert "".join(fragment["text"] for fragment in fragments) == text
    assert fragments[-1]["fragment_end"] == len(text)


@pytest.mark.parametrize("status", ["no_standard_answer", "error", "input_too_large"])
def test_unfinished_evaluation_preserves_original_review_state(status):
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp), query_history_enabled=False)
        generated = AnswerQuestionResult(answer="回答", original_question="質問", question_display="質問",
            selected_strategy="simple", effective_strategy="simple", primary_source_run_id="run1",
            external_data_required=True, external_data_items=("実行ログ",), needs_human_review=True)
        with patch("docrag.evaluation.answer_eval.evaluate_answer_payload", return_value={"status": status}):
            saved = save_answer_result(generated, settings, standard_answer="標準")
        assert saved.payload["needs_human_review"] is True
        assert saved.payload["external_data_items"] == ["実行ログ"]


def test_final_data_dependent_scope_scores_guidance_instead_of_excluding_answer():
    calls = []
    def parse(system, prompt, settings, schema, **kwargs):
        inputs = json.loads(prompt)
        calls.append(inputs)
        if not inputs["is_final_batch"]:
            return output().model_copy(update={"evidence_summary": "暫定の確認結果"})
        return output(scores=(2, 3, 4, 4)).model_copy(update={
            "external_data_items": ["実行ログ"], "evaluated_content": ["不足データと確認手順の説明"],
            "evidence_summary": "実行ログの実値は除外し、確認手順を評価する",
        })
    with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = evaluate_answer_payload(long_payload(), object())
    assert len(calls) > 1
    assert result["status"] == "completed" and result["passed"] is False
    assert result["total_score"] == 15
    assert result["evaluated_content"] == ["文書にあるログ確認手順", "不足データと確認手順の説明"]
    assert result["external_data_items"] == ["実行ログ"]


def test_scope_preparation_never_receives_answer_or_retrieved_evidence():
    standard = "設定画面で業務別担当者(J)を選択してください。今回は主担当が設定されていました。"
    scope = StandardAnswerScope(requirements=[StandardRequirement(
        standard_answer_quote="業務別担当者(J)を選択", requirement="業務別担当者(J)の選択手順")],
        excluded_case_data=["今回の設定値"])
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=scope) as parse:
        result = _prepare_standard_scope(payload() | {"standard_answer": standard}, object(), "enterprise-ai-vision")
    assert result == scope
    assert json.loads(parse.call_args.args[1]) == {"question": payload()["question"], "standard_answer": standard}
    assert parse.call_args.args[3] is StandardAnswerScope


def test_scope_quote_not_in_standard_answer_retries_then_fails():
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=scope_output()) as parse:
        with pytest.raises(EvaluationContractError):
            _prepare_standard_scope({"question": "質問", "standard_answer": "業務別担当者(J)を選択"}, object(), None)
    assert parse.call_count == 2


@pytest.mark.parametrize("checks", [
    [],
    [CoverageCheck(requirement_index=2, status="missing", answer_quote="")],
    [CoverageCheck(requirement_index=1, status="missing", answer_quote="")]*2,
    [CoverageCheck(requirement_index=1, status="addressed", answer_quote="標準回答だけにある操作")],
    [CoverageCheck(requirement_index=1, status="partial", answer_quote="")],
    [CoverageCheck(requirement_index=1, status="missing", answer_quote="回答外の引用")],
])
def test_invalid_fixed_check_or_answer_quote_is_retried_without_publishing_scores(checks):
    invalid = output().model_copy(update={"coverage_checks": checks})
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=invalid) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 2
    assert result["status"] == "error" and result["scores"] is None


@pytest.mark.parametrize("state,expected", [("missing", 0), ("partial", 2.5), ("addressed", 5)])
def test_absent_document_evidence_cannot_remove_expected_procedure(state, expected, fixed_scope):
    required = StandardRequirement(standard_answer_quote="業務別担当者(J)を選択", requirement="業務別担当者(J)の選択手順")
    fixed_scope.return_value = StandardAnswerScope(requirements=[required], excluded_case_data=["今回の主担当設定値"])
    evaluated = output((5, 5, 5, 5)).model_copy(update={
        "coverage": AxisScore(score=5, reason="標準回答の業務別J選択手順は根拠にないため除外"),
        "coverage_checks": [CoverageCheck(requirement_index=1, status=state, answer_quote="" if state=="missing" else "回答")],
    })
    data = payload() | {"standard_answer": "業務別担当者(J)を選択。今回は主担当が設定されていた。", "evidence_items": []}
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=evaluated):
        result = evaluate_answer_payload(data, object())
    assert result["status"] == "completed"
    assert result["scores"]["coverage"]["score"] == expected
    assert required.requirement in result["evaluated_content"]
    assert result["coverage_cap"] == expected
    assert result["total_score"] == 15 + expected
    assert "根拠にないため除外" not in result["scores"]["coverage"]["reason"]
    assert result["model_coverage"]["score"] == 5
    assert result["standard_answer_scope"]["requirements"][0]["requirement"] == required.requirement


def test_frozen_scope_is_not_changed_by_later_evidence_batches():
    seen = []
    def parse(system, prompt, settings, schema, **kwargs):
        inputs = json.loads(prompt)
        seen.append(inputs["standard_answer_scope"])
        return output().model_copy(update={"coverage_checks": [CoverageCheck(
            requirement_index=1, status="missing" if inputs["is_final_batch"] else "addressed",
            answer_quote="" if inputs["is_final_batch"] else "回答")]})
    with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = evaluate_answer_payload(long_payload(), object())
    assert len(seen)>1 and all(item==seen[0] for item in seen)
    assert result["scores"]["coverage"]["score"] == 0
    assert result["passed"] is False


def test_real_scope_stage_then_evaluation_keeps_procedure_without_document_support(fixed_scope):
    fixed_scope.side_effect = _prepare_standard_scope
    data = payload()
    with patch("docrag.evaluation.answer_eval.parse_text_response", side_effect=[scope_output(), output()]) as parse:
        result = evaluate_answer_payload(data, object())
    assert parse.call_count == 2 and result["status"] == "completed"
    assert parse.call_args_list[0].args[3] is StandardAnswerScope
    assert parse.call_args_list[1].args[3] is AnswerEvaluationOutput


def test_partial_coverage_is_not_lost_and_followup_is_still_reported(fixed_scope):
    required = StandardRequirement(standard_answer_quote="ログを確認", requirement="ログ確認")
    extra = StandardRequirement(standard_answer_quote="別の相談", requirement="履歴の出力", relevance="follow_up", relevance_reason="原質問にない追加相談")
    fixed_scope.return_value = StandardAnswerScope(requirements=[required, extra, extra.model_copy(update={"requirement": "別日付の出力"})], excluded_case_data=[])
    checks = [CoverageCheck(requirement_index=i, status="partial" if i == 1 else "missing", answer_quote="回答" if i == 1 else "") for i in (1, 2, 3)]
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output((4, 0, 4, 4)).model_copy(update={"coverage_checks": checks})):
        result = evaluate_answer_payload(payload(), object())
    assert result["scores"]["coverage"]["score"] == 2.5
    assert result["coverage_full_standard"] == 0.83
    assert result["coverage_follow_up"] == 0
    assert len(result["coverage_checks"]) == 3
    assert "履歴の出力" in result["evaluated_content"]
    assert result["model_coverage"]["score"] == 0


def test_scope_cannot_relabel_every_requirement_as_followup():
    scope = scope_output()
    scope.requirements[0].relevance = "follow_up"
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=scope):
        with pytest.raises(EvaluationContractError, match="原質問"):
            _prepare_standard_scope(payload(), object(), None)


def test_prepared_scope_is_reused_without_another_scope_call(fixed_scope):
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output()) as parse:
        result = evaluate_answer_payload(payload(), object(), standard_scope=scope_output())
    fixed_scope.assert_not_called()
    assert parse.call_count == 1
    assert result["standard_answer_scope"] == scope_output().model_dump()
    invalid = scope_output()
    invalid.requirements[0].standard_answer_quote = "他の標準回答にだけある内容"
    with patch("docrag.evaluation.answer_eval.parse_text_response") as parse:
        result = evaluate_answer_payload(payload(), object(), standard_scope=invalid)
    parse.assert_not_called()
    assert result["status"] == "error"


def _rendered_items_answer():
    """回答生成（items 方式）の render() が実際に組み立てる本文。全種類の見出しと出典行を含む。"""
    from docrag.generation.grounded import CheckedItem, render
    from docrag.models.llm import GroundedItem

    def span(function, page):
        return {"source": "manual.pdf", "page": page, "function": ("manual.pdf", function)}

    def item(kind, text, **extra):
        return GroundedItem(kind=kind, text=text, evidence_id="E1", quote=text, **extra)

    return render("結論です。", [
        CheckedItem(item("rule", "対象は登録済みの利用者です。"), span("3.1 対象", 3)),
        CheckedItem(item("operation", "利用者情報画面を開きます。"), span("3.2 利用者情報の変更", 4)),
        CheckedItem(item("confirmation", "帳票の出力結果を確認します。"), span("4.1 帳票出力", 9)),
        CheckedItem(item("operation", "保存ボタンを押す。"), span("3.2 利用者情報の変更", 5), quote_only=True),
        CheckedItem(GroundedItem(kind="gap", text="取消の手順は資料にありません。")),
    ])


def test_rendered_headings_and_citations_do_not_block_passing(fixed_scope):
    # render() の見出し行・出典行は主張ではない。監査未完了と数えると満点でも passed が false になる (#449)。
    from docrag.generation.grounded import is_structural_line

    answer = _rendered_items_answer()
    lines = [line for line in answer.splitlines() if line.strip()]
    structural = [line for line in lines if is_structural_line(line)]
    assert structural == [
        "確認できる内容", "根拠：manual.pdf p.3", "操作手順（3.2 利用者情報の変更）", "根拠：manual.pdf p.4",
        "確認手順（4.1 帳票出力）", "根拠：manual.pdf p.9", "資料の記載（今回への適用は未確認）", "根拠：manual.pdf p.5",
        "資料からは確認できない点",
    ]
    # 原文のみ提示の行は、決定的な原文照合を通った引用そのもの。モデルの主張ではないので監査対象にしない (#580)。
    claims = [line for line in lines if line not in structural and line != "・「保存ボタンを押す。」"]
    assert claims[0] == "結論です。" and len(claims) == 5

    # 評価モデルは主張の段落だけを監査して返す。見出し・出典行には何も返さない。
    evaluated = output().model_copy(update={
        "claim_checks": [ClaimCheck(answer_quote=text, status="data_confirmation", source_id="", evidence_quote="", reason="確認案内")
                         for text in claims],
        "coverage_checks": [CoverageCheck(requirement_index=1, status="addressed", answer_quote=claims[0])],
    })
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=evaluated) as parse:
        result = evaluate_answer_payload({**payload(), "answer_text": answer}, object())

    sent = [passage["text"] for passage in json.loads(parse.call_args.args[1])["answer_passages"]]
    assert sent == claims
    assert result["missing_audit_passage_ids"] == []
    assert result["audit_complete"] is True
    assert result["passed"] is True


def test_claim_citing_the_chunk_id_instead_of_the_span_id_is_bound_by_its_verbatim_quote(fixed_scope):
    # 評価入力の根拠は片段 ID とチャンク ID の両方を持ち、モデルは取り違える。引用が原文どおりなら引用エラーにしない (#563)。
    from docrag.evaluation.answer_eval import _evidence_fragments

    evidence = [{"id": "run:chunk-p1", "chunk_uid": "run:chunk-p1", "source": "fx.pdf",
                 "text": "外貨サービス店 ✓ 対応言語は 18 種類です。 ✓ AMEX T/Cは日本国内購入分のみ"},
                {"id": "run:chunk-p2", "chunk_uid": "run:chunk-p2", "source": "other.pdf", "text": "10 連休の窓口のご利用案内"}]
    span_id = next(_evidence_fragments(evidence))["evidence_id"]
    answer = "対応言語は18種類です。"

    def evaluated(evidence_id, quote, status="supported"):
        return output().model_copy(update={
            "claim_checks": [ClaimCheck(answer_quote=answer, answer_passage_id="A1", status=status, evidence_id=evidence_id,
                                        source_id="fx.pdf", evidence_quote=quote, reason="本文と一致")],
            "coverage_checks": [CoverageCheck(requirement_index=1, status="addressed", answer_quote=answer)]})

    def run(evidence_id, quote):
        with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=evaluated(evidence_id, quote)):
            return evaluate_answer_payload({**payload(), "answer_text": answer, "evidence_items": evidence}, object())

    for evidence_id in (span_id, "run:chunk-p1", "fx.pdf"):
        result = run(evidence_id, "対応言語は 18 種類です。")
        assert [c["status"] for c in result["claim_checks"]] == ["supported"], evidence_id
        assert result["claim_checks"][0]["source_id"] == "run:chunk-p1"
        assert result["audit_complete"] is True and result["citation_error_count"] == 0
    # 評価モデルは引用を再入力しない設計。引用なしでも、catalog 内で一意なチャンク ID ならそのチャンクへ結び付く (#567)。
    result = run("run:chunk-p1", "")
    assert [c["status"] for c in result["claim_checks"]] == ["supported"]
    assert result["claim_checks"][0]["source_id"] == "run:chunk-p1"
    # 引用が原文にない、出典のファイル名だけで引用がない、別の根拠の ID を名指しした場合は、従来どおり引用エラー。
    for evidence_id, quote in (("run:chunk-p1", "対応言語は 20 種類です。"), ("fx.pdf", ""), ("run:chunk-p2", "対応言語は 18 種類です。")):
        result = run(evidence_id, quote)
        assert [c["status"] for c in result["claim_checks"]] == ["citation_error"], (evidence_id, quote)


def test_scope_prompt_keeps_documented_values_as_requirements():
    # 制度上の金額・種類数・期限は資料に書かれた知識。個案データとして除外すると正答が採点されない (#563)。
    from docrag.evaluation.answer_eval import SCOPE_PROMPT

    for phrase in ("誰が尋ねても同じ答えになる値", "数値ごと requirements に残す", "個別の案件でしか決まらない値", "対応言語は12種類です"):
        assert phrase in SCOPE_PROMPT


def test_table_and_ocr_quotes_and_file_name_sources_are_verified_like_the_answer_generator(fixed_scope):
    # 評価モデルは表の tag を外した引用、OCR の改行を詰めた引用、出典のファイル名を返す。回答生成と同じ基準で照合する (#567)。
    table = "<table><tbody><tr><td>対象投資額</td><td>毎年 80 万円</td></tr><tr><td>優遇期間</td><td>最長 5 年間</td></tr></tbody></table>"
    figure = "18\n歳以降\n（注\n1\n）\nは、\n払い出しが可能となります。"
    evidence = [{"id": "nisa:p1", "chunk_uid": "nisa:p1", "source": "nisa.pdf", "text": "概要\n" + table + "\n" + figure,
                 "children": [{"id": "nisa:c1", "chunk_uid": "nisa:c1", "source": "nisa.pdf", "text": table}]},
                {"id": "other:p1", "chunk_uid": "other:p1", "source": "other.pdf", "text": "<table><tr><td>対象投資額</td><td>毎年 80 万円</td></tr></table>"}]
    answer = "対象投資額は毎年80万円です。"

    def run(evidence_id, source_id, quote):
        evaluated = output().model_copy(update={
            "claim_checks": [ClaimCheck(answer_quote=answer, answer_passage_id="A1", status="supported", evidence_id=evidence_id,
                                        source_id=source_id, evidence_quote=quote, reason="表と一致")],
            "coverage_checks": [CoverageCheck(requirement_index=1, status="addressed", answer_quote=answer)]})
        with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=evaluated):
            result = evaluate_answer_payload({**payload(), "answer_text": answer, "evidence_items": evidence}, object())
        return result["claim_checks"][0]

    # チャンク ID + tag を外した表の引用。
    assert run("nisa:c1", "nisa.pdf", "対象投資額 | 毎年 80 万円")["status"] == "supported"
    # ID なし + 出典のファイル名。同じ文書の parent と child の両方に含まれるので、より具体的な child へ結び付く。
    claim = run("", "nisa.pdf", "対象投資額\t毎年 80 万円")
    assert (claim["status"], claim["source_id"]) == ("supported", "nisa:c1")
    # OCR の改行と読点の揺れ。
    assert run("", "nisa.pdf", "18歳以降（注1）は払い出しが可能となります。")["status"] == "supported"
    # 原文にない値と、名指しした文書にない引用は引用エラーのまま。
    assert run("nisa:c1", "nisa.pdf", "対象投資額 | 毎年 90 万円")["status"] == "citation_error"
    assert run("", "other.pdf", "優遇期間 | 最長 5 年間")["status"] == "citation_error"


def test_quote_only_line_with_several_sentences_is_one_passage():
    # 表の引用は複数の文を含む。引用の途中の「。」で切ると、半端な段落が監査未完了になる (#567)。
    from docrag.generation.operation_audit import answer_passages

    text = ("結論です。続きです。\n\n資料の記載（今回への適用は未確認）\n\n"
            "・「① 上限額の引き上げ | 上限額は 100 万円です。 | 上限額は、 120 万円まで 引き上げられます。」\n根拠：nisa.pdf p.1\n1. 「保存を押す。」")
    assert [p["text"] for p in answer_passages(text)] == [
        "結論です。", "続きです。", "資料の記載（今回への適用は未確認）",
        "・「① 上限額の引き上げ | 上限額は 100 万円です。 | 上限額は、 120 万円まで 引き上げられます。」",
        "根拠：nisa.pdf p.1", "1. 「保存を押す。」"]


def test_verbatim_quote_lines_are_limited_to_the_quote_only_section():
    # 他の節の「…」で始まる行はモデルの言い換えであり得るので、監査対象に残す (#580)。
    from docrag.evaluation.answer_eval import _answer_passages

    answer = ("結論です。\n\n確認できる内容\n\n・「保存」を押すと登録されます。\n・「これはモデルが書いた説明です。」\n根拠：m.pdf p.1\n\n"
              "資料の記載（今回への適用は未確認）\n\n・「※到着後、約 1 週間で登録完了となります。」\n根拠：m.pdf p.1\n・「自動支払依頼書記入例」\n\n"
              "資料からは確認できない点\n\n・「取消」の手順は資料にありません。")
    assert [p["text"] for p in _answer_passages(answer)] == [
        "結論です。", "・「保存」を押すと登録されます。", "・「これはモデルが書いた説明です。」", "・「取消」の手順は資料にありません。"]
