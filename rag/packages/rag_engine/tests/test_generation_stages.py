"""複数の小問では計画と生成を分け、原文と質問の条件を保持する。"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import RequestScopePlan
from rag_engine.resources.runtime import bind_prompt_overrides

QUESTION = "同じ版の資料を使ってください。①項目Aの意味は？②項目Bの意味は？"
TEXTS = ["項目Aは入力を区別する識別子です。", "項目Bは処理の状態を示します。"]


def spans() -> list[dict[str, Any]]:
    result = [
        {
            "evidence_id": f"source-{index}",
            "label": f"E{index}",
            "text": text,
            "source": "synthetic.txt",
            "source_id": "doc-a",
            "page": 1,
            "function": ("synthetic.txt", "項目の定義"),
            "origin": "document_text",
            "tags": [],
        }
        for index, text in enumerate(TEXTS, 1)
    ]
    grounded.format_functions(result)
    return result


def run(question: str, parse: Any) -> grounded.Round:
    evidence = spans()
    return grounded.run_round(
        question,
        evidence,
        get_settings(environ={}, dotenv_path=None),
        prompt="原質問: " + question + "\n" + grounded.evidence_block_text(evidence),
        image_paths=(),
        provider_id=None,
        parse_text=parse,
        parse_images=parse,
        audit_mode="none",
    )


def plan(question: str = QUESTION) -> dict[str, Any]:
    return {
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_ids": ["E1", "E2"],
                "evidence_scope": [{"evidence_id": eid, "target": "項目", "relation": "direct"} for eid in ("E1", "E2")],
            }
        ]
    }


def draft(requests: list[str]) -> dict[str, Any]:
    return {
        "summary": "項目の定義を示します。",
        "confidence": "high",
        "items": [
            {
                "kind": "rule",
                "request_id": request_id,
                "text": text,
                "citations": [{"evidence_id": f"E{index}", "line_start": 1, "line_end": 1}],
            }
            for index, (request_id, text) in enumerate(zip(requests, TEXTS, strict=True), 1)
        ],
    }


def test_subquestions_are_planned_before_generation_with_original_context() -> None:
    calls: list[type] = []
    planned = plan()

    def parse(system: str, prompt: str, settings: Any, schema: type, **kwargs: Any) -> Any:
        calls.append(schema)
        if schema is RequestScopePlan:
            inputs = json.loads(prompt)
            assert inputs["question"] == QUESTION
            assert [row["text"] for row in inputs["evidence"]] == [f"1| {text}" for text in TEXTS]
            return RequestScopePlan.model_validate(planned)
        assert calls[0] is RequestScopePlan
        assert "request_coverage" not in schema.model_json_schema()["properties"]
        assert QUESTION in prompt and all(text in prompt for text in TEXTS)
        return schema.model_validate(draft(["Q1", "Q1"]))

    result = run(QUESTION, parse)
    assert len(calls) == 2 and result.audit_calls == 0
    assert [(entry.request_id, entry.evidence_ids) for entry in result.draft.request_coverage] == [("Q1", ["E1", "E2"])]
    assert [entry.item.text for entry in result.checked if not entry.added] == TEXTS
    assert result.timing["generation_calls"] == 2


def test_a_simple_question_keeps_one_generation_call() -> None:
    calls = []

    def parse(system: str, prompt: str, settings: Any, schema: type, **kwargs: Any) -> Any:
        calls.append(schema)
        return generation_draft_wire_model().model_validate({"summary": "", "confidence": "low", "items": []})

    run("項目Aの意味は？", parse)
    assert calls == [generation_draft_wire_model()]


@pytest.mark.parametrize("question", ["項目Aの意味は？", QUESTION], ids=["single-call", "separate-plan"])
def test_generation_keeps_source_coverage_after_planning(question: str) -> None:
    """本文に出典を付ける契約は、計画を別に作る経路でも実際の生成入力に残す。"""
    generation_calls = 0

    def parse(system: str, prompt: str, settings: Any, schema: type, **kwargs: Any) -> Any:
        nonlocal generation_calls
        if schema is RequestScopePlan:
            return RequestScopePlan.model_validate(plan())
        generation_calls += 1
        assert system.count("### 出典の網羅") == 1
        citation_phase = system.split("### 出典の網羅", 1)[1].split("###", 1)[0]
        assert "request_coverage" in citation_phase
        assert "文書ごと" in citation_phase
        assert "支えない原文" in citation_phase
        return schema.model_validate({"summary": "", "confidence": "low", "items": []})

    run(question, parse)
    assert generation_calls == 1


@pytest.mark.parametrize(
    "bad_plan",
    [
        {"request_coverage": []},
        {"request_coverage": [*plan()["request_coverage"], {"request_id": "foreign", "evidence_ids": ["E1"]}]},
        {"request_coverage": [{**row, "evidence_ids": ["unknown"]} for row in plan()["request_coverage"]]},
        {"request_coverage": [*plan()["request_coverage"], plan()["request_coverage"][0]]},
    ],
    ids=["missing", "foreign-request", "unknown-evidence", "duplicate-request"],
)
def test_an_invalid_plan_does_not_start_generation(bad_plan: dict[str, Any]) -> None:
    calls = []

    def parse(system: str, prompt: str, settings: Any, schema: type, **kwargs: Any) -> Any:
        calls.append(schema)
        assert schema is RequestScopePlan
        return RequestScopePlan.model_validate(bad_plan)

    with pytest.raises(grounded.DraftGenerationInvalid):
        run(QUESTION, parse)
    # 作り直しても検査に落ちれば生成へ進まない（作り直しは 1 回だけ。#4098）。
    assert calls == [RequestScopePlan, RequestScopePlan]


def test_each_generation_stage_uses_its_saved_prompt() -> None:
    with bind_prompt_overrides(
        {
            "generation.system": "## Step 1: 単純な質問の保存済み指示",
            "generation.plannedSystem": "## Step 1: 根拠計画後の保存済み指示",
        }
    ):
        assert grounded.generation_system_prompt(QUESTION) == "## Step 1: 根拠計画後の保存済み指示"
        assert grounded.generation_system_prompt("項目Aの意味は？") == "## Step 1: 単純な質問の保存済み指示"


LONG_TEXT = "項目Cは処理の条件を定めます。" * 80


def long_spans() -> list[dict[str, Any]]:
    result = [{**row, "text": LONG_TEXT if row["label"] == "E1" else row["text"]} for row in spans()]
    grounded.format_functions(result)
    return result


def run_long(parse: Any) -> grounded.Round:
    evidence = long_spans()
    assert len(evidence[0]["text"]) > 600
    return grounded.run_round(
        QUESTION,
        evidence,
        get_settings(environ={}, dotenv_path=None),
        prompt="原質問: " + QUESTION + "\n" + grounded.evidence_block_text(evidence),
        image_paths=(),
        provider_id=None,
        parse_text=parse,
        parse_images=parse,
        audit_mode="none",
        invalid_output=lambda exc: isinstance(exc, ValidationError),
    )


def test_a_valid_plan_with_long_evidence_reaches_generation() -> None:
    """#4091: 説明の字数の上限より長い根拠でも、有効な計画は実装の ValidationError で止まらず生成へ進む。"""
    calls: list[Any] = []

    def parse(system: str, prompt: str, settings: Any, schema: Any, **kwargs: Any) -> Any:
        calls.append(schema)
        if schema is RequestScopePlan:
            return RequestScopePlan.model_validate(plan())
        assert "項目Cは処理の条件を定めます。" in prompt and "要求ごとの根拠計画" in prompt
        return schema.model_validate(draft(["Q1", "Q1"]))

    result = run_long(parse)
    assert calls[0] is RequestScopePlan
    assert len(calls) == 2
    assert result.timing["generation_calls"] == 2


def test_invalid_plan_output_is_classified_as_a_draft_failure() -> None:
    """#4091: 生成の前の計画の出力が再実行の後も不合格なら、ほかの生成の出力と同じ失敗として送出する。"""

    def parse(system: str, prompt: str, settings: Any, schema: type, **kwargs: Any) -> Any:
        return RequestScopePlan.model_validate({})

    with pytest.raises(grounded.DraftGenerationInvalid) as raised:
        run_long(parse)
    assert str(raised.value) == "ValidationError"
    assert isinstance(raised.value.__cause__, ValidationError)


def test_plan_call_implementation_errors_are_not_classified() -> None:
    def parse(system: str, prompt: str, settings: Any, schema: type, **kwargs: Any) -> Any:
        raise KeyError("合成の実装の誤り")

    with pytest.raises(KeyError):
        run_long(parse)


def test_invalid_plan_failure_keeps_issue_counts_without_contents() -> None:
    """#4091: 計画の不整合は、どの検査に当たったかの件数だけを診断に残す。"""
    missing = {"request_coverage": [{**plan()["request_coverage"][0], "request_id": "Q9", "evidence_ids": ["unknown"]}]}

    def parse(system: str, prompt: str, settings: Any, schema: type, **kwargs: Any) -> Any:
        return RequestScopePlan.model_validate(missing)

    with pytest.raises(grounded.DraftGenerationInvalid) as raised:
        run_long(parse)
    detail = grounded.generation_failure_detail(raised.value)
    assert detail["category"] == "request_scope_invalid"
    assert detail["missing_request_count"] == 1
    assert detail["foreign_request_count"] == 1
    assert detail["unknown_evidence_label_count"] == 1
    assert detail["evidence_count"] == 2


def _scope_failures(parse_plans: list[Any]) -> tuple[Any, list[dict[str, Any]]]:
    """計画の応答を順に返す。生成まで進めば生成の応答を返す。呼び出しの入力を記録する。"""
    prompts: list[dict[str, Any]] = []
    plans = iter(parse_plans)

    def parse(system: str, prompt: str, settings: Any, schema: Any, **kwargs: Any) -> Any:
        if schema is RequestScopePlan:
            prompts.append(json.loads(prompt))
            value = next(plans)
            if isinstance(value, Exception):
                raise value
            return RequestScopePlan.model_validate(value)
        return schema.model_validate(draft(["Q1", "Q1"]))

    return parse, prompts


MISSING_PLAN = {"request_coverage": [{**plan()["request_coverage"][0], "request_id": "Q9", "evidence_ids": ["unknown"]}]}


def test_valid_plan_is_not_repaired_and_keeps_one_plan_call() -> None:
    """#4098: 通常の質問の呼び出しは増えない。"""
    parse, prompts = _scope_failures([plan()])
    result = run_long(parse)
    assert len(prompts) == 1
    assert "correction" not in prompts[0]
    assert "request_scope_repair" not in result.timing
    assert result.timing["generation_calls"] == 2


def test_invalid_plan_is_repaired_once_with_the_findings_and_reaches_generation() -> None:
    """#4098: 検査に落ちた計画だけ、指摘を付けて 1 回作り直し、同じ検査を通れば生成へ進む。"""
    parse, prompts = _scope_failures([MISSING_PLAN, plan()])
    result = run_long(parse)

    assert len(prompts) == 2
    assert "correction" not in prompts[0]
    correction = prompts[1]["correction"]
    assert correction["previous_plan"]["request_coverage"][0]["request_id"] == "Q9"
    findings = "\n".join(correction["invalid_request_plan"])
    assert "Q1" in findings and "Q9" in findings and "unknown" in findings
    assert result.timing["generation_calls"] == 3
    repair = result.timing["request_scope_repair"]
    assert repair["calls"] == 1
    assert repair["outcome"] == "repaired"
    assert repair["initial_issues"]["missing_request_count"] == 1


def test_a_repair_that_is_still_invalid_stops_without_a_third_plan_and_keeps_the_counts() -> None:
    """#4098: 作り直しても検査に落ちれば生成へ進まない（検査を緩めない）。作り直しは 1 回だけ。"""
    parse, prompts = _scope_failures([MISSING_PLAN, MISSING_PLAN, plan()])
    with pytest.raises(grounded.DraftGenerationInvalid) as raised:
        run_long(parse)

    assert len(prompts) == 2
    assert str(raised.value) == "request_scope_invalid"
    detail = grounded.generation_failure_detail(raised.value)
    assert detail["missing_request_count"] == 1
    assert detail["plan_repair"]["calls"] == 1
    assert detail["plan_repair"]["initial_issues"]["foreign_request_count"] == 1
    assert "outcome" not in detail["plan_repair"]


def test_a_repair_whose_output_is_invalid_falls_back_with_the_first_counts() -> None:
    """#4098: 作り直しの出力が契約を満たさなければ、前の計画の検査の結果で同じ退避へ進む。"""
    with pytest.raises(ValidationError) as schema_error:
        RequestScopePlan.model_validate({})
    parse, prompts = _scope_failures([MISSING_PLAN, schema_error.value])
    with pytest.raises(grounded.DraftGenerationInvalid) as raised:
        run_long(parse)

    assert len(prompts) == 2
    detail = grounded.generation_failure_detail(raised.value)
    assert detail["foreign_request_count"] == 1
    assert detail["plan_repair"]["error"] == "ValidationError"


def test_a_repair_implementation_error_is_not_classified() -> None:
    parse, _prompts = _scope_failures([MISSING_PLAN, KeyError("合成の実装の誤り")])
    with pytest.raises(KeyError):
        run_long(parse)


def _linked_plan(link: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_ids": ["E1"],
                "evidence_scope": [{"evidence_id": "E1", "target": "項目", "relation": "linked", "link": link}],
            }
        ]
    }


def test_code_cuts_the_link_quote_from_the_numbered_range_and_the_input_is_numbered() -> None:
    """#4098: linked の原文はコードが番号から切り出す。計画に原文の欄は無く、原文と番号は食い違わない。"""
    prompts: list[dict[str, Any]] = []
    generated: list[str] = []

    def parse(system: str, prompt: str, settings: Any, schema: Any, **kwargs: Any) -> Any:
        if schema is RequestScopePlan:
            prompts.append(json.loads(prompt))
            return RequestScopePlan.model_validate(_linked_plan({"evidence_id": "E2", "line_start": 1, "line_end": 1}))
        generated.append(prompt)
        return schema.model_validate(draft(["Q1", "Q1"]))

    current = run(QUESTION, parse)
    assert [row["text"] for row in prompts[0]["evidence"]] == [f"1| {text}" for text in TEXTS]
    assert "link_quote" not in json.dumps(RequestScopePlan.model_json_schema())
    scope = current.draft.request_coverage[0].evidence_scope[0]
    assert (scope.relation, scope.link_quote) == ("linked", TEXTS[1])
    # 生成へは番号の形のまま渡し、原文を重ねない。
    assert '"line_start":1' in generated[0] and "link_quote" not in generated[0].split("要求ごとの根拠計画:")[1]


@pytest.mark.parametrize(
    "link",
    [
        None,
        {"evidence_id": "E2", "line_start": 9, "line_end": 9},
        {"evidence_id": "E2", "line_start": 1, "line_end": 0},
        {"evidence_id": "E9", "line_start": 1, "line_end": 1},
    ],
    ids=["no_range", "out_of_bounds", "inverted", "unknown_evidence"],
)
def test_invalid_linked_range_is_a_finding_and_is_repaired(link: dict[str, Any] | None) -> None:
    """#4098: 無効な範囲を推測で直さず、関係の原文が無い指摘として 1 回作り直す。"""
    parse, prompts = _scope_failures([_linked_plan(link), plan()])
    result = run_long(parse)
    assert len(prompts) == 2
    assert result.timing["request_scope_repair"]["initial_issues"]["plan_error_count"] == 1
    assert any("linked" in finding for finding in prompts[1]["correction"]["invalid_request_plan"])
    assert result.timing["request_scope_repair"]["outcome"] == "repaired"


def test_unrelated_evidence_in_the_plan_is_a_finding_and_the_repaired_plan_is_used() -> None:
    """#4098: 直した計画だけが生成へ渡る。"""
    wrong = plan()
    wrong["request_coverage"][0]["evidence_scope"][0]["relation"] = "unrelated"
    right = plan()
    generated: list[str] = []
    prompts: list[dict[str, Any]] = []
    plans = iter([wrong, right])

    def parse(system: str, prompt: str, settings: Any, schema: Any, **kwargs: Any) -> Any:
        if schema is RequestScopePlan:
            prompts.append(json.loads(prompt))
            return RequestScopePlan.model_validate(next(plans))
        generated.append(prompt)
        return schema.model_validate(draft(["Q1", "Q1"]))

    result = run_long(parse)
    assert result.timing["request_scope_repair"]["initial_issues"]["plan_error_count"] == 1
    assert any("E1" in finding for finding in prompts[1]["correction"]["invalid_request_plan"])
    assert RequestScopePlan.model_validate(right).model_dump_json() in generated[0]
    assert RequestScopePlan.model_validate(wrong).model_dump_json() not in generated[0]
