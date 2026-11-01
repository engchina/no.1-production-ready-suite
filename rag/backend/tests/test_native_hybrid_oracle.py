"""合成KB・profileで原生／従来方式の範囲と一回のRRFを確認する。"""

from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

import pytest

from app.api.routes.search import _resolve_query_context
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.chunking import Chunk
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from app.schemas.search import SearchMode, SearchRequest
from tests.test_answer_scope_oracle import _indexed_document


class _Embedding:
    async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("backend", ["oracle_native", "sudachi"])
async def test_native_and_legacy_public_search_keep_scope_and_global_rrf(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_hybrid_search_backend", backend)
    oracle = OracleClient(settings)
    token = uuid4().hex[:10]
    kb = await oracle.create_knowledge_base(name=f"合成検索KB {token}")
    outside = await oracle.create_knowledge_base(name=f"合成範囲外KB {token}")
    ids = []
    for index, kb_id in enumerate([kb.id, kb.id, outside.id]):
        chunks = []
        for page in (1, 2):
            text = f"syntheticneedle {token} item {index} page {page}"
            chunks.append(
                Chunk(
                    text=text,
                    index=page - 1,
                    start_offset=0,
                    end_offset=len(text),
                    metadata={"page_number": page},
                )
            )
        ids.append(
            await _indexed_document(
                oracle,
                file_name=f"synthetic-{token}-{index}.pdf",
                chunks=chunks,
                knowledge_base_ids=[kb_id],
            )
        )
    profile = await oracle.create_search_answer_profile(
        name=f"合成検索profile {token}",
        config=SearchAnswerProfileConfig(knowledge_base_ids=[kb.id]),
    )
    request, effective, _, _ = await _resolve_query_context(
        SearchRequest(query="syntheticneedle", search_answer_profile_id=profile.id), settings
    )
    engine = AnswerEngine(effective, oracle=oracle, genai=_Embedding())  # type: ignore[arg-type]
    state = _SearchState()
    result = await engine._search(
        request,
        state,
        retrieval_queries=["syntheticneedle", "syntheticneedle additional"],
        expand_references=False,
    )
    assert {c.metadata["document_id"] for c in result.child_chunks} == set(ids[:2])
    fusion = state.retrieval_stages[-1]["rrf"]
    assert [row["weight"] for row in fusion["lists"]] == pytest.approx(
        [0.7, 3 / 11] if backend == "oracle_native" else [7 / 11, 0.7 / 11, 3 / 11]
    )
    assert all(row["status"] == "completed" for row in fusion["lists"])
    assert state.filters is not None
    scoped = {
        **state.filters,
        "document_id": ids[0],
        "page_number_min": "1",
        "page_number_max": "1",
    }
    restricted = set_audit_request_context(
        AuditRequestContext(
            allowed_document_ids=frozenset([ids[0]]),
            allowed_knowledge_base_ids=frozenset([kb.id]),
            allowed_search_answer_profile_ids=frozenset([profile.id]),
        )
    )
    try:
        rankings = await oracle.search_many(
            [
                ("syntheticneedle", [0.1] * 1536, SearchMode.VECTOR),
                ("syntheticneedle", [], SearchMode.KEYWORD),
            ],
            10,
            scoped,
        )
        for ranking in rankings:
            assert len(ranking) == 1
            assert ranking[0].document_id == ids[0]
            assert ranking[0].metadata["page_number"] == 1
        assert not await oracle.keyword_search("zzzzunmatchedzzzz", 10, scoped)
        assert not await oracle.keyword_search(
            "syntheticneedle", 10, {**scoped, "document_id": ids[2]}
        )
    finally:
        reset_audit_request_context(restricted)


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("ratio", [(10, 1), (2, 3)])
async def test_native_combined_original_and_provenance_channels(
    ratio: tuple[float, float], monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from types import SimpleNamespace

    settings = get_settings().model_copy(
        update={"rag_hybrid_search_backend": "oracle_native", "rag_rrf_method_ratio": ratio}
    )
    oracle = OracleClient(settings)
    kb = await oracle.create_knowledge_base(name=f"合成経路KB {uuid4().hex[:8]}")
    text = "syntheticoriginal syntheticsemantic syntheticterm"
    await _indexed_document(
        oracle,
        file_name="synthetic-routing.txt",
        chunks=[
            Chunk(
                text=text,
                index=0,
                start_offset=0,
                end_offset=len(text),
                metadata={"page_number": 1},
            )
        ],
        knowledge_base_ids=[kb.id],
    )
    calls = []
    fetch = oracle._fetch_all

    async def record(
        statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        if "DBMS_HYBRID_VECTOR.SEARCH" in statement:
            calls.append(json.loads(str((binds or {})["native_spec"])))
        return await fetch(statement, binds)

    monkeypatch.setattr(oracle, "_fetch_all", record)
    embedded = []

    class Embedding:
        async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            embedded.extend(texts)
            return [[0.1] * 1536 for _ in texts]

    state = _SearchState()
    engine = AnswerEngine(settings, oracle=oracle, genai=cast(Any, Embedding()))
    result = await engine._search(
        SearchRequest(query="syntheticoriginal", filters={"knowledge_base_id": kb.id}),
        state,
        retrieval_queries=["syntheticoriginal", "syntheticsemantic", "syntheticterm"],
        expand_references=False,
        settings=SimpleNamespace(
            retrieval_query_sources={
                "syntheticsemantic": ("質問の言い換え・分解",),
                "syntheticterm": ("用語・同義語だけ",),
            }
        ),
    )
    assert result.child_chunks
    assert embedded == ["syntheticoriginal", "syntheticsemantic"]
    assert sorted(c["search_fusion"] for c in calls) == ["TEXT_ONLY", "UNION", "VECTOR_ONLY"]
    original = next(c for c in calls if c["search_fusion"] == "UNION")
    assert original["search_scorer"] == "WRRF"
    assert original["vector"]["score_weight"] == ratio[0]
    assert original["text"]["score_weight"] == ratio[1]
    fusion = state.retrieval_stages[-1]["rrf"]
    assert [r["status"] for r in fusion["lists"]] == ["completed"] * 3
    assert fusion["lists"][0]["score_source"] == "oracle_wrrf"
    for chunk in state.chunks.values():
        if "native_wrrf_score" in chunk.metadata:
            assert chunk.metadata["native_rrf_contribution"] == pytest.approx(
                float(str(chunk.metadata["native_wrrf_score"])) / 6100
            )
