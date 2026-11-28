"""角括弧を付け忘れた引用のラベルも、今回送った根拠の印として読む（#5750）。

形式だけで決め、質問・根拠に同じ字がある英数字の符号は印にしない。根拠・質問は合成。モデルは使わない。
"""

from __future__ import annotations

import pytest
from test_full_answer_wire import two_sources_catalog

from rag_engine.generation import grounded
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.full_answer_wire import bracket_bare_labels, canonical_full_answer, full_answer_model, render_full_answer

BODY = "資料には質問の条件に当てはまる記載が見当たりません。関連する記載は次のとおりです。"


def _render(units, body, *, kind="insufficient_evidence", question="合成の質問"):
    """``full_answer_model → canonical_full_answer → grounded.verify → render_full_answer``（Issue の合成の対照）。"""
    wire = full_answer_model(units, question=question).model_validate({"answer_body": body, "response_kind": kind, "confidence": "low"})
    draft = canonical_full_answer(wire, units)
    checked, dropped = grounded.verify(question, draft, [snapshot.span() for snapshot in units.snapshots])
    assert dropped == []
    return render_full_answer(draft, checked)


@pytest.mark.parametrize(
    ("suffix", "lines"),
    [("E1.1E2.1", 2), ("E1.1 E2.1.", 2), ("[E1.1][E2.1]", 2), ("", 0)],
    ids=["bare", "bare-spaced-with-period", "bracketed", "none"],
)
def test_bare_labels_open_the_same_sources_as_bracketed_marks(suffix, lines):
    units = two_sources_catalog()
    rendered = _render(units, BODY + suffix)

    assert rendered.text.startswith(BODY)
    assert "E1.1" not in rendered.text
    assert "E2.1" not in rendered.text
    assert len(rendered.citation_lines) == lines
    if lines:
        assert [line["scope_source_ids"] for line in rendered.citation_lines] == [["synthetic-0"], ["synthetic-1"]]


def test_bare_labels_count_as_citations_for_answered_bodies():
    units = two_sources_catalog()
    rendered = _render(units, "合成の記載0の一行目です。E1.1", kind="answered")
    assert len(rendered.citation_lines) == 1


def _code_catalog(text: str) -> SentUnitCatalog:
    span = {"text": text, "label": "E1", "evidence_id": "code-a", "source_id": "code-a", "source": "code.txt", "page": 1}
    return SentUnitCatalog([EvidenceUnitSnapshot.create(span, [(0, len(text))], "synthetic")])


def test_code_in_the_evidence_text_is_not_read_as_a_label():
    """根拠の原文に実在する英数字の符号（ラベルと同じ字）は、本文に書いても印にしない。"""
    units = _code_catalog("合成の装置の型番は E1.1 です。")
    rendered = _render(units, "型番は E1.1 です。")
    assert rendered.text == "型番は E1.1 です。"
    assert rendered.citation_lines == []


def test_code_in_the_question_is_not_read_as_a_label():
    units = two_sources_catalog()
    body = "E2 の設定は資料に見当たりません。"
    assert bracket_bare_labels(body, units, "E2 の設定は？") == body
    assert bracket_bare_labels(body, units, "設定は？") == "[E2] の設定は資料に見当たりません。"


@pytest.mark.parametrize(
    "body",
    [
        "符号 ABC-E1.1 を使います。",
        "符号 E1.1x を使います。",
        "版 2E1.1 を使います。",
        "行 E1.9 は今回の根拠に無い。",
        "符号 E3 を使います。",
    ],
    ids=["hyphen-prefix", "letter-suffix", "digit-prefix", "unknown-unit", "unsent-block"],
)
def test_parts_of_codes_and_unsent_labels_stay_as_text(body):
    units = two_sources_catalog()
    assert bracket_bare_labels(body, units, "合成の質問") == body
    # 今回の根拠に無い単位も、括弧が無ければ形式の不合格にしない。
    payload = {"answer_body": body, "response_kind": "insufficient_evidence", "confidence": "low"}
    assert full_answer_model(units, question="合成の質問").model_validate(payload).answer_body == body


def test_bracketed_marks_are_left_as_they_are():
    units = two_sources_catalog()
    body = "記載[ E1.1 ]と記載【E2】。"
    assert bracket_bare_labels(body, units, "合成の質問") == body
