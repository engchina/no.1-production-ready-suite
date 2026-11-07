"""回答の生成の計画の行（evidence_scope）は、コードが使わない自由記述の欄を持たない（#4594）。

録画では、モデルが自由記述の target の値の後で行を閉じようとし、次の必須の欄の前で空白を出し続けて暴走した。
target は下流のどの判定にも使われないので、生成の wire から外し、行は evidence_id → relation → link だけにする。
計画だけを別に立てる経路（answer.scope）は対象の照合が目的なので、target を残す。
"""

from typing import Any

from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog, unit_scope_model
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import generation_output_model
from rag_engine.models.llm import RequestScopePlan

TEXT = "項目Aは12です。"
QUESTION = "項目Aの値を教えてください。"


def catalog() -> tuple[SentUnitCatalog, EvidenceUnitSnapshot]:
    span = {"label": "E1", "evidence_id": "source-a", "source_id": "s-a", "source": "synthetic.txt", "text": TEXT}
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(TEXT))], "explicit_units_v5")
    return SentUnitCatalog([snapshot]), snapshot


def scope_row(model: Any) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema["$defs"]
    coverage = defs[schema["properties"]["request_coverage"]["items"]["$ref"].rsplit("/", 1)[1]]
    return defs[coverage["properties"]["evidence_scope"]["items"]["$ref"].rsplit("/", 1)[1]]


def test_generation_scope_row_has_no_free_text_target():
    units, _ = catalog()
    row = scope_row(generation_output_model(QUESTION, unit_catalog=units))
    assert list(row["properties"]) == ["evidence_id", "relation", "link"]


def test_generation_plan_row_without_target_reaches_the_canonical_plan():
    units, snapshot = catalog()
    model = generation_output_model(QUESTION, unit_catalog=units)
    raw = {
        "answer_parts": [
            {"request_id": "Q1", "requirement": "項目Aの値", "answer": {"kind": "source", "basis": {"unit_ids": list(snapshot.unit_ids)}}}
        ],
        "summary_part_indexes": [0],
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_scope": [{"evidence_id": "E1", "relation": "linked", "link": {"unit_ids": list(snapshot.unit_ids)}}],
            }
        ],
        "confidence": "high",
    }
    draft = canonical_generation_draft(model.model_validate(raw), unit_catalog=units)
    scope = draft.request_coverage[0].evidence_scope[0]
    assert (scope.evidence_id, scope.relation, scope.target, scope.link_quote) == ("E1", "linked", "", TEXT)
    assert draft.request_coverage[0].evidence_ids == ["E1"]


def test_scope_plan_row_keeps_target():
    units, _ = catalog()
    row = scope_row(unit_scope_model(RequestScopePlan, units, request_ids=("Q1",)))
    assert "target" in row["properties"]
