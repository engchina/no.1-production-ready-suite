"""標準回答による回答の評価（主張ごとの正確性と要点の網羅の判定 1 回）と、その保存。"""

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from rag_engine.adapters.oci import InvalidStructuredOutput
from rag_engine.config import get_settings
from rag_engine.evaluation.answer_eval import (
    CORRECTNESS_PROMPT,
    MAX_EVALUATION_INPUT_BYTES,
    RUBRIC_VERSION,
    AnswerCorrectnessOutput,
    evaluate_answer_payload,
    evaluation_input_budget,
    scored_answer_text,
)
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answering import AnswerQuestionResult
from rag_engine.knowledge.answers import save_answer_result

PARSE = "rag_engine.evaluation.answer_eval.parse_text_response"


def payload():
    return {
        "question": "今回の取込失敗の原因は？",
        "standard_answer": "ログを確認して原因を特定する。",
        "answer_text": "ログを確認して原因を特定します。\n根拠：manual.pdf p.3",
        "evidence_items": [{"id": "c1", "text": "障害の原因はログを確認して特定する。", "source": "manual.pdf"}],
    }


def verdict(value="supported"):
    """判定の出力の偽物（入力のすべての要点を覆い、最初の段落を 1 つの主張にする）。"""

    def fake(system, prompt, settings, schema, **options):
        inputs = json.loads(prompt)
        claims = [{"answer_ids": ["A1"], "verdict": value, "standard_ids": [] if value == "not_in_standard" else ["S1"]}]
        points = [{"point_id": point["id"], "covered": True} for point in inputs["points"]]
        return AnswerCorrectnessOutput.model_validate({"claims": claims, "points": points})

    return fake


def broken_output():
    """adapter が構造化出力として読めなかった応答（JSON が閉じない・必須項目の欠落）。"""
    return InvalidStructuredOutput("構造化出力の JSON が閉じていません。")


def generated_answer(**fields):
    return AnswerQuestionResult(
        answer="回答", answer_text="回答", original_question="質問", question_display="質問", primary_source_run_id="run1", **fields
    )


@pytest.mark.parametrize(("value", "score"), [("supported", 1.0), ("contradicted", 0.0), ("not_in_standard", 0.0)])
def test_one_call_judges_claims_from_question_standard_and_answer_body(value, score):
    with patch(PARSE, side_effect=verdict(value)) as parse:
        result = evaluate_answer_payload(payload(), object(), provider_id="chosen")
    assert parse.call_count == 1
    system, prompt, _settings, schema = parse.call_args.args
    assert system == CORRECTNESS_PROMPT and schema is AnswerCorrectnessOutput
    assert parse.call_args.kwargs["provider_id"] == "chosen"
    # 渡すのは質問・標準回答の文・要点・出典の行を除いた回答の本文の段落だけ（取得した根拠は渡さない）。
    assert json.loads(prompt) == {
        "question": "今回の取込失敗の原因は？",
        "standard_answer": [{"id": "S1", "text": "ログを確認して原因を特定する。"}],
        "points": [{"id": "P1", "text": "ログを確認して原因を特定する。"}],
        "actual_responses": [
            {"question": "今回の取込失敗の原因は？", "answer": [{"id": "A1", "text": "ログを確認して原因を特定します。"}]}
        ],
    }
    standard = "" if value == "not_in_standard" else "ログを確認して原因を特定する。"
    assert result == {
        "rubric_version": RUBRIC_VERSION,
        "external_data_required": None,
        "external_data_items": [],
        "judge_diagnostics": {"judge_calls": 1, "judge_retries": 0, "retry_reasons": []},
        "answer_correctness": score,
        "answer_coverage": 1.0,
        "correctness_reason": {
            "supported": "標準回答と矛盾する主張はありません。",
            "contradicted": "回答の「ログを確認して原因を特定します。」と標準回答の「ログを確認して原因を特定する。」が食い違う。",
            "not_in_standard": "標準回答と合う主張がありません（標準回答に具体的な答えが無いか、回答が質問に答えていません）。",
        }[value],
        "correctness_claims": [{"verdict": value, "answer": "ログを確認して原因を特定します。", "standard": standard}],
        "coverage_points": [{"point": "ログを確認して原因を特定する。", "covered": True}],
        "status": "completed",
        "message": "",
    }


def test_no_standard_answer_skips_network_and_keeps_external_status():
    with patch(PARSE) as parse:
        result = evaluate_answer_payload(
            {"standard_answer": "  ", "external_data_required": True, "external_data_items": ["ログ"]}, object()
        )
    parse.assert_not_called()
    assert result["status"] == "no_standard_answer"
    assert result["external_data_items"] == ["ログ"]


@pytest.mark.parametrize("error", ["schema", "invalid_json"])
def test_invalid_output_is_not_retried_by_the_judge_layer(error):
    if error == "schema":
        with pytest.raises(ValidationError) as raised:
            AnswerCorrectnessOutput.model_validate({"comparisons": "invalid", "uncovered_points": [], "reason": "理由"})
        first = raised.value
    else:
        first = broken_output()
    with patch(PARSE, side_effect=first) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 1
    assert result["status"] == "error"
    assert result["judge_diagnostics"]["judge_retries"] == 0


def test_transport_error_is_not_retried_by_the_judge():
    # 通信・時間切れの再試行は adapter が持つ。判定の層は出力の誤りだけを出し直させる。
    with patch(PARSE, side_effect=TimeoutError("timeout")) as parse:
        result = evaluate_answer_payload(payload(), object())
    assert parse.call_count == 1 and result["status"] == "error"
    assert result["judge_diagnostics"]["judge_retries"] == 0


def test_expired_deadline_stops_the_judge_call():
    from rag_engine.resources.deadline import Deadline, bind_deadline

    with patch(PARSE) as parse, bind_deadline(Deadline(0)):
        result = evaluate_answer_payload(payload(), object())
    parse.assert_not_called()
    assert result["status"] == "error" and result["error_type"] == "DeadlineExceeded"


def test_input_limit_follows_the_judge_model_and_oversized_input_skips_network():
    assert evaluation_input_budget(object()) == MAX_EVALUATION_INPUT_BYTES
    assert evaluation_input_budget(SimpleNamespace(answer_max_input_tokens=128_000)) == 128_000
    assert evaluation_input_budget(SimpleNamespace(answer_max_input_tokens=0)) == MAX_EVALUATION_INPUT_BYTES
    # 既定の上限（48,000 bytes）を超え、128,000 tokens のモデルの上限（128,000 bytes）に収まる長さ
    # （要点が無いので標準回答の文は要点としても渡る）。
    data = payload() | dict(standard_answer="長" * (MAX_EVALUATION_INPUT_BYTES // 5))
    with patch(PARSE, side_effect=verdict()) as parse:
        small = evaluate_answer_payload(data, object())
        assert parse.call_count == 0
        large = evaluate_answer_payload(data, SimpleNamespace(answer_max_input_tokens=128_000))
    assert small["status"] == "input_too_large" and "範囲を絞" in small["message"]
    assert small["judge_diagnostics"]["judge_calls"] == 0
    assert large["status"] == "completed"


def test_scored_answer_text_drops_generated_sections_notes_and_citation_lines():
    labels = answer_labels()
    answer = (
        "結論です。\n\n・説明です。\n根拠：m.pdf p.1\n\n"
        f"{labels.quote_only_section}\n{labels.quote_only_note}\n・原文の行です。\n\n"
        f"{labels.unverified_section}\n\n・「m.pdf」の「第2章」は未反映です。\n\n"
        f"{labels.deadline_section}\n\n・作り直しをしていません。"
    )
    assert scored_answer_text(answer) == "結論です。\n・説明です。\n・原文の行です。"


def test_successful_evaluation_is_saved_and_keeps_generation_external_state():
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp))
        result = generated_answer(external_data_required=True, external_data_items=("実行ログ",))
        with patch(PARSE, side_effect=verdict()) as parse:
            saved = save_answer_result(result, settings, standard_answer="標準", evaluation_provider="chosen")
        stored = json.loads((Path(tmp) / "run1" / "answers" / f"{saved.answer_id}.json").read_text())
        assert stored["evaluation"]["status"] == "completed"
        assert stored["evaluation"]["answer_correctness"] == 1.0
        assert stored["external_data_items"] == stored["evaluation"]["external_data_items"] == ["実行ログ"]
        assert stored["needs_human_review"] is True
        assert parse.call_args.kwargs["provider_id"] == "chosen"


def test_error_does_not_prevent_answer_persistence_or_leak_details():
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp))
        with patch(PARSE, side_effect=RuntimeError("private connection details")):
            saved = save_answer_result(generated_answer(), settings, standard_answer="標準")
        stored = json.loads((Path(tmp) / "run1" / "answers" / f"{saved.answer_id}.json").read_text())
        assert stored["answer_text"] == "回答"
        assert stored["evaluation"]["status"] == "error"
        assert "private connection details" not in json.dumps(stored)


@pytest.mark.parametrize("status", ["no_standard_answer", "error", "input_too_large"])
def test_unfinished_evaluation_preserves_original_review_state(status):
    with TemporaryDirectory() as tmp:
        settings = replace(get_settings(), output_dir=Path(tmp))
        generated = generated_answer(external_data_required=True, external_data_items=("実行ログ",), needs_human_review=True)
        with patch("rag_engine.evaluation.answer_eval.evaluate_answer_payload", return_value={"status": status}):
            saved = save_answer_result(generated, settings, standard_answer="標準")
        assert saved.payload["needs_human_review"] is True
        assert saved.payload["external_data_items"] == ["実行ログ"]
