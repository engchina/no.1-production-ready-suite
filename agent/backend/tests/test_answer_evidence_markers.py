"""回答の根拠の ID の印の範囲を括弧の対応で決める（#5190）。合成の回答と根拠だけを使う。"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.agent.answer_evidence import answer_evidence
from app.features.agent.runtime import RunStep, StepStatus
from app.features.agent.tools import ToolCall, ToolResult

CHUNK_A = "synthetic-doc:0001"
CHUNK_B = "synthetic-doc:0002"


def retrieval_steps() -> list[RunStep]:
    name = "rag__rag_search"
    output: dict[str, Any] = {
        "evidence": [
            {
                "document_id": "document-a",
                "chunk_id": CHUNK_A,
                "excerpt": "合成の設定 A は 30 秒。",
            },
            {"document_id": "document-a", "chunk_id": CHUNK_B, "excerpt": "合成の設定 B は 5 回。"},
        ]
    }
    return [
        RunStep(
            run_id="run-synthetic",
            status=StepStatus.COMPLETED,
            tool_call=ToolCall(name=name, arguments={}),
            tool_result=ToolResult(name=name, success=True, output=output),
        )
    ]


def display(text: str, markers: list[dict[str, Any]]) -> str:
    """画面と同じく、段落の本文から印の原文を取り除いた文（agent/frontend の displayText）。"""
    for marker in markers:
        text = text.replace(marker["text"], "")
    return text


@pytest.mark.parametrize(
    ("mark_a", "mark_b"),
    [
        (f"【根拠: {CHUNK_A}】", f"【根拠: {CHUNK_B}】"),
        (f"【【根拠: {CHUNK_A}】】", f"【【根拠: {CHUNK_B}】】"),
        (f"【 根拠: {CHUNK_A} 】", f"【　根拠: {CHUNK_B}　】"),
        (f"【​根拠: {CHUNK_A}​】", f"【【​根拠: {CHUNK_B}】​】"),
        (f"【 【根拠: {CHUNK_A}】 】", f"【﻿【根拠: {CHUNK_B}】⁠】"),
        (f"〔〔根拠: {CHUNK_A}〕〕", f"[[evidence_id: {CHUNK_B}]]"),
    ],
    ids=["single", "double", "inner-space", "zwsp", "padded-double", "other-brackets"],
)
def test_marker_spans_whole_bracket_pair_and_leaves_no_bracket_in_the_text(
    mark_a: str, mark_b: str
) -> None:
    answer = f"合成の設定 A は 30 秒です{mark_a}。\n合成の設定 B は 5 回です{mark_b}。"
    content = answer_evidence(retrieval_steps(), answer)

    assert content["markers"] == [
        {"text": mark_a, "chunk_ids": [CHUNK_A]},
        {"text": mark_b, "chunk_ids": [CHUNK_B]},
    ]
    assert [
        (display(passage["text"], content["markers"]), passage["chunk_ids"])
        for passage in content["passages"]
    ] == [
        ("合成の設定 A は 30 秒です。", [CHUNK_A]),
        ("合成の設定 B は 5 回です。", [CHUNK_B]),
    ]


def test_unbalanced_brackets_pair_only_the_matching_count() -> None:
    """閉じが多ければ印の外の括弧は本文の括弧と対にし、開きが多ければ余りを印に入れない。"""
    answer = (
        f"合成の注記（【注: 合成の補足【根拠: {CHUNK_A}】】）。\n"
        f"合成の設定 B【【根拠: {CHUNK_B}】。"
    )
    content = answer_evidence(retrieval_steps(), answer)

    assert content["markers"] == [
        {"text": f"【根拠: {CHUNK_A}】", "chunk_ids": [CHUNK_A]},
        {"text": f"【根拠: {CHUNK_B}】", "chunk_ids": [CHUNK_B]},
    ]
    assert display(answer, content["markers"]) == (
        "合成の注記（【注: 合成の補足】）。\n合成の設定 B【。"
    )


@pytest.mark.parametrize(
    "mark",
    ["【【根拠: synthetic-doc:0009】】", "【 根拠: unknown-id 】", "【​根拠: 9999】"],
    ids=["double", "inner-space", "zwsp"],
)
def test_marker_with_an_id_that_was_not_passed_is_still_rejected(mark: str) -> None:
    content = answer_evidence(retrieval_steps(), f"合成の別の記述です{mark}。")

    assert content["markers"] == []
    assert content["passages"] == []
    assert content["cited"] == 0
