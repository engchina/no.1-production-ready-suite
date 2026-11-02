"""説明・要約に書いた年を質問と引いた根拠に照らす（#4042）。入力はすべて合成。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import applications, grounded
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.date_consistency import absent_years
from rag_engine.models.llm import GroundedApplication, GroundedCitation, GroundedItem

RULE = "受付の日を含め14暦日なので4月14日まで有効で、4月15日に停止する。"
QUESTION = "対象Xを2031年4月1日に受け付けました。いつまで有効ですか？"
WRONG = "受付の日を含め14暦日なので、2021年4月14日まで有効です。"
RIGHT = "受付の日を含め14暦日なので、2031年4月14日まで有効です。"


@pytest.mark.parametrize(
    ("text", "question", "sources", "expected"),
    [
        (WRONG, QUESTION, [RULE], ["2021年"]),
        (RIGHT, QUESTION, [RULE], []),
        # 根拠に書いてある年は使ってよい。
        ("施行は2019年4月です。", "いつ施行か？", ["本規則は2019年4月1日から施行する。"], []),
        ("施行は2019/4/1です。", "いつ施行か？", ["本規則は2019年4月1日から施行する。"], []),
        ("施行は2018-04-01です。", "いつ施行か？", ["本規則は2019年4月1日から施行する。"], []),
        # 質問と根拠のどちらにも年が無ければ、足した年。
        ("2024年4月1日から有効です。", "いつから有効か？", ["4月1日から有効とする。"], ["2024年"]),
        # 年をまたぐ計算は正常。
        ("2032年1月5日まで有効です。", "2031年12月29日に受け付けました。", ["受付の日を含め7暦日有効とする。"], []),
        # 根拠の「N 年」の分だけ離れた年は計算で現れる。
        ("2036年3月末まで保存します。", "2031年に作成しました。", ["作成から5年間保存する。"], []),
        ("2041年まで保存します。", "2031年に作成しました。", ["作成から5年間保存する。"], ["2041年"]),
        # 全角・年度も同じ年として読む。
        ("２０３１年度の扱いです。", QUESTION, [RULE], []),
        ("2021年度の扱いです。", QUESTION, [RULE], ["2021年"]),
        # 元号どうしは照らす。略記は元号と同じ。
        ("令和6年4月から有効です。", "令和6年の扱いは？", ["4月から有効。"], []),
        ("R6年4月から有効です。", "令和6年の扱いは？", ["4月から有効。"], []),
        ("令和16年4月から有効です。", "令和6年の扱いは？", ["4月から有効。"], ["令和16年"]),
        ("令和元年の扱いです。", "令和元年の扱いは？", ["4月から有効。"], []),
        # 換算しないので、書き方の違う年は疑わない。
        ("2024年4月から有効です。", "令和6年の扱いは？", ["4月から有効。"], []),
        ("令和6年4月から有効です。", "2024年の扱いは？", ["4月から有効。"], []),
        # 年の無い数は年にしない。期限の計算で現れる月日も疑わない。
        ("2021件を処理し、4月24日に停止します。", QUESTION, [RULE], []),
        ("4月24日まで有効です。", "4月1日に受け付けました。", [RULE], []),
    ],
    ids=str,
)
def test_absent_years(text: str, question: str, sources: list[str], expected: list[str]) -> None:
    assert absent_years(text, question, sources) == expected


def record(text: str = RULE, *, source: str = "synthetic.txt") -> AnswerRecord:
    return AnswerRecord(
        id="c1",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source=source,
        chunk_level="parent",
        chunk_id="c1",
        source_run_id="run",
        metadata={"section_path": ["第1節"]},
    )


def draft(text: str, summary: str = "") -> dict[str, Any]:
    return {
        "request_coverage": [{"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "対象X", "relation": "direct"}]}],
        "items": [{"kind": "rule", "request_id": "Q1", "text": text, "citations": [{"evidence_id": "E1", "line_start": 1, "line_end": 1}]}],
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
        return schema.model_validate(next(values))

    def unused(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("追加の外部呼び出しは不可")

    context = AnswerContext(records=[record()], text=RULE)
    settings = replace(get_settings(environ={}, dotenv_path=None), answer_audit_mode="none")
    with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
        return synthesize_grounded_answer(QUESTION, context, settings, image_prompt_mode="text_only").response, prompts


def test_a_correct_year_adds_no_call_and_no_mark() -> None:
    result, prompts = answer(draft(RIGHT))
    assert len(prompts) == 1
    assert RIGHT in result.answer_text
    assert not result.envelope.get("passage_marks")


def test_a_year_absent_from_the_question_and_the_evidence_is_regenerated_once() -> None:
    result, prompts = answer(draft(WRONG), draft(RIGHT))
    assert len(prompts) == 2
    assert "date_value_issues" in prompts[1] and "2021年" in prompts[1]
    assert RIGHT in result.answer_text and WRONG not in result.answer_text


def test_a_year_that_stays_wrong_is_kept_with_a_mark_and_is_not_rewritten() -> None:
    result, prompts = answer(draft(WRONG), draft(WRONG))
    assert len(prompts) == 2
    # 本文を推測の値へ書き換えず、段落の印で利用者に示す。確かめた説明として扱わない。
    assert WRONG in result.answer_text
    marks = [mark for row in result.envelope["passage_marks"] for mark in row["reasons"]]
    assert any(mark["code"] == "item_check" and "2021年" in mark["detail"] for mark in marks)
    assert result.envelope.get("outcome") != "answered" or result.needs_human_review


def test_a_summary_with_an_absent_year_is_withheld_and_the_body_stays() -> None:
    result, prompts = answer(draft(RIGHT, summary=WRONG), draft(RIGHT, summary=WRONG))
    assert len(prompts) == 2
    assert "date_value_issues" in prompts[1]
    assert WRONG not in result.answer_text and RIGHT in result.answer_text
    assert answer_labels().neutral_summary in result.answer_text


def test_the_correction_is_part_of_the_audit_none_feedback() -> None:
    feedback = {"date_value_issues": [{"text": WRONG, "absent_years": ["2021年"]}]}
    assert grounded.correction_feedback(feedback, grounded.AUDIT_MODE_NONE) == feedback
    assert grounded.feedback_signature(feedback)["date_value_issues"] == 1
    assert grounded.pending_feedback_labels(feedback) == ["質問と引用原文に無い年 1 件"]


def test_an_application_with_an_absent_year_gets_a_warning_and_is_not_rejected() -> None:
    span = {"evidence_id": "c1", "text": RULE, "source": "synthetic.txt"}
    rule = grounded.CheckedItem(
        GroundedItem(kind="rule", request_id="Q1", text=RULE, citations=[GroundedCitation(evidence_id="E1")]), span, draft_index=0
    )
    application = GroundedApplication(
        request_id="Q1", premises=["2031年4月1日"], item_indexes=[0], reasoning="2031年4月1日に14暦日を足す。", result="2021年4月14日まで。"
    )
    checked = applications.check_applications(QUESTION, [application], {0: rule})[0]
    assert not checked.reasons
    assert any("2021年" in warning for warning in checked.warnings)
