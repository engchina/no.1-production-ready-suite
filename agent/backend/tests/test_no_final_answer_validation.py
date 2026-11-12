"""回答の生成後の採点をしない契約（#4714）。合成のデータだけを使う。

回答の対応は RAG の結果と実行の成否だけで決め、本文の語の形では決めない。引用 ID の実在の
確認（回答に書かれた ID を根拠の ID と照らす）は残す。
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

from app.features.agent.answer_outcome import ANSWERED, CONDITIONAL, NEEDS_HUMAN, answer_outcome
from app.features.agent.runtime import RunStep, StepStatus
from app.features.agent.tools import ToolCall, ToolResult
from app.settings import Settings

ANSWER = "項目 A の設定は画面 B で変更します。"


def _step(name: str, output: dict[str, Any]) -> RunStep:
    return RunStep(
        run_id="run-1",
        status=StepStatus.COMPLETED,
        tool_call=ToolCall(name=f"rag__{name}", arguments={}),
        tool_result=ToolResult(name=f"rag__{name}", success=True, output=output),
    )


def test_outcome_takes_no_validation_input() -> None:
    assert "validation" not in inspect.signature(answer_outcome).parameters


def test_outcome_follows_the_rag_result_without_reading_the_answer_wording() -> None:
    steps = [_step("rag_search", {"outcome": "answered"})]
    worded = "資料には記載がありません。項目 A の場合と項目 B の場合で異なります。"
    outcome = answer_outcome(worded, steps=steps)
    assert outcome["value"] == ANSWERED
    assert outcome["basis"] == "rag_search"
    assert outcome["signals"] == []


@pytest.mark.parametrize("rag_outcome", ["conditional", "insufficient_evidence"])
def test_outcome_keeps_the_value_returned_by_rag(rag_outcome: str) -> None:
    steps = [_step("rag_search", {"outcome": rag_outcome})]
    assert answer_outcome(ANSWER, steps=steps)["value"] == rag_outcome


def test_needs_human_comes_only_from_the_explicit_handoff() -> None:
    assert answer_outcome(ANSWER, steps=[])["value"] != NEEDS_HUMAN
    handoff = [_step("rag_lookup_guides", {"guides": [{"decision": "handoff"}]})]
    outcome = answer_outcome(ANSWER, steps=handoff)
    assert (outcome["value"], outcome["basis"]) == (NEEDS_HUMAN, "guide_handoff")


def test_incomplete_section_reading_keeps_the_answer_conditional() -> None:
    steps = [_step("rag_search", {"outcome": "answered"})]
    outcome = answer_outcome(ANSWER, steps=steps, coverage={"status": "incomplete"})
    assert outcome["value"] == CONDITIONAL
    assert outcome["signals"] == ["unread_sections"]


def test_final_validation_setting_and_modules_are_gone() -> None:
    assert "agent_final_validation_enabled" not in Settings.model_fields
    for name in ("answer_validation", "answer_validation_view"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"app.features.agent.{name}")


def test_written_citation_ids_are_checked_against_the_evidence_ids() -> None:
    from app.features.agent.answer_references import cited_chunk_ids

    known = ["doc-1:1", "doc-1:19"]
    assert cited_chunk_ids("根拠は [doc-1:1] です。", known) == {"doc-1:1"}
    assert cited_chunk_ids("根拠は [doc-9:1] です。", known) == set()
