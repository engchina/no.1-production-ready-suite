"""説明・要約に書いた質問の対象の名前を、引いた根拠に照らす（#4327）。入力はすべて合成。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from citation_unit_support import generated_fixture

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.question_names import absent_question_names, question_statements

RULE = "利用者が外部サービスを使う場合、管理部が承認したものに限る。"
QUESTION = "業務でServiceXは使ってよいですか？"
LEAP = "ServiceXは管理部が承認した外部サービスなので利用できます。"
GENERAL = "外部サービスは、管理部が承認したものに限り利用できます。"
GAP = "ServiceXが管理部の承認を受けたかは資料で確認できません。"


@pytest.mark.parametrize(
    ("text", "question", "sources", "expected"),
    [
        (LEAP, QUESTION, [RULE], ["ServiceX"]),
        (GENERAL, QUESTION, [RULE], []),
        # 根拠に名前があれば確かめられる（全角・大小の違いは同じ名前）。
        (LEAP, QUESTION, ["ServiceXは承認済みである。"], []),
        (LEAP, QUESTION, ["ＳＥＲＶＩＣＥＸは承認済みである。"], []),
        # 利用者が事実として述べた名前は、当てはめの前提なので確かめない。
        (LEAP, "ServiceXを導入しました。利用条件は何ですか？", [RULE], []),
        # 一般の語や言い換えを疑わないよう、英字で始まる 3 字以上の語だけを名前とみなす。
        ("基幹システムは利用できます。", "基幹システムは使えますか？", [RULE], []),
        ("PCは利用できます。", "PCは使えますか？", [RULE], []),
        # 依頼の文も問いと同じ。
        (LEAP, "ServiceXの利用可否を教えてください。", [RULE], ["ServiceX"]),
        # 分類の例として挙げた名前は確かめない（#4343）。
        ("ServiceX等の外部サービスは利用できません。", "ServiceXなどの外部サービスは使えますか？", [RULE], []),
        # 説明に書かなければ確かめない。
        (GENERAL, "ServiceXは使えますか？", [RULE], []),
    ],
    ids=["leap", "general", "in-source", "width-case", "stated", "katakana", "short-name", "request", "example", "not-written"],
)
def test_absent_question_names(text: str, question: str, sources: list[str], expected: list[str]) -> None:
    assert absent_question_names(text, question, sources) == expected


def test_question_statements_keep_only_facts() -> None:
    question = "項目を作りました。\n承認は必要ですか。手順を教えてください。"
    assert question_statements(question) == ["項目を作りました。"]


def record() -> AnswerRecord:
    return AnswerRecord(
        id="c1",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=RULE,
        source="synthetic.txt",
        chunk_level="parent",
        chunk_id="c1",
        source_run_id="run",
        metadata={"section_path": ["第1節"]},
    )


def draft(text: str, summary: str = "", *, gap: str = "") -> dict[str, Any]:
    gaps = [{"kind": "gap", "request_id": "Q1", "text": gap, "citations": []}] if gap else []
    return {
        "request_coverage": [{"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "対象", "relation": "direct"}]}],
        "items": [
            {"kind": "rule", "request_id": "Q1", "text": text, "citations": [{"evidence_id": "E1", "line_start": 1, "line_end": 1}]},
            *gaps,
        ],
        "summary": summary or text,
        "summary_item_indexes": [0],
        "confidence": "medium",
    }


def answer(*outputs: dict[str, Any]) -> tuple[Any, list[str]]:
    """監査なしの回答を、用意した出力で通す。LLM の呼び出しの入力を返す。"""
    prompts: list[str] = []
    values = iter(outputs)

    def parse(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        prompts.append(prompt)
        return generated_fixture(schema, prompt, next(values))

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("追加の外部呼び出しは不可")

    context = AnswerContext(records=[record()], text=RULE)
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none")
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        return synthesize_grounded_answer(QUESTION, context, settings, image_prompt_mode="text_only").response, prompts


def test_answer_without_the_name_adds_no_call_and_no_mark() -> None:
    result, prompts = answer(draft(GENERAL))
    assert len(prompts) == 1
    assert GENERAL in result.answer_text
    assert not result.envelope.get("passage_marks")


def test_name_absent_from_the_evidence_is_regenerated_once() -> None:
    result, prompts = answer(draft(LEAP), draft(GENERAL, gap=GAP))
    assert len(prompts) == 2
    assert "subject_name_issues" in prompts[1] and "ServiceX" in prompts[1]
    assert GENERAL in result.answer_text and LEAP not in result.answer_text
    assert GAP in result.answer_text


def test_name_that_stays_is_kept_with_a_mark() -> None:
    """未確認の説明は本文に残して印を付ける。確かめた説明として扱わない。"""
    result, prompts = answer(draft(LEAP), draft(LEAP))
    assert len(prompts) == 2
    assert LEAP in result.answer_text
    marks = [mark for row in result.envelope["passage_marks"] for mark in row["reasons"]]
    assert any(mark["code"] == "item_check" and "ServiceX" in mark["detail"] for mark in marks)
    assert answer_labels().neutral_summary in result.answer_text


def test_summary_with_an_absent_name_is_withheld_and_the_body_stays() -> None:
    result, prompts = answer(draft(GENERAL, summary=LEAP), draft(GENERAL, summary=LEAP))
    assert len(prompts) == 2
    assert LEAP not in result.answer_text and GENERAL in result.answer_text
    assert answer_labels().neutral_summary in result.answer_text


def test_summary_may_repeat_a_name_written_as_a_gap() -> None:
    result, prompts = answer(draft(GENERAL, summary=GAP, gap=GAP))
    assert len(prompts) == 1
    assert answer_labels().neutral_summary not in result.answer_text


def test_the_correction_is_part_of_the_audit_none_feedback() -> None:
    feedback = {"subject_name_issues": [{"text": LEAP, "absent_names": ["ServiceX"]}]}
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_NONE) == feedback
    assert grounded.feedback_signature(feedback)["subject_name_issues"] == 1
    assert grounded.pending_feedback_labels(feedback) == ["引用原文に無い対象の名前 1 件"]
