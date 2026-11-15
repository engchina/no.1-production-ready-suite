"""RAG の入口の処理の順（#5099・#5100）: 基準日 → プロファイルの解決 → 質問の文を使う最初の処理
（入力の安全チェック・チャットの補完）→ 業務ガイドの照合。合成の質問と fake の DB で確かめる。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pr_system_settings.guardrails import GuardrailResult
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.config import get_settings
from app.rag.answer_engine import AS_OF_FILTER_KEY, _with_effective_as_of
from app.schemas.search import SearchRequest
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle
from tests.test_support_guide_runtime import GUIDES

pytestmark = pytest.mark.usefixtures("system_access")


class _Store:
    def __init__(self, _oracle: object) -> None:
        pass

    async def published_contents(self, _profile: str) -> list[Any]:
        return GUIDES


def _install(monkeypatch: MonkeyPatch, order: list[str]) -> None:
    fake = FakeKnowledgeOracle(support_guide_enabled=True)
    monkeypatch.setattr(search_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(search_route, "SupportGuideStore", _Store)
    build = search_route.build_guide_query

    async def spy_build(*args: Any, **kwargs: Any) -> Any:
        order.append(f"guide:{args[4].splitlines()[-1]}")
        return await build(*args, **kwargs)

    monkeypatch.setattr(search_route, "build_guide_query", spy_build)


async def test_gate_runs_after_the_profile_and_the_guide_uses_the_gated_question(
    monkeypatch: MonkeyPatch,
) -> None:
    order: list[str] = []
    _install(monkeypatch, order)

    async def gate(request: SearchRequest, settings: Any) -> SearchRequest:
        # プロファイルの解決（参照 KB の展開）は済んでいる。
        assert request.knowledge_base_ids
        order.append("gate")
        return request.model_copy(update={"query": "合成の補った質問"})

    request = SearchRequest(query="合成の短い発話", search_answer_profile_id="bv-1")
    resolved, _, _, applied = await search_route._resolve_query_context(
        request, get_settings(), question_gate=gate
    )
    assert applied == "bv-1"
    assert order == ["gate", "guide:合成の補った質問"]
    assert resolved.query == "合成の補った質問"


async def test_stopped_question_is_not_matched_against_guides(monkeypatch: MonkeyPatch) -> None:
    order: list[str] = []
    _install(monkeypatch, order)

    async def gate(_request: SearchRequest, _settings: Any) -> None:
        order.append("gate")
        return None

    request = SearchRequest(query="合成の止める質問", search_answer_profile_id="bv-1")
    resolved, settings, _, _ = await search_route._resolve_query_context(
        request, get_settings(), question_gate=gate
    )
    assert order == ["gate"]
    assert resolved.query == "合成の止める質問"
    assert not settings.rag_support_guide


async def test_search_entry_checks_the_question_before_the_guide(
    monkeypatch: MonkeyPatch,
) -> None:
    order: list[str] = []
    _install(monkeypatch, order)

    async def check(request: SearchRequest, _settings: Any) -> tuple[SearchRequest, Any]:
        order.append("guardrail")
        masked = request.model_copy(update={"query": "合成の [機微情報] の質問"})
        return masked, GuardrailResult(allowed=True, sanitized_text=masked.query, findings=[])

    monkeypatch.setattr(search_route, "check_search_question", check)
    request = SearchRequest(query="合成の個人情報の質問", search_answer_profile_id="bv-1")
    (resolved, *_), checked = await search_route._checked_query_context(request, get_settings())
    assert order == ["guardrail", "guide:合成の [機微情報] の質問"]
    assert resolved.query == "合成の [機微情報] の質問"
    assert checked.allowed


async def test_as_of_is_decided_once_at_the_entry(monkeypatch: MonkeyPatch) -> None:
    _install(monkeypatch, [])
    monkeypatch.setattr(search_route, "settings_today", lambda: date(2026, 1, 2))
    request = SearchRequest(query="合成の質問", search_answer_profile_id="bv-1")
    resolved, settings, _, _ = await search_route._resolve_query_context(request, get_settings())
    assert settings.rag_answer_as_of == "2026-01-02"
    # 回答エンジンは入口で決めた日を使い、明示した基準日は変えない。
    effective = _with_effective_as_of(resolved, settings.rag_answer_as_of)
    assert effective.filters[AS_OF_FILTER_KEY] == "2026-01-02"
    explicit = resolved.model_copy(update={"filters": {AS_OF_FILTER_KEY: "2025-04-01"}})
    assert _with_effective_as_of(explicit, settings.rag_answer_as_of).filters == {
        AS_OF_FILTER_KEY: "2025-04-01"
    }
