"""合成 KB・プロファイルの実 SQL と RRF の権限境界を検証する。AI は決定論の埋め込み。"""

from typing import Any
from uuid import uuid4

import pytest

from app.api.routes.search import _resolve_query_context
from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.chunking import Chunk
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.schemas.search import SearchRequest
from tests.test_answer_scope_oracle import _indexed_document


class _Embedding:
    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


@pytest.mark.usefixtures("oracle_db")
async def test_weighted_raw_lists_keep_profile_and_explicit_permission_scope() -> None:
    oracle = OracleClient()
    token = uuid4().hex[:8]
    kb = await oracle.create_knowledge_base(name=f"合成 RRF KB {token}")
    other = await oracle.create_knowledge_base(name=f"合成 RRF 範囲外 KB {token}")
    doc_ids = []
    for index, kb_id in enumerate([kb.id, kb.id, other.id]):
        text = f"合成項目 {index} の説明です。"
        doc_ids.append(
            await _indexed_document(
                oracle,
                file_name=f"rrf-{token}-{index}.pdf",
                chunks=[
                    Chunk(
                        text=text,
                        index=0,
                        start_offset=0,
                        end_offset=len(text),
                        metadata={"page_number": 1},
                    )
                ],
                knowledge_base_ids=[kb_id],
            )
        )
    profile = await oracle.create_search_answer_profile(
        name=f"合成 RRF profile {token}",
        config=SearchAnswerProfileConfig(knowledge_base_ids=[kb.id]),
    )
    settings = Settings(
        rag_auto_field_filter_enabled=False,
        rag_support_guide_enabled=False,
        rag_reference_expansion_enabled=False,
        rag_entity_expansion_enabled=False,
        rag_graph_expansion_enabled=False,
    )
    request, effective, _, _ = await _resolve_query_context(
        SearchRequest(query="合成項目", search_answer_profile_id=profile.id), settings
    )
    state = _SearchState()
    engine = AnswerEngine(effective, oracle=oracle, genai=_Embedding())  # type: ignore[arg-type]
    result = await engine._search(
        request,
        state,
        retrieval_queries=["合成項目", "追加検索文"],
        vector_only_queries=["合成項目", "追加検索文"],
        expand_references=False,
    )
    assert {chunk.document_id for chunk in result.child_chunks} == set(doc_ids[:2])
    rrf = state.retrieval_stages[-1]["rrf"]
    assert [item["weight"] for item in rrf["lists"]] == pytest.approx([7 / 15, 0.2])
    restricted = set_audit_request_context(
        AuditRequestContext(
            allowed_document_ids=frozenset([doc_ids[0]]),
            allowed_knowledge_base_ids=frozenset([kb.id]),
            allowed_search_answer_profile_ids=frozenset([profile.id]),
        )
    )
    try:
        # 利用者の文書権限と明示した範囲を SQL に残し、別 KB と別文書を混ぜない。
        scoped_request = request.model_copy(
            update={
                "filters": {
                    **request.filters,
                    "document_id": doc_ids[0],
                    "page_number_min": "1",
                    "page_number_max": "1",
                }
            }
        )
        result = await engine._search(
            scoped_request,
            _SearchState(),
            retrieval_queries=["合成項目"],
            vector_only_queries=["合成項目"],
            expand_references=False,
        )
        assert {chunk.document_id for chunk in result.child_chunks} == {doc_ids[0]}
    finally:
        reset_audit_request_context(restricted)
