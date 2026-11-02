"""説明の文に写された内部の位置を外す検査（#4153）。入力はすべて合成の説明と根拠。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.citation_locator import clean_evidence_locators
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem

SENTENCE = "設備を変更するときは費用を申請者が負担します"


def citation(evidence_id: str = "E1", lines: str = "4") -> GroundedCitation:
    return GroundedCitation(evidence_id=evidence_id, lines=lines)


def clean(text: str, *citations: GroundedCitation, codes: Sequence[str] = (), source_text: str = ""):
    return clean_evidence_locators(text, codes=codes, citations=citations, source_text=source_text)


@pytest.mark.parametrize(
    ("written", "cited"),
    [
        ("（E1, 行4）", citation("E1", "4")),
        ("（E6, 行1‑4）", citation("E6", "1-4")),
        ("(E6, 行 1-4)", citation("E6", "1-4")),
        ("（E1、4行目）", citation("E1", "4")),
        ("【E1 line 4】", citation("E1", "4")),
        # 出典の欄と違う番号でも、資料に無い根拠の ID を持つ括弧は位置の写し。
        ("（E1, 行9）", citation("E2", "4")),
        ("（E1）", citation("E1", "4")),
        ("（E1、E2）", citation("E1", "4")),
    ],
)
def test_a_copy_of_the_citation_leaves_no_fragment(written: str, cited: GroundedCitation) -> None:
    cleaned = clean(f"{SENTENCE}{written}。", cited)
    assert cleaned.text == f"{SENTENCE}。"
    assert not cleaned.residual


@pytest.mark.parametrize(
    ("written", "cited"),
    [("（行4）", "4"), ("（行4）", "3-5"), ("（3-5行目）", "3-5")],
)
def test_a_unit_number_inside_the_own_range_is_a_copy_without_the_id(written: str, cited: str) -> None:
    assert clean(f"{SENTENCE}{written}。", citation("E1", cited)).text == f"{SENTENCE}。"


@pytest.mark.parametrize(
    "text",
    [
        # 自分の出典の範囲に入らない番号は、文書の行の話かもしれない。
        f"{SENTENCE}（行9）。",
        # 行の語を含む別の語・単位の語の無い数字・文を含む括弧。
        "支払いは（銀行4）を使います。",
        "表は（4）の形式です。",
        "明細は 4 行まで入力します。",
        "2 行目の項目を入力します。",
        "（4 行目までの入力は不要）の場合があります。",
    ],
)
def test_ordinary_mentions_of_lines_stay(text: str) -> None:
    cleaned = clean(text, citation("E1", "4"))
    assert cleaned.text == text
    assert not cleaned.residual


def test_a_written_line_in_the_cited_source_stays() -> None:
    text = f"{SENTENCE}（行4）。"
    assert clean(text, citation("E1", "4"), source_text="別表（行4）に記載").text == text


def test_a_code_of_the_document_stays() -> None:
    assert clean("コード（E12）は保留です。", citation("E1", "4"), codes=["E12"]).text == "コード（E12）は保留です。"
    assert clean("コード E12 は保留です。", citation("E1", "4"), codes=["E12"]).text == "コード E12 は保留です。"
    assert clean("対象（E1、E12）です。", citation("E1", "4"), codes=["E12"]).text == "対象（E12）です。"
    assert clean("対象（E12, 行9）です。", citation("E1", "4"), codes=["E12"]).text == "対象（E12, 行9）です。"
    # 自分が引いた根拠の ID と本文のコードが同じ字でも、番号付きの括弧は位置の写し。
    assert clean("対象です（E12, 行4）。", citation("E12", "4"), codes=["E12"]).text == "対象です。"


@pytest.mark.parametrize("text", ["E1 によると費用は申請者の負担です。", "費用は（E1 の表では申請者）の負担です。"])
def test_an_id_mixed_into_the_sentence_is_reported_for_correction(text: str) -> None:
    cleaned = clean(text, citation("E1", "4"))
    assert "E1" not in cleaned.text
    assert cleaned.residual == ("E1",)


def test_the_label_of_a_copy_can_name_the_missing_citation() -> None:
    assert clean(f"{SENTENCE}（E3, 行2）。").labels == ("E3",)


# ---- 検査と是正の経路 ----


SPAN = {
    "evidence_id": "E1",
    "label": "E1",
    "source_id": "s1",
    "source": "文書A",
    "page": 1,
    "text": f"第一の行です。\n第二の行です。\n第三の行です。\n{SENTENCE}。",
    "citation_view": None,
}


def draft(text: str) -> GroundedDraft:
    return GroundedDraft(
        items=[GroundedItem(kind="rule", text=text, citations=[citation("E1", "4")])],
        summary="",
        confidence="high",
    )


def verified(text: str):
    span = {key: value for key, value in SPAN.items() if value is not None}
    checked, _ = grounded.verify("費用はだれが負担しますか", draft(text), [span])
    return next(entry for entry in checked if entry.draft_index == 0)


def test_verify_publishes_the_sentence_without_the_copy() -> None:
    entry = verified(f"{SENTENCE}（E1, 行4）。")
    assert entry.item.text == f"{SENTENCE}。"
    assert entry.span is not None and entry.span["evidence_id"] == "E1"
    assert not entry.internal_label_issues
    assert not [mark for mark in entry.marks if mark["code"] == "item_check"]


def test_verify_marks_a_mixed_id_without_rewriting_the_rest() -> None:
    entry = verified("E1 によると費用は申請者が負担します。")
    assert entry.internal_label_issues == ["E1"]
    assert "によると費用は申請者が負担します。" in entry.item.text
    assert any(mark["code"] == "item_check" and grounded.INTERNAL_LABEL_CHECK in mark["detail"] for mark in entry.marks)


def test_the_correction_is_part_of_the_audit_none_feedback() -> None:
    feedback = {"internal_label_issues": [{"text": "によると費用は申請者が負担します。"}]}
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_NONE) == feedback
    assert grounded.feedback_signature(feedback)["internal_label_issues"] == 1
    assert grounded.pending_feedback_labels(feedback) == ["説明の文に混ざった内部の番号 1 件"]


# ---- 回答の全体の経路（監査なし） ----

LONG_RULE = "\n".join(f"第{index}の規定の本文です。費用の扱いは申請の区分ごとに定めます。" * 2 for index in range(1, 5))
FULL_QUESTION = "費用はだれが負担しますか？"
FULL_RULE = f"{LONG_RULE}\n{SENTENCE}。"


def output(text: str) -> dict[str, Any]:
    return {
        "request_coverage": [{"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "費用", "relation": "direct"}]}],
        "items": [{"kind": "rule", "request_id": "Q1", "text": text, "citations": [{"evidence_id": "E1", "line_start": 5, "line_end": 5}]}],
        "summary": "",
        "summary_item_indexes": [],
        "confidence": "medium",
    }


def answer(*outputs: dict[str, Any]) -> tuple[Any, list[str]]:
    prompts: list[str] = []
    values = iter(outputs)

    def parse(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        prompts.append(prompt)
        return schema.model_validate(next(values))

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("追加の外部呼び出しは不可")

    record = AnswerRecord(
        id="c1",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=FULL_RULE,
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="c1",
        source_run_id="run",
        metadata={"section_path": ["第1節"]},
    )
    context = AnswerContext(records=[record], text=FULL_RULE)
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none")
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        return synthesize_grounded_answer(FULL_QUESTION, context, settings, image_prompt_mode="text_only").response, prompts


def test_a_copied_position_needs_no_extra_call_and_leaves_no_fragment() -> None:
    result, prompts = answer(output(f"{SENTENCE}（E1, 行5）。"))
    assert len(prompts) == 1
    assert f"{SENTENCE}。" in result.answer_text
    assert "行5" not in result.answer_text
    assert "（," not in result.answer_text
    assert not [mark for row in result.envelope.get("passage_marks") or [] for mark in row["reasons"] if mark["code"] == "item_check"]


def test_a_mixed_id_is_regenerated_once() -> None:
    result, prompts = answer(output(f"E1 によると{SENTENCE}。"), output(f"{SENTENCE}。"))
    assert len(prompts) == 2
    assert "internal_label_issues" in prompts[1]
    assert f"{SENTENCE}。" in result.answer_text


def test_a_mixed_id_that_stays_keeps_a_mark() -> None:
    result, prompts = answer(output(f"E1 によると{SENTENCE}。"), output(f"E1 によると{SENTENCE}。"))
    assert len(prompts) == 2
    assert "E1" not in result.answer_text
    marks = [mark for row in result.envelope["passage_marks"] for mark in row["reasons"]]
    assert any(mark["code"] == "item_check" and grounded.INTERNAL_LABEL_CHECK in mark["detail"] for mark in marks)
