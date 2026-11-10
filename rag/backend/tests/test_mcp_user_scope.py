"""MCP の入口(``POST /api/mcp``)から、自動の検索条件を足しても、呼び出し元が指定した条件・基準日・
サービストークンの範囲(検索・回答プロファイルと KB)が残ることを確かめる(#3260)。

``rag_search``(回答あり)と ``rag_retrieve_evidence``(planning=rag /
caller。#2651)を、サービストークン(claim ``profile_ids``。#1379)で呼ぶ。検索 API の解決・
``RagPipeline``・``AnswerEngine`` は本物を動かし、Oracle・embedding・LLM と実体・関係情報の拡張の
計画だけを決定論の代役にする(内容は合成)。

- すべての検索、実体・関係情報の拡張の範囲、章節の拡張の読み取りに、呼び出し元の条件と基準日が残る。
- どの検索・拡張も、トークンの範囲に絞った監査 context(認可の述語)で Oracle を読む。
- 本文が明記した参照と章節の補充も、明示した文書名・分類・基準日と認可の範囲を保つ(#4026)。
"""

from __future__ import annotations

import json
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
from app.rag import section_map_service
from app.rag.pipeline import RagPipeline
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.schemas.search import ExtractionFieldCondition, SearchRequest
from tests.security_support import ProductionAuth, enable_production_auth
from tests.support import AsgiTestClient
from tests.test_answer_engine import FakeGenAi, _fake_llm
from tests.test_answer_user_scope import (
    AUTO_FIELD,
    AUTO_SAME_FIELD,
    MANUAL_FIELD,
    ContextReferenceOracle,
    PlanRecorder,
    ScopeRecordingOracle,
    _field_values,
    _manual_fields,
    _reference_corpus,
)
from tests.test_cross_references import FakeGenAi as ReferenceGenAi
from tests.test_cross_references import (
    SectionScopeMap,
    _section_answer_llm,
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
TOOLS = [
    pytest.param("rag_search", {}, id="rag_search"),
    pytest.param("rag_retrieve_evidence", {}, id="retrieve-planning-rag"),
    pytest.param(
        "rag_retrieve_evidence",
        {"planning": "caller", "queries": ["登録 手順"]},
        id="retrieve-planning-caller",
    ),
]


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
        "rag_auto_field_filter_enabled": True,
        "rag_reference_expansion_enabled": True,
        "rag_entity_expansion_enabled": True,
        "rag_graph_expansion_enabled": True,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(profile_scope, "OracleClient", lambda *_a, **_k: ScopedViewOracle(VIEWS))
    monkeypatch.setattr(answer_engine, "_today", lambda: TODAY)
    return production


@pytest.fixture
def plans(monkeypatch: MonkeyPatch) -> Iterator[tuple[PlanRecorder, PlanRecorder]]:
    entity, graph = PlanRecorder(), PlanRecorder()
    monkeypatch.setattr(answer_engine, "plan_entity_expansion", entity)
    monkeypatch.setattr(answer_engine, "plan_graph_expansion", graph)
    yield entity, graph


def _install(monkeypatch: MonkeyPatch, oracle: Any, genai: Any) -> None:
    """検索 API・MCP・pipeline の Oracle と embedding を代役にし、自動の項目の条件を読ませる。"""
    for module in (search_route, mcp_tools, pipeline_module):
        monkeypatch.setattr(module, "OracleClient", lambda *_a, **_k: oracle)
    monkeypatch.setattr(pipeline_module, "OciGenAiClient", lambda *_a, **_k: genai)

    async def no_sections(chunk_ids: list[str]) -> dict[str, Any]:
        return {}  # 根拠の所属の章節(結果の表示の補助)は読まない

    monkeypatch.setattr(mcp_tools, "evidence_sections", no_sections)

    async def read_auto_fields(
        self: RagPipeline, request: SearchRequest, **kwargs: Any
    ) -> list[ExtractionFieldCondition]:
        # 質問から読み取った(自動の)項目の条件。1 つは呼び出し元の手の条件と同じ項目で別の値。
        return [AUTO_SAME_FIELD, AUTO_FIELD]

    monkeypatch.setattr(RagPipeline, "_read_field_conditions", read_auto_fields)


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
    plans: tuple[PlanRecorder, PlanRecorder],
    tool: str,
    extra: dict[str, Any],
    caller_filters: dict[str, str],
    as_of: str,
) -> None:
    """すべての検索と、実体・関係情報の拡張に、呼び出し元の条件が残る。

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
    # caller が検索文を作った場合は独立した LLM 項目条件も足さない。
    if "file_name" in caller_filters:
        assert (first.get("file_name"), first.get("page_number_min")) == ("manual", "1")
    else:
        assert (first.get("file_name"), first.get("page_number_min")) == (None, None)
    manual = (MANUAL_FIELD.name, MANUAL_FIELD.value)
    if extra.get("planning") == "caller":
        assert _field_values(first) == [manual]
    else:
        assert _field_values(first) == [manual, (AUTO_FIELD.name, AUTO_FIELD.value)]
    # どの検索も同じ条件で検索する(caller の追加の検索文を含む)。
    assert all(item == first for item in oracle.filters)
    if extra.get("planning") == "caller":
        assert "登録 手順" in oracle.queries
    # どの検索にも、呼び出し元の条件・KB・基準日が残る。
    scalar = {key: value for key, value in expected.items() if key != "extraction_fields"}
    for item in oracle.filters:
        assert {key: item.get(key) for key in scalar} == scalar
        assert manual in _field_values(item)
        assert AUTO_SAME_FIELD.value not in [value for _name, value in _field_values(item)]
    # 実体・関係情報の拡張は、主の検索と同じ呼び出し元の範囲の中で広げる。
    entity, graph = plans
    assert entity.scopes and graph.scopes
    assert all(scope == expected for scope in [*entity.scopes, *graph.scopes])
    # 認可の範囲は、すべての検索と拡張で、トークンの範囲に絞った監査 context の述語で当たる。
    _assert_token_scope([*oracle.contexts, *entity.contexts, *graph.contexts])
    assert _access_knowledge_bases(oracle.contexts[-1], expected) == set(SCOPED_KNOWLEDGE_BASES)


@pytest.mark.parametrize("tool,extra", TOOLS)
def test_mcp_reference_entity_and_graph_keep_caller_file_name(
    auth: ProductionAuth,
    monkeypatch: MonkeyPatch,
    plans: tuple[PlanRecorder, PlanRecorder],
    tool: str,
    extra: dict[str, Any],
) -> None:
    """参照・実体・関係の全補充に、呼び出し元の明示条件と認可を渡す(#4026)。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)
    oracle = ScopedViewReferenceOracle()
    _install(monkeypatch, oracle, ReferenceGenAi())
    filters = {"file_name": "合成文書A", "large_category": "分類A", "as_of": "2020-06-30"}

    result = _call(
        tool,
        {
            "query": "合成の手順",
            "search_answer_profile_id": "bv-1",
            "filters": filters,
            "include_superseded": True,
            **extra,
        },
        _headers(auth),
    )

    # 明示した文書名の外の参照先は根拠に足さない。
    evidence = result["structuredContent"]["evidence"]
    assert "doc-b:cs-9:0" not in [item["chunk_id"] for item in evidence]
    reference_filters = [
        *(call["filters"] for call in oracle.document_batch_calls),
        *(call["filters"] for call in oracle.reference_calls),
    ]
    assert reference_filters
    kept = {**filters, "knowledge_base_id": "kb-1,kb-2", "include_superseded": "true"}
    assert all(item == kept for item in reference_filters)
    # 実体・関係情報の拡張は、呼び出し元の文書名・分類を残す(自動の項目の条件は含めない)。
    entity, graph = plans
    expected = {**filters, **kept}
    assert entity.scopes and graph.scopes
    assert all(scope == expected for scope in [*entity.scopes, *graph.scopes])
    _assert_token_scope([*oracle.contexts, *entity.contexts, *graph.contexts])


class SectionReadRecorder(SectionMapStub):
    """章節の本文を読むときの監査 context も残す章節の代役。"""

    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[AuditRequestContext] = []

    async def read_section(self, document_id: str, section_id: str, **kwargs: Any) -> Any:
        from app.rag.request_context import current_audit_request_context

        self.contexts.append(current_audit_request_context())
        return await super().read_section(document_id, section_id, **kwargs)


@pytest.mark.parametrize(
    "caller_filters,as_of",
    [
        pytest.param({"as_of": "2020-06-30"}, "2020-06-30", id="explicit-as-of"),
        pytest.param({}, TODAY.isoformat(), id="default-as-of"),
    ],
)
def test_mcp_section_expansion_reads_with_caller_conditions_and_as_of(
    auth: ProductionAuth,
    monkeypatch: MonkeyPatch,
    caller_filters: dict[str, str],
    as_of: str,
) -> None:
    """章節の拡張(#1436)は、主の検索と同じ条件・基準日・トークンの範囲で読む。

    基準日は呼び出し元が明示した値か、回答の初めに決めた既定の日(#2602)。
    """
    import rag_engine.adapters.oci as engine_oci

    # CRAG の判定は候補の関連を返さない(不足)ので、章節を広げる。
    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)
    for name, value in {
        "rag_section_expansion_enabled": True,
        "rag_domain_keywords": ["受注番号"],
    }.items():
        monkeypatch.setattr(get_settings(), name, value)
    stub = SectionReadRecorder()
    monkeypatch.setattr(section_map_service, "chunk_section", stub.chunk_section)
    monkeypatch.setattr(section_map_service, "section_outline", stub.section_outline)
    monkeypatch.setattr(section_map_service, "read_section", stub.read_section)
    oracle = ScopedViewSearchOracle()
    _install(monkeypatch, oracle, FakeGenAi())
    filters = {"middle_category": "分類B", "extraction_fields": _manual_fields(), **caller_filters}

    _call(
        "rag_search",
        {"query": QUESTION, "search_answer_profile_id": "bv-1", "filters": filters},
        _headers(auth),
    )

    expected = {**filters, "knowledge_base_id": "kb-1,kb-2", "as_of": as_of}
    # 質問から文書名・ページを足さない。
    assert (oracle.filters[0].get("file_name"), oracle.filters[0].get("page_number_min")) == (
        None,
        None,
    )
    searched = oracle.filters[0]
    assert {key: searched.get(key) for key in expected if key != "extraction_fields"} == {
        key: value for key, value in expected.items() if key != "extraction_fields"
    }
    # 章節の本文は、主の検索と同じ条件(呼び出し元の条件・KB・基準日)で読む。
    assert stub.reads
    assert all(read_filters == searched for _section, read_filters in stub.reads)
    assert json.loads(expected["extraction_fields"]) == [MANUAL_FIELD.model_dump()]
    _assert_token_scope(stub.contexts)


class ScopedViewSectionReferenceOracle(_ResultLookups, ScopedViewOracle, ContextReferenceOracle):
    """検索・回答プロファイルの解決と、文書 A の候補が文書 B の第3章を参照する検索を受ける代役。"""

    def __init__(self) -> None:
        corpus = _section_corpus()
        ScopedViewOracle.__init__(self, VIEWS)
        ContextReferenceOracle.__init__(self, corpus[:1], corpus)


def test_mcp_section_expansion_does_not_read_reference_outside_caller_file_name(
    auth: ProductionAuth, monkeypatch: MonkeyPatch
) -> None:
    """明示範囲外の参照先とその章節を読み込まず、元文書の章節は同じ範囲で読む。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _section_answer_llm)
    for name, value in {
        "rag_section_expansion_enabled": True,
        "rag_entity_expansion_enabled": False,
        "rag_graph_expansion_enabled": False,
    }.items():
        monkeypatch.setattr(get_settings(), name, value)
    stub = SectionScopeMap(_section_corpus(), {"kb-1": {"doc-a", "doc-b", "doc-c"}})
    monkeypatch.setattr(section_map_service, "chunk_section", stub.chunk_section)
    monkeypatch.setattr(section_map_service, "section_outline", stub.section_outline)
    monkeypatch.setattr(section_map_service, "read_section", stub.read_section)
    oracle = ScopedViewSectionReferenceOracle()
    _install(monkeypatch, oracle, ReferenceGenAi({"手順": 0.9}))
    filters = {"file_name": "合成文書A", "large_category": "分類A", "as_of": "2020-06-30"}

    result = _call(
        "rag_search",
        {
            "query": "合成の操作の手順は？",
            "search_answer_profile_id": "bv-1",
            "filters": filters,
            "include_superseded": True,
        },
        _headers(auth),
    )

    evidence = result["structuredContent"]["evidence"]
    assert not any(item["document_id"] == "doc-b" for item in evidence)
    reference_reads = [scoped for document, _section, scoped in stub.reads if document == "doc-b"]
    assert reference_reads == []
    own_reads = [scoped for document, _section, scoped in stub.reads if document == "doc-a"]
    assert own_reads
    assert all(scoped["file_name"] == "合成文書A" for scoped in own_reads)
    _assert_token_scope(stub.contexts)
    assert "回答に未反映の関連章節" not in json.dumps(
        result["structuredContent"], ensure_ascii=False
    )
