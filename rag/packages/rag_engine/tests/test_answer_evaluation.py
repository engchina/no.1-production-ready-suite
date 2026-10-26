"""回答評価の照合・主張の監査・除外範囲・保存失敗分離を検証する。"""

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from rag_engine.adapters.oci import InvalidStructuredOutput
from rag_engine.config import get_settings
from rag_engine.evaluation.answer_eval import (
    MAX_EVALUATION_INPUT_BYTES,
    AnswerEvaluationOutput,
    ClaimCheck,
    CoverageCheck,
    EvaluationContractError,
    StandardAnswerScope,
    StandardRequirement,
    _input_bytes,
    _prepare_standard_scope,
    evaluate_answer_payload,
    grounded_quote,
    standard_answer_units,
    standard_scope_cache,
)
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answering import AnswerQuestionResult, AnswerRecord, answer_evidence_items, parse_answer_response
from rag_engine.knowledge.answers import save_answer_result


@pytest.fixture(autouse=True)
def fixed_scope():
    """既存の評価・保存テストでは、範囲抽出I/Oを独立した固定入力に置き換える。"""
    with patch("rag_engine.evaluation.answer_eval._prepare_standard_scope", return_value=scope_output()) as mocked:
        yield mocked


def scope_output():
    return StandardAnswerScope(
        requirements=[
            StandardRequirement(
                standard_answer_unit_ids=["S1"],
                standard_answer_quote="ログを確認して原因を特定する。",
                requirement="文書にあるログ確認手順",
            )
        ],
        excluded_case_data=["実際の原因"],
    )


def output():
    """ログ確認手順を説明済みと照合し、確認案内を監査した固定の評価結果を返す。"""
    return AnswerEvaluationOutput(
        claim_checks=[
            ClaimCheck(answer_quote="回答", status="data_confirmation", source_id="", evidence_quote="", reason="未確認データの確認案内")
        ],
        coverage_checks=[CoverageCheck(requirement_index=1, status="addressed", answer_quote="回答")],
        evidence_summary="文書で確認したログ確認手順と出典",
    )


def payload():
    return {
        "question": "今回の取込失敗の原因は？",
        "standard_answer": "ログを確認して原因を特定する。",
        "answer_text": "回答",
        "evidence_items": [{"id": "c1", "text": "手順本文" * 150, "text_preview": "短縮表示", "source": "manual.pdf"}],
    }


def test_completed_returns_checks_without_scores_and_sends_full_evidence():
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=output()) as parse:
        result = evaluate_answer_payload(
            payload() | {"external_data_required": True, "external_data_items": ["当該実行のログ"]}, object(), provider_id="chosen"
        )
    assert result["status"] == "completed" and result["rubric_version"] == 11
    assert result["requirement_coverage"] == 1.0
    # 点数・合否は付けない。評価の基準の閾値による合否は backend が計算する(#680)。
    assert not {"scores", "total_score", "passed", "max_score", "pass_threshold"} & set(result)
    assert result["external_data_items"] == ["当該実行のログ"]
    prompt = json.loads(parse.call_args.args[1])
    assert prompt["evidence_items"][0]["text"] == "手順本文" * 150
    assert "generation_assessment" not in prompt
    assert parse.call_args.args[3] is AnswerEvaluationOutput
    assert parse.call_args.kwargs["provider_id"] == "chosen"
    for phrase in ("requirement_coverage", "claim_support_rate", "点数・合否は付けない"):
        assert phrase in parse.call_args.args[0]


def test_invalid_output_is_retried_once_with_same_evidence():
    invalid = output().model_copy(update={"coverage_checks": []})
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=[invalid, output()]) as parse:
        result = evaluate_answer_payload(payload(), object(), provider_id="chosen")
    assert result["status"] == "completed" and result["batch_count"] == 1
    assert parse.call_count == 2
    first = json.loads(parse.call_args_list[0].args[1])
    retry = json.loads(parse.call_args_list[1].args[1])
    assert retry.pop("validation_feedback")
    assert retry == first


def test_repeated_invalid_output_is_error_without_partial_result():
    invalid = output().model_copy(update={"coverage_checks": []})
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=invalid) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 2
    assert result["status"] == "error" and "requirement_coverage" not in result


def test_adapter_validation_error_also_retries_once():
    with pytest.raises(ValidationError) as error:
        AnswerEvaluationOutput(**(output().model_dump() | {"coverage_checks": [{"requirement_index": 0, "status": "invalid"}]}))
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=[error.value, output()]) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 2
    assert result["status"] == "completed"


def test_removed_axis_fields_are_rejected():
    # 旧 4 軸(#680 で削除)を返すモデルの出力は schema 違反にして再試行させる。
    with pytest.raises(ValidationError):
        AnswerEvaluationOutput(**(output().model_dump() | {"accuracy": {"score": 5, "reason": "理由"}}))


def test_no_standard_answer_skips_network_and_keeps_external_status():
    with patch("rag_engine.evaluation.answer_eval.parse_text_response") as parse:
        result = evaluate_answer_payload(
            {"standard_answer": "  ", "external_data_required": True, "external_data_items": ["ログ"]}, object()
        )
    parse.assert_not_called()
    assert result["status"] == "no_standard_answer"
    assert result["external_data_items"] == ["ログ"]


def test_error_does_not_prevent_answer_persistence_or_leak_details():
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp), query_history_enabled=False)
        result = AnswerQuestionResult(
            answer="回答",
            answer_text="回答",
            original_question="質問",
            question_display="質問",
            selected_strategy="simple",
            effective_strategy="simple",
            primary_source_run_id="run1",
        )
        with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=RuntimeError("private connection details")):
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
    record = AnswerRecord(
        id="c1",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="text",
        text="metadata付き本文",
        body_text="根拠全文" * 150,
    )
    assert answer_evidence_items([record])[0]["text"] == "根拠全文" * 150


def test_successful_evaluation_is_saved_and_keeps_generation_external_state():
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp), query_history_enabled=False)
        result = AnswerQuestionResult(
            answer="回答",
            answer_text="回答",
            original_question="質問",
            question_display="質問",
            selected_strategy="simple",
            effective_strategy="simple",
            primary_source_run_id="run1",
            external_data_required=True,
            external_data_items=("実行ログ",),
        )
        with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=output()) as parse:
            saved = save_answer_result(result, settings, standard_answer="標準", evaluation_provider="chosen")
        stored = json.loads((Path(tmp) / "run1" / "answers" / f"{saved.answer_id}.json").read_text())
        assert stored["evaluation"]["status"] == "completed"
        assert stored["evaluation"]["requirement_coverage"] == 1.0
        assert stored["external_data_items"] == stored["evaluation"]["external_data_items"] == ["実行ログ"]
        assert stored["needs_human_review"] is True
        assert parse.call_args.kwargs["provider_id"] == "chosen"


def long_payload():
    """12個の親と子の全文を送り、末尾の根拠を保持することを検証する。"""
    data = payload()
    data["evidence_items"] = [
        {
            "id": f"p{i}",
            "source": "manual.pdf",
            "text": "手順条件。" * 1700,
            "children": [{"id": f"c{i}", "source": "manual.pdf", "text": '😀\n"\\条件' * 1200 + f"末尾{i}"}],
        }
        for i in range(12)
    ]
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
        return output().model_copy(update={"evidence_summary": f"batch {len(calls)} 確認済み事実と出典"})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = evaluate_answer_payload(data, object(), provider_id="chosen")
    assert result["status"] == "completed"
    assert result["requirement_coverage"] == 1.0
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
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=[partial, RuntimeError("private")]):
        result = evaluate_answer_payload(long_payload(), object())
    assert result["status"] == "error" and result["batch_count"] == 1
    assert "requirement_coverage" not in result
    assert "private" not in result["message"]


def test_oversized_fixed_input_skips_network_and_explains_scope_reduction():
    with patch("rag_engine.evaluation.answer_eval.parse_text_response") as parse:
        result = evaluate_answer_payload(payload() | {"standard_answer": "長" * MAX_EVALUATION_INPUT_BYTES}, object())
    parse.assert_not_called()
    assert result["status"] == "input_too_large"
    assert "範囲を絞" in result["message"]


def test_missing_intermediate_summary_is_not_treated_as_complete():
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=output().model_copy(update={"evidence_summary": ""})):
        result = evaluate_answer_payload(long_payload(), object())
    assert result["status"] == "error" and "requirement_coverage" not in result


def test_long_reasons_are_not_repeated_in_next_batch():
    long_reason = "理由" * 5000
    oversized = output().model_copy(
        update={
            "evidence_summary": "摘要",
            "claim_checks": [
                ClaimCheck(answer_quote="回答", status="data_confirmation", source_id="", evidence_quote="", reason=long_reason)
            ],
        }
    )
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=oversized) as parse:
        result = evaluate_answer_payload(long_payload(), object())
    assert parse.call_count > 1
    assert result["status"] == "completed"
    previous = json.loads(parse.call_args.args[1])["previous_evaluation"]
    assert "coverage_checks" not in previous and long_reason not in json.dumps(previous, ensure_ascii=False)


def test_tight_remaining_budget_splits_fragments_without_losing_unicode():
    from collections import deque

    from rag_engine.evaluation.answer_eval import _next_batch

    text = '😀\\"\n' * 500
    item = {"id": "c1", "text": text, "fragment_start": 0, "fragment_end": len(text)}
    empty = {"previous_evaluation": None, "batch_index": 1, "is_final_batch": False, "evidence_items": []}
    limit = _input_bytes(empty) + 500
    pending = deque([item])
    fragments = []
    with patch("rag_engine.evaluation.answer_eval.MAX_EVALUATION_INPUT_BYTES", limit):
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
        generated = AnswerQuestionResult(
            answer="回答",
            original_question="質問",
            question_display="質問",
            selected_strategy="simple",
            effective_strategy="simple",
            primary_source_run_id="run1",
            external_data_required=True,
            external_data_items=("実行ログ",),
            needs_human_review=True,
        )
        with patch("rag_engine.evaluation.answer_eval.evaluate_answer_payload", return_value={"status": status}):
            saved = save_answer_result(generated, settings, standard_answer="標準")
        assert saved.payload["needs_human_review"] is True
        assert saved.payload["external_data_items"] == ["実行ログ"]


def test_scope_preparation_never_receives_answer_or_retrieved_evidence():
    standard = "設定画面で業務別担当者(J)を選択してください。今回は主担当が設定されていました。"
    scope = StandardAnswerScope(
        requirements=[StandardRequirement(standard_answer_unit_ids=["S1"], requirement="業務別担当者(J)の選択手順")],
        excluded_case_data=["今回の設定値"],
    )
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=scope) as parse:
        result = _prepare_standard_scope(payload() | {"standard_answer": standard}, object(), "enterprise-ai-vision")
    assert result.requirements[0].standard_answer_unit_ids == ["S1"] and result.excluded_case_data == ["今回の設定値"]
    # 標準回答は単位（id と原文）で渡し、原文の全体・生成回答・根拠は渡さない。
    assert json.loads(parse.call_args.args[1]) == {
        "question": payload()["question"],
        "standard_answer_units": [
            {"id": "S1", "text": "設定画面で業務別担当者(J)を選択してください。"},
            {"id": "S2", "text": "今回は主担当が設定されていました。"},
        ],
    }
    assert parse.call_args.args[3] is StandardAnswerScope
    assert result.requirements[0].standard_answer_quote == "設定画面で業務別担当者(J)を選択してください。"


def test_scope_unit_id_not_in_standard_answer_retries_then_fails():
    scope = StandardAnswerScope(
        requirements=[StandardRequirement(standard_answer_unit_ids=["S2"], requirement="業務別担当者(J)の選択手順")],
        excluded_case_data=[],
    )
    with (
        patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=scope) as parse,
        pytest.raises(EvaluationContractError, match="standard_answer_units に無い ID"),
    ):
        _prepare_standard_scope({"question": "質問", "standard_answer": "業務別担当者(J)を選択"}, object(), None)
    assert parse.call_count == 2


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [CoverageCheck(requirement_index=2, status="missing", answer_quote="")],
        [CoverageCheck(requirement_index=1, status="missing", answer_quote="")] * 2,
        [CoverageCheck(requirement_index=1, status="addressed", answer_quote="標準回答だけにある操作")],
        [CoverageCheck(requirement_index=1, status="partial", answer_quote="")],
        [CoverageCheck(requirement_index=1, status="missing", answer_quote="回答外の引用")],
    ],
)
def test_invalid_fixed_check_or_answer_quote_is_retried_without_publishing_scores(checks):
    invalid = output().model_copy(update={"coverage_checks": checks})
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=invalid) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 2
    assert result["status"] == "error" and "requirement_coverage" not in result


@pytest.mark.parametrize("state,expected", [("missing", 0), ("partial", 0.5), ("addressed", 1)])
def test_absent_document_evidence_cannot_remove_expected_procedure(state, expected, fixed_scope):
    required = StandardRequirement(
        standard_answer_unit_ids=["S1"], standard_answer_quote="業務別担当者(J)を選択。", requirement="業務別担当者(J)の選択手順"
    )
    fixed_scope.return_value = StandardAnswerScope(requirements=[required], excluded_case_data=["今回の主担当設定値"])
    evaluated = output().model_copy(
        update={
            "coverage_checks": [CoverageCheck(requirement_index=1, status=state, answer_quote="" if state == "missing" else "回答")],
        }
    )
    data = payload() | {"standard_answer": "業務別担当者(J)を選択。今回は主担当が設定されていた。", "evidence_items": []}
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=evaluated):
        result = evaluate_answer_payload(data, object())
    assert result["status"] == "completed"
    assert result["requirement_coverage"] == expected
    assert result["standard_answer_scope"]["requirements"][0]["requirement"] == required.requirement


def test_frozen_scope_is_not_changed_by_later_evidence_batches():
    seen = []

    def parse(system, prompt, settings, schema, **kwargs):
        inputs = json.loads(prompt)
        seen.append(inputs["standard_answer_scope"])
        return output().model_copy(
            update={
                "coverage_checks": [
                    CoverageCheck(
                        requirement_index=1,
                        status="missing" if inputs["is_final_batch"] else "addressed",
                        answer_quote="" if inputs["is_final_batch"] else "回答",
                    )
                ]
            }
        )

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = evaluate_answer_payload(long_payload(), object())
    assert len(seen) > 1 and all(item == seen[0] for item in seen)
    assert result["requirement_coverage"] == 0


def test_real_scope_stage_then_evaluation_keeps_procedure_without_document_support(fixed_scope):
    fixed_scope.side_effect = _prepare_standard_scope
    data = payload()
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=[scope_output(), output()]) as parse:
        result = evaluate_answer_payload(data, object())
    assert parse.call_count == 2 and result["status"] == "completed"
    assert parse.call_args_list[0].args[3] is StandardAnswerScope
    assert parse.call_args_list[1].args[3] is AnswerEvaluationOutput


def test_partial_coverage_is_not_lost_and_followup_is_still_reported(fixed_scope):
    required = StandardRequirement(standard_answer_unit_ids=["S1"], standard_answer_quote="ログを確認", requirement="ログ確認")
    extra = StandardRequirement(
        standard_answer_unit_ids=["S2"],
        standard_answer_quote="別の相談",
        requirement="履歴の出力",
        relevance="follow_up",
        relevance_reason="原質問にない追加相談",
    )
    fixed_scope.return_value = StandardAnswerScope(
        requirements=[required, extra, extra.model_copy(update={"requirement": "別日付の出力"})], excluded_case_data=[]
    )
    checks = [
        CoverageCheck(requirement_index=i, status="partial" if i == 1 else "missing", answer_quote="回答" if i == 1 else "")
        for i in (1, 2, 3)
    ]
    with patch(
        "rag_engine.evaluation.answer_eval.parse_text_response", return_value=output().model_copy(update={"coverage_checks": checks})
    ):
        result = evaluate_answer_payload(payload(), object())
    # 指標は required だけの対応率。追加相談(follow_up)と全項目は参考に残す。
    assert result["requirement_coverage"] == 0.5
    assert result["requirement_coverage_full"] == 0.1667
    assert result["requirement_coverage_follow_up"] == 0
    assert len(result["coverage_checks"]) == 3


def test_scope_cannot_relabel_every_requirement_as_followup():
    scope = scope_output()
    scope.requirements[0].relevance = "follow_up"
    with (
        patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=scope),
        pytest.raises(EvaluationContractError, match="原質問"),
    ):
        _prepare_standard_scope(payload(), object(), None)


def test_prepared_scope_is_reused_without_another_scope_call(fixed_scope):
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=output()) as parse:
        result = evaluate_answer_payload(payload(), object(), standard_scope=scope_output())
    fixed_scope.assert_not_called()
    assert parse.call_count == 1
    assert result["standard_answer_scope"] == scope_output().model_dump()
    invalid = scope_output()
    invalid.requirements[0].standard_answer_unit_ids = ["S9"]
    with patch("rag_engine.evaluation.answer_eval.parse_text_response") as parse:
        result = evaluate_answer_payload(payload(), object(), standard_scope=invalid)
    parse.assert_not_called()
    assert result["status"] == "error"


def _rendered_items_answer():
    """回答生成（items 方式）の render() が実際に組み立てる本文。全種類の見出しと出典行を含む。"""
    from rag_engine.generation.grounded import CheckedItem, render
    from rag_engine.models.llm import GroundedCitation, GroundedItem

    def span(function, page):
        return {"source": "manual.pdf", "page": page, "function": ("manual.pdf", function)}

    def item(kind, text, **extra):
        return GroundedItem(kind=kind, text=text, citations=[GroundedCitation(evidence_id="E1", quote=text)], **extra)

    return render(
        "結論です。",
        [
            CheckedItem(item("rule", "対象は登録済みの利用者です。"), span("3.1 対象", 3)),
            CheckedItem(item("operation", "利用者情報画面を開きます。"), span("3.2 利用者情報の変更", 4)),
            CheckedItem(item("confirmation", "帳票の出力結果を確認します。"), span("4.1 帳票出力", 9)),
            CheckedItem(item("operation", "保存ボタンを押す。"), span("3.2 利用者情報の変更", 5), quote_only=True),
            # 該当が未確認の説明は「参考情報」の節に原文の引用で置く（#2040・#2213）。原文なので主張として監査しない。
            CheckedItem(item("rule", "上限は申請の種類で異なります。", applies="unverified"), span("3.3 上限", 6)),
            CheckedItem(GroundedItem(kind="gap", text="取消の手順は資料にありません。")),
        ],
    )


def test_rendered_headings_and_citations_do_not_block_passing(fixed_scope):
    # render() の見出し行・出典行は主張ではない。監査未完了と数えると満点でも passed が false になる (#449)。
    from rag_engine.generation.grounded import is_structural_line

    answer = _rendered_items_answer()
    lines = [line for line in answer.splitlines() if line.strip()]
    structural = [line for line in lines if is_structural_line(line)]
    assert structural == [
        "資料により確認できた事項",
        "根拠：manual.pdf p.3",
        "操作手順（3.2 利用者情報の変更）",
        "根拠：manual.pdf p.4",
        "確認手順（4.1 帳票出力）",
        "根拠：manual.pdf p.9",
        "参考情報",
        "以下は資料の原文です。本質問の条件への該当は確認できていません。",
        "根拠：manual.pdf p.5",
        "根拠：manual.pdf p.6",
        "資料から確認できなかった事項",
    ]
    # 原文のみ提示の行は、決定的な原文照合を通った引用そのもの。モデルの主張ではないので監査対象にしない (#580)。
    quotes = {"・「保存ボタンを押す。」", "・「上限は申請の種類で異なります。」"}
    assert quotes <= set(lines)
    claims = [line for line in lines if line not in structural and line not in quotes]
    assert claims[0] == "結論です。" and len(claims) == 5

    # 評価モデルは主張の段落だけを監査して返す。見出し・出典行には何も返さない。
    evaluated = output().model_copy(
        update={
            "claim_checks": [
                ClaimCheck(answer_quote=text, status="data_confirmation", source_id="", evidence_quote="", reason="確認案内")
                for text in claims
            ],
            "coverage_checks": [CoverageCheck(requirement_index=1, status="addressed", answer_quote=claims[0])],
        }
    )
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=evaluated) as parse:
        result = evaluate_answer_payload({**payload(), "answer_text": answer}, object())

    sent = [passage["text"] for passage in json.loads(parse.call_args.args[1])["answer_passages"]]
    assert sent == claims
    assert result["missing_audit_passage_ids"] == []
    assert "unassessed" not in {claim["status"] for claim in result["claim_checks"]}


def test_generated_boilerplate_is_not_audited_as_a_claim():
    # 回答生成の定型の前置きと「参考情報」の節の直下の説明は主張ではない。監査に回すと not_a_claim が
    # unassessed になる(#680・#2040)。
    from rag_engine.evaluation.answer_eval import _answer_passages

    labels = answer_labels()
    answer = (
        labels.neutral_summary
        + f"\n\n{labels.quote_only_section}\n{labels.quote_only_note}"
        + "\n・特典はQUOカード（5千円）です。条件は15百万円以上の契約実行です。"
        + "\n根拠：info.pdf p.1"
    )
    assert [p["text"] for p in _answer_passages(answer)] == ["・特典はQUOカード（5千円）です。", "条件は15百万円以上の契約実行です。"]


def test_claim_citing_the_chunk_id_instead_of_the_span_id_is_bound_by_its_verbatim_quote(fixed_scope):
    # 評価入力の根拠は片段 ID とチャンク ID の両方を持ち、モデルは取り違える。引用が原文どおりなら引用エラーにしない (#563)。
    from rag_engine.evaluation.answer_eval import _evidence_fragments

    evidence = [
        {
            "id": "run:chunk-p1",
            "chunk_uid": "run:chunk-p1",
            "source": "fx.pdf",
            "text": "外貨サービス店 ✓ 対応言語は 18 種類です。 ✓ AMEX T/Cは日本国内購入分のみ",
        },
        {"id": "run:chunk-p2", "chunk_uid": "run:chunk-p2", "source": "other.pdf", "text": "10 連休の窓口のご利用案内"},
    ]
    span_id = next(_evidence_fragments(evidence))["evidence_id"]
    answer = "対応言語は18種類です。"

    def evaluated(evidence_id, quote, status="supported"):
        return output().model_copy(
            update={
                "claim_checks": [
                    ClaimCheck(
                        answer_quote=answer,
                        answer_passage_id="A1",
                        status=status,
                        evidence_id=evidence_id,
                        source_id="fx.pdf",
                        evidence_quote=quote,
                        reason="本文と一致",
                    )
                ],
                "coverage_checks": [CoverageCheck(requirement_index=1, status="addressed", answer_quote=answer)],
            }
        )

    def run(evidence_id, quote):
        with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=evaluated(evidence_id, quote)):
            return evaluate_answer_payload({**payload(), "answer_text": answer, "evidence_items": evidence}, object())

    for evidence_id in (span_id, "run:chunk-p1", "fx.pdf"):
        result = run(evidence_id, "対応言語は 18 種類です。")
        assert [c["status"] for c in result["claim_checks"]] == ["supported"], evidence_id
        assert result["claim_checks"][0]["source_id"] == "run:chunk-p1"
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
    from rag_engine.evaluation.answer_eval import SCOPE_PROMPT

    for phrase in ("誰が尋ねても同じ答えになる値", "数値ごと requirements に残す", "個別の案件でしか決まらない値", "対応言語は12種類です"):
        assert phrase in SCOPE_PROMPT


def test_table_and_ocr_quotes_and_file_name_sources_are_verified_like_the_answer_generator(fixed_scope):
    # 評価モデルは表の tag を外した引用、OCR の改行を詰めた引用、出典のファイル名を返す。回答生成と同じ基準で照合する (#567)。
    table = "<table><tbody><tr><td>対象投資額</td><td>毎年 80 万円</td></tr><tr><td>優遇期間</td><td>最長 5 年間</td></tr></tbody></table>"
    figure = "18\n歳以降\n（注\n1\n）\nは、\n払い出しが可能となります。"
    evidence = [
        {
            "id": "nisa:p1",
            "chunk_uid": "nisa:p1",
            "source": "nisa.pdf",
            "text": "概要\n" + table + "\n" + figure,
            "children": [{"id": "nisa:c1", "chunk_uid": "nisa:c1", "source": "nisa.pdf", "text": table}],
        },
        {
            "id": "other:p1",
            "chunk_uid": "other:p1",
            "source": "other.pdf",
            "text": "<table><tr><td>対象投資額</td><td>毎年 80 万円</td></tr></table>",
        },
    ]
    answer = "対象投資額は毎年80万円です。"

    def run(evidence_id, source_id, quote):
        evaluated = output().model_copy(
            update={
                "claim_checks": [
                    ClaimCheck(
                        answer_quote=answer,
                        answer_passage_id="A1",
                        status="supported",
                        evidence_id=evidence_id,
                        source_id=source_id,
                        evidence_quote=quote,
                        reason="表と一致",
                    )
                ],
                "coverage_checks": [CoverageCheck(requirement_index=1, status="addressed", answer_quote=answer)],
            }
        )
        with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=evaluated):
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
    from rag_engine.generation.operation_audit import answer_passages

    text = (
        "結論です。続きです。\n\n参考情報\n\n"
        "・「① 上限額の引き上げ | 上限額は 100 万円です。 | 上限額は、 120 万円まで 引き上げられます。」"
        "\n根拠：nisa.pdf p.1\n1. 「保存を押す。」"
    )
    assert [p["text"] for p in answer_passages(text)] == [
        "結論です。",
        "続きです。",
        "参考情報",
        "・「① 上限額の引き上げ | 上限額は 100 万円です。 | 上限額は、 120 万円まで 引き上げられます。」",
        "根拠：nisa.pdf p.1",
        "1. 「保存を押す。」",
    ]


def test_generated_section_lines_are_not_audited_as_claims():
    # 読めなかった章節・時間の上限で省いた処理の行は、システムが決まった文で書いた案内（#2040）。
    from rag_engine.evaluation.answer_eval import _answer_passages

    answer = (
        "結論です。\n\n資料により確認できた事項\n\n・説明です。\n根拠：m.pdf p.1\n\n"
        f"{answer_labels().unverified_section}\n\n・「m.pdf」の「第2章」（p.3〜5）は、読み込み量の上限に達したため未反映です。\n\n"
        f"{answer_labels().deadline_section}\n\n・回答の時間の上限が近いため、作り直しをしていません。\n\n"
        "資料から確認できなかった事項\n\n・取消の手順は資料にありません。"
    )
    assert [p["text"] for p in _answer_passages(answer)] == ["結論です。", "・説明です。", "・取消の手順は資料にありません。"]


def test_verbatim_quote_lines_are_limited_to_the_quote_only_section():
    # 他の節の「…」で始まる行はモデルの言い換えであり得るので、監査対象に残す (#580)。
    from rag_engine.evaluation.answer_eval import _answer_passages

    answer = (
        "結論です。\n\n資料により確認できた事項\n\n・「保存」を押すと登録されます。\n"
        "・「これはモデルが書いた説明です。」\n根拠：m.pdf p.1\n\n"
        "参考情報\n\n以下は資料の原文です。本質問の条件への該当は確認できていません。\n"
        "・該当が未確認の説明です。\n根拠：m.pdf p.2\n"
        "・「※到着後、約 1 週間で登録完了となります。」"
        "\n根拠：m.pdf p.1\n・「自動支払依頼書記入例」\n\n"
        "資料から確認できなかった事項\n\n・「取消」の手順は資料にありません。"
    )
    # 節の直下の説明は主張ではない。同じ節の該当が未確認の説明（モデルの言い換え）の後も、原文の行は外す（#2040）。
    assert [p["text"] for p in _answer_passages(answer)] == [
        "結論です。",
        "・「保存」を押すと登録されます。",
        "・「これはモデルが書いた説明です。」",
        "・該当が未確認の説明です。",
        "・「取消」の手順は資料にありません。",
    ]


def test_six_metrics_correctness_uses_reference_without_retrieved_evidence():
    """忠実性・網羅性が高くても、結論の正確性を独立して判定する。"""
    from rag_engine.evaluation.answer_eval import AnswerCorrectnessOutput

    with patch(
        "rag_engine.evaluation.answer_eval.parse_text_response",
        side_effect=[output(), AnswerCorrectnessOutput(verdict="incorrect", reason="結論が標準回答と異なる")],
    ) as parse:
        result = evaluate_answer_payload(payload() | {"six_metrics": True}, object())
    assert result["status"] == "completed"
    assert result["answer_correctness"] == 0
    assert result["requirement_coverage"] == 1
    assert "requirement_coverage_full" not in result
    request = json.loads(parse.call_args.args[1])
    assert request["question"] == payload()["question"]
    assert request["standard_answer"] == payload()["standard_answer"]
    assert "evidence_items" not in request


def test_six_metrics_audits_claims_without_standard(fixed_scope):
    """標準回答なしでも根拠監査を呼び、標準回答に依存する指標は測らない。"""
    checks = AnswerEvaluationOutput(
        coverage_checks=[],
        claim_checks=[ClaimCheck(answer_quote="回答", status="unsupported", source_id="", evidence_quote="", reason="根拠なし")],
    )
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", return_value=checks) as parse:
        result = evaluate_answer_payload(payload() | {"standard_answer": "", "six_metrics": True, "allow_without_standard": True}, object())
    assert result["status"] == "completed"
    assert result["requirement_coverage"] is None
    assert "answer_correctness" not in result
    assert result["claim_checks"][0]["status"] == "unsupported"
    assert parse.call_count == 1
    fixed_scope.assert_not_called()


# ---- 判定の出し直し・引用の接地・比較範囲の使い回し（#3570） -----------------------------------

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"


def broken_output():
    """adapter が構造化出力として読めなかった応答（JSON が閉じない・必須項目の欠落）。"""
    return InvalidStructuredOutput("構造化出力の JSON が閉じていません。")


def scope_or_output(system, prompt, settings, schema, **kwargs):
    """比較範囲の固定には固定の範囲、照合・監査には固定の評価結果を返す。"""
    return scope_output() if schema is StandardAnswerScope else output()


def test_invalid_structured_output_is_retried_with_feedback_and_scored():
    with patch(PARSE, side_effect=[broken_output(), output()]) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert result["status"] == "completed" and result["requirement_coverage"] == 1.0
    assert parse.call_count == 2
    assert "JSON" in json.loads(parse.call_args_list[1].args[1])["validation_feedback"]
    assert result["judge_diagnostics"]["judge_retries"] == 1
    assert result["judge_diagnostics"]["retry_reasons"] == ["judge:InvalidStructuredOutput"]


def test_scope_stage_retries_invalid_structured_output(fixed_scope):
    fixed_scope.side_effect = _prepare_standard_scope
    with patch(PARSE, side_effect=[broken_output(), scope_output(), output()]) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert result["status"] == "completed" and parse.call_count == 3
    diagnostics = result["judge_diagnostics"]
    assert diagnostics["standard_scope_retries"] == 1 and diagnostics["judge_retries"] == 0
    assert diagnostics["retry_reasons"] == ["standard_scope:InvalidStructuredOutput"]
    assert diagnostics["standard_scope_reused"] is False


def test_repeated_invalid_structured_output_is_error_with_diagnostics():
    with patch(PARSE, side_effect=[broken_output(), broken_output()]) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 2
    assert result["status"] == "error" and result["error_type"] == "InvalidStructuredOutput"
    assert result["judge_diagnostics"]["judge_retries"] == 1


def test_transport_error_is_not_retried_by_the_judge():
    # 通信・時間切れの再試行は adapter が持つ。判定の層は出力の誤りだけを出し直させる。
    with patch(PARSE, side_effect=TimeoutError("timeout")) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 1 and result["status"] == "error"
    assert result["judge_diagnostics"]["judge_retries"] == 0


@pytest.mark.parametrize(
    ("quote", "source", "expected"),
    [
        ("業務別担当者(J) を選択", "設定画面で業務別担当者（Ｊ）を選択してください。", "業務別担当者（Ｊ）を選択"),
        ("ログを確認して", "ログを\n確認して原因を特定する。", "ログを\n確認して"),
        ("ガイド", "ｶﾞｲﾄﾞを開く。", "ｶﾞｲﾄﾞ"),
        ("手順A、手順B", "手順A，手順B の順に進む。", "手順A，手順B"),
        ("「設定」を開く", "“設定”を開く。", "“設定”を開く"),
        ("期限は30日以内", "期限は３０日以内です。", "期限は３０日以内"),
        ("ログを確認", "ログを確認", "ログを確認"),
    ],
    ids=["width-and-space", "line-break", "halfwidth-kana", "comma-variant", "quote-marks", "fullwidth-digits", "exact"],
)
def test_grounded_quote_snaps_formatting_variants_to_the_original(quote, source, expected):
    assert grounded_quote(quote, source) == expected
    assert expected in source


@pytest.mark.parametrize(
    ("quote", "source"),
    [
        ("期限は31日以内", "期限は３０日以内です。"),
        ("ログを削除して", "ログを確認して原因を特定する。"),
        ("  ", "ログを確認"),
        ("、。", "ログを確認"),
    ],
    ids=["changed-digit", "changed-word", "blank", "punctuation-only"],
)
def test_grounded_quote_rejects_quotes_absent_after_normalization(quote, source):
    assert grounded_quote(quote, source) == ""


def test_scope_quote_is_rebuilt_from_the_original_text_of_the_units():
    # 続く単位は間の改行も含む原文の 1 つの範囲、離れた単位は省略の印でつなぐ。ID の表記の揺れはそろえる。
    standard = "設定画面で業務別担当者（Ｊ）を\n選択してください。\n\n今回は主担当でした。\n受付日より３０日以内に返送する。"
    scope = StandardAnswerScope(
        requirements=[
            StandardRequirement(standard_answer_unit_ids=[" s2", "S1", "S1"], requirement="業務別担当者の選択手順"),
            StandardRequirement(standard_answer_unit_ids=["Ｓ4", "S1"], requirement="選択と返送の期限"),
        ],
        excluded_case_data=["今回の主担当"],
    )
    with patch(PARSE, return_value=scope) as parse:
        result = _prepare_standard_scope(payload() | dict(standard_answer=standard), object(), None)
    assert parse.call_count == 1
    first, second = result.requirements
    assert first.standard_answer_unit_ids == ["S1", "S2"]
    assert first.standard_answer_quote == "設定画面で業務別担当者（Ｊ）を\n選択してください。"
    assert second.standard_answer_unit_ids == ["S1", "S4"]
    assert second.standard_answer_quote == "設定画面で業務別担当者（Ｊ）を…受付日より３０日以内に返送する。"


def test_answer_quotes_with_formatting_variants_are_snapped_to_the_answer_text():
    answer = "ログを確認してください。"
    claim = ClaimCheck(answer_quote="ログを 確認して", status="data_confirmation", source_id="", evidence_quote="", reason="確認案内")
    # 全角の空白（U+3000）を挟んだ引用。
    coverage = CoverageCheck(requirement_index=1, status="addressed", answer_quote="ログを" + chr(0x3000) + "確認")
    evaluated = output().model_copy(update=dict(claim_checks=[claim], coverage_checks=[coverage]))
    with patch(PARSE, return_value=evaluated) as parse:
        result = evaluate_answer_payload(payload() | dict(answer_text=answer), object())
    assert parse.call_count == 1 and result["status"] == "completed"
    assert result["coverage_checks"][0]["answer_quote"] == "ログを確認"
    assert result["claim_checks"][0]["answer_quote"] == "ログを確認して"


def test_scope_is_fixed_once_per_job_and_reused_across_passes(fixed_scope):
    fixed_scope.side_effect = _prepare_standard_scope
    with patch(PARSE, side_effect=scope_or_output) as parse, standard_scope_cache():
        first = evaluate_answer_payload(payload(), object())
        # 別の pass（プロファイルの範囲）の同じケース。根拠は違っても比較範囲は同じ。
        second = evaluate_answer_payload(payload() | dict(evidence_items=[]), object())
    scope_calls = [call for call in parse.call_args_list if call.args[3] is StandardAnswerScope]
    assert len(scope_calls) == 1 and fixed_scope.call_count == 1
    assert first["status"] == second["status"] == "completed"
    assert first["standard_answer_scope"] == second["standard_answer_scope"]
    assert first["judge_diagnostics"]["standard_scope_reused"] is False
    assert second["judge_diagnostics"]["standard_scope_reused"] is True


def test_scope_cache_separates_keys_and_does_not_keep_failures(fixed_scope):
    fixed_scope.side_effect = _prepare_standard_scope
    other = payload() | dict(standard_answer="ログを確認して再実行する。")
    responses = [broken_output(), broken_output()] + [scope_output(), output()] * 3
    with patch(PARSE, side_effect=responses), standard_scope_cache():
        failed = evaluate_answer_payload(payload(), object())
        retried = evaluate_answer_payload(payload(), object())
        separate = evaluate_answer_payload(other, object())
        other_judge = evaluate_answer_payload(payload(), object(), provider_id="judge-b")
    assert failed["status"] == "error"
    assert fixed_scope.call_count == 4
    for result in (retried, separate, other_judge):
        assert result["status"] == "completed"
        assert result["judge_diagnostics"]["standard_scope_reused"] is False


def test_scope_is_not_shared_outside_an_evaluation_job(fixed_scope):
    fixed_scope.side_effect = _prepare_standard_scope
    with patch(PARSE, side_effect=scope_or_output):
        evaluate_answer_payload(payload(), object())
        result = evaluate_answer_payload(payload(), object())
    assert fixed_scope.call_count == 2
    assert result["judge_diagnostics"]["standard_scope_reused"] is False


# 以下は #3570 の続き（実モデルの判定で比較範囲の固定が失敗した引用の形）を合成の文で再現する。
_WIDE_SPACE = chr(0x3000)
_STRONG = chr(0x2A) * 2
_BAR = chr(0x7C)
_SOURCE = (
    "結論" + chr(10) + "申請できます。" + chr(10) + "根拠" + chr(10) + "資料Aの第1章は、窓口で申請を受け付けると定めています。" + chr(10)
)
_SOURCE += "資料Bの第2章は、申請は受付から14日以内に審査すると定めています。" + chr(10)
_SOURCE += "文書X_規程集の規程集第5条は、申請書の受付と審査の手順を定めています。" + chr(10)
_SOURCE += "文書Y_要領集の要領集第2条も、同じ手順を定めています。" + chr(10)
_SOURCE += "確認項目" + chr(10) + "書類の写しがあるか" + chr(10) + "署名と押印があるか" + chr(10)
_SOURCE += _BAR + " 申請 " + _BAR + " 受付から30日以内 " + _BAR + chr(10) + _STRONG + "申請書" + _STRONG + "を提出する。"


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        (
            "文書X_規程集第5条は、申請書の受付と審査の手順を定めています。",
            "文書X_規程集の規程集第5条は、申請書の受付と審査の手順を定めています。",
        ),
        (
            "根拠" + _WIDE_SPACE + "資料Bの第2章は、申請は受付から14日以内に審査すると定めています。",
            "資料Bの第2章は、申請は受付から14日以内に審査すると定めています。",
        ),
        (
            "文書X_規程集第5条は、申請書の受付と審査の手順を定めています。 文書Y_要領集第2条も、同じ手順を定めています。",
            "文書X_規程集の規程集第5条は、申請書の受付と審査の手順を定めています。"
            + chr(10)
            + "文書Y_要領集の要領集第2条も、同じ手順を定めています。",
        ),
        (
            "確認項目" + chr(0x2424) + "書類の写しがあるか" + chr(0x2424) + "署名と押印があるか",
            "確認項目" + chr(10) + "書類の写しがあるか" + chr(10) + "署名と押印があるか",
        ),
        ("申請 受付から30日以内", "申請 " + _BAR + " 受付から30日以内"),
        ("申請書を提出する", "申請書" + _STRONG + "を提出する"),
        (
            "資料Aの第1章は、窓口で申請を受け付けると定めています。…14日以内に審査すると定めています。",
            "資料Aの第1章は、窓口で申請を受け付けると定めています。"
            + chr(10)
            + "資料Bの第2章は、申請は受付から14日以内に審査すると定めています。",
        ),
    ],
    ids=[
        "elided-repeated-phrase",
        "distant-heading-prefix",
        "joined-lines-with-elisions",
        "newline-control-picture",
        "table-syntax",
        "markdown-emphasis",
        "ellipsis-fragments",
    ],
)
def test_grounded_quote_aligns_near_verbatim_quotes_to_one_original_span(quote, expected):
    assert grounded_quote(quote, _SOURCE) == expected
    assert expected in _SOURCE


@pytest.mark.parametrize(
    "quote",
    [
        "資料Bの第2章は、申請は受付から15日以内に審査すると定めています。",
        "資料Bの第2章は、申請は受付から十四日以内に審査すると定めています。",
        "窓口の対応が継続していても同じ扱いになりますか。",
        "申請が受理されれば、受付から二週間程度で結果が通知されると定めています。",
        "資料Bの第2章は、申請を窓口で受け付けた日に却下すると定めています。",
        "14日以内に審査すると定めています。…資料Aの第1章は、窓口で申請を受け付けると定めています。",
        "申請書を返却する",
    ],
    ids=[
        "changed-digit",
        "digit-as-kanji-numeral",
        "question-text",
        "paraphrase",
        "changed-words",
        "fragments-out-of-order",
        "short-changed",
    ],
)
def test_grounded_quote_still_rejects_changed_numbers_and_text_not_in_the_source(quote):
    assert grounded_quote(quote, _SOURCE) == ""


def _scope_with(*unit_ids):
    return StandardAnswerScope(
        requirements=[
            StandardRequirement(standard_answer_unit_ids=list(ids), requirement="比較項目" + str(index))
            for index, ids in enumerate(unit_ids)
        ],
        excluded_case_data=[],
    )


def test_standard_answer_units_split_lines_sentences_tables_and_lists():
    text = "\n".join(
        [
            "## 手順",
            "",
            "- 画面Aを開く。保存を押す。",
            "- 確認する！続けますか？はい。",
            "| 項目 | 値。注記 |",
            "  ",
            "1. 「中の。文」を選ぶ。",
            "「引用の行。二つ目。」",
        ]
    )
    units = standard_answer_units(text)
    assert units == [
        {"id": "S1", "text": "## 手順"},
        {"id": "S2", "text": "- 画面Aを開く。"},
        {"id": "S3", "text": "保存を押す。"},
        {"id": "S4", "text": "- 確認する！"},
        {"id": "S5", "text": "続けますか？"},
        {"id": "S6", "text": "はい。"},
        {"id": "S7", "text": "| 項目 | 値。注記 |"},
        {"id": "S8", "text": "1. 「中の。文」を選ぶ。"},
        {"id": "S9", "text": "「引用の行。二つ目。」"},
    ]
    # 単位は原文のまま（並べると空白・改行を除いて元の本文になる）。
    assert all(unit["text"] in text for unit in units)
    assert "".join("".join(unit["text"].split()) for unit in units) == "".join(text.split())
    # 長い行は回答の段落と同じ字数で連続した範囲に分ける。
    assert [len(unit["text"]) for unit in standard_answer_units("あ" * 1300)] == [600, 600, 100]


def test_scope_items_quoting_the_question_are_dropped_and_counted(fixed_scope):
    fixed_scope.side_effect = _prepare_standard_scope
    data = payload() | dict(question="窓口の対応が継続していても同じ扱いになりますか。", standard_answer=_SOURCE)
    # 2 番目の項目は標準回答に無い ID だけを指す（質問の文を項目にした）。
    scope = _scope_with(["S4"], ["Q1"], ["S6"])
    with patch(
        PARSE,
        side_effect=[
            scope,
            output().model_copy(
                update=dict(
                    coverage_checks=[
                        CoverageCheck(requirement_index=1, status="addressed", answer_quote="回答"),
                        CoverageCheck(requirement_index=2, status="missing", answer_quote=""),
                    ]
                )
            ),
        ],
    ) as parse:
        result = evaluate_answer_payload(data, object())
    assert parse.call_count == 2 and result["status"] == "completed"
    quotes = [item["standard_answer_quote"] for item in result["standard_answer_scope"]["requirements"]]
    assert quotes == [
        "資料Aの第1章は、窓口で申請を受け付けると定めています。",
        "文書X_規程集の規程集第5条は、申請書の受付と審査の手順を定めています。",
    ]
    assert result["judge_diagnostics"]["standard_scope_dropped"] == 1
    assert result["judge_diagnostics"]["standard_scope_retries"] == 0
    # 外した数はシステムの診断。照合・監査のモデルには渡さない。
    judged = json.loads(parse.call_args_list[1].args[1])["standard_answer_scope"]
    assert "dropped_requirements" not in judged and len(judged["requirements"]) == 2
    assert "dropped_requirements" not in json.dumps(StandardAnswerScope.model_json_schema())
    # 照合・監査のモデルに渡す比較項目の形は変えない（単位の ID は渡さず、引用の原文を渡す）。
    assert [set(item) for item in judged["requirements"]] == [
        {"standard_answer_quote", "requirement", "relevance", "relevance_reason", "requirement_index"}
    ] * 2
    assert [item["standard_answer_quote"] for item in judged["requirements"]] == quotes
    stored = result["standard_answer_scope"]["requirements"]
    assert [item["standard_answer_unit_ids"] for item in stored] == [["S4"], ["S6"]]


def test_scope_with_mostly_unknown_unit_ids_is_retried_with_the_item_numbers():
    data = payload() | dict(question="窓口の対応が継続していても同じ扱いになりますか。", standard_answer=_SOURCE)
    bad = _scope_with(["Q1"], ["S4"], ["S99"])
    good = _scope_with(["S4"])
    with patch(PARSE, side_effect=[bad, good]) as parse:
        scope = _prepare_standard_scope(data, object(), None)
    assert parse.call_count == 2 and scope.dropped_requirements == 0
    feedback = json.loads(parse.call_args_list[1].args[1])["validation_feedback"]
    assert "1・3 番目" in feedback and "S1" in feedback and "S12" in feedback and "質問の文を項目にしない" in feedback


def test_scope_item_with_some_unknown_unit_ids_is_retried_with_the_item_number():
    # 一部の ID だけが単位に無い項目は外さず、項目の番号を指摘して出し直させる。
    data = payload() | dict(standard_answer=_SOURCE)
    bad = _scope_with(["S4"], ["S5", "S13"], ["S6"])
    good = _scope_with(["S4"], ["S5"], ["S6"])
    with patch(PARSE, side_effect=[bad, good]) as parse:
        scope = _prepare_standard_scope(data, object(), None)
    assert parse.call_count == 2 and scope.dropped_requirements == 0
    assert [item.standard_answer_unit_ids for item in scope.requirements] == [["S4"], ["S5"], ["S6"]]
    feedback = json.loads(parse.call_args_list[1].args[1])["validation_feedback"]
    assert "requirements の 2 番目" in feedback


def test_scope_schema_and_prompt_refer_to_unit_ids_only():
    from rag_engine.evaluation.answer_eval import SCOPE_PROMPT

    schema = StandardAnswerScope.model_json_schema()
    requirement = schema["$defs"]["StandardRequirement"]
    # モデルが返すのは単位の ID。引用の原文はシステムが単位から組み立てる（schema に出さない）。
    assert set(requirement["properties"]) == {"standard_answer_unit_ids", "requirement", "relevance", "relevance_reason"}
    assert requirement["required"] == ["standard_answer_unit_ids", "requirement"]
    assert requirement["properties"]["standard_answer_unit_ids"]["minItems"] == 1
    assert set(schema["properties"]) == {"requirements", "excluded_case_data"}
    # 指示は入力の単位（standard_answer_units）と schema の項目を名指しし、原文の写しを求めない。
    assert "standard_answer_units" in SCOPE_PROMPT and "standard_answer_unit_ids" in SCOPE_PROMPT
    assert "standard_answer_quote" not in SCOPE_PROMPT
    assert "質問の文を項目にしない" in SCOPE_PROMPT


def test_judge_input_budget_follows_the_model_input_limit():
    from types import SimpleNamespace

    from rag_engine.evaluation.answer_eval import evaluation_input_budget

    assert evaluation_input_budget(object()) == MAX_EVALUATION_INPUT_BYTES
    assert evaluation_input_budget(SimpleNamespace(answer_max_input_tokens=128_000)) == 128_000
    assert evaluation_input_budget(SimpleNamespace(answer_max_input_tokens=16_000)) == 16_000
    assert evaluation_input_budget(SimpleNamespace(answer_max_input_tokens=0)) == MAX_EVALUATION_INPUT_BYTES
    # 長い回答（固定入力が既定の上限を超える）も、モデルの上限の中なら 1 回で評価する。
    long_answer = "回答の段落。" * 1500
    data = payload() | dict(answer_text=long_answer)
    with patch(PARSE, return_value=output().model_copy(update=dict(claim_checks=[]))) as parse:
        small = evaluate_answer_payload(data, object())
        large = evaluate_answer_payload(data, SimpleNamespace(answer_max_input_tokens=128_000))
    assert small["status"] == "input_too_large"
    assert large["status"] == "completed" and large["batch_count"] == 1
    # 既定の上限では範囲の固定の前に止め、モデルの上限では 1 回の照合・監査で終える。
    assert parse.call_count == 1


def test_cumulative_evaluation_is_leaned_when_no_room_is_left_for_evidence():
    from collections import deque

    from rag_engine.evaluation.answer_eval import _MIN_EVIDENCE_ROOM_BYTES, _compact_previous, _next_batch

    claims = [
        ClaimCheck(
            answer_quote="回答",
            answer_passage_id="P" + str(i),
            status="supported",
            evidence_id="E1",
            source_id="",
            evidence_quote="",
            reason="理由" * 60,
        )
        for i in range(40)
    ]
    previous = _compact_previous(output().model_copy(update=dict(claim_checks=claims, evidence_summary="摘要" * 1500)))
    full = dict(previous_evaluation=previous, batch_index=2, is_final_batch=False, evidence_items=[])
    budget = _input_bytes(full) + _MIN_EVIDENCE_ROOM_BYTES // 2
    item = dict(id="c1", text="根拠の本文" * 400, fragment_start=0, fragment_end=2000)
    inputs = _next_batch(dict(), deque([item]), previous, 2, budget)
    sent = inputs["previous_evaluation"]
    assert len(sent["claim_checks"]) == 40 and all("reason" not in check for check in sent["claim_checks"])
    assert len(sent["evidence_summary"]) == 1000 and sent["summary_truncated"] is True
    assert inputs["evidence_items"] and _input_bytes(inputs) <= budget


def test_near_verbatim_quote_with_a_changed_word_is_stored_as_the_original_text():
    # 1 語だけ写し違えた長い引用は、原文の範囲に結び付けて原文を保存する（写し違えた語は記録に残らない）。
    quote = "資料Bの第2章は、申請は受付から14日以内に却下すると定めています。"
    assert grounded_quote(quote, _SOURCE) == "資料Bの第2章は、申請は受付から14日以内に審査すると定めています。"
