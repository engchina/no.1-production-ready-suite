"""合成KB・profileで原生／従来方式の範囲と一回のRRFを確認する。"""

import json
from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

import pytest

from app.api.routes.search import _resolve_query_context
from app.clients.oracle import OracleClient
from app.config import Settings, get_settings
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


class _FirstNativeReadOracle(OracleClient):
    """初回の範囲件数と実行行数だけを保持し、合成 CI の空結果を切り分ける。"""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.read_tag = "native_first_read_" + uuid4().hex[:12]
        self.first_reads: list[dict[str, object]] = []

    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        native = "DBMS_HYBRID_VECTOR.SEARCH" in sql
        if native:
            sql = sql.replace(
                "SELECT ", f"SELECT /*+ gather_plan_statistics */ /* {self.read_tag} */ ", 1
            )
        rows = await super()._fetch_all(sql, binds)
        if native:
            spec = json.loads(str((binds or {})["native_spec"]))
            self.first_reads.append(
                {
                    "read": "native",
                    "fusion": spec["search_fusion"],
                    "rows": len(rows),
                    "accuracy": spec.get("vector", {}).get("accuracy"),
                    "filter_type": spec.get("vector", {}).get("filter_type"),
                }
            )
        elif sql.lstrip().startswith("SELECT c.chunk_id, c.native_scope_id"):
            self.first_reads.append(
                {
                    "read": "allowed",
                    "rows": len(rows),
                    "scopes": len({r.get("native_scope_id") for r in rows}),
                    "with_similarity": "VECTOR_DISTANCE" in sql,
                }
            )
        return rows

    async def first_read_diagnostics(self) -> dict[str, object]:
        try:
            plan = await super()._fetch_all(
                """SELECT p.id, p.parent_id, p.operation, p.options,
                    p.last_starts, p.last_output_rows, p.last_cr_buffer_gets
                FROM v$sql q JOIN v$sql_plan_statistics_all p
                    ON p.sql_id=q.sql_id AND p.child_number=q.child_number
                WHERE q.sql_text LIKE :tag
                ORDER BY q.sql_id, p.id FETCH FIRST 160 ROWS ONLY""",
                {"tag": f"SELECT /*+ gather_plan_statistics */ /* {self.read_tag} */%"},
            )
        except Exception as exc:
            return {"reads": self.first_reads, "plan_error": type(exc).__name__}
        return {"reads": self.first_reads, "plan": plan}


@pytest.mark.usefixtures("oracle_db")
async def test_native_text_index_keeps_numeric_words_and_vector_scope() -> None:
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    settings = get_settings().model_copy(
        update={
            "rag_hybrid_search_backend": "oracle_native",
            "rag_vector_index_profile": "balanced",
            "oracle_vector_target_accuracy": 100,
        }
    )
    assert resolve_vector_index_adapter(settings).target_accuracy == 100
    oracle = _FirstNativeReadOracle(settings)
    ranges = await oracle._fetch_all(
        "SELECT ixv_value FROM ctx_user_index_values "
        "WHERE ixv_index_name='RAG_CHUNKS_HYBRID_IDX' "
        "AND ixv_attribute='RANGE_SEARCH_ENABLE' AND ixv_value IS NOT NULL"
    )
    assert ranges == []
    chunks = [
        Chunk(
            text=f"syntheticquantity {quantity}",
            index=index,
            start_offset=0,
            end_offset=len(f"syntheticquantity {quantity}"),
            metadata={"page_number": index + 1},
        )
        for index, quantity in enumerate((24, 99))
    ]
    document_id = await _indexed_document(
        oracle, file_name=f"synthetic-numeric-{uuid4().hex}.txt", chunks=chunks
    )
    rankings = await oracle.search_many(
        [("24", [], SearchMode.KEYWORD), ("24", [0.1] * 1536, SearchMode.VECTOR)],
        5,
        {"document_id": document_id},
    )
    for (_, _, mode), ranking in zip(
        [("24", [], SearchMode.KEYWORD), ("24", [0.1] * 1536, SearchMode.VECTOR)],
        rankings,
        strict=True,
    ):
        failure = getattr(ranking, "failure", None)
        if failure is not None:
            raise AssertionError(f"{mode.value}: 検索に失敗しました") from failure
        status = getattr(ranking, "status", "completed")
        if status != "completed":
            diagnostics = await _empty_native_scope_diagnostics(
                oracle, document_id, {"document_id": document_id}
            )
            pytest.fail(
                f"{mode.value}: status={status}, diagnostics={diagnostics}, "
                f"first={await oracle.first_read_diagnostics()}"
            )
    assert len(rankings[0]) == 1
    assert rankings[0][0].metadata["page_number"] == 1
    assert len(rankings[1]) == 2
    assert all(item.document_id == document_id for ranking in rankings for item in ranking)


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize(
    "backend",
    [pytest.param("oracle_native", id=f"native-scope-{i}") for i in range(4)]
    + [pytest.param("sudachi", id="legacy")],
)
async def test_native_and_legacy_public_search_keep_scope_and_global_rrf(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_hybrid_search_backend", backend)
    oracle = _FirstNativeReadOracle(settings)
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
        searches = [
            ("syntheticneedle", [0.1] * 1536, SearchMode.VECTOR),
            ("syntheticneedle", [], SearchMode.KEYWORD),
        ]
        rankings = await oracle.search_many(searches, 10, scoped)
        for (_, _, mode), ranking in zip(searches, rankings, strict=True):
            failure = getattr(ranking, "failure", None)
            if failure is not None:
                raise AssertionError(f"{backend}/{mode.value}: 検索が失敗しました") from failure
            status = getattr(ranking, "status", "completed")
            diagnostics = (
                await _empty_native_scope_diagnostics(oracle, ids[0], scoped)
                if status == "empty" and backend == "oracle_native"
                else None
            )
            assert status == "completed", (
                f"{backend}/{mode.value}: status={status}, diagnostics={diagnostics}, "
                f"first={await oracle.first_read_diagnostics() if status != 'completed' else None}"
            )
            assert len(ranking) == 1, f"{backend}/{mode.value}: count={len(ranking)}"
            assert ranking[0].document_id == ids[0]
            assert ranking[0].metadata["page_number"] == 1
        assert not await oracle.keyword_search("zzzzunmatchedzzzz", 10, scoped)
        assert not await oracle.keyword_search(
            "syntheticneedle", 10, {**scoped, "document_id": ids[2]}
        )
    finally:
        reset_audit_request_context(restricted)


async def _empty_native_scope_diagnostics(
    oracle: OracleClient, document_id: str, filters: dict[str, str]
) -> dict[str, object]:
    """空結果の断言を維持し、可視性と近似探索を分ける。本文・接続情報は出さない。"""
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    visibility = await oracle._fetch_all(
        """SELECT COUNT(*) AS chunks, COUNT(v.doc_rowid) AS indexed_vectors
        FROM rag_chunks c LEFT JOIN RAG_CHUNKS_HYBRID_IDX$VECTORS v ON v.doc_rowid=c.ROWID
        WHERE c.document_id=:id
          AND JSON_VALUE(c.metadata_json, '$.page_number' RETURNING NUMBER)=1""",
        {"id": document_id},
    )
    settings = oracle._settings.model_copy(
        update={"rag_vector_index_profile": "balanced", "oracle_vector_target_accuracy": 100}
    )
    exact = await OracleClient(settings).search_many(
        [("syntheticneedle", [0.1] * 1536, SearchMode.VECTOR)], 10, filters
    )
    failure = getattr(exact[0], "failure", None)
    return {
        "visibility": visibility,
        "requested_accuracy": resolve_vector_index_adapter(oracle._settings).target_accuracy,
        "accuracy_100_status": getattr(exact[0], "status", "completed"),
        "accuracy_100_count": len(exact[0]),
        "accuracy_100_failure": type(failure).__name__ if failure else None,
    }


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


@pytest.mark.usefixtures("oracle_db")
async def test_large_native_scope_is_compressed_without_losing_page_or_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_hybrid_search_backend", "oracle_native")
    oracle = OracleClient(settings)
    token = uuid4().hex[:10]
    kb = await oracle.create_knowledge_base(name=f"合成大規模KB {token}")
    profile = await oracle.create_search_answer_profile(
        name=f"合成大規模profile {token}",
        config=SearchAnswerProfileConfig(knowledge_base_ids=[kb.id]),
    )
    chunks = [
        Chunk(
            text=f"syntheticneedle {token} item {i}",
            index=i,
            start_offset=0,
            end_offset=20,
            metadata={"page_number": 1 if i < 4 else 2},
        )
        for i in range(400)
    ]
    doc = await _indexed_document(
        oracle, file_name=f"synthetic-{token}.txt", chunks=chunks, knowledge_base_ids=[kb.id]
    )
    request, effective, _, _ = await _resolve_query_context(
        SearchRequest(query="syntheticneedle", search_answer_profile_id=profile.id), settings
    )
    filters = {"knowledge_base_id": kb.id, "document_id": doc}
    full = await oracle.search_many(
        [("syntheticneedle", [0.1] * 1536, SearchMode.HYBRID)], 10, filters
    )
    assert full[0] and getattr(full[0], "status", "") == "completed"
    assert all(hit.metadata["document_id"] == doc for hit in full[0])
    page = await oracle.search_many(
        [("syntheticneedle", [0.1] * 1536, SearchMode.HYBRID)],
        10,
        {**filters, "page_number_min": "1", "page_number_max": "1"},
    )
    assert len(page[0]) == 4 and all(hit.metadata["page_number"] == 1 for hit in page[0])
    long_page = await oracle.search_many(
        [("syntheticneedle", [0.1] * 1536, SearchMode.HYBRID)],
        10,
        {**filters, "page_number_min": "2", "page_number_max": "2"},
    )
    assert long_page[0] and all(hit.metadata["page_number"] == 2 for hit in long_page[0])
    engine = AnswerEngine(effective, oracle=oracle, genai=_Embedding())  # type: ignore[arg-type]
    result = await engine._search(
        request, _SearchState(), retrieval_queries=["syntheticneedle"], expand_references=False
    )
    assert result.child_chunks and all(
        hit.metadata["document_id"] == doc for hit in result.child_chunks
    )


@pytest.mark.usefixtures("oracle_db")
async def test_hybrid_payload_ignores_scope_updates_but_tracks_text_and_embedding() -> None:
    """専用の合成表で同期対象の列と、既存triggerの更新を確認する。"""
    import json

    from app.clients.native_hybrid_schema import native_hybrid_sync_columns_migration_sql

    client = OracleClient()

    async def execute(sql: str, binds: dict[str, Any]) -> None:
        def operation(connection: Any) -> None:
            with connection.cursor() as cursor:
                cursor.execute(sql, binds)

        await client._run_transaction(operation)

    table = f"RAG_TEST_SYNC_{uuid4().hex[:10].upper()}"
    trigger = f"{table}_TRG"
    setup = (
        native_hybrid_sync_columns_migration_sql()
        .replace("RAG_CHUNKS_HYBRID_SYNC", trigger)
        .replace("table_name = 'RAG_CHUNKS'", f"table_name = '{table}'")
        .replace("rag_chunks", table)
        .rstrip("/\n ")
    )
    await execute(
        f"CREATE TABLE {table} (id NUMBER, search_text CLOB, embedding VECTOR(3, FLOAT32), "
        "native_scope_id NUMBER, hybrid_payload JSON)",
        {},
    )
    try:
        await execute(setup, {})
        await execute(setup, {})
        await execute(
            f"INSERT INTO {table} (id,search_text,embedding) "
            "VALUES (1,'synthetic-before',TO_VECTOR('[1,0,0]'))",
            {},
        )

        async def payload() -> dict[str, Any]:
            rows = await client._fetch_all(
                f"SELECT JSON_SERIALIZE(hybrid_payload RETURNING VARCHAR2(4000)) AS payload "
                f"FROM {table} WHERE id=1",
                {},
            )
            return cast(dict[str, Any], json.loads(str(rows[0]["payload"])))

        assert (await payload())["text"] == "synthetic-before"
        # 派生JSONの再代入を検知するため、独立した合成表だけへ印を置く。
        await execute(f"ALTER TRIGGER {trigger} DISABLE", {})
        await execute(
            f"UPDATE {table} SET hybrid_payload=JSON('{{\"sentinel\":true}}') WHERE id=1", {}
        )
        await execute(f"ALTER TRIGGER {trigger} ENABLE", {})
        await execute(f"UPDATE {table} SET native_scope_id=7 WHERE id=1", {})
        assert await payload() == {"sentinel": True}
        await execute(f"UPDATE {table} SET search_text='synthetic-after' WHERE id=1", {})
        assert (await payload())["text"] == "synthetic-after"
        await execute(f"UPDATE {table} SET embedding=TO_VECTOR('[0,1,0]') WHERE id=1", {})
        assert (await payload())["embedding"] == [0, 1, 0]
    finally:
        await execute(f"DROP TABLE {table} PURGE", {})


@pytest.mark.usefixtures("oracle_db")
async def test_save_index_allocates_native_scope_without_precreated_chunk_set() -> None:
    """取込と同じ保存順で、新しい親集合と数値範囲を原子的に作る。"""
    from app.schemas.extraction import StructuredExtraction

    oracle = OracleClient()
    doc = await oracle.create_document(
        file_name="synthetic-new-scope.txt",
        object_storage_path="local://synthetic-new-scope.txt",
        content_type="text/plain",
    )
    scope = f"cs_new_{uuid4().hex[:16]}"
    await oracle.save_index(
        doc.id,
        StructuredExtraction(raw_text="syntheticneedle", confidence=1),
        [Chunk(index=0, text="syntheticneedle", start_offset=0, end_offset=15)],
        [[0.1] * 1536],
        chunk_set_id=scope,
    )
    rows = await oracle._fetch_all(
        "SELECT c.native_scope_id, s.native_scope_id AS mapped_scope FROM rag_chunks c "
        "JOIN rag_native_search_scopes s ON s.chunk_set_id=c.chunk_set_id "
        "WHERE c.chunk_set_id=:id",
        {"id": scope},
    )
    assert len(rows) == 1
    assert rows[0]["native_scope_id"] == rows[0]["mapped_scope"]
    assert int(str(rows[0]["native_scope_id"])) > 0
