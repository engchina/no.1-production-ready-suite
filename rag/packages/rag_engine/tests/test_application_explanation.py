"""説明の段（#4393）の入力・提示文・表示の門を、実モデルの応答の記録の再生で確かめる（実サービスを呼ばない）。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import application_explanation as explanation
from rag_engine.generation.application_review import APPLICATION_AUDIT_SYSTEM_PROMPT
from rag_engine.generation.applications import CheckedApplication, render_lines
from rag_engine.models.audit_wire import audit_wire_schema
from rag_engine.models.llm import ApplicationAudit, GroundedApplication, GroundedAudit, GroundedFactAudit, ResponseLanguage

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "application_explanation_recorded.json").read_text(encoding="utf-8"))
CASES = {case["name"]: case for case in RECORDED["cases"]}
# 記録の題の語（合成）。提示文・schema・module に入れない（利用者の指示「不要硬编码」）。
CASE_STRINGS = (
    "項目A",
    "項目B",
    "SyntheticFeature",
    "SYNTHETIC_ITEMS",
    "SELECT",
    "English",
    "単価",
    "合計額",
    "multiple_rows",
    "quote_sql",
    "synthetic",
)


def gold_strings() -> set[str]:
    """記録の題の値（2 桁以上の数値・原文・結論）。"""
    values: set[str] = set(CASE_STRINGS)
    for case in RECORDED["cases"]:
        for text in [case["question"], *case["sources"], case["conclusion"], case["recorded_explanation"]]:
            values.update(run for run in explanation.number_runs(text) if len(run) >= 2)
        values.update(case["sources"])
        values.add(case["conclusion"])
    return values


def shipped_texts() -> dict[str, str]:
    texts: dict[str, str] = {}
    for instruction in (None, "回答言語の指定の原文"):
        texts[f"system:{instruction}"] = explanation.explanation_system(instruction)
        texts[f"schema:{instruction}"] = json.dumps(explanation.explanation_schema(instruction).model_json_schema(), ensure_ascii=False)
    texts["module"] = Path(explanation.__file__).read_text(encoding="utf-8")
    texts["audit_system"] = APPLICATION_AUDIT_SYSTEM_PROMPT
    for canonical in (ApplicationAudit, GroundedAudit, GroundedFactAudit):
        schema = audit_wire_schema(canonical, (), application_indexes=(0,))
        texts[f"audit_schema:{canonical.__name__}"] = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return texts


@pytest.mark.parametrize("name", sorted(shipped_texts()))
def test_shipped_prompts_and_schemas_contain_no_case_or_gold_strings(name):
    text = shipped_texts()[name]
    for needle in gold_strings():
        assert needle not in text, (name, needle)


@pytest.mark.parametrize("instruction", [None, "回答言語の指定の原文"])
def test_inputs_are_question_and_sources_only(instruction):
    prompt = json.loads(explanation.explanation_prompt("質問の原文", ["原文1", "原文2"], instruction))
    expected = {explanation.KEY_QUESTION: "質問の原文", explanation.KEY_SOURCES: ["原文1", "原文2"]}
    if instruction is not None:
        expected[explanation.KEY_LANGUAGE] = instruction
    assert prompt == expected
    system = explanation.explanation_system(instruction)
    assert system.startswith("## Step 1: 入力を読む") and "## Step 2: 説明を書く" in system and "## Step 3: 出力する" in system
    # 既定の契約はコードの既定の言語、明示の契約は入力の指定に従わせる（言語名の一覧を持たない）。
    assert (explanation.DEFAULT_LANGUAGE_LABEL in system) is (instruction is None)
    assert (explanation.KEY_LANGUAGE in system) is (instruction is not None)
    schema = explanation.explanation_schema(instruction).model_json_schema()
    assert schema["required"] == ["explanation"] and schema["additionalProperties"] is False


def test_number_runs_unicode_and_separators():
    assert explanation.number_runs("合計は 3,400円 です") == ["3400"]
    assert explanation.number_runs("３４００円") == ["3400"]
    assert explanation.number_runs("1、2 と 12,34") == ["1", "2", "12", "34"]
    assert explanation.number_runs("1,2345") == ["1", "2345"]
    assert explanation.number_runs("１，２３４と٣") == ["1234", "3"]
    assert explanation.number_runs("COUNT(*) を数える") == []


def gate(text, *, conclusion="結論は42個です。", sources=("原文の値は6と7です。",), question="値を掛けるといくつですか。", default=True):
    return explanation.display_gate(text, question=question, sources=list(sources), conclusion=conclusion, default_language=default)


def test_gate_requires_conclusion_numbers_kana_and_no_foreign_latin_run():
    assert gate("6と7を掛けると 6 × 7 = 42 です。")["status"] == "confirmed"
    assert gate("6と7を掛けると 6 × 7 = 142 です。")["reason"] == "conclusion_numbers_missing"
    assert gate("６と７を掛けると４２です。")["status"] == "confirmed"
    assert gate("6 × 7 = 42")["reason"] == "no_kana"
    assert gate("値は 42 です。The product of both values")["reason"] == "latin_run_not_in_inputs"
    assert gate("")["reason"] == "empty" and gate("   ")["reason"] == "empty" and gate(None)["reason"] == "empty"
    # 入力の原文にある Latin の連なり（識別子・SQL）は原文のまま書いてよい。
    sql = "COUNT ROWS FROM TABLE_X"
    assert gate(f"次を実行します。{sql}", conclusion="実行します。", sources=[sql])["status"] == "confirmed"
    # 明示の言語契約では文字種を検査しない（数値の検査は残す）。
    assert gate("Multiply 6 by 7 to get 42.", default=False)["status"] == "confirmed"
    assert gate("Multiply 6 by 7 to get 41.", default=False)["reason"] == "conclusion_numbers_missing"
    # 数値の無い結論は数値の検査の対象外。
    assert gate("手順のとおりに進めます。", conclusion="手順のとおりに進めます。")["status"] == "confirmed"


def test_gate_hides_text_that_would_be_read_as_a_citation_line():
    prefix = explanation.answer_labels().citation_prefix
    assert gate(f"6と7を掛けると42です。\n{prefix}原文")["reason"] == "citation_marker_in_text"


@pytest.mark.parametrize("name", sorted(CASES))
def test_recorded_v7_outputs_pass_the_display_gate(name):
    case = CASES[name]
    result = explanation.display_gate(
        case["recorded_explanation"],
        question=case["question"],
        sources=case["sources"],
        conclusion=case["conclusion"],
        default_language=case["instruction"] is None,
    )
    assert result["status"] == case["expected_status"] == "confirmed"


def rule(text):
    item = SimpleNamespace(kind="rule", applies="matched", text=text, quote=text)
    return SimpleNamespace(item=item, span={"text": text, "source": "synthetic.txt"}, quote_only=False, removed=False, state="")


def checked(case, *, valid=True, reasoning="The derivation written by the generation step."):
    entry = CheckedApplication(
        GroundedApplication(item_indexes=[0], reasoning=reasoning, result=case["conclusion"]), [rule(text) for text in case["sources"]]
    )
    entry.assessment = {"status": "valid" if valid else "invalid", "reason": "合成の判定"}
    if not valid:
        entry.reject("監査: 合成の判定")
    return entry


def language(case):
    if case["instruction"] is None:
        return ResponseLanguage()
    return ResponseLanguage(selection={"kind": "explicit", "quote": case["instruction"]})


@pytest.mark.parametrize("name", sorted(CASES))
def test_recorded_replay_through_explain_applications(name):
    case = CASES[name]
    entry = checked(case)
    calls = []

    def replay(system, prompt, settings, schema, **options):
        calls.append((system, json.loads(prompt)))
        return schema.model_validate({"explanation": case["recorded_explanation"]})

    trace = explanation.explain_applications(
        case["question"],
        [entry],
        language(case),
        parse_text=replay,
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )
    assert trace["calls"] == 1 and len(calls) == 1
    system, user = calls[0]
    assert user[explanation.KEY_QUESTION] == case["question"] and user[explanation.KEY_SOURCES] == case["sources"]
    assert user.get(explanation.KEY_LANGUAGE) == case["instruction"]
    # 審査済みの結論・生成の推導の文は入力に入れない。
    assert "result" not in json.dumps(user) and entry.application.reasoning not in json.dumps(user, ensure_ascii=False)
    if case["conclusion"] not in case["question"] + "".join(case["sources"]):
        assert case["conclusion"] not in json.dumps(user, ensure_ascii=False)
    assert entry.presentation["status"] == "confirmed"
    assert entry.presentation["reasoning"] == case["recorded_explanation"].strip()
    rendered = "\n".join(render_lines([entry], lambda _: "根拠：synthetic.txt", lambda _: None))
    assert case["recorded_explanation"].strip() in rendered and entry.application.reasoning not in rendered


def test_one_call_per_valid_application_and_none_for_invalid():
    case = CASES["multiple_rows-r1"]
    entries = [checked(case), checked(case, valid=False), checked(case)]
    calls = []

    def replay(system, prompt, settings, schema, **options):
        calls.append(prompt)
        return schema.model_validate({"explanation": case["recorded_explanation"]})

    trace = explanation.explain_applications(
        case["question"], entries, None, parse_text=replay, settings=get_settings(environ={}, dotenv_path=None), provider_id=None
    )
    assert trace["calls"] == len(calls) == 2
    assert [entry.presentation["status"] for entry in entries] == ["confirmed", "not_requested", "confirmed"]
    assert render_lines([entries[1]], lambda _: "根拠：synthetic.txt", lambda _: None) == []


@pytest.mark.parametrize("failure", ["hidden_by_gate", "call_error", "language_unconfirmed"])
def test_failure_hides_only_the_explanation(failure):
    case = CASES["multiple_rows-r1"]
    entry = checked(case)
    calls = []

    def replay(system, prompt, settings, schema, **options):
        calls.append(prompt)
        if failure == "call_error":
            raise RuntimeError("synthetic transport failure")
        # 既定の言語の契約で、かなを含まない英語の説明は門で隠れる。
        return schema.model_validate({"explanation": CASES["explicit_english-r1"]["recorded_explanation"]})

    response_language = (
        ResponseLanguage(selection={"kind": "explicit", "quote": "質問に無い指定"}) if failure == "language_unconfirmed" else None
    )
    explanation.explain_applications(
        case["question"],
        [entry],
        response_language,
        parse_text=replay,
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
    )
    expected = {"hidden_by_gate": "hidden", "call_error": "unrecorded", "language_unconfirmed": "hidden"}[failure]
    assert entry.presentation["status"] == expected
    assert len(calls) == (0 if failure == "language_unconfirmed" else 1)
    assert entry.published and entry.assessment["status"] == "valid"
    rendered = "\n".join(render_lines([entry], lambda _: "根拠：synthetic.txt", lambda _: None))
    assert case["conclusion"] in rendered and "根拠：synthetic.txt" in rendered
    assert "当てはめ" not in rendered and entry.application.reasoning not in rendered


def test_permission_errors_are_not_hidden_as_a_display_failure():
    case = CASES["multiple_rows-r1"]

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    with pytest.raises(PermissionError):
        explanation.explain_applications(
            case["question"],
            [checked(case)],
            None,
            parse_text=denied,
            settings=get_settings(environ={}, dotenv_path=None),
            provider_id=None,
        )


def test_multiline_markdown_is_kept_for_the_answer_text():
    case = CASES["quote_sql-r1"]
    entry = checked(case)

    def replay(system, prompt, settings, schema, **options):
        return schema.model_validate({"explanation": case["recorded_explanation"]})

    explanation.explain_applications(
        case["question"], [entry], None, parse_text=replay, settings=get_settings(environ={}, dotenv_path=None), provider_id=None
    )
    lines = render_lines([entry], lambda _: "根拠：synthetic.txt", lambda _: None)
    body = "\n".join(lines)
    assert "```\n" in body and body.index("当てはめ：") < body.index("```") < body.index("根拠：synthetic.txt")


# ---- 改行などの制御文字のエスケープの写し（#4567） -----------------------------------------------


def test_escaped_line_breaks_are_restored_when_the_inputs_have_none():
    # 合成の再現: モデルが JSON の文字列の中で改行を二重にエスケープし、逆斜線と n の 2 文字が説明に残る。
    escaped = "原文の値は6と7です。\\n6と7を掛けると 6 × 7 = 42 です。\\r\\n結論は42個です。\\t以上です。"
    result = gate(escaped)
    assert result["status"] == "confirmed"
    assert result["text"] == "原文の値は6と7です。\n6と7を掛けると 6 × 7 = 42 です。\n結論は42個です。\t以上です。"
    assert "\\" not in result["text"]


def test_escape_sequences_present_in_the_inputs_are_kept_verbatim():
    # 入力の原文に同じ形の逆斜線の連なりがあれば、原文の写し（パス・SQL の文字列など）と区別できないので変えない。
    sql = "SELECT REPLACE(COL_X, '\\n', ' ') FROM TABLE_X"
    text = f"次の SQL を原文のまま実行します。{sql}"
    result = gate(text, conclusion="実行します。", sources=[sql])
    assert result == {"status": "confirmed", "text": text}


def test_restored_line_breaks_are_checked_by_the_citation_line_rule():
    prefix = explanation.answer_labels().citation_prefix
    assert gate(f"6と7を掛けると42です。\\n{prefix}原文")["reason"] == "citation_marker_in_text"


def test_escaped_line_breaks_reach_the_answer_text_as_real_line_breaks():
    case = CASES["multiple_rows-r1"]
    entry = checked(case)
    escaped = case["recorded_explanation"].replace("です。", "です。\\n", 1)

    def replay(system, prompt, settings, schema, **options):
        return schema.model_validate({"explanation": escaped})

    explanation.explain_applications(
        case["question"], [entry], None, parse_text=replay, settings=get_settings(environ={}, dotenv_path=None), provider_id=None
    )
    assert entry.presentation["status"] == "confirmed"
    body = "\n".join(render_lines([entry], lambda _: "根拠：synthetic.txt", lambda _: None))
    assert "\\n" not in body and case["recorded_explanation"].replace("です。", "です。\n", 1).strip() in body
