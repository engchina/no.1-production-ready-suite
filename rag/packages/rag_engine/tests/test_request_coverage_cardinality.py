"""要求ごとの根拠の計画（request_coverage）は、その回の要求 1 つにつき 1 件だけを受ける（#4587）。

同じ要求の計画を言い回しを変えて繰り返す出力（合成の録画応答）を、構造で受けない。
"""

import json
from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_parts_wire import answer_parts_model
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog, unit_scope_model
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.generation.grounded import generation_output_model, generation_request_ids
from rag_engine.models.llm import RequestScopePlan

TEXT = "項目Aは12です。"
QUESTION = "項目Aの値を教えてください。"


def catalog() -> tuple[SentUnitCatalog, EvidenceUnitSnapshot]:
    span = {"label": "E1", "evidence_id": "source-a", "source_id": "s-a", "source": "synthetic.txt", "text": TEXT}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(TEXT))], "explicit_units_v5")
    return SentUnitCatalog([snapshot]), snapshot


def plan(request_id: str, target: str = "項目A") -> dict[str, Any]:
    return {"request_id": request_id, "evidence_scope": [{"evidence_id": "E1", "target": target, "relation": "direct", "link": None}]}


def draft(coverage: list[dict[str, Any]], snapshot: EvidenceUnitSnapshot) -> dict[str, Any]:
    return {
        "answer_parts": [
            {
                "request_id": "Q1",
                "requirement": "項目Aの値",
                "answer": {"kind": "source", "basis": {"unit_ids": list(snapshot.unit_ids)}},
            }
        ],
        "summary_part_indexes": [0],
        "request_coverage": coverage,
        "confidence": "high",
    }


def coverage_schema(model: Any) -> dict[str, Any]:
    schema = model.model_json_schema()
    field = schema["properties"]["request_coverage"]
    item = schema["$defs"][field["items"]["$ref"].rsplit("/", 1)[1]]
    return {"field": field, "request_id": item["properties"]["request_id"]}


def test_generation_schema_bounds_coverage_to_the_request_ids():
    units, _ = catalog()
    ids = generation_request_ids(QUESTION)
    assert ids == ("Q1",)
    schema = coverage_schema(generation_output_model(QUESTION, unit_catalog=units))
    assert schema["field"]["minItems"] == schema["field"]["maxItems"] == len(ids)
    assert schema["request_id"].get("enum", [schema["request_id"].get("const")]) == list(ids)


def test_recorded_repetition_of_one_request_is_rejected():
    """要求が 1 つの質問で、同じ request_id の計画を target だけ変えて繰り返した出力（録画の形の合成）。"""
    units, snapshot = catalog()
    model = generation_output_model(QUESTION, unit_catalog=units)
    repeated = [plan("Q1", f"項目Aの記録値（言い換え{n}）") for n in range(6)]
    with pytest.raises(ValidationError):
        model.model_validate(draft(repeated, snapshot))


def test_one_plan_per_request_passes_and_reaches_the_canonical_draft():
    units, snapshot = catalog()
    model = generation_output_model(QUESTION, unit_catalog=units)
    canonical = canonical_generation_draft(model.model_validate(draft([plan("Q1")], snapshot)), unit_catalog=units)
    assert [entry.request_id for entry in canonical.request_coverage] == ["Q1"]
    assert canonical.request_coverage[0].evidence_ids == ["E1"]


@pytest.mark.parametrize(
    "coverage",
    [[], [{"request_id": "Q2", "evidence_scope": []}]],
    ids=["missing", "foreign-id"],
)
def test_missing_or_foreign_plan_is_rejected(coverage):
    units, snapshot = catalog()
    model = generation_output_model(QUESTION, unit_catalog=units)
    with pytest.raises(ValidationError):
        model.model_validate(draft(coverage, snapshot))


def test_several_requests_take_one_plan_each_and_reject_a_repeated_id():
    units, snapshot = catalog()
    model = answer_parts_model(generation_draft_wire_model(), units, request_ids=("Q1", "Q2"))
    assert coverage_schema(model)["field"]["maxItems"] == 2
    model.model_validate(draft([plan("Q1"), plan("Q2")], snapshot))
    with pytest.raises(ValidationError, match="Q1"):
        model.model_validate(draft([plan("Q1"), plan("Q1", "項目Aの値")], snapshot))


def test_scope_plan_schema_takes_one_plan_per_request():
    units, _ = catalog()
    model = unit_scope_model(RequestScopePlan, units, request_ids=("Q1",))
    schema = coverage_schema(model)
    assert schema["field"]["minItems"] == schema["field"]["maxItems"] == 1
    model.model_validate({"request_coverage": [plan("Q1")]})
    with pytest.raises(ValidationError):
        model.model_validate({"request_coverage": [plan("Q1"), plan("Q1", "別の言い方")]})


def test_generation_call_sends_the_bounded_coverage_schema():
    """実際の生成の呼び出しに渡す schema も、要求の数で絞る。"""
    captured: list[Any] = []

    def parse(system, prompt, config, schema, **options):
        captured.append(schema)
        raise RuntimeError("schema を確かめたら止める")

    def unused(*args, **kwargs):
        raise AssertionError("不要な外部呼出")

    record = AnswerRecord(
        id="source-a",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=TEXT,
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="source-a",
        source_run_id="synthetic",
        metadata={},
    )
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)
    with (
        bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)),
        pytest.raises(RuntimeError, match="schema を確かめたら止める"),
    ):
        synthesize_grounded_answer(QUESTION, AnswerContext(records=[record], text=TEXT), settings, image_prompt_mode="text_only")
    generation = next(schema for schema in captured if "request_coverage" in schema.model_json_schema()["properties"])
    field = coverage_schema(generation)["field"]
    assert field["minItems"] == field["maxItems"] == 1


def test_recorded_repetitive_output_is_rejected_and_a_single_plan_recording_passes():
    """#4586 の録画応答（合成の判定表と区分の規則。要求は Q1 だけ）を、生成の schema で検証し直す。"""
    from test_source_only_derivation import FIXTURE, RULE, TABLE
    from test_source_only_derivation import QUESTION as TABLE_QUESTION

    table = {"label": "E1", "evidence_id": "table-a", "source_id": "s-t", "source": "synthetic-table.md", "text": TABLE}
    rule = {"label": "E2", "evidence_id": "rule-a", "source_id": "s-r", "source": "synthetic-rule.md", "text": RULE}
    lines, start = [], 0
    for line in TABLE.split("\n"):
        lines.append((start, start + len(line)))
        start += len(line) + 1
    table_units = EvidenceUnitSnapshot.create(table, lines, "explicit_units_v5")
    rule_units = EvidenceUnitSnapshot.create(rule, [(0, len(RULE))], "explicit_units_v5")
    units = SentUnitCatalog([table_units, rule_units])
    mapping = {"{header}": table_units.unit_ids[0], "{rule}": rule_units.unit_ids[0], "{E:table}": "E1", "{E:rule}": "E2"}
    mapping.update({f"{{row:{number}}}": table_units.unit_ids[number] for number in range(1, len(lines))})
    model = generation_output_model(TABLE_QUESTION, unit_catalog=units)

    def replay(name: str) -> dict[str, Any]:
        text = json.dumps(FIXTURE["outputs"][name], ensure_ascii=False)
        for key, value in mapping.items():
            text = text.replace(key, value)
        return json.loads(text)

    repeated = replay("deployed_42dd5db")
    assert len(repeated["request_coverage"]) > 1
    assert {plan["request_id"] for plan in repeated["request_coverage"]} == {"Q1"}
    with pytest.raises(ValidationError, match="request_coverage"):
        model.model_validate(repeated)
    single = model.model_validate(replay("deployed_c00108e_retry"))
    assert [plan.request_id for plan in single.request_coverage] == ["Q1"]
