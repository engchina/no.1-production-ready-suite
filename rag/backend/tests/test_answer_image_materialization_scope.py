"""既に切り出した画像も、根拠の版をまたいで使わない。"""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from app.rag import answer_engine as module
from app.schemas.search import RetrievedChunk


def _chunk(chunk_id: str, chunk_set_id: str | None) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="document-a",
        chunk_id=chunk_id,
        text="画像の説明",
        score=1,
        file_name="synthetic.pdf",
        metadata={
            "recipe_id": "recipe-a",
            "chunk_set_id": chunk_set_id,
            "page_width": 100,
            "page_height": 100,
            "engine_metadata_json": json.dumps(
                {"image_evidence": [{"image_id": "image-a", "page": 1, "bbox": [0, 0, 10, 10]}]}
            ),
        },
    )


@pytest.mark.parametrize("later_scope", [None, "set-b"])
async def test_existing_image_file_is_not_reused_across_scopes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, later_scope: str | None
) -> None:
    loaded: list[str] = []
    cropped: list[bytes] = []

    async def source(*args: Any, chunk_set_id: str, **kwargs: Any) -> bytes:
        loaded.append(chunk_set_id)
        return chunk_set_id.encode()

    def crop(source: bytes, *args: Any, **kwargs: Any) -> bytes:
        cropped.append(source)
        return b"PNG:" + source

    monkeypatch.setattr(module, "load_parsed_source", source)
    monkeypatch.setattr(module, "crop_png", crop)
    engine = object.__new__(module.AnswerEngine)
    engine._oracle = cast(Any, object())
    state = module._SearchState(work_dir=tmp_path)
    first = _chunk("chunk-a", "set-a")
    later = _chunk("chunk-b", later_scope)
    await engine._materialize_image_evidence(first, state)
    receipt: dict[str, Any] = {}
    await engine._materialize_image_evidence(later, state, receipt=receipt)
    if later_scope is None:
        assert receipt["status"] == "source_unavailable"
        assert later.chunk_id not in state.chunks
        assert loaded == ["set-a"]
        assert cropped == [b"set-a"]
    else:
        assert loaded == ["set-a", "set-b"]
        assert cropped == [b"set-a", b"set-b"]
        paths = []
        for original in [first, later]:
            metadata = json.loads(
                str(state.chunks[original.chunk_id].metadata["engine_metadata_json"])
            )
            image = metadata["image_evidence"][0]
            paths.append(tmp_path / image["source_run_id"] / image["crop_path"])
        assert paths[0] != paths[1]
        assert [p.read_bytes() for p in paths] == [b"PNG:set-a", b"PNG:set-b"]


async def test_same_version_reuses_existing_image_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded: list[str] = []

    async def source(*args: Any, chunk_set_id: str, **kwargs: Any) -> bytes:
        loaded.append(chunk_set_id)
        return b"same-source"

    monkeypatch.setattr(module, "load_parsed_source", source)
    monkeypatch.setattr(module, "crop_png", lambda *args, **kwargs: b"PNG:same-source")
    engine = object.__new__(module.AnswerEngine)
    engine._oracle = cast(Any, object())
    state = module._SearchState(work_dir=tmp_path)
    await engine._materialize_image_evidence(_chunk("chunk-a", "set-a"), state)
    receipt: dict[str, Any] = {}
    await engine._materialize_image_evidence(_chunk("chunk-b", "set-a"), state, receipt=receipt)
    assert loaded == ["set-a"]
    assert receipt["status"] == "completed"
    assert receipt["images"][0]["status"] == "file_reused"
    assert len(list(tmp_path.rglob("*.png"))) == 1


async def test_missing_scope_cannot_reuse_the_chunk_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def source(*args: Any, **kwargs: Any) -> bytes:
        return b"same-source"

    monkeypatch.setattr(module, "load_parsed_source", source)
    monkeypatch.setattr(module, "crop_png", lambda *args, **kwargs: b"PNG:same-source")
    engine = object.__new__(module.AnswerEngine)
    engine._oracle = cast(Any, object())
    state = module._SearchState(work_dir=tmp_path)
    first = _chunk("chunk-a", "set-a")
    await engine._materialize_image_evidence(first, state)
    state.chunks.pop(first.chunk_id)
    receipt: dict[str, Any] = {}
    await engine._materialize_image_evidence(_chunk("chunk-a", None), state, receipt=receipt)
    assert receipt["status"] == "source_unavailable"
    assert receipt["metadata_applied"] is False
    assert first.chunk_id not in state.chunks
