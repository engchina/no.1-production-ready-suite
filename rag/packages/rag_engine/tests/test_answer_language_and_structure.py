"""回答の言語の差し込み・小問ごとの冒頭の結論・構造だけの原文の行の非表示（#4755）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from test_citation_unit_ids import snapshot

from rag_engine.generation import grounded
from rag_engine.generation import response_language as response_language_module
from rag_engine.generation.answer_parts_wire import answer_parts_model
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.generation.response_language import default_response_language, generation_language_schema
from rag_engine.models.llm import GroundedItem
from rag_engine.resources.runtime import bind_prompt_overrides

PLAIN_QUESTION = "項目Aの設定はどこで確認しますか。"
SUBQUESTIONS = "次の3点を教えてください。(1) 項目Aの値は何ですか。(2) 項目Bの条件は何ですか。(3) 項目Cの手順は何ですか。"


# ---- 回答の言語の差し込み ----


def test_generation_prompt_states_the_default_language_from_the_language_pack() -> None:
    prompt = grounded.generation_system_prompt()
    assert "{{language}}" not in prompt
    assert default_response_language() in prompt
    # 規定の文の差し込みが残っていない。
    assert "{{" not in prompt


def test_saved_role_can_use_the_language_placeholder() -> None:
    with bind_prompt_overrides({"generation.role": "## Step 1: 回答する\n- 既定の言語: {{language}}"}):
        prompt = grounded.generation_system_prompt()
    assert f"- 既定の言語: {default_response_language()}\n" in prompt
    assert "{{" not in prompt


def test_saved_role_without_the_placeholder_still_gets_the_default_language() -> None:
    """回答の言語は固定の部分が持つので、保存した役割の文に差し込みが無くても渡る。"""
    with bind_prompt_overrides({"generation.role": "## Step 1: 回答する\n- 保存した指示"}):
        prompt = grounded.generation_system_prompt()
    assert prompt.startswith("## Step 1: 回答する\n- 保存した指示\n")
    assert default_response_language() in prompt


def test_unknown_locale_falls_back_to_the_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(response_language_module, "current_pack", lambda: SimpleNamespace(locale="fr"))
    assert default_response_language() == "fr"


def test_language_contract_names_every_generated_text_field() -> None:
    description = generation_language_schema(generation_draft_wire_model(), PLAIN_QUESTION).model_fields["response_language"].description
    assert description is not None
    for field in ("result", "reasoning", "requirement", "text", "external_data_items", "reference_materials"):
        assert field in description


# ---- 小問ごとの冒頭の結論 ----


def _subquestion_case() -> tuple[dict[str, Any], type, SentUnitCatalog]:
    original = snapshot()
    catalog = SentUnitCatalog([original])
    schema = answer_parts_model(generation_draft_wire_model(), catalog, request_ids=("Q1",), subquestions=("(1)", "(2)", "(3)"))
    raw = {
        "response_kind": "answered",
        "answer_parts": [
            {
                "request_id": "Q1",
                "requirement": "(1) の値",
                "subquestion": "(1)",
                "answer": {"kind": "answer", "basis": {"unit_ids": [original.unit_ids[0]]}, "reasoning": "原文の値。", "result": "結論1"},
            },
            {
                "request_id": "Q1",
                "requirement": "(2) の条件",
                "subquestion": "(2)",
                "answer": {"kind": "answer", "basis": {"unit_ids": [original.unit_ids[1]]}, "reasoning": "原文の条件。", "result": "結論2"},
            },
            {
                "request_id": "Q1",
                "requirement": "(3) の手順",
                "subquestion": "(3)",
                "answer": {"kind": "answer", "basis": {"unit_ids": [original.unit_ids[2]]}, "reasoning": "原文の手順。", "result": "結論3"},
            },
        ],
        "summary_part_indexes": [0, 1],
        "confidence": "high",
    }
    return raw, schema, catalog


def test_every_subquestion_has_a_line_in_the_summary_even_when_the_model_chose_two() -> None:
    raw, schema, catalog = _subquestion_case()
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.summary.splitlines() == ["結論1", "結論2", "結論3"]
    # 冒頭の結論の根拠の依存も足した小問の分まで結ぶ（非公開なら機械的に保留する根拠）。
    assert len(draft.summary_application_indexes) == 3


def test_a_subquestion_answered_only_by_a_gap_adds_no_summary_line() -> None:
    raw, schema, catalog = _subquestion_case()
    raw["answer_parts"][2]["answer"] = {"kind": "gap", "text": "資料に記載がありません。"}
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.summary.splitlines() == ["結論1", "結論2"]


def test_subquestion_label_is_a_closed_set_in_the_generation_schema() -> None:
    raw, schema, _ = _subquestion_case()
    raw["answer_parts"][0]["subquestion"] = "(9)"
    with pytest.raises(ValueError):
        schema.model_validate(raw)


def test_generation_schema_has_the_subquestion_field_only_for_questions_with_subquestions() -> None:
    plain = grounded.generation_output_model(PLAIN_QUESTION, unit_catalog=SentUnitCatalog([snapshot()])).model_json_schema()
    assert "subquestion" not in str(plain)
    enumerated = grounded.generation_output_model(SUBQUESTIONS, unit_catalog=SentUnitCatalog([snapshot()])).model_json_schema()
    assert "subquestion" in str(enumerated)
    assert "(3)" in str(enumerated)


def test_recorded_output_without_the_subquestion_field_still_parses() -> None:
    raw, schema, catalog = _subquestion_case()
    for part in raw["answer_parts"]:
        part.pop("subquestion")
    draft = canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog)
    assert draft.summary.splitlines() == ["結論1", "結論2"]


# ---- 構造だけの原文の行（見出し・列名の行・JSON の断片）----


def span(text: str, *, source: str = "synthetic.md", section_path: tuple[str, ...] = ("第1章 概要", "項目 2")) -> dict[str, Any]:
    return {
        "evidence_id": f"id-{abs(hash(text))}",
        "label": "E1",
        "source_id": "parent-1",
        "source": source,
        "page": 1,
        "section_path": list(section_path),
        "text": text,
        "function": (source, "合成の説明"),
    }


def rule(text: str, source_span: dict[str, Any]) -> grounded.CheckedItem:
    return grounded.CheckedItem(GroundedItem(kind="rule", text=text), span=source_span, draft_index=0)


BODY = "処理の開始時には構成の確認を行う。"
TABLE_TEXT = f"| 列A | 列B | 列C |\n| --- | --- | --- |\n| 値1 | 値2 | 値3 |\n{BODY}"


@pytest.mark.parametrize(
    ("line", "source_text", "source"),
    [
        ("項目 2", f"項目 2\n{BODY}", "synthetic.md"),
        ("## 項目 2", f"## 項目 2\n{BODY}", "synthetic.md"),
        ("| 列A | 列B | 列C |", TABLE_TEXT, "synthetic.md"),
        (
            '{ "title": "合成", "sections": [ { "lead": "説明である。',
            '{ "title": "合成の文書" }',
            "synthetic.json",
        ),
        ('"この節では項目について説明する。処理を確認する。', '{ "lead": "この節" }', "synthetic.json"),
        ('確認を行う。", "別の説明である。', '{ "lead": "確認" }', "synthetic.json"),
    ],
)
def test_structure_only_lines_are_not_shown_as_confirmed_facts(line: str, source_text: str, source: str) -> None:
    rendered = grounded.render_answer("", [rule(line, span(source_text, source=source)), rule(BODY, span(BODY))])
    assert line not in rendered.text
    assert BODY in rendered.text
    assert rendered.text.count("根拠：") == len(rendered.citation_lines)


@pytest.mark.parametrize(
    "line",
    [
        "| 値1 | 値2 | 値3 |",  # 表のデータの行は事実。
        "項目 2 は無効です。",  # 見出しと同じ語で始まる文は本文。
        'コードは "A1" を指定する。',  # JSON の文書でない原文の引用符。
    ],
)
def test_data_rows_and_sentences_are_still_shown(line: str) -> None:
    rendered = grounded.render_answer("", [rule(line, span(f"項目 2\n{line}\n{TABLE_TEXT}"))])
    assert line in rendered.text


def test_a_sentence_in_a_json_document_without_json_punctuation_is_shown() -> None:
    line = "各工程は入力の版を記録する。"
    rendered = grounded.render_answer("", [rule(line, span('{ "lead": "各工程は入力の版を記録する。" }', source="synthetic.json"))])
    assert line in rendered.text


def test_structure_only_lines_are_kept_when_nothing_else_would_be_shown() -> None:
    """構造の行しか無い回答は、消すと本文が空になるので元のまま示す。"""
    rendered = grounded.render_answer("", [rule("項目 2", span(f"項目 2\n{BODY}"))])
    assert "項目 2" in rendered.text


def _catalog_for(text: str, source: str, units: list[tuple[int, int]]) -> tuple[EvidenceUnitSnapshot, SentUnitCatalog]:
    value = {
        "evidence_id": "E1",
        "label": "E1",
        "source_id": "source-1",
        "source": source,
        "text": text,
        "section_path": ["第1章 概要", "項目 2"],
    }
    original = EvidenceUnitSnapshot.create(value, units, "synthetic-units-v1")
    return original, SentUnitCatalog([original])


def test_a_source_part_of_only_a_heading_is_not_used_as_the_summary() -> None:
    original, catalog = _catalog_for(f"項目 2\n{BODY}", "synthetic.md", [(0, 4), (5, 5 + len(BODY))])
    schema = answer_parts_model(generation_draft_wire_model(), catalog, request_ids=("Q1",))
    raw = {
        "response_kind": "answered",
        "answer_parts": [
            {"request_id": "Q1", "requirement": "見出し", "answer": {"kind": "source", "basis": {"unit_ids": [original.unit_ids[0]]}}},
            {"request_id": "Q1", "requirement": "本文", "answer": {"kind": "source", "basis": {"unit_ids": [original.unit_ids[1]]}}},
        ],
        "summary_part_indexes": [0, 1],
        "confidence": "high",
    }
    assert canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog).summary == BODY


def test_summary_keeps_a_heading_part_when_no_other_part_is_chosen() -> None:
    original, catalog = _catalog_for(f"項目 2\n{BODY}", "synthetic.md", [(0, 4), (5, 5 + len(BODY))])
    schema = answer_parts_model(generation_draft_wire_model(), catalog, request_ids=("Q1",))
    raw = {
        "response_kind": "answered",
        "answer_parts": [
            {"request_id": "Q1", "requirement": "見出し", "answer": {"kind": "source", "basis": {"unit_ids": [original.unit_ids[0]]}}},
        ],
        "summary_part_indexes": [0],
        "confidence": "high",
    }
    assert canonical_generation_draft(schema.model_validate(raw), unit_catalog=catalog).summary == "項目 2"


# ---- 内部の番号を根拠の見出しに置かない ----


def test_evidence_group_heading_has_no_internal_number() -> None:
    spans = [span("本文の説明です。"), {**span("別の本文です。", source="other.md"), "evidence_id": "id-2", "label": "E2"}]
    block = grounded.format_functions(spans, model_view=True)
    assert "機能 F" not in block
    assert "合成の説明" in block
