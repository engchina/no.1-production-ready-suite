"""回答画像の原本は記録された chunk set の版だけから読む。"""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.rag import answer_engine as module
from app.rag.document_crop import DocumentSourceNotFoundError
from app.schemas.search import RetrievedChunk


def _engine() -> module.AnswerEngine:
    engine = object.__new__(module.AnswerEngine)
    engine._oracle = cast(Any, object())  # loader は全テストで差し替える。
    return engine


def _chunk(chunk_set_id: Any, *, recipe_id: str = "recipe-a") -> RetrievedChunk:
    return RetrievedChunk(
        document_id="document-a",
        chunk_id=f"chunk-{recipe_id}-{chunk_set_id!r}",
        text="画像の説明",
        score=1,
        file_name="synthetic.pdf",
        metadata={"recipe_id": recipe_id, "chunk_set_id": chunk_set_id},
    )


@pytest.mark.parametrize("chunk_set_id", [None, "", " \t\n", 42, {}])
async def test_missing_chunk_set_never_reads_another_source(
    monkeypatch: pytest.MonkeyPatch, chunk_set_id: Any
) -> None:
    calls: list[Any] = []

    async def source(*args: Any, **kwargs: Any) -> bytes:
        calls.append((args, kwargs))
        return b"current-recipe-file"

    monkeypatch.setattr(module, "load_parsed_source", source)
    state = module._SearchState()
    assert await _engine()._parsed_source(_chunk(chunk_set_id), state) is None
    assert calls == []
    assert state.sources == {}


async def test_missing_scope_does_not_poison_later_recorded_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def source(*args: Any, chunk_set_id: str, **kwargs: Any) -> bytes:
        calls.append(chunk_set_id)
        return chunk_set_id.encode()

    monkeypatch.setattr(module, "load_parsed_source", source)
    engine, state = _engine(), module._SearchState()
    assert await engine._parsed_source(_chunk(None), state) is None
    assert await engine._parsed_source(_chunk("set-b", recipe_id="recipe-b"), state) == b"set-b"
    assert await engine._parsed_source(_chunk(None, recipe_id="recipe-c"), state) is None
    assert calls == ["set-b"]


async def test_recorded_versions_are_separate_and_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def source(*args: Any, chunk_set_id: str, **kwargs: Any) -> bytes:
        calls.append(chunk_set_id)
        return chunk_set_id.encode()

    monkeypatch.setattr(module, "load_parsed_source", source)
    engine, state = _engine(), module._SearchState()
    for value in ["set-a", "set-b", "set-a"]:
        assert await engine._parsed_source(_chunk(value), state) == value.encode()
    assert calls == ["set-a", "set-b"]


async def test_unknown_recorded_version_has_no_recipe_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def source(*args: Any, chunk_set_id: str, **kwargs: Any) -> bytes:
        calls.append(chunk_set_id)
        raise DocumentSourceNotFoundError("版未記録")

    monkeypatch.setattr(module, "load_parsed_source", source)
    engine, state = _engine(), module._SearchState()
    for _ in range(2):
        assert await engine._parsed_source(_chunk("unknown"), state) is None
    assert calls == ["unknown"]


async def test_missing_version_image_is_explicitly_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded: list[bool] = []
    cropped: list[bool] = []

    async def source(*args: Any, **kwargs: Any) -> bytes:
        loaded.append(True)
        return b"wrong-current-source"

    def crop(*args: Any, **kwargs: Any) -> bytes:
        cropped.append(True)
        return b"png"

    monkeypatch.setattr(module, "load_parsed_source", source)
    monkeypatch.setattr(module, "crop_png", crop)
    chunk = _chunk(None)
    chunk.metadata.update(
        page_width=100,
        page_height=100,
        engine_metadata_json=json.dumps(
            {"image_evidence": [{"image_id": "image-a", "page": 1, "bbox": [0, 0, 10, 10]}]}
        ),
    )
    state = module._SearchState(work_dir=tmp_path)
    receipt: dict[str, Any] = {}
    await _engine()._materialize_image_evidence(chunk, state, receipt=receipt)
    assert loaded == cropped == []
    assert receipt["status"] == "source_unavailable"
    assert receipt["images"][0]["status"] == "source_unavailable"
    assert receipt["metadata_applied"] is False
    assert chunk.chunk_id not in state.chunks
    assert not list(tmp_path.rglob("*.png"))
