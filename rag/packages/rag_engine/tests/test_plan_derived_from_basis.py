"""回答の生成の計画は、モデルに書かせず要点の basis からコードが導く（#4611）。

basis に選んだ根拠が、その要求で答えに使う根拠（relation=direct）。録画の出力（``fixtures/plan_basis_replay_outputs.json``。
合成の判定表と区分の規則）を、テストの原文単位 ID と根拠の label に置き換えて再生する。
- 4592-live: 計画の行で basis の表の根拠に unrelated / linked を書いた出力（PR #4613 の試行）。計画の行は読まないので、
  表の根拠は計画に残る。
- 4609-live: basis の根拠に direct の行を書いた出力（main 相当）。草稿の要点・説明・要約は記録と同じ。
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from application_verdict_stub import APPLICATION_SCHEMAS, audit_reply

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft
from rag_engine.generation.grounded import (
    _outside_request_plan,
    _request_plan_errors,
    generation_output_model,
    unreviewed_catalog_spans,
)
from rag_engine.retrieval.article_catalog import ARTICLE_CATALOG_KEY
from rag_engine.retrieval.table_header_lines import TABLE_HEADER_LINES_KEY

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "plan_basis_replay_outputs.json").read_text(encoding="utf-8"))
HEADER = "| 行ID | 条件 | 点数 |"
POINTS = zip("ABCDEFGHI", (2, 3, 1, 4, 2, 1, 3, 2, 5), strict=True)
ROWS = [f"| A{number} | 属性{name}=有 | {points} |" for number, (name, points) in enumerate(POINTS, 1)]
INTRO = "合成資料 値と規則\n判定表。各行の条件に一致するときだけ点数を加算し、一致しない行は0点とします。"
TABLE = "\n".join([HEADER, *ROWS])
RULE = "区分の規則\n合計点の区分は、0〜9点は区分A、10〜19点は区分B、20〜29点は区分Cです。"
SPANS = [
    {"label": "E1", "evidence_id": "intro-a", "source_id": "s-a", "source": "synthetic.md", "text": INTRO},
    {
        "label": "E2",
        "evidence_id": "table-a",
        "source_id": "s-a",
        "source": "synthetic.md",
        "text": TABLE,
        TABLE_HEADER_LINES_KEY: [HEADER],
    },
    {"label": "E3", "evidence_id": "rule-a", "source_id": "s-a", "source": "synthetic.md", "text": RULE},
]


def _snapshot(span: dict[str, Any]) -> EvidenceUnitSnapshot:
    units, position = [], 0
    for line in span["text"].split("\n"):
        units.append((position, position + len(line)))
        position += len(line) + 1
    return EvidenceUnitSnapshot.create(span, units, "explicit_units_v5")


SNAPSHOTS = [_snapshot(span) for span in SPANS]
CATALOG = SentUnitCatalog(SNAPSHOTS)


def _replace(value: Any, units: dict[str, str], labels: dict[str, str]) -> Any:
    text = json.dumps(value, ensure_ascii=False)
    text = re.sub(r"\{u:(.*?)\}(?=\")", lambda found: units[json.loads(f'"{found.group(1)}"')], text)
    text = re.sub(r"\{E:(.*?)\}(?=\")", lambda found: labels[json.loads(f'"{found.group(1)}"')], text)
    return json.loads(text)


def _test_ids() -> tuple[dict[str, str], dict[str, str]]:
    """テストの根拠の、単位の原文 → 原文単位 ID と、先頭の単位の原文 → label。"""
    units = {}
    for snapshot in SNAPSHOTS:
        text = snapshot.span()["text"]
        units.update({text[start:end]: unit_id for (start, end), unit_id in zip(snapshot.units, snapshot.unit_ids, strict=True)})
    labels = {span["text"].split("\n", 1)[0]: span["label"] for span in SPANS}
    return units, labels


def _canonical(case: str, index: int = -1):
    entry = FIXTURE["cases"][case]
    units, labels = _test_ids()
    output = _replace(entry["outputs"][index]["parsed_output"], units, labels)
    model = generation_output_model(entry["question"], unit_catalog=CATALOG)
    return canonical_generation_draft(model.model_validate(output), unit_catalog=CATALOG), entry


def _cited_labels(draft) -> set[str]:
    return {span["label"] for item in draft.items if item.kind != "gap" for span in SPANS if span["evidence_id"] == item.evidence_id}


def test_generation_schema_has_no_plan_rows():
    for case in FIXTURE["cases"].values():
        schema = generation_output_model(case["question"], unit_catalog=CATALOG).model_json_schema()
        assert "request_coverage" not in schema["properties"]
        assert "relation" not in json.dumps(schema)


TABLE_BASIS = ["dv1-direct-value", "dv2-direct-value", "mr1-multi-request", "rk1-rows-known", "rk2-rows-known"]


@pytest.mark.parametrize("case", TABLE_BASIS)
def test_table_basis_stays_in_the_plan_even_when_the_recorded_plan_row_said_unrelated(case):
    """#4613 の試行の出力（basis の表の根拠に unrelated / linked の行）でも、表の根拠は計画に残り、説明は計画の外にならない。"""
    draft, entry = _canonical(f"4592-live/{case}")
    recorded_rows = entry["outputs"][0]["parsed_output"]["request_coverage"][0]["evidence_scope"]
    assert {row["relation"] for row in recorded_rows} <= {"linked", "unrelated"}
    assert [plan.request_id for plan in draft.request_coverage] == ["Q1"]
    assert "E2" in draft.request_coverage[0].evidence_ids
    assert {(scope.evidence_id, scope.relation) for scope in draft.request_coverage[0].evidence_scope} == {
        (label, "direct") for label in _cited_labels(draft)
    }
    assert _request_plan_errors(draft, SPANS) == []
    for item in draft.items:
        if item.kind != "gap":
            span = next(span for span in SPANS if span["evidence_id"] == item.evidence_id)
            assert not _outside_request_plan(item, span, draft, SPANS)


def test_header_only_basis_adds_no_plan_row():
    """見出しの行だけを basis にした要点（#4592）は答えとして受けないので、その根拠を計画に足さない。"""
    draft, _entry = _canonical("4592-live/dv3-direct-value")
    assert [(plan.request_id, plan.evidence_ids, plan.evidence_scope) for plan in draft.request_coverage] == [("Q1", [], [])]
    assert draft.items == []


MAIN_CASES = ["dv1-direct-value", "dv2-direct-value", "dv3-direct-value", "dv4-direct-value", "mr1-multi-request", "rk1-rows-known"]


@pytest.mark.parametrize("case", MAIN_CASES)
def test_main_outputs_keep_the_same_draft_and_plan_only_the_cited_evidence(case):
    """main 相当の出力は、要点・説明・導出・要約が記録と同じで、計画は引いた根拠の direct の行だけ（label は表示 ID）。"""
    draft, entry = _canonical(f"4609-live/{case}")
    recorded = entry["recorded_final"]
    assert [item.text for item in draft.items] == recorded["item_texts"]
    assert [part.model_dump(mode="json") for part in draft.answer_parts] == recorded["answer_parts"]
    assert [app.result for app in draft.applications] == [app["result"] for app in recorded["applications"]]
    assert (draft.summary, draft.summary_item_indexes, draft.summary_application_indexes) == (
        recorded["summary"],
        recorded["summary_item_indexes"],
        recorded["summary_application_indexes"],
    )
    assert len(draft.request_coverage) == 1
    plan = draft.request_coverage[0]
    assert set(plan.evidence_ids) == _cited_labels(draft)
    assert all(re.fullmatch(r"E\d+", label) for label in plan.evidence_ids)
    assert _request_plan_errors(draft, SPANS) == []


def test_several_parts_with_the_same_evidence_take_one_row_and_gap_adds_none():
    units, _labels = _test_ids()
    model = generation_output_model("行A4の点数と区分を教えてください。", unit_catalog=CATALOG)
    value = model.model_validate(
        {
            "answer_parts": [
                {"request_id": "Q1", "requirement": "行A4の点数", "answer": {"kind": "source", "basis": {"unit_ids": [units[ROWS[3]]]}}},
                {"request_id": "Q1", "requirement": "行A2の点数", "answer": {"kind": "source", "basis": {"unit_ids": [units[ROWS[1]]]}}},
                {"request_id": "Q1", "requirement": "区分", "answer": {"kind": "gap", "text": "区分の根拠が不足する。"}},
            ],
            "summary_part_indexes": [0],
            "confidence": "high",
        }
    )
    draft = canonical_generation_draft(value, unit_catalog=CATALOG)
    assert [(plan.request_id, plan.evidence_ids) for plan in draft.request_coverage] == [("Q1", ["E2"])]


def test_uncited_catalog_evidence_is_always_reviewed():
    """計画に unrelated の行が無くなったので、引かなかった目録の条はすべて検討の対象になる（#3526 の検討の対象の変化）。"""
    draft, _entry = _canonical("4609-live/dv1-direct-value")
    spans = [dict(span, **({ARTICLE_CATALOG_KEY: True} if span["label"] != "E2" else {})) for span in SPANS]
    assert [span["label"] for span in unreviewed_catalog_spans(draft, spans)] == ["E1", "E3"]


def _record(identity: str, text: str, metadata: dict[str, Any] | None = None) -> AnswerRecord:
    return AnswerRecord(
        id=identity,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="synthetic.md",
        chunk_level="parent",
        chunk_id=identity,
        source_run_id="synthetic",
        metadata={"layout": {"native_text_ranges": [{"start": 0, "end": len(text)}]}, **(metadata or {})},
    )


def _prompt_ids(prompt: str) -> tuple[dict[str, str], dict[str, str]]:
    """生成の入力の番号付きの原文から、単位の原文 → 原文単位 ID と、先頭の単位の原文 → label を作る。"""
    units: dict[str, str] = {}
    labels: dict[str, str] = {}
    label = ""
    for line in prompt.splitlines():
        if found := re.match(r"\[(E\d+)\]", line):
            label = found.group(1)
        if found := re.match(r"\d+\| \[(U[0-9a-f]{24})\] (.*)", line):
            units[found.group(2)] = found.group(1)
            labels.setdefault(found.group(2), label)
    return units, labels


def test_recorded_unrelated_plan_row_no_longer_withholds_the_answer():
    """#4613 の試行では表の根拠が計画の外になり「回答できません」だった出力が、計画の作り直しなしに表の行を示す。"""
    entry = FIXTURE["cases"]["4592-live/dv1-direct-value"]
    calls: list[str] = []

    def parse(system, prompt, config, schema, **options):
        calls.append(schema.__name__)
        if schema in APPLICATION_SCHEMAS:
            return audit_reply(schema, [{"index": 0, "valid": True}], prompt=prompt, summary_supported=True)
        assert schema.__name__ == "GroundedDraftWire", schema.__name__
        units, labels = _prompt_ids(prompt)
        return schema.model_validate(_replace(entry["outputs"][0]["parsed_output"], units, labels))

    def unexpected(*args, **kwargs):
        raise AssertionError("追加の検索・画像の呼び出しは不要")

    metadata = {"content_kind": "table", "table_row_tree_column_keys": json.dumps(["行ID", "条件", "点数"], ensure_ascii=False)}
    records = [_record("intro-a", INTRO), _record("table-a", TABLE, metadata), _record("rule-a", RULE)]
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none", answer_max_correction_rounds=0, llm_retries=0)
    with bind_dependencies(AnswerDependencies(unexpected, unexpected, parse, unexpected, unexpected)):
        response = synthesize_grounded_answer(
            entry["question"], AnswerContext(records=records, text="\n".join([INTRO, TABLE, RULE])), settings, image_prompt_mode="text_only"
        ).response
    timing = response.generation_trace["rounds"][0]["timing"]
    assert calls.count("GroundedDraftWire") == 1
    assert timing["plan_source"] == "derived_from_basis"
    assert timing["generation_calls"] == 1
    assert "request_plan_repair" not in timing and "request_plan_errors" not in timing
    assert ROWS[3] in response.answer_text
