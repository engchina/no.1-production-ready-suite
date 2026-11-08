"""表の行の判定の段を、回答の経路（synthesize_grounded_answer）と説明の段に通す（#4349）。

- 送った根拠に投影できる表があれば、推導（applications）の有無に関わらず段を動かす（推導が無い回答でも行の判定を残す）。
- 説明の段に行の判定（表の行の判定）を渡し、説明の文が確かめた判定と食い違う入力にしない。

LLM は合成の題の録画（xai.grok-4.7・low）と決定論の stub だけ。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from application_verdict_stub import audit_reply, is_application_schema
from audit_fixtures import synthetic_explanation
from citation_unit_support import canonical_fixture
from test_grounded_answer import context, draft, ids, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import application_explanation as explanation
from rag_engine.generation import row_conditions as rc
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.applications import CheckedApplication
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import GroundedApplication, GroundedDraft

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "row_conditions_recorded.json").read_text(encoding="utf-8"))
CASE = FIXTURE["cases"]["reg-period-reversed"]
QUESTION = CASE["question"]
TABLE_TEXT = CASE["evidence_text"]
FUNCTION = "合成資料-値と規則"


def recorded(call: str) -> str:
    return next(
        row["output_text"]
        for row in FIXTURE["recorded"]
        if (row["round"], row["case_id"], row["call"]) == ("reg", "reg-period-reversed", call)
    )


def settings(*, step_model: bool = True) -> Any:
    """段のモデル（``step_model=False`` は登録していないモデル: backend は model だけを渡す）。"""
    environ = {
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": "answer-model",
        "RAG_ENGINE_ANSWER_AUDIT_MODE": "none",
        "RAG_ENGINE_ROW_CONDITION_MODEL": "xai.grok-4.7",
        "RAG_ENGINE_ROW_CONDITION_REASONING_EFFORT_OVERRIDE": "low",
    }
    if step_model:
        environ.update(
            {
                "RAG_ENGINE_ROW_CONDITION_ENDPOINT": "https://row.invalid/v1",
                "RAG_ENGINE_ROW_CONDITION_API_KEY": "synthetic-row",
                "RAG_ENGINE_ROW_CONDITION_REASONING_EFFORT": "low",
            }
        )
    return get_settings(environ=environ, dotenv_path=None)


class Model:
    """生成・監査・説明・行の判定の schema ごとに決定論の応答を返し、呼び出しを記録する。"""

    def __init__(self, generated: dict[str, Any]) -> None:
        self.generated = generated
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        name = getattr(schema, "__name__", "")
        self.calls.append((name, system, prompt))
        if name == "RowConditionType":
            return schema.model_validate_json(recorded("type"))
        if name == "RowApplicability":
            return schema.model_validate_json(recorded("judge"))
        if explanation.is_explanation_schema(schema):
            return synthetic_explanation(schema, prompt)
        if is_application_schema(schema):
            return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt)
        if issubclass(schema, generation_draft_wire_model()) or schema is GroundedDraft:
            return canonical_fixture(self.generated)
        raise AssertionError(f"想定外の schema: {name}")


def answer(generated_for: Any, *, step_model: bool = True) -> tuple[Any, Model]:
    ctx = context(record(0, TABLE_TEXT, FUNCTION, source="synthetic-period.md"))
    model = Model(generated_for(ids(ctx, QUESTION)[TABLE_TEXT]))

    def unused(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unexpected I/O")

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = synthesize_grounded_answer(QUESTION, ctx, settings(step_model=step_model), image_prompt_mode="text_only")
    return result.response, model


def no_derivation(_eid: str) -> dict[str, Any]:
    """生成が「資料に記載なし」として推導を作らなかった草稿。"""
    return draft({"kind": "gap", "request_id": "Q1", "text": "資料からは割合を確認できません。"}, summary="")


def with_derivation(eid: str) -> dict[str, Any]:
    quote = TABLE_TEXT.splitlines()[2]
    value = draft({"kind": "rule", "request_id": "Q1", "text": quote, "citations": [{"evidence_id": eid, "quote": quote}]}, summary="")
    value["applications"] = [
        {
            "request_id": "Q1",
            "premises": ["前営業日に行った"],
            "item_indexes": [0],
            "reasoning": "表の行を当てはめる。",
            "result": "割合は 50% です。",
        }
    ]
    return value


def test_row_step_runs_when_a_sent_table_exists_even_without_derivations() -> None:
    result, model = answer(no_derivation)
    names = [name for name, _system, _prompt in model.calls]
    assert names.count("RowConditionType") == 1 and names.count("RowApplicability") == 1
    trace = result.generation_trace["row_conditions"]
    assert trace["status"] == "completed"
    assert [table["table_result"] for table in trace["tables"]] == ["determined"]
    assert not result.generation_trace["applications"]


def test_unavailable_step_model_is_recorded_even_without_derivations() -> None:
    result, model = answer(no_derivation, step_model=False)
    assert not [name for name, _s, _p in model.calls if name.startswith("Row")]
    assert result.generation_trace["row_conditions"]["status"] == "model_unavailable"


def test_answer_without_a_sent_table_does_not_open_the_row_step() -> None:
    ctx_text = "合成資料\n手続Yの申請は受付日から10日以内に行います。"
    ctx = context(record(0, ctx_text, FUNCTION, source="synthetic-control.md"))
    model = Model(draft({"kind": "gap", "request_id": "Q1", "text": "確認できません。"}, summary=""))

    def unused(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unexpected I/O")

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        result = synthesize_grounded_answer("手続Yの申請期限を教えてください。", ctx, settings(), image_prompt_mode="text_only").response
    assert "row_conditions" not in result.generation_trace
    assert not [name for name, _s, _p in model.calls if name.startswith("Row")]


def test_answer_passes_row_judgments_to_the_explanation_stage() -> None:
    result, model = answer(with_derivation)
    prompts = [json.loads(prompt) for name, _system, prompt in model.calls if name == "ApplicationExplanation"]
    assert prompts, [name for name, _s, _p in model.calls]
    assert prompts[0][explanation.ROW_KEYS["applies"]] == ["操作Aを2営業日前の17時より後に行った"]
    assert prompts[0][explanation.ROW_KEYS["does_not_apply"]] == ["操作Aを2営業日前の17時までに行った"]


# ---------------------------------------------------------------- 説明の段の入力（単体）


def _table_result() -> rc.RowConditionsResult:
    from test_row_conditions import Replay, row_settings, table_for

    return rc.evaluate_tables(
        QUESTION, [table_for("reg-period-reversed")], parse_text=Replay([recorded("type"), recorded("judge")]), settings=row_settings()
    )


def _entry(evidence_id: str) -> CheckedApplication:
    quote = TABLE_TEXT.splitlines()[2]
    item = SimpleNamespace(
        item=SimpleNamespace(kind="rule", applies="matched", text=quote, quote=quote, condition=""),
        span={"evidence_id": evidence_id},
        quote_only=False,
        removed=False,
        state="",
    )
    entry = CheckedApplication(GroundedApplication(request_id="Q1", item_indexes=[0], reasoning="r", result="割合は 50% です。"), [item])
    entry.assessment = {"status": "valid"}
    return entry


def _explain(entry: CheckedApplication, result: rc.RowConditionsResult | None) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def parse(system: str, prompt: str, call_settings: Any, schema: Any, **options: Any) -> Any:
        seen.append((system, prompt))
        return schema.model_validate({"explanation": "前営業日は2営業日前の17時より後なので、割合は 50% です。"})

    explanation.explain_applications(
        QUESTION,
        [entry],
        None,
        parse_text=parse,
        settings=get_settings(environ={}, dotenv_path=None),
        provider_id=None,
        row_conditions=result,
    )
    return seen


def test_explanation_input_carries_the_row_judgments_of_the_entry_table() -> None:
    result = _table_result()
    [(system, prompt)] = _explain(_entry(result.tables[0].evidence_id), result)
    data = json.loads(prompt)
    assert data[explanation.KEY_QUESTION] == QUESTION
    assert data[explanation.ROW_KEYS["applies"]] == ["操作Aを2営業日前の17時より後に行った"]
    assert data[explanation.ROW_KEYS["does_not_apply"]] == ["操作Aを2営業日前の17時までに行った"]
    assert explanation.ROW_KEYS["cannot_determine"] not in data
    # 送った行の節だけを Step 1 が読み方として示す。行の節は説明の語にしてよい（門の内部の名前ではない。#4615）。
    step1, _step2 = system.split("## Step 2:")
    assert explanation.ROW_KEYS["applies"] in step1 and explanation.ROW_KEYS["cannot_determine"] not in system
    assert explanation.internal_labels(data) == []


def test_explanation_input_is_unchanged_without_row_judgments() -> None:
    entry = _entry("E-other")
    [(system, prompt)] = _explain(entry, None)
    assert prompt == explanation.explanation_prompt(QUESTION, explanation.application_sources(entry), None)
    assert system == explanation.explanation_system(None)
    # 推導が使わない表の判定は渡さない。
    result = _table_result()
    [(system, prompt)] = _explain(_entry("E-other"), result)
    assert not set(explanation.ROW_KEYS.values()) & set(json.loads(prompt)) and system == explanation.explanation_system(None)
