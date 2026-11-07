"""図の VLM の 1 件が回答本文の field の無い応答で失敗しても、文書の取込を続ける（#4570）。

その図だけを「VLM の本文なし」（``vision_error`` あり・画像本文の投影なし）にし、warning と失敗の
件数で見えるようにする。文書の分割は止めず、検索でその図の chunk を旧い画像説明として止めない。
あとでその図だけを読み直せる（読み取り済みの図は呼び直さない）。
合成の PDF と決定論のスタブで確かめる。
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from rag_engine.chunking.records import _vision_projection_status
from rag_parser_core.vision_text import VISION_TEXT_PROJECTION_KEY

from app.rag.answer_engine import _stored_child
from app.rag.vision import WARNING_PARTIAL_FAILURE
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import RetrievedChunk
from tests.test_ingestion_pdf_segments import _pdf
from tests.test_vision_pipeline import (
    PromptOracle,
    Runner,
    Storage,
    _ingest,
    _install,
    _pipeline,
    _saved_extraction,
)

FAILING_PICTURE_ID = "docling-p1-9"
READ_PICTURE_ID = "docling-p2-9"


class Reader:
    """Vision のスタブ。``failing_id`` の図の prompt には回答本文の field の無い応答を返す。"""

    def __init__(self, failing_id: str | None = None) -> None:
        self.failing_id = failing_id
        self.prompts: list[str] = []

    async def generate_from_images(self, images: Any, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if self.failing_id is not None and self.failing_id in prompt:
            return json.dumps({"search_keywords": ["索引専用の語"]}, ensure_ascii=False)
        return json.dumps({"retrieval_text": "合成の図の説明です。"}, ensure_ascii=False)

    async def generate(self, prompt: str, context: str, **_: Any) -> str:
        return "[]"


class ChunkOracle(PromptOracle):
    """保存した chunk（検索の行と同じ metadata）も残す fake。"""

    def __init__(self) -> None:
        super().__init__()
        self.saved_chunks: list[Any] = []

    async def save_index(
        self,
        document_id: str,
        extraction: StructuredExtraction,
        chunks: list[Any],
        vectors: list[list[float]],
        chunk_set_id: str | None = None,
    ) -> None:
        await super().save_index(document_id, extraction, chunks, vectors, chunk_set_id)
        self.saved_chunks = list(chunks)


def _pictures(extraction: StructuredExtraction) -> dict[str, dict[str, Any]]:
    layout = cast(dict[str, Any], extraction.parser_artifacts["layout_records"])
    return {
        record["id"]: record
        for record in layout["records"]
        if record["category"] == "Picture" and record["raw_type"] == "picture"
    }


async def _ingest_with_one_failed_picture(
    monkeypatch: pytest.MonkeyPatch, storage: Storage | None = None
) -> tuple[ChunkOracle, Reader]:
    _install(monkeypatch, Runner())
    reader = Reader(failing_id=FAILING_PICTURE_ID)
    oracle = ChunkOracle()
    await _ingest(_pipeline(cast(Any, reader), oracle, storage or Storage()), _pdf(2))
    return oracle, reader


async def test_one_failed_picture_does_not_stop_the_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """その図だけが VLM の本文なしになり、文書は分割・保存まで進み、失敗は warning に残る。"""
    oracle, reader = await _ingest_with_one_failed_picture(monkeypatch)

    assert len(reader.prompts) == 2
    assert oracle.saved_chunk_count > 0
    extraction = _saved_extraction(oracle)
    assert WARNING_PARTIAL_FAILURE in extraction.warnings
    summary = cast(dict[str, Any], extraction.parser_artifacts["vision"])
    assert summary["failed"] == 1 and summary["succeeded"] == 1

    pictures = _pictures(extraction)
    failed = pictures[FAILING_PICTURE_ID]
    assert failed["raw"]["vision_status"] == "failed"
    assert "説明フィールド" in failed["raw"]["vision_error"]
    assert VISION_TEXT_PROJECTION_KEY not in failed["raw"]
    assert failed["text"] == ""
    assert _vision_projection_status(failed) == "vision_failed"
    read = pictures[READ_PICTURE_ID]
    assert read["raw"]["vision_status"] == "succeeded"
    assert _vision_projection_status(read) == "verified"

    # 失敗した図の VLM の語（検索専用の語も）は chunk に入らない。
    assert all(
        "索引専用の語" not in chunk.text
        and "索引専用の語" not in str(chunk.metadata.get("engine_search_text") or "")
        for chunk in oracle.saved_chunks
    )
    assert any("合成の図の説明です。" in chunk.text for chunk in oracle.saved_chunks)
    # 失敗した図の chunk の出所には「VLM の本文なし」と失敗の理由が残る（図の場所は根拠に残す）。
    failed_refs = [
        ref
        for chunk in oracle.saved_chunks
        for ref in json.loads(str(chunk.metadata["source_record_refs_json"]))
        if ref["record_id"] == FAILING_PICTURE_ID
    ]
    assert failed_refs
    assert all(ref["vision_text_projection_status"] == "vision_failed" for ref in failed_refs)
    assert all("説明フィールド" in ref["vision_error"] for ref in failed_refs)


async def test_search_does_not_treat_the_failed_picture_as_old_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存した chunk を検索の候補にしても、旧い画像説明（未分離の回答本文）として止めない。"""
    oracle, _ = await _ingest_with_one_failed_picture(monkeypatch)

    for index, chunk in enumerate(oracle.saved_chunks):
        hit = RetrievedChunk(
            document_id="document-a",
            chunk_id=f"chunk-{index}",
            text=chunk.text,
            score=1.0,
            file_name="文書A.pdf",
            metadata=dict(chunk.metadata),
        )
        _stored_child(hit, rrf_score=1.0)


async def test_reingest_reads_only_the_failed_picture(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ文書の取込をやり直すと、保存した抽出結果から失敗した図だけを VLM に渡す。"""
    storage = Storage()
    oracle, _ = await _ingest_with_one_failed_picture(monkeypatch, storage)

    reader = Reader()
    await _ingest(_pipeline(cast(Any, reader), oracle, storage), _pdf(2))

    assert len(reader.prompts) == 1 and FAILING_PICTURE_ID in reader.prompts[0]
    extraction = _saved_extraction(oracle)
    assert WARNING_PARTIAL_FAILURE not in extraction.warnings
    pictures = _pictures(extraction)
    assert all(_vision_projection_status(record) == "verified" for record in pictures.values())
    assert any("合成の図の説明です。" in chunk.text for chunk in oracle.saved_chunks)
