"""PipelineStageClient(chunking 委譲 + 安全縮退)のテスト。"""

from __future__ import annotations

from typing import Any

import httpx
from pytest import MonkeyPatch
from rag_parser_core.extraction import StructuredExtraction
from rag_pipeline_core.stage import ChunkingStageRequest, ChunkingStageResponse, ChunkModel

from app.clients.pipeline_stage import PipelineStageClient
from app.config import Settings


def _request() -> ChunkingStageRequest:
    return ChunkingStageRequest(extraction=StructuredExtraction(raw_text="本文。" * 10))


def test_disabled_returns_none() -> None:
    # 既定(enabled=False)は remote 委譲しない → None(呼び出し側 in-process)。
    client = PipelineStageClient(Settings(rag_chunking_service_enabled=False))
    assert client.run_chunking(_request()) is None


def test_enabled_but_no_url_returns_none() -> None:
    client = PipelineStageClient(
        Settings(rag_chunking_service_enabled=True, rag_chunking_service_url="")
    )
    assert client.is_enabled("chunking") is False
    assert client.run_chunking(_request()) is None


def test_remote_success_returns_chunks(monkeypatch: MonkeyPatch) -> None:
    response_payload = ChunkingStageResponse(
        chunks=[ChunkModel(text="c1", index=0, start_offset=0, end_offset=2, metadata={"k": "v"})]
    ).model_dump()

    class _FakeResponse:
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

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    client = PipelineStageClient(
        Settings(rag_chunking_service_enabled=True, rag_chunking_service_url="http://svc:8000")
    )
    chunks = client.run_chunking(_request())
    assert chunks is not None
    assert chunks[0].text == "c1"
    assert chunks[0].metadata["k"] == "v"


def test_remote_failure_degrades_to_none(monkeypatch: MonkeyPatch) -> None:
    class _BoomClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _BoomClient:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(self, *a: Any, **k: Any) -> Any:
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", _BoomClient)
    client = PipelineStageClient(
        Settings(rag_chunking_service_enabled=True, rag_chunking_service_url="http://svc:8000")
    )
    # 未達は None(呼び出し側で in-process 縮退)。例外を投げない。
    assert client.run_chunking(_request()) is None


# --- vector_index / graphrag 委譲 -------------------------------------------


def _fake_post(monkeypatch: MonkeyPatch, payload: dict[str, Any]) -> None:
    class _Resp:
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
        {
            "profile": "full",
            "build_entities": True,
            "build_relationships": True,
            "build_claims": True,
            "build_community_summary": True,
            "temporal": False,
        },
    )
    from rag_pipeline_core.stage import GraphStageRequest

    client = PipelineStageClient(
        Settings(rag_graph_service_enabled=True, rag_graph_service_url="http://svc")
    )
    res = client.run_graph(GraphStageRequest(profile="full"))
    assert res is not None and res.build_claims is True


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
    params = resolve_vector_index_adapter(Settings(rag_vector_index_profile="accurate"))
    assert params.profile == "accurate" and params.target_accuracy == 98


def test_run_generation_remote(monkeypatch: MonkeyPatch) -> None:
    _fake_post(
        monkeypatch,
        {"profile": "inline_cited", "system_prompt": "逐句付与", "structured_output": False},
    )
    from rag_pipeline_core.stage import GenerationStageRequest

    client = PipelineStageClient(
        Settings(rag_generation_service_enabled=True, rag_generation_service_url="http://svc")
    )
    res = client.run_generation(GenerationStageRequest(profile="inline_cited"))
    assert res is not None and res.system_prompt == "逐句付与"


def test_generation_adapter_override_beats_service(monkeypatch: MonkeyPatch) -> None:
    from app.rag.generation_adapter import resolve_generation_adapter

    _fake_post(
        monkeypatch,
        {"profile": "inline_cited", "system_prompt": "service prompt", "structured_output": False},
    )
    settings = Settings(
        rag_generation_profile="inline_cited",
        rag_generation_service_enabled=True,
        rag_generation_service_url="http://svc",
        rag_generation_system_prompt_override="persona override",
    )
    params = resolve_generation_adapter(settings)
    # persona override は service 解決より優先。
    assert params.system_prompt == "persona override"
    assert params.profile == "inline_cited"
