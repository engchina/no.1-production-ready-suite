"""回答の生成で、basis に選んだ根拠の direct の計画の行はコードが作り、モデルは判断が要る行だけを書く（#4611）。

モデルの計画の行（evidence_scope）の relation は linked / unrelated だけ。要点の basis に選んだ根拠は答えに使う根拠なので、
direct の行は単位の ID からコードが導く。モデルが同じ根拠に書いた linked / unrelated の判断は覆さない。
計画だけを別に立てる経路（answer.scope）は direct を含む 3 値のまま。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from application_verdict_stub import APPLICATION_SCHEMAS, audit_reply
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog, unit_scope_model
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import generation_output_model
from rag_engine.models.llm import RequestScopePlan
from rag_engine.retrieval.table_header_lines import TABLE_HEADER_LINES_KEY

QUESTION = "項目Aの値を教えてください。"
TEXTS = {"E1": "項目Aは12です。", "E2": "項目Bは画面Cで登録した後に確定します。", "E3": "項目Dの規則。"}


def catalog() -> tuple[SentUnitCatalog, dict[str, EvidenceUnitSnapshot]]:
    snapshots = {}
    for label, text in TEXTS.items():
        span = {"label": label, "evidence_id": f"source-{label}", "source_id": f"s-{label}", "source": "synthetic.txt", "text": text}
        snapshots[label] = EvidenceUnitSnapshot.create(span, [(0, len(text))], "explicit_units_v5")
    return SentUnitCatalog(list(snapshots.values())), snapshots


def scope_row(model: Any) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema["$defs"]
    coverage = defs[schema["properties"]["request_coverage"]["items"]["$ref"].rsplit("/", 1)[1]]
    return defs[coverage["properties"]["evidence_scope"]["items"]["$ref"].rsplit("/", 1)[1]]


def draft_for(parts: list[dict[str, Any]], scope: list[dict[str, Any]]):
    units, _ = catalog()
    model = generation_output_model(QUESTION, unit_catalog=units)
    raw = {
        "answer_parts": parts,
        "summary_part_indexes": [0],
        "request_coverage": [{"request_id": "Q1", "evidence_scope": scope}],
        "confidence": "high",
    }
    return canonical_generation_draft(model.model_validate(raw), unit_catalog=units)


def source(label: str, requirement: str = "項目Aの値") -> dict[str, Any]:
    return {
        "request_id": "Q1",
        "requirement": requirement,
        "answer": {"kind": "source", "basis": {"unit_ids": list(catalog()[1][label].unit_ids)}},
    }


def decisions(draft) -> list[tuple[str, str]]:
    return [(scope.evidence_id, scope.relation) for scope in draft.request_coverage[0].evidence_scope]


def test_generation_rows_carry_only_judged_relations():
    units, _ = catalog()
    row = scope_row(generation_output_model(QUESTION, unit_catalog=units))
    assert list(row["properties"]) == ["evidence_id", "relation", "link"]
    assert row["properties"]["relation"]["enum"] == ["linked", "unrelated"]


def test_generation_rejects_a_model_written_direct_row():
    units, snapshots = catalog()
    model = generation_output_model(QUESTION, unit_catalog=units)
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                "answer_parts": [source("E1")],
                "request_coverage": [{"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "relation": "direct", "link": None}]}],
                "confidence": "high",
            }
        )


def test_scope_plan_keeps_direct():
    units, _ = catalog()
    row = scope_row(unit_scope_model(RequestScopePlan, units, request_ids=("Q1",)))
    assert "direct" in json.dumps(row["properties"]["relation"])


def test_basis_evidence_gets_a_direct_row_from_code():
    draft = draft_for([source("E1")], [])
    assert decisions(draft) == [("E1", "direct")]
    assert draft.request_coverage[0].evidence_ids == ["E1"]
    assert draft.request_coverage[0].evidence_scope[0].link_quote == ""


def test_derivation_basis_gets_direct_rows_and_gap_gets_none():
    _units, snapshots = catalog()
    parts = [
        {
            "request_id": "Q1",
            "requirement": "項目Aの2倍",
            "answer": {"kind": "answer", "basis": {"unit_ids": list(snapshots["E1"].unit_ids)}, "reasoning": "12×2", "result": "24"},
        },
        {"request_id": "Q1", "requirement": "項目Dの値", "answer": {"kind": "gap", "text": "項目Dの値の資料が必要です。"}},
    ]
    assert decisions(draft_for(parts, [])) == [("E1", "direct")]


def test_model_judgement_on_the_same_evidence_is_kept():
    _units, snapshots = catalog()
    linked = {"evidence_id": "E2", "relation": "linked", "link": {"unit_ids": list(snapshots["E2"].unit_ids)}}
    unrelated = {"evidence_id": "E3", "relation": "unrelated", "link": None}
    draft = draft_for([source("E1"), source("E2", "前提の操作"), source("E3", "別の規則")], [linked, unrelated])
    assert decisions(draft) == [("E2", "linked"), ("E3", "unrelated"), ("E1", "direct")]
    assert draft.request_coverage[0].evidence_scope[0].link_quote == TEXTS["E2"]
    assert draft.request_coverage[0].evidence_ids == ["E2", "E1"]


def test_header_only_part_adds_no_row():
    """答えとして受けない見出しの行だけの要点（#4592）の根拠は、計画に足さない。"""
    text = "| 行ID | 点数 |\n| A1 | 2 |"
    span = {
        "label": "E1",
        "evidence_id": "t",
        "source_id": "s",
        "source": "s.md",
        "text": text,
        TABLE_HEADER_LINES_KEY: ["| 行ID | 点数 |"],
    }
    snapshot = EvidenceUnitSnapshot.create(span, [(0, 12), (13, len(text))], "explicit_units_v5")
    units = SentUnitCatalog([snapshot])
    model = generation_output_model(QUESTION, unit_catalog=units)
    raw = {
        "answer_parts": [
            {"request_id": "Q1", "requirement": "行A1", "answer": {"kind": "source", "basis": {"unit_ids": [snapshot.unit_ids[0]]}}}
        ],
        "request_coverage": [{"request_id": "Q1", "evidence_scope": []}],
        "confidence": "high",
    }
    draft = canonical_generation_draft(model.model_validate(raw), unit_catalog=units)
    assert draft.request_coverage[0].evidence_scope == []


def test_plan_repair_input_shows_only_the_model_rows():
    """計画の不整合の作り直しの入力に、コードが作った direct の行を出さない（モデルは書けない）。"""
    prompts: list[str] = []
    records = [
        AnswerRecord(
            id=f"rec-{label}",
            engine="docling",
            engine_label="Docling",
            page=1,
            seq_no=index,
            category="Chunk",
            text=text,
            source=f"synthetic-{label}.txt",
            chunk_level="parent",
            chunk_id=f"rec-{label}",
            source_run_id="synthetic",
            metadata={"layout": {"native_text_ranges": [{"start": 0, "end": len(text)}]}},
        )
        for index, (label, text) in enumerate(TEXTS.items(), 1)
    ]

    def units_of(prompt: str) -> dict[str, tuple[str, str]]:
        import re

        found, label = {}, ""
        for line in prompt.splitlines():
            if hit := re.match(r"\[(E\d+)\]", line):
                label = hit.group(1)
            if hit := re.match(r"\d+\| \[(U[0-9a-f]{24})\] (.*)", line):
                found[hit.group(2)] = (label, hit.group(1))
        return found

    def parse(system, prompt, config, schema, **options):
        if schema in APPLICATION_SCHEMAS:
            return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt, summary_supported=True)
        assert schema.__name__ == "GroundedDraftWire", schema.__name__
        prompts.append(prompt)
        found = units_of(prompt)
        label_a, unit_a = found[TEXTS["E1"]]
        label_d, _unit_d = found[TEXTS["E3"]]
        scope = [{"evidence_id": label_d, "relation": "unrelated", "link": None}]
        if len(prompts) == 1:  # 同じ根拠に 2 つの判断（計画の検査の不整合）
            scope.append({"evidence_id": label_d, "relation": "linked", "link": {"unit_ids": [found[TEXTS["E3"]][1]]}})
        return schema.model_validate(
            {
                "answer_parts": [
                    {"request_id": "Q1", "requirement": "項目Aの値", "answer": {"kind": "source", "basis": {"unit_ids": [unit_a]}}}
                ],
                "summary_part_indexes": [0],
                "request_coverage": [{"request_id": "Q1", "evidence_scope": scope}],
                "question_type": ["fact"],
                "confidence": "high",
            }
        )

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像の呼び出しは不要")

    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)
    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            QUESTION, AnswerContext(records=records, text="\n".join(TEXTS.values())), settings, image_prompt_mode="text_only"
        ).response
    assert len(prompts) == 2
    repair = json.loads(prompts[1][prompts[1].index('{"invalid_request_plan"') :])
    relations = [scope["relation"] for plan in repair["previous_draft"]["request_coverage"] for scope in plan["evidence_scope"]]
    assert relations == ["unrelated", "linked"]
    assert TEXTS["E1"] in response.answer_text
    round_ = response.generation_trace["rounds"][0]
    assert round_["timing"]["request_plan_repair"]["calls"] == 1
    plan = round_["draft"]["request_coverage"][0]
    assert [(scope["evidence_id"], scope["relation"]) for scope in plan["evidence_scope"]][:2] == [("E3", "unrelated"), ("E1", "direct")]
