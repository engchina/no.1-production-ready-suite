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
    result = _score(_judged(coverage=0.85))
    metrics = {metric["name"]: metric for metric in result["metrics"]}
    assert result["suite"] == "standard"
    # 正解の文書・期待する語が要る指標は 1 件の回答では測らない。
    assert set(metrics) <= {
        "faithfulness",
        "citation_traceability_coverage",
        "claim_support_rate",
        "requirement_coverage",
        "refusal_accuracy",
    }
    assert {"claim_support_rate", "requirement_coverage", "refusal_accuracy"} <= set(metrics)
    assert metrics["requirement_coverage"] == {
        "name": "requirement_coverage",
        "value": 0.85,
        "threshold": 0.8,
        "passed": True,
        "reference": False,
    }
    # strict は requirement_coverage の閾値が 0.9 なので同じ回答が不合格になる。
    strict = {m["name"]: m for m in _score(_judged(coverage=0.85), "strict")["metrics"]}
    assert strict["requirement_coverage"]["threshold"] == 0.9
    assert strict["requirement_coverage"]["passed"] is False


def test_unsupported_claim_or_refusal_fails_the_answer() -> None:
    unsupported = _score(_judged(claim="unsupported"))
    assert unsupported["passed"] is False
    assert {m["name"]: m["value"] for m in unsupported["metrics"]}["claim_support_rate"] == 0.0
    # 監査できなかった主張(unassessed)は、根拠のない主張とは数えない。
    assert {m["name"]: m["value"] for m in _score(_judged(claim="unassessed"))["metrics"]}[
        "claim_support_rate"
    ] == 1.0
    refused = _score(_judged(), answer="", citations=[])
    metrics = {m["name"]: m["value"] for m in refused["metrics"]}
    # 標準回答がある質問は答えるべき質問。拒答は refusal_accuracy 0 で、忠実さは測らない。
    assert metrics["refusal_accuracy"] == 0.0
    assert "faithfulness" not in metrics and refused["passed"] is False


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
    # 語句の一致の近似は言い換えで下がる。1 件の回答では参考値で、合否は他の指標で決める。
    result = _score(_judged(), answer="特典として商品券を差し上げます。")
    metrics = {metric["name"]: metric for metric in result["metrics"]}
    assert metrics["faithfulness"]["reference"] is True
    assert metrics["faithfulness"]["passed"] is False
    assert result["passed"] is all(
        metric["passed"] for name, metric in metrics.items() if name != "faithfulness"
    )


# Agent の「資料で確認できず除いた内容」の節の事例（RAG と Agent の両方のテストが読む。#1456）。
UNVERIFIED_SECTION: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "platform/contracts/answer-passages/unverified-section.json"
    ).read_text("utf-8")
)


@pytest.mark.parametrize("case", UNVERIFIED_SECTION["answers"], ids=lambda case: str(case["id"]))
def test_grounding_text_excludes_the_unverified_section_of_the_agent(
    case: dict[str, str],
) -> None:
    """faithfulness は Agent の最終の検証が足した節（#1456）を数えない。

    節の見出しは「資料で確認できず除いた内容」。
    D の回答は外した段落の原文と検証の理由を末尾の節に並べ、外した段落が多い Run ほど語句の一致が
    下がっていた。モデルが本文に書いた「資料からは確認できない点」などの節は本文として残す。
    """
    from app.rag.answer_metrics import AGENT_UNVERIFIED_HEADING, grounding_text

    assert UNVERIFIED_SECTION["heading"] == AGENT_UNVERIFIED_HEADING
    assert grounding_text(case["answer"]) == grounding_text(case["body"])
    if case["answer"] != case["body"]:
        assert AGENT_UNVERIFIED_HEADING not in grounding_text(case["answer"])
