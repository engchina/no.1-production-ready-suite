"""標準回答による評価の指標と合否(評価の基準の指標と閾値。#680)。"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.rag.answer_metrics import score_answer_evaluation
from app.schemas.search import RetrievedChunk

ANSWER = "特典は QUO カード 5 千円です。"
CITATIONS = [
    RetrievedChunk(
        document_id="d1",
        chunk_id="c1",
        text="特典 住宅ローン契約実行者に QUO カード 5 千円を贈呈",
        score=0.9,
        metadata={"evidence_model_used": True},
    )
]


def _judged(coverage: float = 1.0, claim: str = "supported") -> dict[str, object]:
    return {
        "status": "completed",
        "requirement_coverage": coverage,
        "answer_correctness": 1.0,
        "claim_checks": [{"status": claim}],
    }


def _score(
    evaluation: Mapping[str, object],
    suite: str = "standard",
    *,
    answer: str = ANSWER,
    citations: Sequence[RetrievedChunk] = CITATIONS,
) -> dict[str, Any]:
    return score_answer_evaluation(
        evaluation,
        answer=answer,
        citations=citations,
        insufficient_reason="",
        settings=Settings(rag_evaluation_suite=suite),
    )


def test_only_metrics_measurable_for_one_answer_are_judged_with_suite_thresholds() -> None:
    result = _score(_judged(coverage=0.95))
    metrics = {metric["name"]: metric for metric in result["metrics"]}
    assert result["suite"] == "standard"
    assert set(metrics) == {"answer_correctness", "claim_support_rate", "requirement_coverage"}
    assert metrics["requirement_coverage"] == {
        "name": "requirement_coverage",
        "value": 0.95,
        "threshold": 0.9,
        "passed": True,
        "reference": False,
    }
    strict = {m["name"]: m for m in _score(_judged(coverage=0.95), "strict")["metrics"]}
    assert strict["requirement_coverage"]["threshold"] == 1.0
    assert strict["requirement_coverage"]["passed"] is False


def test_unsupported_claim_or_refusal_fails_the_answer() -> None:
    unsupported = _score(_judged(claim="unsupported"))
    assert unsupported["passed"] is False
    assert {m["name"]: m["value"] for m in unsupported["metrics"]}["claim_support_rate"] == 0
    unassessed = _score(_judged(claim="unassessed"))
    assert unassessed["claim_support_rate"] is None
    assert (
        next(m for m in unassessed["metrics"] if m["name"] == "claim_support_rate")["value"] is None
    )
    refused = _score(_judged(), answer="", citations=[])
    assert refused["claim_support_rate"] is None
    assert all(m["name"] != "refusal_accuracy" for m in refused["metrics"])


def test_unfinished_evaluation_is_returned_as_is() -> None:
    evaluation = {"status": "error", "message": "評価を完了できませんでした。"}
    assert _score(evaluation) == evaluation


def test_faithfulness_is_reference_only_and_ignores_generated_boilerplate() -> None:
    from rag_engine.generation.answer_labels import answer_labels

    from app.rag.answer_metrics import grounding_text

    labels = answer_labels()
    answer = f"{labels.neutral_summary}\n\n{labels.rules_section}\n\n・{ANSWER}\n根拠：info.pdf p.1"
    # 見出し・節の直下の説明・前置きは文言に依らず主張から外す（#2040 で文言を変えても同じ）。
    assert (
        grounding_text(
            f"{answer}\n\n{answer_labels().quote_only_section}\n\n{answer_labels().quote_only_note}"
        )
        == f"・{ANSWER}"
    )
    assert grounding_text(answer) == f"・{ANSWER}"
    # 読めなかった章節の行はシステムの案内なので数えない（#2040）。
    unread = (
        f"{answer_labels().unverified_section}\n\n"
        "・「a.pdf」の「第2章」は、読み込み量の上限に達したため未反映です。"
    )
    assert grounding_text(f"{answer}\n\n{unread}") == f"・{ANSWER}"
    # 語句一致ではなく、主張の支持の判定を採用する。
    result = _score(_judged(), answer="合成の言い換えです。")
    metrics = {metric["name"]: metric for metric in result["metrics"]}
    assert "faithfulness" not in metrics
    assert metrics["claim_support_rate"]["value"] == 1.0
    assert metrics["claim_support_rate"]["reference"] is False
    assert result["passed"] is True


# Agent の最終の検証が回答の末尾に足す節の事例（RAG と Agent の両方のテストが読む。#1456・#2738）。
UNVERIFIED_SECTION: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "platform/contracts/answer-passages/unverified-section.json"
    ).read_text("utf-8")
)


@pytest.mark.parametrize("case", UNVERIFIED_SECTION["examples"], ids=lambda case: str(case["id"]))
def test_grounding_text_excludes_the_sections_of_the_agent(case: dict[str, str]) -> None:
    """faithfulness は Agent の最終の検証が足した節（headings。#1456・#2738）を数えない。

    節は外した段落・印の段落の引用と理由で、本文の主張ではない。印の段落そのものは本文に残る。
    """
    from app.rag.answer_metrics import agent_system_headings, grounding_text

    assert set(UNVERIFIED_SECTION["headings"]) <= agent_system_headings()
    assert grounding_text(case["answer"]) == grounding_text(case["body"])
    for heading in UNVERIFIED_SECTION["headings"]:
        assert heading not in grounding_text(case["answer"])


def test_grounding_text_excludes_the_section_of_saved_agent_answers() -> None:
    """#2738 より前に保存した Agent の回答の「資料で確認できず除いた内容」の節も数えない。

    #1456。
    """
    from app.rag.answer_metrics import AGENT_UNVERIFIED_HEADING, grounding_text

    answer = (
        "承認の記録は 5 年間保管します。\n\n## 資料からは確認できない点\n- 保管の場所は記載が無い。"
        f"\n\n**{AGENT_UNVERIFIED_HEADING}**\n\n- 根拠で確かめられない: 「延長は 90 日まで」"
    )
    body = (
        "承認の記録は 5 年間保管します。\n\n## 資料からは確認できない点\n- 保管の場所は記載が無い。"
    )
    assert grounding_text(answer) == grounding_text(body)


def test_grounding_text_keeps_marked_passages_and_drops_the_system_sections() -> None:
    """faithfulness は印の段落を本文として数え、外した段落・確認が必要な点の節は数えない。

    #2738。
    """
    from rag_engine.generation.answer_labels import answer_labels

    from app.rag.answer_metrics import grounding_text

    labels = answer_labels()
    rag_answer = "\n".join(
        [
            "設定は 2 段階で変更します。",
            "",
            labels.rules_section,
            "",
            "・区分Ⅱの選択肢の値は 01 です。",
            "根拠：設定.pdf p.1",
            "",
            labels.removed_section,
            "",
            labels.removed_section_note,
            "・「変更は翌月から反映されます。」：根拠と矛盾（当月から反映）",
            "",
            labels.marked_section,
            "",
            labels.marked_section_note,
            "・「区分Ⅱの選択肢の値は 01 です。」：根拠の表の対応と違う可能性",
        ]
    )
    assert grounding_text(rag_answer).splitlines() == [
        "設定は 2 段階で変更します。",
        "・区分Ⅱの選択肢の値は 01 です。",
    ]
    agent_answer = (
        "登録後は自動で通知されます。\n\n**根拠と矛盾し除いた内容**\n\n"
        "- 「翌月から反映」（根拠と矛盾）"
        "\n\n## 確認が必要な点\n- 「登録後は自動で通知されます。」：根拠で確かめられない"
    )
    assert grounding_text(agent_answer) == "登録後は自動で通知されます。"
