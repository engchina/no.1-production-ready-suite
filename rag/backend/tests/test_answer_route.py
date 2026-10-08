"""回答の経路（固定の RAG と Agent の振り分け。#1283）。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.rag.answer_route import answer_route


def _diagnostics(outcome: str, handoff: list[str] | None = None) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "envelope": {"schema_version": 1, "outcome": outcome, "handoff_reasons": handoff or []},
    }


def test_route_records_outcome_as_reason_without_escalation() -> None:
    """答えた・条件付き・確認・人への引き継ぎ・資料の不足は、固定の RAG のまま（提案しない）。"""
    for outcome in (
        "answered",
        "conditional",
        "needs_clarification",
        "needs_human",
        "insufficient_evidence",
    ):
        route = answer_route(_diagnostics(outcome, ["gaps"]))
        assert route == {
            "schema_version": 1,
            "path": "rag",
            "reason": outcome,
            "signals": ["gaps"],
            "escalation_suggested": False,
            "escalation_reason": "",
        }


def test_route_suggests_agent_when_environment_data_is_needed() -> None:
    """現場の実データの確認が要る回答だけ、Agent で続けることを提案する。"""
    route = answer_route(_diagnostics("needs_environment_data", ["environment_data"]))
    assert route["escalation_suggested"] is True
    assert route["escalation_reason"] == "needs_environment_data"
    assert route["signals"] == ["environment_data"]


def test_route_conditional_with_environment_signal_does_not_escalate() -> None:
    """手順を答えたうえで一部の値の確認が要る（conditional）回答は提案しない（回答で完了できる）。"""
    route = answer_route(_diagnostics("conditional", ["environment_data"]))
    assert route["escalation_suggested"] is False


def test_route_without_envelope_is_unknown() -> None:
    """回答の構造が無い・知らない対応は unknown にして提案しない。"""
    assert answer_route({})["reason"] == "unknown"
    assert answer_route({"outcome": "something"})["reason"] == "unknown"
    # 回答の構造が無くても、診断の outcome があれば使う。
    assert answer_route({"outcome": "needs_environment_data"})["escalation_suggested"] is True


def test_agent_app_url_setting_is_validated() -> None:
    """Agent の画面の URL は http(s) の絶対 URL だけ。末尾の / を外す（#1283）。"""
    assert Settings(_env_file=None).rag_agent_app_url == ""
    assert (
        Settings(_env_file=None, rag_agent_app_url=" https://agent.example.com/ ").rag_agent_app_url
        == "https://agent.example.com"
    )
    assert (
        Settings(_env_file=None, rag_agent_app_url="http://10.0.0.5:8080/agent").rag_agent_app_url
        == "http://10.0.0.5:8080/agent"
    )
    for invalid in ("agent.example.com", "javascript:alert(1)", "https://a.example.com/?x=1"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, rag_agent_app_url=invalid)


async def test_pipeline_records_route_for_guide_clarification() -> None:
    """業務ガイドの確認の質問（モデルを呼ばない回答）にも経路を記録する。"""
    from app.rag.pipeline import RagPipeline
    from app.schemas.search import SearchRequest

    settings = Settings(
        _env_file=None,
        rag_support_guide={
            "guide_id": "g-1",
            "revision": 1,
            "title": "権限の付与",
            "decision": "clarify",
            "short_answer": "対象は個別ですか、グループですか。",
        },
    )
    pipeline = RagPipeline(
        settings=settings,
        oracle=object(),  # type: ignore[arg-type]
        genai=object(),  # type: ignore[arg-type]
    )
    response = await pipeline.run(SearchRequest(query="権限を付与したい"))
    assert response.diagnostics.answer is not None
    route = cast(dict[str, Any], response.diagnostics.answer["route"])
    assert (route["reason"], route["escalation_suggested"]) == ("needs_clarification", False)
