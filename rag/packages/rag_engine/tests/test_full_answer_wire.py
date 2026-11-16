"""合成の同じ本文を、引用と種別の違いで書き換えない。"""

import pytest
from pydantic import ValidationError

from rag_engine.generation import grounded
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.full_answer_wire import canonical_full_answer, full_answer_model, render_full_answer


def catalog():
    text = "合成装置Aの入力電圧は12 Vです。"
    span = {
        "text": text,
        "source": "synthetic.txt",
        "source_id": "child-a",
        "evidence_id": "child-a",
        "label": "E1",
        "page": "",
        "scope_source_ids": ["child-a"],
    }
    snapshot = EvidenceUnitSnapshot.create(span, [(0, len(text))], "synthetic")
    return SentUnitCatalog([snapshot]), snapshot.unit_labels[0]


@pytest.mark.parametrize("kind", ["answered", "insufficient_evidence", "conditional", "needs_clarification", "needs_environment_data"])
@pytest.mark.parametrize("cited", [False, True])
def test_preserves_complete_body_regardless_of_type_or_citation_count(kind, cited):
    units, unit = catalog()
    body = "入力電圧は12 Vです。" + (f"[{unit}]" if cited else "")
    wire = full_answer_model(units).model_validate({"answer_body": body, "response_kind": kind, "confidence": "high"})
    draft = canonical_full_answer(wire, units)
    checked, dropped = grounded.verify("入力電圧は？", draft, [units.snapshots[0].span()])
    result = render_full_answer(draft, checked)
    assert "入力電圧は12 Vです。" in result.text
    assert "確認できなかった" not in result.text
    assert draft._response_kind == kind
    assert dropped == []
    assert len(result.citation_lines) == int(cited)
    if cited:
        assert result.citation_lines[0]["scope_source_ids"] == ["child-a"]


def test_body_without_a_label_adopts_no_evidence():
    """引用は本文の印だけで受ける。印の無い本文は根拠を採用せず、本文をそのまま返す。"""
    units, _unit = catalog()
    wire = full_answer_model(units).model_validate(
        {"answer_body": "資料に記載がなく、回答できません。", "response_kind": "insufficient_evidence", "confidence": "low"}
    )
    draft = canonical_full_answer(wire, units)
    assert draft.items == []
    assert render_full_answer(draft, []).text == wire.answer_body


def test_citation_list_outside_the_body_is_not_part_of_the_contract():
    units, unit = catalog()
    with pytest.raises(ValidationError):
        full_answer_model(units).model_validate(
            {"answer_body": "合成の回答", "response_kind": "answered", "confidence": "high", "citations": [{"unit_ids": [unit]}]}
        )
    assert "citations" not in full_answer_model(units).model_json_schema()["properties"]


def test_unavailable_unit_is_a_format_failure():
    units, _unit = catalog()
    with pytest.raises(ValidationError) as rejected:
        full_answer_model(units).model_validate({"answer_body": "合成の回答[E1.9]", "response_kind": "answered", "confidence": "high"})
    assert "[E1.9]" in str(rejected.value)


def test_label_of_evidence_not_sent_stays_as_text():
    """今回送っていない根拠のラベルは印にしない（本文の字として残し、意味は解析しない）。"""
    units, _unit = catalog()
    wire = full_answer_model(units).model_validate({"answer_body": "合成の回答[E7]", "response_kind": "answered", "confidence": "high"})
    draft = canonical_full_answer(wire, units)
    assert draft.items == []
    assert render_full_answer(draft, []).text == "合成の回答[E7]"


def test_shared_entry_keeps_body_with_an_explicit_reference():
    from test_answer_flow_single_generation import ROW, _run, context, record

    body = "保存年数は5年です。"
    response, calls = _run(
        "保存年数は？",
        context(record(0, ROW, "合成の規則", source="synthetic.md")),
        lambda units: {"answer_body": body + f"[{units[ROW]}]", "response_kind": "answered", "confidence": "high"},
    )
    assert calls == ["FullAnswerWire"]
    assert body in response.answer_text
    assert response.envelope["outcome"] == "answered"
    assert response.cited_evidence
    assert response.generation_trace["citation_lines"]
    assert response.generation_trace["llm_calls"] == 1


def test_replays_the_correct_text_misclassified_as_a_gap_before_the_new_contract():
    from rag_engine.generation.answer_parts_wire import answer_parts_model
    from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model

    units, _unit = catalog()
    correct = "入力電圧は12 Vです。"
    old_schema = answer_parts_model(generation_draft_wire_model(), units)
    old = canonical_generation_draft(
        old_schema.model_validate(
            {
                "response_kind": "answered",
                "confidence": "high",
                "answer_parts": [
                    {
                        "request_id": "Q1",
                        "requirement": "入力電圧",
                        "answer": {"kind": "gap", "text": correct},
                    }
                ],
                "summary_part_indexes": [],
            }
        ),
        unit_catalog=units,
    )
    assert old._response_kind == "insufficient_evidence"
    new_schema = full_answer_model(units)
    new = canonical_full_answer(
        new_schema.model_validate({"answer_body": correct, "response_kind": "answered", "confidence": "high"}),
        units,
    )
    assert new._response_kind == "answered"
    assert render_full_answer(new, []).text == correct


def test_inline_citations_do_not_split_a_sentence_or_a_table_row():
    units, unit = catalog()
    body = f"値は12 Vです[{unit}]。\n\n| 項目 | 値 |\n| --- | --- |\n| A | 12 V[{unit}] |"
    wire = full_answer_model(units).model_validate({"answer_body": body, "response_kind": "answered", "confidence": "high"})
    draft = canonical_full_answer(wire, units)
    checked, _ = grounded.verify("入力電圧は？", draft, [units.snapshots[0].span()])
    result = render_full_answer(draft, checked)
    assert result.text.startswith("値は12 Vです。\n根拠：")
    assert "| 項目 | 値 |\n| --- | --- |\n| A | 12 V |\n根拠：" in result.text
    assert len(result.citation_lines) == 2


def test_literal_source_line_does_not_shift_the_bound_citation_ordinal():
    units, unit = catalog()
    wire = full_answer_model(units).model_validate(
        {"answer_body": f"根拠：本文に書かれたラベル\n\n入力電圧は12 Vです。[{unit}]", "response_kind": "answered", "confidence": "high"}
    )
    draft = canonical_full_answer(wire, units)
    checked, _ = grounded.verify("入力電圧は？", draft, [units.snapshots[0].span()])
    rendered = render_full_answer(draft, checked)
    assert rendered.citation_lines[0] == {}
    assert rendered.citation_lines[1]["scope_source_ids"] == ["child-a"]
    assert rendered.text.count(grounded.answer_labels().citation_prefix) == 2


def records_catalog():
    """合成の CSV の根拠: 複数の記録を持つ根拠 1 つと、記録 1 つずつの根拠。記録には資料の番号（R01 など）がある。"""
    records = [f"資料ID: R{number:02d} / 本文: 合成の記録{number}の説明です。" for number in range(1, 41)]
    spans = [
        {"text": "\n".join(records[:10]), "label": "E1", "number_of_units": 10},
        *({"text": records[index], "label": f"E{index - 8}", "number_of_units": 1} for index in range(10, 40)),
    ]
    snapshots = []
    for position, span in enumerate(spans):
        text = span["text"]
        lines = text.split("\n")
        offsets, cursor = [], 0
        for line in lines:
            offsets.append((cursor, cursor + len(line)))
            cursor += len(line) + 1
        snapshots.append(
            EvidenceUnitSnapshot.create(
                {
                    "text": text,
                    "label": span["label"],
                    "evidence_id": f"synthetic-{position}",
                    "source_id": f"synthetic-{position}",
                    "source": "synthetic.csv",
                    "page": 1,
                    "scope_source_ids": [f"synthetic-{position}"],
                },
                offsets,
                "synthetic",
            )
        )
    return SentUnitCatalog(snapshots)


def test_replayed_output_that_cites_the_evidence_labels_adopts_the_selected_units():
    """記録した出力の形（本文の末尾に、選んだ根拠のラベルを書いた）を再生する。内部のラベルは利用者に出ず、選んだ根拠を採用する。"""
    units = records_catalog()
    for marks in ("[E2.1][E3.1]", "[E2][E3]"):
        body = "1. 合成の手順の一つ目です。\n2. 合成の手順の二つ目です。" + marks
        wire = full_answer_model(units).model_validate({"answer_body": body, "response_kind": "answered", "confidence": "high"})
        draft = canonical_full_answer(wire, units)
        assert [item.text for item in draft.items] == [
            "資料ID: R11 / 本文: 合成の記録11の説明です。",
            "資料ID: R12 / 本文: 合成の記録12の説明です。",
        ]
        checked, dropped = grounded.verify("合成の手順は？", draft, [snapshot.span() for snapshot in units.snapshots])
        rendered = render_full_answer(draft, checked)
        assert dropped == []
        assert "[E2" not in rendered.text and "[E3" not in rendered.text
        assert rendered.text.startswith("1. 合成の手順の一つ目です。\n2. 合成の手順の二つ目です。\n根拠：")
        assert len(rendered.citation_lines) == 2


def test_full_width_brackets_are_read_as_the_same_label_marks():
    """全角の括弧で書いた印も同じ印として読み、内部のラベルを利用者に出さない（#5091）。"""
    units = records_catalog()
    for marks in ("【E2.1】【E3.1】", "［E2］［E3］", "[E2.1]【E3.1】"):
        body = "1. 合成の手順の一つ目です。\n2. 合成の手順の二つ目です。" + marks
        wire = full_answer_model(units).model_validate({"answer_body": body, "response_kind": "answered", "confidence": "high"})
        draft = canonical_full_answer(wire, units)
        assert len(draft.items) == 2
        checked, dropped = grounded.verify("合成の手順は？", draft, [snapshot.span() for snapshot in units.snapshots])
        rendered = render_full_answer(draft, checked)
        assert dropped == []
        assert not any(mark in rendered.text for mark in ("E2", "E3", "【", "［"))
        assert len(rendered.citation_lines) == 2
    # 存在しない行のラベルは、全角でも形式の誤りとして拒む。
    with pytest.raises(ValidationError):
        full_answer_model(units).model_validate(
            {"answer_body": "合成の値です。【E1.999】", "response_kind": "answered", "confidence": "high"}
        )


def test_unit_labels_are_named_by_the_block_and_do_not_reuse_document_numbers():
    """単位のラベルは根拠のラベルと順番だけで作り、資料の番号や hash の数字の並びを持たない。"""
    units = records_catalog()
    document_numbers = {f"R{number:02d}" for number in range(1, 41)}
    assert units.unit_labels[:3] == ("E1.1", "E1.2", "E1.3")
    for snapshot in units.snapshots:
        assert all(name.startswith(snapshot.label + ".") for name in snapshot.unit_labels)
        assert not any(name.split(".")[0] in document_numbers for name in snapshot.unit_labels)
    # 記録 R17 は根拠 E8 の行。本文の印 [E8.1] はその行に結ぶ（ほかの記録の単位を補完しない）。
    wire = full_answer_model(units).model_validate(
        {"answer_body": "合成の値です。[E8.1]", "response_kind": "answered", "confidence": "high"}
    )
    draft = canonical_full_answer(wire, units)
    assert [item.text for item in draft.items] == ["資料ID: R17 / 本文: 合成の記録17の説明です。"]


def test_consecutive_unit_labels_are_one_reference_and_reused_labels_are_not_duplicated():
    units = records_catalog()
    body = "一つ目です。[E1.2][E1.3]\n二つ目です。[E1.2][E1.3]\n三つ目です。[E1.5]"
    wire = full_answer_model(units).model_validate({"answer_body": body, "response_kind": "answered", "confidence": "high"})
    draft = canonical_full_answer(wire, units)
    assert [citation.lines for item in draft.items for citation in item.citations] == ["2-3", "5"]
    checked, _ = grounded.verify("合成の質問", draft, [snapshot.span() for snapshot in units.snapshots])
    rendered = render_full_answer(draft, checked)
    assert "[E1" not in rendered.text
    assert len(rendered.citation_lines) == 3


@pytest.mark.parametrize(
    "marks",
    [
        "[E2.1][E3.1]",
        "[ E2.1 ][ E3.1 ]",
        "[　E2.1　]　[　E3.1　]",
        "[​E2.1][E3.1​]",
        "[﻿E2.1﻿] [⁠E3.1]",
        "［ E2 ］［ E3 ］",
        "【​ E2.1 】【E3】",
    ],
    ids=["plain", "ascii-space", "ideographic-space", "zwsp", "bom-and-word-joiner", "full-width-block", "lenticular-mixed"],
)
def test_marks_with_spaces_or_format_characters_inside_brackets_bind_the_same_units(marks):
    """括弧の内側の空白（半角・全角）と書式文字（ZWSP・BOM など）は形式として読み、同じ単位に結ぶ（#5186）。"""
    units = records_catalog()
    body = "1. 合成の手順の一つ目です。\n2. 合成の手順の二つ目です。" + marks
    wire = full_answer_model(units).model_validate({"answer_body": body, "response_kind": "answered", "confidence": "high"})
    draft = canonical_full_answer(wire, units)
    assert [item.text for item in draft.items] == [
        "資料ID: R11 / 本文: 合成の記録11の説明です。",
        "資料ID: R12 / 本文: 合成の記録12の説明です。",
    ]
    checked, dropped = grounded.verify("合成の手順は？", draft, [snapshot.span() for snapshot in units.snapshots])
    rendered = render_full_answer(draft, checked)
    assert dropped == []
    assert not any(mark in rendered.text for mark in ("E2", "E3", "[", "【", "［", "​", "﻿", "⁠"))
    assert rendered.text.startswith("1. 合成の手順の一つ目です。\n2. 合成の手順の二つ目です。\n根拠：")
    assert len(rendered.citation_lines) == 2


def test_padded_marks_still_reject_units_not_sent_and_keep_labels_not_sent_as_text():
    """空白を許しても、今回の根拠に無い行のラベルは形式の誤り、送っていない根拠のラベルは本文の字のまま（#5186）。"""
    units, _unit = catalog()
    for body in ("合成の回答[ E1.9 ]", "合成の回答[​E1.9]", "合成の回答［　E1.9　］"):
        with pytest.raises(ValidationError):
            full_answer_model(units).model_validate({"answer_body": body, "response_kind": "answered", "confidence": "high"})
    wire = full_answer_model(units).model_validate({"answer_body": "合成の回答[ E7 ]", "response_kind": "answered", "confidence": "high"})
    draft = canonical_full_answer(wire, units)
    assert draft.items == []
    assert render_full_answer(draft, []).text == "合成の回答[ E7 ]"


def two_sources_catalog():
    """合成の根拠 2 つ（別の文書・頁）。各根拠は 2 行。"""
    snapshots = []
    for position, (label, source, page) in enumerate((("E1", "doc_a.txt", 2), ("E2", "doc_b.txt", 5))):
        text = f"合成の記載{position}の一行目です。\n合成の記載{position}の二行目です。"
        first = text.index("\n")
        snapshots.append(
            EvidenceUnitSnapshot.create(
                {
                    "text": text,
                    "label": label,
                    "evidence_id": f"synthetic-{position}",
                    "source_id": f"synthetic-{position}",
                    "source": source,
                    "page": page,
                    "scope_source_ids": [f"synthetic-{position}"],
                },
                [(0, first), (first + 1, len(text))],
                "synthetic",
            )
        )
    return SentUnitCatalog(snapshots)


def _render(units, body):
    wire = full_answer_model(units).model_validate({"answer_body": body, "response_kind": "answered", "confidence": "high"})
    draft = canonical_full_answer(wire, units)
    checked, dropped = grounded.verify("合成の質問", draft, [snapshot.span() for snapshot in units.snapshots])
    assert dropped == []
    return draft, render_full_answer(draft, checked)


@pytest.mark.parametrize(
    "marks",
    [
        "（[E1.1]）",
        "（[E1][E2]）",
        "（[E1]、[E2]）",
        " （ [E1] [E2] ）",
        "([E1], [E2])",
        "（【​E1】・［ E2 ］）",
        "（[　E1.1　]　[⁠E2.2]）",
    ],
    ids=["single", "consecutive", "comma", "spaced", "ascii-parens", "full-width-and-zwsp", "ideographic-space-and-word-joiner"],
)
def test_parentheses_holding_only_marks_are_removed_with_the_marks(marks):
    """丸括弧の中が印と空白・区切りの記号・書式文字だけなら、括弧ごと外して空の括弧を残さない（#5231）。"""
    units = two_sources_catalog()
    draft, rendered = _render(units, f"合成の値は二つです{marks}。")
    assert rendered.text.startswith("合成の値は二つです。\n根拠：")
    assert not any(char in rendered.text for char in "（）()[]【】［］")
    # 出典の行と引用の結び付きは、段落の末尾に印を書いた本文と変わらない。
    plain_draft, plain = _render(units, "合成の値は二つです。" + marks.strip().strip("（）()"))
    assert [item.text for item in draft.items] == [item.text for item in plain_draft.items]
    assert rendered.citation_lines == plain.citation_lines
    assert rendered.text.split("\n")[1:] == plain.text.split("\n")[1:]


@pytest.mark.parametrize(
    ("body", "displayed"),
    [
        (
            "提供された資料（[E1] および [E2]）には記載がありません。",
            "提供された資料（doc_a.txt p.2 および doc_b.txt p.5）には記載がありません。",
        ),
        ("詳細は（[E1.1][E1.2]を参照）です。", "詳細は（doc_a.txt p.2を参照）です。"),
        ("詳細は（［ E1 ］​ と【E2.1】の両方）です。", "詳細は（doc_a.txt p.2​ とdoc_b.txt p.5の両方）です。"),
    ],
    ids=["connector", "consecutive-units-of-one-source", "full-width-and-zwsp"],
)
def test_marks_inside_parentheses_with_other_text_become_the_source_names(body, displayed):
    """丸括弧の中に印のほかの字があれば、印を出典の名前（出典の行と同じ書き方）に置き換え、括弧と字を保つ（#5231）。"""
    units = two_sources_catalog()
    draft, rendered = _render(units, body)
    paragraph, *references = rendered.text.split("\n")
    assert paragraph == displayed
    assert all(line.startswith(grounded.answer_labels().citation_prefix) for line in references)
    assert len(rendered.citation_lines) == len(references) == len(draft.items)
    assert [line["scope_source_ids"] for line in rendered.citation_lines] == [
        [item.citations[0]._unit_binding.snapshot.span()["evidence_id"]] for item in draft.items
    ]


def test_parentheses_without_marks_stay_as_written():
    """印の無い丸括弧（Markdown のリンクを含む）は変えない（#5231）。"""
    units = two_sources_catalog()
    _draft, rendered = _render(units, "定格（合成）の値は [リンク](https://example.com/a) にあります（）。[E1]")
    assert rendered.text.startswith("定格（合成）の値は [リンク](https://example.com/a) にあります（）。\n根拠：")


def test_answer_without_any_bound_citation_is_not_reported_with_the_declared_confidence():
    """本文の印が 1 つも根拠に結ばれない回答は、ほかの経路と同じく引用の無い回答として確かさを low にする（#5186）。"""
    from test_answer_flow_single_generation import ROW, _run, context, record

    body = "保存年数は5年です。"
    response, _calls = _run(
        "保存年数は？",
        context(record(0, ROW, "合成の規則", source="synthetic.md")),
        lambda _units: {"answer_body": body + "[ E99 ]", "response_kind": "answered", "confidence": "high"},
    )
    assert body in response.answer_text
    assert not response.cited_evidence
    assert response.confidence == "low"
    # 空白を入れた印は根拠に結ばれ、宣言どおりの確かさを保つ。
    response, _calls = _run(
        "保存年数は？",
        context(record(0, ROW, "合成の規則", source="synthetic.md")),
        lambda units: {"answer_body": body + f"[ {units[ROW]} ]", "response_kind": "answered", "confidence": "high"},
    )
    assert response.cited_evidence
    assert response.confidence == "high"
    assert "[" not in response.answer_text.split("根拠：")[0]


def test_response_kind_is_defined_once_in_the_instructions_and_declared_after_the_body():
    """種別の各値の定義は、モデルが必ず読む生成の指示の 1 か所にあり、応答 schema の説明では繰り返さない（#5229）。

    応答 schema の項目の説明は、モデルによっては構造の制約だけに使われて読まれないため、定義を schema に置かない。
    """
    from typing import get_args

    from rag_engine.generation.answer_envelope import AnswerOutcome

    kinds = get_args(AnswerOutcome)
    assert set(grounded.RESPONSE_KIND_DEFINITIONS) == set(kinds)
    system = grounded.generation_system_prompt()
    for kind, definition in grounded.RESPONSE_KIND_DEFINITIONS.items():
        assert system.count(f"- {kind}: {definition}") == 1
    units, _unit = catalog()
    schema = full_answer_model(units).model_json_schema()
    properties = list(schema["properties"])
    assert properties.index("answer_body") < properties.index("response_kind")
    description = schema["properties"]["response_kind"]["description"]
    assert not any(kind in description for kind in kinds)
