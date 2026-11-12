"""回答の検索の入口(``AnswerEngine.run``)から、利用者の範囲が残ることを確かめる(#3260)。

すべての検索(CRAG の補正検索を含む)と実体・関係情報の拡張に、利用者が指定した条件(文書名・分類・
手の項目の条件・ナレッジベース・旧版の扱い)・基準日・認可の範囲(監査 context の predicate)が残る。
本文が明記した参照を辿る交差参照(#1280)でも、利用者が指定した文書名・分類の範囲を保つ。
DB と LLM は呼ばない(決定論の代役。内容は合成)。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Any, cast

import pytest

import app.rag.answer_engine as answer_engine
from app.clients.oracle import _oracle_retrieval_where
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.cross_references import ReferenceTarget
from app.rag.request_context import (
    AuditRequestContext,
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.search import ExtractionFieldCondition, RetrievedChunk, SearchMode, SearchRequest
from tests.test_answer_engine import CoverageOracle, FakeGenAi, FakeOracle, _fake_llm
from tests.test_cross_references import FakeGenAi as ReferenceGenAi
from tests.test_cross_references import ReferenceOracle, _retrieved

# 認可の範囲(文書・分類・ナレッジベース)。検索条件ではなく SQL の述語で当たる。
ACCESS = AuditRequestContext(
    request_id="request-3260",
    allowed_document_ids=frozenset({"doc-1", "doc-a", "doc-b"}),
    allowed_category_names=frozenset({"分類a"}),
    allowed_knowledge_base_ids=frozenset({"kb-1", "kb-a"}),
)
# 利用者が手で指定した項目の条件（絞り込みは利用者が明示した条件だけ）。
MANUAL_FIELD = ExtractionFieldCondition(name="区分", value_type="string", op="eq", value="甲")


def _manual_fields() -> str:
    return json.dumps([MANUAL_FIELD.model_dump()], ensure_ascii=False, separators=(",", ":"))


def _field_values(filters: dict[str, str]) -> list[tuple[str, str]]:
    return [
        (item["name"], item["value"])
        for item in json.loads(filters.get("extraction_fields") or "[]")
    ]


def _access_binds(filters: dict[str, str]) -> dict[str, object]:
    """検索の SQL に渡る認可の範囲の bind(``_oracle_retrieval_where`` が監査 context から作る)。"""
    _sql, binds = _oracle_retrieval_where(dict(filters))
    return {key: value for key, value in binds.items() if key.startswith("access_")}


@pytest.fixture
def access() -> Iterator[AuditRequestContext]:
    token = set_audit_request_context(ACCESS)
    try:
        yield ACCESS
    finally:
        reset_audit_request_context(token)


class ScopeRecordingOracle(FakeOracle):
    """検索の代役。

    検索ごとに、渡った条件と、その時点の監査 context(認可の範囲)を記録する。
    """

    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[AuditRequestContext] = []
        self.queries: list[str] = []

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        scoped = dict(filters or {})
        self.filters.append(scoped)
        self.queries.append(query)
        self.contexts.append(current_audit_request_context())
        return self.chunks[:1]


class PlanRecorder:
    """実体・関係情報の拡張の計画の代役。渡された範囲と監査 context を記録し、何も足さない。"""

    def __init__(self) -> None:
        self.scopes: list[dict[str, str]] = []
        self.contexts: list[AuditRequestContext] = []

    async def __call__(self, store: Any, filters: dict[str, str], **_: Any) -> list[Any]:
        self.scopes.append(dict(filters))
        self.contexts.append(current_audit_request_context())
        return []


@pytest.fixture
def plans(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[PlanRecorder, PlanRecorder]]:
    entity, graph = PlanRecorder(), PlanRecorder()
    monkeypatch.setattr(answer_engine, "plan_entity_expansion", entity)
    monkeypatch.setattr(answer_engine, "plan_graph_expansion", graph)
    yield entity, graph


def _engine(oracle: FakeOracle, **settings: Any) -> AnswerEngine:
    return AnswerEngine(
        Settings(
            rag_reference_expansion_enabled=True,
            rag_entity_expansion_enabled=True,
            rag_graph_expansion_enabled=True,
            **settings,
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, FakeGenAi()),
        entity_store=cast(Any, object()),
        graph_store=cast(Any, object()),
    )


@pytest.mark.parametrize(
    ("question", "user_filters"),
    [
        (
            "sample.pdf の12ページの登録手順は？",
            {
                "knowledge_base_id": "kb-1",
                "file_name": "manual",
                "large_category": "分類A",
                "include_superseded": "true",
                "as_of": "2020-06-30",
            },
        ),
        (
            # 質問から文書名・ページを条件にしない。
            "sample.pdf の12ページの登録手順は？",
            {"knowledge_base_id": "kb-1", "middle_category": "分類B"},
        ),
        (
            # 利用者の明示ページを保持し、質問の文書名を条件にしない。
            "sample.pdf の12ページの登録手順は？",
            {"knowledge_base_id": "kb-1", "page_number_min": "1", "page_number_max": "3"},
        ),
    ],
    ids=["user-file-name", "question-file-name", "user-page"],
)
async def test_user_conditions_as_of_and_access_stay_in_every_search(
    monkeypatch: pytest.MonkeyPatch,
    access: AuditRequestContext,
    plans: tuple[PlanRecorder, PlanRecorder],
    question: str,
    user_filters: dict[str, str],
) -> None:
    """すべての検索と、実体・関係情報の拡張に、利用者の条件・基準日・認可の範囲が残る。"""
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)
    monkeypatch.setattr(answer_engine, "_today", lambda: date(2026, 4, 1))
    oracle = ScopeRecordingOracle()
    filters = {**user_filters, "extraction_fields": _manual_fields()}
    request = SearchRequest(query=question, filters=filters)

    outcome = await _engine(oracle).run(request)

    # 利用者の条件 + 回答の初めに決めた基準日(指定があればその日)。
    expected = {"as_of": "2026-04-01", **filters}
    assert outcome.citations
    # 項目の条件は利用者が手で指定したものだけ（質問から条件を足さない）。
    first = oracle.filters[0]
    assert _field_values(first) == [(MANUAL_FIELD.name, MANUAL_FIELD.value)]
    # どの検索も同じ条件で検索する。
    assert all(item == first for item in oracle.filters)
    # どの検索にも、利用者の条件・基準日が残る。
    for item in oracle.filters:
        assert {key: item.get(key) for key in expected if key != "extraction_fields"} == {
            key: value for key, value in expected.items() if key != "extraction_fields"
        }
        assert (MANUAL_FIELD.name, MANUAL_FIELD.value) in _field_values(item)
    # 実体・関係情報の拡張は、主の検索と同じ利用者の範囲の中で広げる(文書名・分類・項目も残す)。
    entity, graph = plans
    assert entity.scopes and graph.scopes
    assert all(scope == expected for scope in [*entity.scopes, *graph.scopes])
    # 認可の範囲は、すべての検索と拡張で同じ監査 context の述語で当たる。
    assert oracle.contexts
    assert set(oracle.contexts) == {access}
    assert set(entity.contexts) == set(graph.contexts) == {access}
    # その context で組む検索の SQL は、文書・分類・ナレッジベースの認可の述語を持つ。
    assert {"access_document_id_0", "access_category_name_0", "access_knowledge_base_id_0"} <= set(
        _access_binds(oracle.filters[-1])
    )


class ContextReferenceOracle(ReferenceOracle):
    """交差参照の代役。参照先を読むときの監査 context(認可の範囲)も記録する。"""

    def __init__(self, hits: list[RetrievedChunk], sections: list[RetrievedChunk]) -> None:
        super().__init__(hits, sections)
        self.contexts: list[AuditRequestContext] = []

    def _in_scope(self, filters: dict[str, str], chunk: RetrievedChunk) -> bool:
        file_name = filters.get("file_name")
        return super()._in_scope(filters, chunk) and (
            not file_name or file_name in (chunk.file_name or "")
        )

    async def retrieval_reference_documents(
        self, filters: dict[str, str], *, title: str, limit: int
    ) -> list[tuple[str, str, tuple[str, ...]]]:
        self.contexts.append(current_audit_request_context())
        return await super().retrieval_reference_documents(filters, title=title, limit=limit)

    async def retrieval_reference_chunks(
        self,
        filters: dict[str, str],
        *,
        document_id: str,
        section_path: str,
        chunk_set_id: str | None = None,
        limit: int,
    ) -> list[RetrievedChunk]:
        self.contexts.append(current_audit_request_context())
        return await super().retrieval_reference_chunks(
            filters,
            document_id=document_id,
            section_path=section_path,
            chunk_set_id=chunk_set_id,
            limit=limit,
        )


def _reference_corpus() -> ContextReferenceOracle:
    """利用者が指定した文書名の文書 A の候補が、本文で文書 B の節を明記して参照する。"""
    target = ReferenceTarget("「手順」", "title", "手順", "合成文書B")
    anchor = _retrieved(
        "doc-a:cs-1:0",
        "合成文書Bの「手順」を参照。",
        "第1章 概要",
        document_id="doc-a",
        file_name="合成文書A.pdf",
        targets=[target],
    )
    referenced = _retrieved(
        "doc-b:cs-9:0",
        "合成の手順の本文。",
        "第2章 手順",
        document_id="doc-b",
        file_name="合成文書B.pdf",
        chunk_set_id="cs-9",
    )
    return ContextReferenceOracle([anchor], [referenced])


async def test_reference_entity_and_graph_expansion_keep_the_explicit_file_name(
    access: AuditRequestContext, plans: tuple[PlanRecorder, PlanRecorder]
) -> None:
    """明示参照・実体・関係の補充は、全て明示範囲・基準日・認可を保つ(#4026)。"""
    oracle = _reference_corpus()
    engine = AnswerEngine(
        Settings(
            rag_reference_expansion_enabled=True,
            rag_entity_expansion_enabled=True,
            rag_graph_expansion_enabled=True,
        ),
        oracle=cast(Any, oracle),
        genai=cast(Any, ReferenceGenAi()),
        entity_store=cast(Any, object()),
        graph_store=cast(Any, object()),
    )
    filters = {
        "knowledge_base_id": "kb-a",
        "file_name": "合成文書A",
        "large_category": "分類A",
        "include_superseded": "true",
        "as_of": "2020-06-30",
    }
    request = SearchRequest(query="合成の手順", filters=filters)

    result = await engine._search(request, _SearchState(), retrieval_queries=["合成の手順"])

    # 範囲外の参照先を根拠に足さず、候補の照合にも明示条件を渡す。
    assert "doc-b:cs-9:0" not in [child.chunk_uid for child in result.child_chunks]
    reference_filters = [
        *(call["filters"] for call in oracle.document_batch_calls),
        *(call["filters"] for call in oracle.reference_calls),
    ]
    assert reference_filters
    kept = filters
    assert all(item == kept for item in reference_filters)
    # 実体・関係情報の拡張は、利用者が指定した文書名・分類を残す。
    entity, graph = plans
    assert entity.scopes == [filters]
    assert graph.scopes == [filters]
    # 認可の範囲は、参照を辿るときも同じ監査 context の述語で当たる。
    assert oracle.contexts
    assert set(oracle.contexts) == {access}
    assert set(entity.contexts) == set(graph.contexts) == {access}


class ScopeRecordingCoverageOracle(CoverageOracle):
    """要求ごとの再検索の代役(#1279)。"""

    def __init__(self) -> None:
        super().__init__()
        self.searches: list[tuple[str, dict[str, str], AuditRequestContext]] = []

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        scoped = dict(filters or {})
        self.searches.append((query, scoped, current_audit_request_context()))
        return await super().hybrid_search(query, embedding, top_k, mode, scoped)
