"""業務ガイドを回答に使う（#1238）。照合・条件の状態・進め方・確認・MCP。

内容は架空の「サンプル業務ポータル」の例だけを使う。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.api.routes import search_answer_profile_knowledge as knowledge_route
from app.main import app
from app.mcp import tools as mcp_tools
from app.rag.support_guide_runtime import (
    apply_guide_to_diagnostics,
    clarification_questions,
    guide_clarification,
    guide_rule,
    guide_short_circuit_outcome,
    match_guide,
    rank_guides,
    resolve_guide_clarification,
    short_circuit_answer,
    with_guide_rule,
)
from app.schemas.search import SearchDiagnostics, SearchResponse
from app.schemas.support_guide import SupportGuideContent
from tests.support import AsgiTestClient
from tests.test_mcp_api import _call, _token, auth  # noqa: F401 - fixture を使う
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)


def grant_guide(**overrides: Any) -> SupportGuideContent:
    payload: dict[str, Any] = {
        "title": "アクセス権限を付与する",
        "goal": {
            "expected_result": "対象の利用者が機能を使える",
            "intent_examples": ["アクセス権限を付与したい"],
            "match_terms": ["権限を付与", "アクセス権限"],
        },
        "conditions": [
            {
                "id": "target",
                "label": "付与先",
                "allowed_values": ["個別", "グループ"],
                "question": "権限は個別の利用者とグループのどちらに付けますか？",
            },
            {
                "id": "approved",
                "label": "部門長の承認",
                "type": "boolean",
                "required": False,
                "unknown_handling": "branch",
            },
        ],
        "steps": [
            {"id": "open", "title": "詳細画面の権限タブを開く", "retrieval_hints": ["権限タブ"]},
            {"id": "grant", "title": "権限を選んで付与する", "depends_on": ["open"]},
        ],
        "branches": [
            {
                "id": "b1",
                "when": {"condition_id": "target", "values": ["グループ"]},
                "goto_step": "grant",
                "note": "承認を得てから",
            },
        ],
        "impact": {"scope": "group", "approval_required": True},
        "handoff": {"conditions": ["承認者が不在"], "contact": "サポート窓口"},
    }
    payload.update(overrides)
    return SupportGuideContent.model_validate(payload)


def export_guide() -> SupportGuideContent:
    return SupportGuideContent.model_validate(
        {
            "title": "地域別集計表を出力する",
            "goal": {"expected_result": "集計表を得る", "match_terms": ["地域別集計表"]},
            "steps": [{"id": "s1", "title": "集計メニューから出力する"}],
        }
    )


GUIDES = [("g-grant", 2, grant_guide()), ("g-export", 1, export_guide())]


# ---- 照合と条件 --------------------------------------------------------------------------


def test_match_picks_the_best_guide_and_reads_conditions() -> None:
    match = match_guide(GUIDES, "アクセス権限を付与したいです")
    assert match is not None
    assert (match.guide_id, match.revision) == ("g-grant", 2)
    # 付与先が分からず、確かめる（承認は必須でないので聞かない）。
    assert match.decision == "clarify"
    assert [condition.id for condition in match.unknown] == ["target"]

    # 質問に選択肢の語が 1 つだけ出れば既知。
    known = match_guide(GUIDES, "グループにアクセス権限を付与したい")
    assert known is not None and known.decision == "answer"
    assert known.summary()["known_conditions"][0] == {
        "id": "target",
        "label": "付与先",
        "value": "グループ",
        "source": "question",
    }
    # 両方出たら決めつけない。
    assert match_guide(GUIDES, "個別とグループの権限を付与したい").decision == "clarify"  # type: ignore[union-attr]
    # 渡した値（確認の答え）を優先する。不正な値は使わない。
    assert match_guide(GUIDES, "権限を付与", {"target": "個別"}).decision == "answer"  # type: ignore[union-attr]
    assert match_guide(GUIDES, "権限を付与", {"target": "全員"}).decision == "clarify"  # type: ignore[union-attr]
    # 言い換え（value_aliases）が質問に出れば、その選択肢が分かっているとみなす。
    aliased = [
        (
            "g-a",
            1,
            grant_guide(
                conditions=[
                    {
                        "id": "target",
                        "label": "付与先",
                        "allowed_values": ["個別", "グループ"],
                        "question": "どちらですか？",
                        "value_aliases": {"個別": ["検証用アカウント"]},
                    }
                ]
            ),
        )
    ]
    by_alias = match_guide(aliased, "検証用アカウントにアクセス権限を付与したい")
    assert by_alias is not None and by_alias.decision == "answer"
    assert by_alias.summary()["known_conditions"][0]["value"] == "個別"
    # チャット（送信の前に確かめる）では、残った不明の条件は分岐で答える。
    assert match_guide(GUIDES, "権限を付与", interactive=True).decision == "branch"  # type: ignore[union-attr]

    assert match_guide(GUIDES, "パスワードの長さは？") is None
    expired = [("g-old", 1, grant_guide(applicability={"effective_to": "2026-01-01"}))]
    assert match_guide(expired, "権限を付与", today=date(2026, 10, 7)) is None


def test_handoff_and_ranking() -> None:
    handoff = grant_guide(
        conditions=[
            {
                "id": "target",
                "label": "付与先",
                "allowed_values": ["個別", "グループ"],
                "unknown_handling": "handoff",
            }
        ]
    )
    match = match_guide([("g", 1, handoff)], "権限を付与したい")
    assert match is not None and match.decision == "handoff"
    assert "サポート窓口へお問い合わせください" in short_circuit_answer(match)

    ranked = rank_guides(GUIDES, "アクセス権限を付与して地域別集計表も出したい", limit=5)
    assert [item.guide_id for item in ranked] == ["g-grant", "g-export"]
    assert rank_guides(GUIDES, "アクセス権限を付与", limit=1)[0].guide_id == "g-grant"


def test_guide_becomes_a_pinned_rule_with_steps_and_hints() -> None:
    match = match_guide(GUIDES, "グループにアクセス権限を付与したい")
    assert match is not None
    rule = guide_rule(match)
    assert rule["tags"] == ["pinned", "support_guide"]
    assert rule["triggers"] == ["権限タブ"]
    assert "分かっている条件: 付与先=グループ" in rule["content"]
    assert "付与先が「グループ」以外の場合の手順は答えに含めない" in rule["content"]
    assert "手順 2. 権限を選んで付与する（open の後）" in rule["content"]
    assert "実施の前に承認が要る" in rule["content"]
    payload = with_guide_rule({"terms": [{"term": "x"}], "rules": [{"id": "r1"}]}, match)
    assert [item["id"] for item in payload["rules"]] == ["guide-g-grant", "r1"]
    assert payload["terms"] == [{"term": "x"}]


def test_short_circuit_and_diagnostics() -> None:
    match = match_guide(GUIDES, "アクセス権限を付与したい")
    assert match is not None
    questions = clarification_questions(match)
    assert questions == [
        {
            "condition_id": "target",
            "label": "付与先",
            "question": "権限は個別の利用者とグループのどちらに付けますか？",
            "options": ["個別", "グループ"],
        }
    ]
    guide = {
        **match.summary(),
        "clarifications": questions,
        "short_answer": short_circuit_answer(match),
    }
    outcome = guide_short_circuit_outcome(guide)
    assert outcome.citations == []
    assert outcome.diagnostics["outcome"] == "needs_clarification"
    assert outcome.diagnostics["envelope"]["clarifications"] == questions
    assert outcome.diagnostics["guide"]["guide_id"] == "g-grant"
    assert "（個別／グループ）" in outcome.answer

    branch = {
        **match.summary(),
        "decision": "branch",
        "unknown_conditions": [{"id": "target", "label": "付与先", "handling": "ask"}],
    }
    diagnostics: dict[str, Any] = {
        "outcome": "answered",
        "envelope": {"outcome": "answered", "conditions": []},
    }
    apply_guide_to_diagnostics(diagnostics, branch)
    assert diagnostics["outcome"] == "conditional"
    assert diagnostics["envelope"]["conditions"] == ["付与先"]
    assert diagnostics["envelope"]["guide"]["revision"] == 2


def test_chat_clarification_round_trip() -> None:
    match = match_guide(GUIDES, "アクセス権限を付与したい")
    assert match is not None
    found = guide_clarification(match)
    assert found is not None
    rule_id, title, clarification = found
    assert (rule_id, title) == ("guide:g-grant:target", "アクセス権限を付与する")
    assert [option.label for option in clarification.options] == ["個別", "グループ"]
    assert clarification.multiple is False
    resolved = resolve_guide_clarification(GUIDES, rule_id, ["o2"])
    assert resolved is not None
    conditions, context = resolved
    assert conditions == {"target": "グループ"}
    assert "選んだ答え: グループ" in context
    assert resolve_guide_clarification(GUIDES, rule_id, ["o9"]) is None
    assert resolve_guide_clarification(GUIDES, "guide:missing:target", ["o1"]) is None
    assert resolve_guide_clarification(GUIDES, "R1", ["o1"]) is None


# ---- API ---------------------------------------------------------------------------------


def test_clarification_suggest_falls_back_to_guide_conditions(monkeypatch: MonkeyPatch) -> None:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)

    async def published(_oracle: object, _profile: str) -> list[Any]:
        return GUIDES

    monkeypatch.setattr(knowledge_route, "_published_guides", published)
    body = client.post(
        "/api/search-answer-profiles/bv-1/clarifications/suggest",
        json={"query": "アクセス権限を付与したい"},
    ).json()["data"]["suggestion"]
    assert body["rule_id"] == "guide:g-grant:target"
    assert body["clarification"]["question"].startswith("権限は個別の利用者と")
    none = client.post(
        "/api/search-answer-profiles/bv-1/clarifications/suggest",
        json={"query": "グループにアクセス権限を付与したい"},
    ).json()["data"]
    assert none["suggestion"] is None


class _Profile:
    id = "bv-1"


class _FakeOracle:
    async def get_search_answer_profile(self, profile_id: str) -> Any:
        return _Profile() if profile_id == "bv-1" else None


@pytest.mark.usefixtures("auth")
def test_mcp_lookup_guides_and_search_clarifications(auth: Any, monkeypatch: MonkeyPatch) -> None:  # noqa: F811
    async def published(_oracle: object, _profile: str) -> list[Any]:
        return GUIDES

    monkeypatch.setattr(mcp_tools, "OracleClient", _FakeOracle)
    monkeypatch.setattr(search_route, "_published_guides", published)
    user = auth.user_with_permissions("searcher", ["menu.search"])
    body = _call(
        "rag_lookup_guides",
        {"query": "アクセス権限を付与したい", "search_answer_profile_id": "bv-1"},
        _token(user.user_uuid),
    )["structuredContent"]
    guide = body["guides"][0]
    assert (guide["guide_id"], guide["revision"], guide["decision"]) == ("g-grant", 2, "clarify")
    assert guide["clarifications"][0]["options"] == ["個別", "グループ"]
    assert [step["id"] for step in guide["steps"]] == ["open", "grant"]
    assert (guide["impact_scope"], guide["approval_required"]) == ("group", True)
    known = _call(
        "rag_lookup_guides",
        {
            "query": "アクセス権限を付与したい",
            "search_answer_profile_id": "bv-1",
            "conditions": {"target": "個別"},
        },
        _token(user.user_uuid),
    )["structuredContent"]["guides"][0]
    assert known["decision"] == "answer"
    assert known["known_conditions"][0]["value"] == "個別"
    missing = _call(
        "rag_lookup_guides",
        {"query": "x", "search_answer_profile_id": "other"},
        _token(user.user_uuid),
    )
    assert missing["isError"] is True

    match = match_guide(GUIDES, "アクセス権限を付与したい")
    assert match is not None
    outcome = guide_short_circuit_outcome(
        {
            **match.summary(),
            "clarifications": clarification_questions(match),
            "short_answer": short_circuit_answer(match),
        }
    )
    captured: dict[str, Any] = {}

    async def fake_run(request: Any) -> SearchResponse:
        captured["conditions"] = request.conditions
        return SearchResponse(
            answer=outcome.answer,
            citations=[],
            trace_id="t-1",
            elapsed_ms=1.0,
            diagnostics=SearchDiagnostics(answer=outcome.diagnostics),
        )

    monkeypatch.setattr(search_route, "_run_search_with_timeout", fake_run)
    result = _call(
        "rag_search",
        {
            "query": "アクセス権限を付与したい",
            "search_answer_profile_id": "bv-1",
            "conditions": {"approved": "はい"},
        },
        _token(user.user_uuid),
    )["structuredContent"]
    assert captured["conditions"] == {"approved": "はい"}
    assert result["outcome"] == "needs_clarification"
    assert result["clarifications"][0]["condition_id"] == "target"
    assert result["guide"]["guide_id"] == "g-grant"
    assert result["evidence"] == []
