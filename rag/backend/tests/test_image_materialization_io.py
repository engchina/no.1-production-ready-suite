"""図の切出しの実測・再利用・失敗を保存形式で確かめる。"""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.search_stages import IMAGE_EVIDENCE_STAGE

from app.config import Settings
from app.rag import answer_engine as module
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.pipeline import _observe_engine_call
from tests.test_answer_engine import FakeGenAi, _figure_oracle
from tests.test_expansion_stage_io import calls


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "mode",
    ["crop", "file", "cache", "source", "workdir", "dimensions", "invalid", "crop_error", "fatal"],
)
async def test_image_stage_records_actual_outcome_without_media(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, enabled: bool, mode: str
) -> None:
    oracle = _figure_oracle()
    figure, plain = oracle.chunks[:2]
    metadata = json.loads(str(figure.metadata["engine_metadata_json"]))
    metadata["image_evidence"][0]["media"] = "private-media-marker"
    if mode == "invalid":
        metadata["image_evidence"][0]["bbox"] = []
    figure = figure.model_copy(
        update={"metadata": {**figure.metadata, "engine_metadata_json": json.dumps(metadata)}}
    )
    if mode == "dimensions":
        figure = figure.model_copy(update={"metadata": {**figure.metadata, "page_width": None}})
    state = _SearchState(work_dir=None if mode == "workdir" else tmp_path)
    engine = AnswerEngine(
        Settings(_env_file=None), oracle=cast(Any, oracle), genai=cast(Any, FakeGenAi())
    )
    crop_calls: list[int] = []
    source_calls: list[str] = []

    async def source(chunk: Any, current: Any) -> bytes | None:
        source_calls.append(chunk.chunk_id)
        return None if mode == "source" else b"private-pdf-marker"

    def crop(*args: Any) -> bytes:
        crop_calls.append(1)
        if mode == "crop_error":
            raise ValueError("private-error-marker")
        if mode == "fatal":
            raise OSError("private-error-marker")
        return b"private-png-marker"

    monkeypatch.setattr(engine, "_parsed_source", source)
    monkeypatch.setattr(module, "crop_png", crop)
    if not enabled:

        def forbidden(*args: Any) -> Any:
            raise AssertionError("本文 OFF で入力snapshotを構築しない")

        monkeypatch.setattr(module, "_image_materialization_input", forbidden)
    if mode == "cache":
        state.materialized_images[figure.chunk_id] = str(figure.metadata["engine_metadata_json"])
    if mode == "file":
        path = tmp_path / module._document_run_id(figure.document_id) / "crops" / "docling-p1-5.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"private-png-marker")
    with (
        call_content_policy(lambda: enabled),
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        if mode == "fatal":
            with pytest.raises(OSError, match="private-error-marker"):
                await engine._materialize_images([figure, plain], state)
        else:
            await engine._materialize_images([figure, plain], state)
    call = calls(summary)[IMAGE_EVIDENCE_STAGE]
    assert call.capture_enabled is enabled
    serialized = call.model_dump_json()
    assert all(
        marker not in serialized
        for marker in (
            "private-media-marker",
            "private-pdf-marker",
            "private-png-marker",
            "private-error-marker",
            str(tmp_path),
            "crop_path",
        )
    )
    assert len(crop_calls) == int(mode in {"crop", "crop_error", "fatal"})
    assert len(source_calls) == int(mode in {"crop", "source", "crop_error", "fatal"})
    if not enabled:
        assert call.input.text == call.output.text == ""
        return
    assert call.visibility == "system_admin"
    data = json.loads(call.input.text)
    assert data["chunks"][0]["cache_present"] is (mode == "cache")
    assert data["chunks"][0]["images"][0]["image_id"] == "docling-p1-5"
    output = json.loads(call.output.text)
    assert output["status"] == ("error" if mode == "fatal" else "completed")
    expected = {
        "cache": "cache_reused",
        "source": "source_unavailable",
        "workdir": "work_directory_unavailable",
        "dimensions": "page_dimensions_unavailable",
    }.get(mode, "completed")
    if mode != "fatal":
        assert output["chunks"][0]["status"] == expected
        assert output["chunks"][1]["status"] == "no_images"
    assert output["cropped_count"] == int(mode == "crop")
    assert output["file_reused_count"] == int(mode == "file")
    assert output["cache_reused_count"] == int(mode == "cache")
    if mode in {"invalid", "crop_error"}:
        assert output["chunks"][0]["images"][0]["status"] == (
            "invalid_reference" if mode == "invalid" else "crop_unavailable"
        )
    if mode == "fatal":
        assert output["error_type"] == "OSError"
        assert len(output["chunks"]) == 1


async def test_plain_chunks_do_not_create_image_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    oracle = _figure_oracle()
    engine = AnswerEngine(
        Settings(_env_file=None), oracle=cast(Any, oracle), genai=cast(Any, FakeGenAi())
    )
    state = _SearchState()
    with (
        operation_trace("rag.search", operation_id="synthetic") as summary,
        bind_trace_observer(_observe_engine_call),
    ):
        await engine._materialize_images([oracle.chunks[1]], state)
    assert IMAGE_EVIDENCE_STAGE not in calls(summary)
    assert state.materialized_images[oracle.chunks[1].chunk_id] is None


async def test_repeated_image_stage_reuses_actual_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    oracle = _figure_oracle()
    figure = oracle.chunks[0]
    state = _SearchState(work_dir=tmp_path)
    engine = AnswerEngine(
        Settings(_env_file=None), oracle=cast(Any, oracle), genai=cast(Any, FakeGenAi())
    )
    count: list[int] = []

    async def source(*args: Any) -> bytes:
        return b"synthetic-pdf"

    def crop(*args: Any) -> bytes:
        count.append(1)
        return b"synthetic-png"

    monkeypatch.setattr(engine, "_parsed_source", source)
    monkeypatch.setattr(module, "crop_png", crop)
    for index in range(2):
        with (
            call_content_policy(lambda: True),
            operation_trace("rag.search", operation_id=f"synthetic-{index}") as summary,
            bind_trace_observer(_observe_engine_call),
        ):
            await engine._materialize_images([figure], state)
        call = calls(summary)[IMAGE_EVIDENCE_STAGE]
        assert json.loads(call.input.text)["chunks"][0]["cache_present"] is bool(index)
        output = json.loads(call.output.text)
        assert output["cropped_count"] == 1 - index
        assert output["cache_reused_count"] == index
        assert output["chunks"][0]["metadata_applied"] is True
    assert count == [1]
    updated = json.loads(str(state.chunks[figure.chunk_id].metadata["engine_metadata_json"]))
    assert updated["image_evidence"][0]["crop_path"] == "crops/docling-p1-5.png"
