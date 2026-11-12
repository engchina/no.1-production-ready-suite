"""独立したDB検索の実入力・分路結果・cacheを合成応答で照合する。"""

from concurrent.futures import CancelledError
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_adb_vector_store import _ConnectionContext, _stored_chunk
from test_completion_stage_io import PolicyRecorder

from rag_engine.adapters import oci
from rag_engine.adapters.oracle import store
from rag_engine.config import get_settings
from rag_engine.knowledge.classification import ClassificationFilter
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.metadata_filters import RetrievalMetadataFilter


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failure", ["none", "vector", "all"])
def test_search_snapshots_match_actual_rankings_and_failures(monkeypatch, enabled, failure):
    store._pool_cache.clear()
    monkeypatch.setattr(store, "_pool_cache", {})
    pool = [_stored_chunk(f"uid-{n}", f"chunk-{n}", page_start=n, chunk_seq=n) for n in (1, 2)]
    loads = []
    monkeypatch.setattr(store, "load_adb_settings", lambda *args: "synthetic")
    monkeypatch.setattr(store, "connect_adb_thin", lambda *args: _ConnectionContext(object()))
    monkeypatch.setattr(store, "load_domain_keywords", lambda *args: ["合成"])
    monkeypatch.setattr(store, "_available_embedding_count", lambda *args, **kwargs: 1)

    def load(*args, **kwargs):
        loads.append(kwargs)
        return pool

    def vector(*args, **kwargs):
        if failure != "none":
            raise ValueError("private-error-sentinel")
        return [("uid-1", 0.1)]

    def text(*args, **kwargs):
        if failure == "all":
            raise ValueError("private-error-sentinel")
        return [("uid-2", 0.2)]

    monkeypatch.setattr(store, "_load_chunk_run_chunks", load)
    monkeypatch.setattr(store, "_vector_search", vector)
    monkeypatch.setattr(store, "_text_search", text)
    monkeypatch.setattr(store, "oracle_text_query", lambda query, **kwargs: "{" + query + "}")
    config = replace(get_settings(environ={}, dotenv_path=None), hybrid_search_backend="oracle_separate_sql", image_embedding_enabled=False)
    recorder = PolicyRecorder(enabled)
    if not enabled:
        monkeypatch.setattr(store, "asdict", lambda *args: pytest.fail("disabled snapshot serialized"))
    for _ in range(2):
        with bind_trace_observer(recorder.observe):
            arguments = dict(
                chunk_run_id="synthetic-run",
                retrieval_queries=["合成質問", "合成仮説"],
                vector_only_queries=["合成仮説"],
                extra_text_queries=["{追加}"],
                candidate_limit=2,
                query_embedder=lambda *args: [0.1] * 1536,
                settings=config,
                classification_filter=ClassificationFilter(large_category="synthetic-category"),
                metadata_filter=RetrievalMetadataFilter(page_numbers=(2,)),
            )
            if failure == "all":
                with pytest.raises(store.AdbHybridSearchUnavailable):
                    store.search_adb_hybrid_chunks(**arguments)
            else:
                result = store.search_adb_hybrid_chunks(**arguments)
                assert result.all_chunks == pool
    assert len(loads) == 1
    for key, calls in recorder.calls.items():
        assert len(calls) == 2, key
        for index, call in enumerate(calls):
            if not enabled:
                assert "input" not in call and "output" not in call
                continue
            assert call["input"] and call["output"]
            if key[1] == store.CHUNK_POOL_LOAD_STAGE:
                assert call["input"]["reuse_cached_pool"] is bool(index)
                assert call["output"]["reused"] is bool(index)
                assert [row["chunk_uid"] for row in call["output"]["chunks"]] == ["uid-1", "uid-2"]
            elif key[1] == store.VECTOR_SEARCH_STAGE:
                assert call["input"]["candidate_limit"] == 2
                assert call["input"]["metadata_filter"]["page_numbers"] == [2]
                assert call["input"]["classification_filter"]["large_category"] == "synthetic-category"
                assert call["output"]["rankings"] == ([[("uid-1", 0.1)]] * 2 if failure == "none" else [[], []])
                assert len(call["output"]["failures"]) == (0 if failure == "none" else 2)
            elif key[1] == store.TEXT_SEARCH_STAGE:
                assert call["output"]["expressions"] == ["{合成質問}", "{追加}"]
                assert call["output"]["rankings"] == ([[], []] if failure == "all" else [[("uid-2", 0.2)]] * 2)
            else:
                assert key[1] == "検索結果の統合（RRF）"
                assert len(call["input"]["rankings"]) == 4
                assert call["output"]["fusion_execution"] == ("empty" if failure == "all" else "outer_rrf")
    assert "private-error-sentinel" not in str(recorder.calls)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("outcome", ["retry", "error", "cancel"])
def test_rerank_parent_records_real_result_failure_or_cancellation(monkeypatch, enabled, outcome):
    calls = []

    def once(query, documents, settings, top_n=None):
        calls.append((query, documents, top_n))
        if outcome == "cancel":
            raise CancelledError("private-error-sentinel")
        if outcome == "error" or len(calls) == 1:
            raise TimeoutError("private-error-sentinel")
        return [oci.RerankTextRank(1, 0.9)]

    monkeypatch.setattr(oci, "_rerank_text_once", once)
    monkeypatch.setattr(oci, "_is_retryable_error", lambda exc: isinstance(exc, TimeoutError))
    monkeypatch.setattr(oci.time, "sleep", lambda *args: None)
    settings = SimpleNamespace(
        rerank_model="synthetic", rerank_retries=1, rerank_retry_initial_wait_seconds=0, rerank_retry_max_wait_seconds=0
    )
    recorder = PolicyRecorder(enabled)
    with bind_trace_observer(recorder.observe):
        if outcome == "retry":
            ranks = oci._rerank_text_with_retry("合成質問", ["合成A", "合成B"], settings)
            assert ranks[0].index == 1
        else:
            with pytest.raises(CancelledError if outcome == "cancel" else TimeoutError):
                oci._rerank_text_with_retry("合成質問", ["合成A", "合成B"], settings)
    stage = recorder.stage("rerank.request")
    assert len(calls) == (1 if outcome == "cancel" else 2)
    if not enabled:
        assert "input" not in stage and "output" not in stage
        return
    assert stage["input"]["documents"] == calls[0][1]
    assert stage["input"]["top_n"] == 2
    assert stage["output"]["receipt"]["status"] == {"retry": "completed", "error": "error", "cancel": "cancelled"}[outcome]
    if outcome == "retry":
        assert stage["output"]["rankings"][0]["index"] == 1
    else:
        assert stage["output"]["rankings"] is None
        assert stage["output"]["receipt"]["error_type"] == ("CancelledError" if outcome == "cancel" else "TimeoutError")
    assert "private-error-sentinel" not in str(stage)
