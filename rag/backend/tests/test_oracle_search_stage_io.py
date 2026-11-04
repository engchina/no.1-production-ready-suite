"""既存Oracle検索工程の実引数・返却・分岐失敗を、実DBを呼ばずに確かめる。"""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import operation_trace
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.search_stages import (
    HYBRID_SEARCH_STAGE,
    TEXT_SEARCH_STAGE,
    VECTOR_SEARCH_STAGE,
)

from app.clients import oracle as module
from app.clients.oracle import NativeSearchRows, OracleClient
from app.rag.pipeline import _observe_engine_call
from app.schemas.search import SearchMode
from tests.test_oracle_adapter import _oci_settings
from tests.test_validation_stage_io import call_of, tracing  # noqa: F401 - fixture


def row() -> dict[str, object]:
    return {
        "document_id": "doc-a",
        "chunk_id": "chunk-a",
        "chunk_text": "合成本文",
        "metadata_json": '{"chunk_index": 1}',
        "file_name": "synthetic.txt",
        "score": 0.8,
        "native_rrf_contribution": 0.1,
        "native_effective_vector_rank": 1,
        "native_effective_text_rank": 2,
        "native_wrrf_score": 0.9,
    }


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mode", [SearchMode.VECTOR, SearchMode.KEYWORD, SearchMode.HYBRID])
@pytest.mark.parametrize("failed", [True, False])
async def test_native_search_records_effective_scope_and_actual_return(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    mode: SearchMode,
    failed: bool,
) -> None:
    client = OracleClient(settings=_oci_settings())
    rows = NativeSearchRows(
        [row()],
        {
            "raw_count": 3,
            "min_similarity": 0.2,
            "retained_count": 1,
            "excluded_count": 2,
            "rejected_vector_count": 2,
            "preserved_text_count": 0,
            "missing_similarity_count": 0,
        },
        {
            "method": "exact_scope",
            "scope_vector_chunks": 3,
            "exact_max_chunks": 10,
            "requested": 0,
            "expected": 3,
            "returned": 1,
            "shortage": True,
        },
    )
    read = AsyncMock(
        return_value=rows, side_effect=RuntimeError("private-error-body") if failed else None
    )
    monkeypatch.setattr(client, "_native_rows", read)
    filters = {"document_id": "doc-a", "page_start": "1"}
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        hits = await client._native_search_hits("合成の質問", [1, 0, 0], 0, filters, mode)
    stage = {
        SearchMode.VECTOR: VECTOR_SEARCH_STAGE,
        SearchMode.KEYWORD: TEXT_SEARCH_STAGE,
        SearchMode.HYBRID: HYBRID_SEARCH_STAGE,
    }[mode]
    call = call_of(summary, stage)
    assert hits.status == ("failed" if failed else "completed")
    assert "private-error-body" not in call.model_dump_json()
    if enabled:
        request = json.loads(call.input.text)
        assert request["filters"] == filters
        assert request["top_k"] == 0
        result = json.loads(call.output.text)
        assert result["status"] == hits.status
        if failed:
            assert result["error_type"] == "RuntimeError"
        else:
            assert result["chunks"][0]["chunk_id"] == "chunk-a"
            assert result["threshold"] == rows.threshold
            assert result["vector_retrieval"] == rows.vector_retrieval
    else:
        assert call.input.text == call.output.text == ""


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("keyword", [True, False])
@pytest.mark.parametrize("failed", [True, False])
async def test_single_search_records_actual_arguments_and_does_not_swallow_error(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    keyword: bool,
    failed: bool,
) -> None:
    client = OracleClient(settings=_oci_settings())
    read = AsyncMock(
        return_value=[row()], side_effect=RuntimeError("private-error-body") if failed else None
    )
    monkeypatch.setattr(client, "_keyword_rows" if keyword else "_vector_rows", read)
    monkeypatch.setattr(
        module, "_oracle_text_query", lambda *args, **kwargs: "synthetic-expression"
    )
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):

        async def run() -> Any:
            if keyword:
                return await client.keyword_search("合成の質問", 3, {"document_id": "doc-a"})
            return await client.vector_search(
                [1, 0, 0], 3, {"document_id": "doc-a"}, query="合成の質問"
            )

        if failed:
            with pytest.raises(RuntimeError):
                await run()
        else:
            hits = await run()
            assert len(hits) == 1
    call = call_of(summary, TEXT_SEARCH_STAGE if keyword else VECTOR_SEARCH_STAGE)
    assert "private-error-body" not in call.model_dump_json()
    if enabled:
        request = json.loads(call.input.text)
        assert request["query"] == "合成の質問"
        assert request["filters"] == {"document_id": "doc-a"}
        if keyword:
            assert read.await_args is not None
            assert request["text_expression"] == read.await_args.args[0]
        result = json.loads(call.output.text)
        if failed:
            assert result["returned"] is False
            assert result["error_type"] == "RuntimeError"
        else:
            assert result["chunks"][0]["metadata"]["retrieval_mode"] == (
                "keyword" if keyword else "vector"
            )
    else:
        assert call.input.text == call.output.text == ""


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failures", [0, 1, 2])
@pytest.mark.parametrize("keyword", [True, False])
async def test_parallel_branch_parent_keeps_failures_distinct_from_empty_results(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    failures: int,
    keyword: bool,
) -> None:
    client = OracleClient(settings=_oci_settings())
    monkeypatch.setattr(
        module, "_oracle_text_query", lambda *args, **kwargs: "synthetic-expression"
    )

    async def read(query: str, **kwargs: Any) -> list[dict[str, object]]:
        if (query == "fail" and failures) or failures == 2:
            raise RuntimeError("private-error-body")
        return [row(), row()]

    async def vector_read(embedding: Any, **kwargs: Any) -> list[dict[str, object]]:
        return await read("ok" if embedding[0] else "fail", **kwargs)

    monkeypatch.setattr(client, "_keyword_rows", read)
    monkeypatch.setattr(client, "_vector_rows", vector_read)
    monkeypatch.setattr(module, "_oracle_text_query", lambda query, **kwargs: query)
    mode = SearchMode.KEYWORD if keyword else SearchMode.VECTOR
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        results = await client.search_many(
            [("ok", [1, 0, 0], mode), ("fail", [0, 1, 0], mode)],
            3,
            {"document_id": "doc-a"},
        )
    assert len(results) == 2
    call = call_of(summary, TEXT_SEARCH_STAGE if keyword else VECTOR_SEARCH_STAGE)
    assert "private-error-body" not in call.model_dump_json()
    if enabled:
        request = json.loads(call.input.text)
        assert [branch["query"] for branch in request["branches"]] == ["ok", "fail"]
        if keyword:
            assert [branch["text_expression"] for branch in request["branches"]] == ["ok", "fail"]
        else:
            assert [branch["embedding"] for branch in request["branches"]] == [[1, 0, 0], [0, 1, 0]]
        result = json.loads(call.output.text)
        assert result["status"] == ["completed", "partial", "failed"][failures]
        assert sum(branch["status"] == "failed" for branch in result["branches"]) == failures
        for branch in result["branches"]:
            if branch["status"] == "completed":
                assert branch["raw_count"] == 2
                assert len(branch["results"]) == 1
    else:
        assert call.input.text == call.output.text == ""


async def test_content_off_does_not_build_search_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OracleClient(settings=_oci_settings())
    monkeypatch.setattr(client, "_vector_rows", AsyncMock(return_value=[row()]))

    def forbidden(*args: Any) -> Any:
        raise AssertionError("本文OFFでsnapshotを組み立てない")

    monkeypatch.setattr(module, "_search_hits_snapshot", forbidden)
    with (
        call_content_policy(lambda: False),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        assert len(await client.vector_search([1, 0, 0], 3)) == 1
    assert call_of(summary, VECTOR_SEARCH_STAGE).output.text == ""
