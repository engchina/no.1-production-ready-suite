"""PipelineStageClient(chunking 委譲)のテスト。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch
from rag_parser_core.extraction import StructuredExtraction
from rag_pipeline_core.stage import ChunkingStageRequest, ChunkingStageResponse, ChunkModel

from app.clients.pipeline_stage import PipelineStageClient, PipelineStageServiceError
from app.config import Settings


def _request() -> ChunkingStageRequest:
    return ChunkingStageRequest(extraction=StructuredExtraction(raw_text="本文。" * 10))


def test_chunking_disabled_returns_none(monkeypatch: MonkeyPatch) -> None:
    class _UnexpectedClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise AssertionError("disabled chunking service must not be called")

    monkeypatch.setattr(httpx, "Client", _UnexpectedClient)
    client = PipelineStageClient(
        Settings(rag_chunking_service_enabled=False, rag_chunking_service_url="http://svc:8000")
    )
    assert client.run_chunking(_request()) is None


def test_chunking_without_url_returns_none() -> None:
    client = PipelineStageClient(Settings(rag_chunking_service_url=""))
    assert client.run_chunking(_request()) is None


def test_remote_success_returns_chunks(monkeypatch: MonkeyPatch) -> None:
    response_payload = ChunkingStageResponse(
        chunks=[ChunkModel(text="c1", index=0, start_offset=0, end_offset=2, metadata={"k": "v"})]
    ).model_dump()

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return response_payload

    class _FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(self, *a: Any, **k: Any) -> _FakeResponse:
            return _FakeResponse()

        def request(self, method: str, *a: Any, **k: Any) -> _FakeResponse:
            assert method == "POST"
            return self.post(*a, **k)

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    client = PipelineStageClient(
        Settings(rag_chunking_service_enabled=True, rag_chunking_service_url="http://svc:8000")
    )
    chunks = client.run_chunking(_request())
    assert chunks is not None
    assert chunks[0].text == "c1"
    assert chunks[0].metadata["k"] == "v"


def test_remote_connection_failure_returns_none(monkeypatch: MonkeyPatch) -> None:
    class _BoomClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _BoomClient:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(self, *a: Any, **k: Any) -> Any:
            raise httpx.ConnectError("refused")

        def request(self, method: str, *a: Any, **k: Any) -> Any:
            assert method == "POST"
            return self.post(*a, **k)

    monkeypatch.setattr(httpx, "Client", _BoomClient)
    client = PipelineStageClient(
        Settings(rag_chunking_service_enabled=True, rag_chunking_service_url="http://svc:8000")
    )
    assert client.run_chunking(_request()) is None


def test_remote_invalid_payload_raises_when_service_responds(monkeypatch: MonkeyPatch) -> None:
    _fake_post(monkeypatch, {"chunks": [{"text": "missing indexes"}]})

    client = PipelineStageClient(
        Settings(rag_chunking_service_enabled=True, rag_chunking_service_url="http://svc:8000")
    )
    with pytest.raises(PipelineStageServiceError) as exc:
        client.run_chunking(_request())
    assert exc.value.error_code == "chunking_service_unavailable"


# --- vector_index / graphrag 委譲 -------------------------------------------


def _fake_post(monkeypatch: MonkeyPatch, payload: dict[str, Any]) -> None:
    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return payload

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(self, *a: Any, **k: Any) -> _Resp:
            return _Resp()

        def request(self, method: str, *a: Any, **k: Any) -> _Resp:
            assert method == "POST"
            return self.post(*a, **k)

    monkeypatch.setattr(httpx, "Client", _Client)


def test_run_vector_index_remote(monkeypatch: MonkeyPatch) -> None:
    _fake_post(
        monkeypatch,
        {
            "profile": "accurate",
            "target_accuracy": 98,
            "neighbors": 48,
            "efconstruction": 800,
            "distance": "COSINE",
            "requires_reprovision": True,
        },
    )
    from rag_pipeline_core.stage import VectorIndexStageRequest

    client = PipelineStageClient(
        Settings(rag_vector_index_service_enabled=True, rag_vector_index_service_url="http://svc")
    )
    res = client.run_vector_index(VectorIndexStageRequest(profile="accurate"))
    assert res is not None and res.target_accuracy == 98 and res.requires_reprovision is True


def test_run_graph_remote(monkeypatch: MonkeyPatch) -> None:
    _fake_post(
        monkeypatch,
        {"profile": "entities", "build_entities": True, "build_relationships": True},
    )
    from rag_pipeline_core.stage import GraphStageRequest

    client = PipelineStageClient(
        Settings(rag_graph_service_enabled=True, rag_graph_service_url="http://svc")
    )
    res = client.run_graph(GraphStageRequest(profile="entities"))
    assert res is not None and res.build_relationships is True


def test_vector_index_adapter_delegates_when_enabled(monkeypatch: MonkeyPatch) -> None:
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    _fake_post(
        monkeypatch,
        {
            "profile": "fast",
            "target_accuracy": 85,
            "neighbors": 16,
            "efconstruction": 300,
            "distance": "COSINE",
            "requires_reprovision": True,
        },
    )
    settings = Settings(
        rag_vector_index_profile="fast",
        rag_vector_index_service_enabled=True,
        rag_vector_index_service_url="http://svc",
    )
    params = resolve_vector_index_adapter(settings)
    assert params.profile == "fast" and params.target_accuracy == 85


def test_vector_index_adapter_falls_back_when_disabled() -> None:
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    # 既定(service 無効)は in-process 解決(現行挙動)。
    params = resolve_vector_index_adapter(
        Settings(rag_vector_index_profile="accurate", rag_vector_index_service_enabled=False)
    )
    assert params.profile == "accurate" and params.target_accuracy == 98


def test_vector_index_adapter_falls_back_when_service_unreachable(
    monkeypatch: MonkeyPatch,
) -> None:
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    class _BoomClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _BoomClient:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def request(self, method: str, *a: Any, **k: Any) -> Any:
            assert method == "POST"
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", _BoomClient)
    params = resolve_vector_index_adapter(
        Settings(
            rag_vector_index_profile="accurate",
            rag_vector_index_service_enabled=True,
            rag_vector_index_service_url="http://svc",
        )
    )
    assert params.profile == "accurate" and params.target_accuracy == 98


class _CountingVectorIndexClient:
    """vector_index の POST を数える fake(#828)。``fail`` で接続失敗・HTTP error を返す。"""

    calls = 0
    fail: str | None = None
    timeouts: list[Any] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        type(self).timeouts.append(k.get("timeout"))

    def __enter__(self) -> _CountingVectorIndexClient:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def request(self, method: str, url: str, *a: Any, **k: Any) -> Any:
        type(self).calls += 1
        if type(self).fail == "connect":
            raise httpx.ConnectError("timed out")
        if type(self).fail == "http":
            request = httpx.Request(method, url)
            raise httpx.HTTPStatusError(
                "boom", request=request, response=httpx.Response(500, request=request)
            )

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {
                    "profile": "accurate",
                    "target_accuracy": 98,
                    "neighbors": 48,
                    "efconstruction": 800,
                    "distance": "COSINE",
                    "requires_reprovision": True,
                }

        return _Resp()


@pytest.fixture
def counting_vector_index_client(monkeypatch: MonkeyPatch) -> type[_CountingVectorIndexClient]:
    _CountingVectorIndexClient.calls = 0
    _CountingVectorIndexClient.fail = None
    _CountingVectorIndexClient.timeouts = []
    monkeypatch.setattr(httpx, "Client", _CountingVectorIndexClient)
    return _CountingVectorIndexClient


def _vector_index_service_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "rag_vector_index_profile": "accurate",
        "rag_vector_index_service_enabled": True,
        "rag_vector_index_service_url": "http://svc",
    }
    values.update(overrides)
    return Settings(**values)


def test_vector_index_adapter_memoizes_remote_resolution(
    counting_vector_index_client: type[_CountingVectorIndexClient],
) -> None:
    """検索のたびにサービスへ HTTP を送らない(#828)。"""
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    settings = _vector_index_service_settings()
    first = resolve_vector_index_adapter(settings)
    second = resolve_vector_index_adapter(settings)
    assert first == second
    assert first.target_accuracy == 98
    assert counting_vector_index_client.calls == 1


def test_vector_index_adapter_memoizes_fallback_when_service_unreachable(
    counting_vector_index_client: type[_CountingVectorIndexClient],
) -> None:
    """応答しないサービスを検索のたびに待たない。縮退の結果もキャッシュする(#828)。"""
    from app.rag.vector_index_adapter import (
        reset_vector_index_static_cache,
        resolve_vector_index_adapter,
    )

    counting_vector_index_client.fail = "connect"
    settings = _vector_index_service_settings()
    assert resolve_vector_index_adapter(settings).target_accuracy == 98
    assert resolve_vector_index_adapter(settings).target_accuracy == 98
    assert counting_vector_index_client.calls == 1

    reset_vector_index_static_cache()
    resolve_vector_index_adapter(settings)
    assert counting_vector_index_client.calls == 2


def test_vector_index_adapter_cache_is_keyed_by_profile_and_accuracy(
    counting_vector_index_client: type[_CountingVectorIndexClient],
) -> None:
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    counting_vector_index_client.fail = "connect"
    resolve_vector_index_adapter(_vector_index_service_settings())
    balanced = resolve_vector_index_adapter(
        _vector_index_service_settings(
            rag_vector_index_profile="balanced", oracle_vector_target_accuracy=90
        )
    )
    assert balanced.profile == "balanced" and balanced.target_accuracy == 90
    assert counting_vector_index_client.calls == 2


def test_vector_index_adapter_does_not_cache_remote_errors(
    counting_vector_index_client: type[_CountingVectorIndexClient],
) -> None:
    """応答済み remote の HTTP error は止めたまま、キャッシュしない。"""
    from app.rag.vector_index_adapter import resolve_vector_index_adapter

    counting_vector_index_client.fail = "http"
    settings = _vector_index_service_settings()
    for _ in range(2):
        with pytest.raises(PipelineStageServiceError):
            resolve_vector_index_adapter(settings)
    assert counting_vector_index_client.calls == 2


def test_pipeline_stage_client_uses_short_connect_timeout(
    counting_vector_index_client: type[_CountingVectorIndexClient],
) -> None:
    """接続の確立は短い上限、応答の待ちは stage の timeout(#828)。"""
    from rag_pipeline_core.stage import VectorIndexStageRequest

    from app.clients.pipeline_stage import PIPELINE_STAGE_CONNECT_TIMEOUT_SECONDS

    client = PipelineStageClient(
        _vector_index_service_settings(rag_pipeline_stage_timeout_seconds=120.0)
    )
    client.run_vector_index(VectorIndexStageRequest(profile="accurate"))
    timeout = counting_vector_index_client.timeouts[-1]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == PIPELINE_STAGE_CONNECT_TIMEOUT_SECONDS
    assert timeout.read == 120.0

    short = PipelineStageClient(
        _vector_index_service_settings(rag_pipeline_stage_timeout_seconds=2.0)
    )
    short.run_vector_index(VectorIndexStageRequest(profile="accurate"))
    assert counting_vector_index_client.timeouts[-1].connect == 2.0
