"""Agent の回答の節の扱い（旧 Agent の回答との互換。#1456・#2738・#3240 の段 1）。

faithfulness（`grounding_text`）は、Agent の最終の検証が回答の末尾に足す節と、#2738 より前に保存した
Agent の回答の「資料で確認できず除いた内容」の節を、回答の本文の主張に数えない。RAG 自身の回答の節は
test_answer_metrics.py。#3240 の段 4 で旧 Agent の回答の扱いを suite の Agent の adapter へ移す
ときは、このファイルのテストを一緒に移し、RAG 自身の節のテストは RAG に残す。
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.rag.answer_metrics import AGENT_UNVERIFIED_HEADING, agent_system_headings, grounding_text

# Agent の最終の検証が回答の末尾に足す節の事例（RAG と Agent の両方のテストが読む。#1456・#2738）。
UNVERIFIED_SECTION: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "platform/contracts/answer-passages/unverified-section.json"
    ).read_text("utf-8")
)
ANSWER_SECTIONS: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parent / "fixtures/agent_evaluation_baseline/answer_sections.json"
    ).read_text("utf-8")
)


def _with_labels(template: str) -> str:
    """fixture の {name} を言語資源の answer_labels() の項目に置き換える。"""
    from rag_engine.generation.answer_labels import answer_labels

    labels = answer_labels()
    return re.sub(r"\{(\w+)\}", lambda match: str(getattr(labels, match.group(1))), template)


def test_agent_headings_are_the_legacy_heading_and_the_rag_section_labels() -> None:
    """Agent の節の見出しは、#2738 より前の見出しと、RAG の回答の節の見出し（言語資源）。

    旧形式の見出しは RAG の不足の節の見出し（answer_checks.gap_section_titles）にも入っている。
    """
    from rag_engine.generation.answer_labels import answer_labels

    from app.rag.answer_checks import gap_section_titles

    labels = answer_labels()
    assert AGENT_UNVERIFIED_HEADING == "資料で確認できず除いた内容"
    assert agent_system_headings() == {
        AGENT_UNVERIFIED_HEADING,
        labels.removed_section,
        labels.marked_section,
    }
    assert AGENT_UNVERIFIED_HEADING in gap_section_titles()


@pytest.mark.parametrize("case", UNVERIFIED_SECTION["examples"], ids=lambda case: str(case["id"]))
def test_grounding_text_excludes_the_sections_of_the_agent(case: dict[str, str]) -> None:
    """faithfulness は Agent の最終の検証が足した節（headings。#1456・#2738）を数えない。

    節は外した段落・印の段落の引用と理由で、本文の主張ではない。印の段落そのものは本文に残る。
    """
    assert set(UNVERIFIED_SECTION["headings"]) <= agent_system_headings()
    assert grounding_text(case["answer"]) == grounding_text(case["body"])
    for heading in UNVERIFIED_SECTION["headings"]:
        assert heading not in grounding_text(case["answer"])


@pytest.mark.parametrize(
    "case", ANSWER_SECTIONS["legacy_agent_sections"], ids=lambda case: str(case["id"])
)
def test_grounding_text_of_agent_answer_sections_matches_the_baseline(
    case: dict[str, str],
) -> None:
    """保存した Agent の回答（#2738 の前と後）の節の扱いが基線と一致する。"""
    assert grounding_text(_with_labels(case["answer"])) == _with_labels(case["grounding"])
