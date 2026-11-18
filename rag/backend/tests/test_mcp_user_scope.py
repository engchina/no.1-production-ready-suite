"""MCP の入口(``POST /api/mcp``)から、呼び出し元が指定した条件・基準日・
サービストークンの範囲(検索・回答プロファイルと KB)が残ることを確かめる(#3260)。

``rag_search``(基本の検索。#5352)を、サービストークン
(claim ``profile_ids``。#1379)で呼ぶ。検索 API の解決・
``RagPipeline``・``AnswerEngine`` は本物を動かし、Oracle・embedding・LLM と実体の拡張の
計画だけを決定論の代役にする(内容は合成)。

- すべての検索に、呼び出し元の条件と基準日が残り、根拠の拡張（実体・関係情報）はしない。
- どの検索も、トークンの範囲に絞った監査 context(認可の述語)で Oracle を読む。
- 拡張に呼び出し元の条件・認可が残ることは、回答の工程のテスト（test_answer_user_scope.py）が
  確かめる（MCP で拡張する rag_chat はナレッジベース・filters を受けない）。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from pr_system_settings.auth.service_token import issue_service_token
from pytest import MonkeyPatch

import app.rag.answer_engine as answer_engine
import app.rag.pipeline as pipeline_module
from app.api.routes import search as search_route
from app.clients.oracle import _oracle_retrieval_where
from app.config import get_settings
from app.main import app
from app.mcp import profile_scope
from app.mcp import tools as mcp_tools
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from tests.security_support import ProductionAuth, enable_production_auth
from tests.support import AsgiTestClient
from tests.test_answer_engine import FakeGenAi, _fake_llm
from tests.test_answer_user_scope import (
    MANUAL_FIELD,
    ContextReferenceOracle,
    PlanRecorder,
    ScopeRecordingOracle,
    _field_values,
    _manual_fields,
    _reference_corpus,
)
from tests.test_cross_references import (
    _section_corpus,
)
from tests.test_mcp_profile_scope import SECRET
from tests.test_section_expansion_answer import SectionMapStub
from tests.test_security_scope import ScopedViewOracle

client = AsgiTestClient(app)
VIEWS = {
    "bv-1": SearchAnswerProfileConfig(knowledge_base_ids=["kb-1", "kb-2"]),
    "bv-2": SearchAnswerProfileConfig(knowledge_base_ids=["kb-3"]),
}
# トークンの範囲(bv-1)の参照先の KB(利用者の KB ∩ 範囲)。
SCOPED_KNOWLEDGE_BASES = frozenset({"kb-1", "kb-2"})
TODAY = date(2026, 4, 1)
QUESTION = "sample.pdf の12ページの登録手順は？"
TOOLS = [pytest.param("rag_search", {}, id="rag_search")]


class _ResultLookups:
    """MCP の結果を組むときの補助の読み取り(抽出のレシピ・旧版)。何も無いとして返す。"""

    async def chunk_set_extraction_recipe_ids(self, chunk_set_ids: list[str]) -> dict[str, str]:
        return {}

    async def retrieval_older_versions(
        self, filters: dict[str, str], *_: Any, **__: Any
    ) -> list[Any]:
        return []


class ScopedViewSearchOracle(_ResultLookups, ScopedViewOracle, ScopeRecordingOracle):
    """検索・回答プロファイルの解決(範囲で絞る)と検索の条件の記録を受ける代役。"""

    def __init__(self) -> None:
        ScopedViewOracle.__init__(self, VIEWS)
        ScopeRecordingOracle.__init__(self)


class ScopedViewReferenceOracle(_ResultLookups, ScopedViewOracle, ContextReferenceOracle):
    """検索・回答プロファイルの解決と、交差参照の参照先を返す検索の両方を受ける代役。"""

    def __init__(self) -> None:
        reference = _reference_corpus()
        ScopedViewOracle.__init__(self, VIEWS)
        ContextReferenceOracle.__init__(self, reference.hits, reference.sections)


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> ProductionAuth:
    production = enable_production_auth(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_service_token_secret", SECRET)
    for name, value in {
        "rag_reference_expansion_enabled": True,
        "rag_entity_expansion_enabled": True,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(profile_scope, "OracleClient", lambda *_a, **_k: ScopedViewOracle(VIEWS))
    monkeypatch.setattr(answer_engine, "_today", lambda: TODAY)
    # 基準日は入口で 1 回だけ決める（#5099）。
    monkeypatch.setattr(search_route, "settings_today", lambda: TODAY)
    return production


@pytest.fixture
def plans(monkeypatch: MonkeyPatch) -> Iterator[PlanRecorder]:
    entity = PlanRecorder()
    monkeypatch.setattr(answer_engine, "plan_entity_expansion", entity)
    yield entity


def _install(monkeypatch: MonkeyPatch, oracle: Any, genai: Any) -> None:
    """検索 API・MCP・pipeline の Oracle と embedding を代役にする。"""
    for module in (search_route, mcp_tools, pipeline_module):
        monkeypatch.setattr(module, "OracleClient", lambda *_a, **_k: oracle)
    monkeypatch.setattr(pipeline_module, "OciGenAiClient", lambda *_a, **_k: genai)

    async def no_sections(chunk_ids: list[str]) -> dict[str, Any]:
        return {}  # 根拠の所属の章節(結果の表示の補助)は読まない

    monkeypatch.setattr(mcp_tools, "evidence_sections", no_sections)


def _headers(auth: ProductionAuth) -> dict[str, str]:
    user = auth.user_with_permissions(
        "searcher",
        ["menu.search"],
        search_answer_profile_ids=["bv-1", "bv-2"],
        knowledge_base_ids=["kb-1", "kb-2", "kb-3"],
    )
    token = issue_service_token(
        SECRET,
        subject=user.user_uuid,
        audience="rag",
        issuer="agent",
        claims={"run_id": "run-1", "agent_id": "agent-1"},
        profile_ids=["bv-1"],
    )
    return {"Authorization": f"Bearer {token}"}


def _call(name: str, arguments: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    assert result["isError"] is False, result
    return result


def _assert_token_scope(contexts: list[AuditRequestContext]) -> None:
    """どの読み取りも、トークンの範囲に絞った同じ監査 context(認可の述語)で Oracle を読む。"""
    assert contexts
    assert {context.allowed_search_answer_profile_ids for context in contexts} == {
        frozenset({"bv-1"})
    }
    assert {context.allowed_knowledge_base_ids for context in contexts} == {SCOPED_KNOWLEDGE_BASES}


def _access_knowledge_bases(context: AuditRequestContext, filters: dict[str, str]) -> set[object]:
    """その監査 context で組む検索の SQL の、ナレッジベースの認可の述語の bind の値。"""
    token = set_audit_request_context(context)
    try:
        _sql, binds = _oracle_retrieval_where(dict(filters))
    finally:
        reset_audit_request_context(token)
    return {value for key, value in binds.items() if key.startswith("access_knowledge_base_id_")}


@pytest.mark.parametrize("tool,extra", TOOLS)
@pytest.mark.parametrize(
    "caller_filters,as_of",
    [
        pytest.param({"as_of": "2020-06-30"}, "2020-06-30", id="explicit-as-of"),
        pytest.param({}, TODAY.isoformat(), id="default-as-of"),
        # 呼び出し元が文書名・ページを指定したので、質問の文書名・ページは足さない。
        pytest.param(
            {"file_name": "manual", "page_number_min": "1", "page_number_max": "3"},
            TODAY.isoformat(),
            id="caller-file-name-and-page",
        ),
    ],
)
def test_mcp_keeps_caller_conditions_as_of_and_token_scope(
    auth: ProductionAuth,
    monkeypatch: MonkeyPatch,
    plans: PlanRecorder,
    tool: str,
    extra: dict[str, Any],
    caller_filters: dict[str, str],
    as_of: str,
) -> None:
    """すべての検索に呼び出し元の条件が残り、根拠の拡張はしない（基本の検索。#5352）。

    呼び出し元の条件は文書名・ページ・分類・手の項目の条件・旧版の扱い(include_superseded)・
    基準日。ナレッジベースはトークンの範囲のプロファイルの参照先。
    """
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)
    oracle = ScopedViewSearchOracle()
    _install(monkeypatch, oracle, FakeGenAi())
    filters = {
        "large_category": "分類A",
        "extraction_fields": _manual_fields(),
        **caller_filters,
    }

    _call(
        tool,
        {
            "query": QUESTION,
            "search_answer_profile_id": "bv-1",
            "filters": filters,
            "include_superseded": True,
            **extra,
        },
        _headers(auth),
    )

    expected = {
        **filters,
        "knowledge_base_id": "kb-1,kb-2",
        "include_superseded": "true",
        "as_of": as_of,
    }
    assert oracle.filters
    first = oracle.filters[0]
    # 質問の文書名・ページは DB 条件にしない。呼び出し元の明示条件を維持する。
    if "file_name" in caller_filters:
        assert (first.get("file_name"), first.get("page_number_min")) == ("manual", "1")
    else:
        assert (first.get("file_name"), first.get("page_number_min")) == (None, None)
    manual = (MANUAL_FIELD.name, MANUAL_FIELD.value)
    # 項目の条件は呼び出し元が明示したものだけ（質問から条件を足さない）。
    assert _field_values(first) == [manual]
    # どの検索も同じ条件で検索する。
    assert all(item == first for item in oracle.filters)
    # どの検索にも、呼び出し元の条件・KB・基準日が残る。
    scalar = {key: value for key, value in expected.items() if key != "extraction_fields"}
    for item in oracle.filters:
        assert {key: item.get(key) for key in scalar} == scalar
        assert _field_values(item) == [manual]
    # 基本の検索は実体の拡張をしない。
    entity = plans
    assert not entity.scopes
    # 認可の範囲は、すべての検索で、トークンの範囲に絞った監査 context の述語で当たる。
    _assert_token_scope(oracle.contexts)
    assert _access_knowledge_bases(oracle.contexts[-1], expected) == set(SCOPED_KNOWLEDGE_BASES)


class SectionReadRecorder(SectionMapStub):
    """章節の本文を読むときの監査 context も残す章節の代役。"""

    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[AuditRequestContext] = []

    async def read_section(self, document_id: str, section_id: str, **kwargs: Any) -> Any:
        from app.rag.request_context import current_audit_request_context

        self.contexts.append(current_audit_request_context())
        return await super().read_section(document_id, section_id, **kwargs)


class ScopedViewSectionReferenceOracle(_ResultLookups, ScopedViewOracle, ContextReferenceOracle):
    """検索・回答プロファイルの解決と、文書 A の候補が文書 B の第3章を参照する検索を受ける代役。"""

    def __init__(self) -> None:
        corpus = _section_corpus()
        ScopedViewOracle.__init__(self, VIEWS)
        ContextReferenceOracle.__init__(self, corpus[:1], corpus)
