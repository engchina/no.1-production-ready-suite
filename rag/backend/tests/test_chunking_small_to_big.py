"""Small-to-Big 分割(small_to_big)の backend Chunk 変換。"""

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from rag_engine.retrieval.text_search_tokenizer import normalize_text_search_index_text

from app.api.routes import documents as documents_route
from app.api.routes.documents import (
    _processing_config_drift_groups,
    _snapshot_used_removed_chunking_strategy,
)
from app.clients.oracle import _chunk_search_text
from app.config import Settings
from app.main import app
from app.rag.chunking_small_to_big import (
    CHUNK_STRATEGY_FALLBACK_REASON_KEY,
    CHUNK_STRATEGY_REQUESTED_KEY,
    ENGINE_SEARCH_TEXT_KEY,
    FIRST_PAGE_CONTEXT_KEY,
    LayoutRecordsMissingError,
    build_parent_child_chunks,
    has_layout_records,
    small_to_big_chunking_config,
    small_to_big_fallback_needed,
)
from app.rag.chunking_strategy import SmallToBigParams, normalize_chunking_strategy
from app.rag.ingestion import IngestionPipeline
from app.rag.ingestion_quality import build_ingestion_quality_report
from app.schemas.document import DocumentDetail, FileStatus
from app.schemas.extraction import StructuredExtraction
from tests.support import AsgiTestClient


def _record(seq: int, category: str, text: str, bbox: list[float]) -> dict[str, object]:
    return {
        "id": f"docling-p1-{seq}",
        "engine": "docling",
        "page": 1,
        "seq_no": seq,
        "bbox": bbox,
        "coord_system": "image_top_left",
        "page_width": 1000,
        "page_height": 1400,
        "category": category,
        "text": text,
        "confidence": None,
        "raw_type": category.lower(),
        "raw": {},
    }


def _extraction(body_records: list[dict[str, object]] | None = None) -> StructuredExtraction:
    records = [
        _record(1, "Title", "受注登録マニュアル", [50, 40, 900, 90]),
        _record(2, "Section-header", "受注入力画面", [50, 120, 600, 160]),
        _record(3, "Text", "受注番号を入力し、登録ボタンを押します。", [50, 180, 900, 240]),
        _record(
            4,
            "Table",
            "<table><tr><th>項目</th><th>説明</th></tr><tr><td>受注番号</td><td>必須</td></tr></table>",
            [50, 260, 900, 420],
        ),
    ]
    if body_records is not None:
        records = [*records[:2], *body_records]
    return StructuredExtraction(
        raw_text="受注登録マニュアル",
        parser_artifacts={
            "layout_records": {
                "version": 1,
                "pages": [
                    {"page": 1, "width": 1000, "height": 1400, "pdf_width": 600, "pdf_height": 840}
                ],
                "records": records,
            }
        },
    )


def test_small_to_big_chunks_keep_parent_search_text_and_element_refs() -> None:
    chunks = build_parent_child_chunks(_extraction(), source_name="manual.pdf")

    assert chunks
    body = next(chunk for chunk in chunks if "登録ボタン" in chunk.text)
    metadata = body.metadata
    search_text = metadata[ENGINE_SEARCH_TEXT_KEY]
    assert isinstance(search_text, str) and "登録ボタン" in search_text
    assert "manual.pdf" in search_text
    assert metadata["chunk_group_id"] == metadata["parent_chunk_id"]
    assert "登録ボタン" in str(metadata["parent_text"])
    assert "docling-p1-3" in str(metadata["element_ids"]).split(",")
    assert metadata["page_start"] == 1
    assert json.loads(str(metadata["bbox"]))[0] == 50.0
    assert json.loads(str(metadata["engine_metadata_json"]))["schema_version"] == 4
    table = next(chunk for chunk in chunks if chunk.metadata["content_kind"] == "table")
    assert "受注番号" in table.text
    # Oracle Text へは rag_poc の search_text を索引する。
    assert _chunk_search_text(body) == search_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"vision_model": "vision-model-1", "vision_status": "succeeded"}, "vision"),
        ({"vision_model": "vision-model-1", "vision_error": "timeout"}, "ocr"),
        ({}, "ocr"),
    ],
)
def test_small_to_big_figure_records_whether_vlm_wrote_text(
    raw: dict[str, object], expected: str
) -> None:
    """図の chunk に本文の出どころ（VLM の説明か OCR か）と図の領域を残す（#1282）。"""
    picture = _record(3, "Picture", "申請画面。右上の承認ボタンを押す。", [100, 200, 700, 900])
    picture["raw"] = raw
    chunks = build_parent_child_chunks(
        _extraction([picture, _record(4, "Text", "本文です。", [50, 950, 900, 1000])]),
        source_name="manual.pdf",
    )

    figure = next(chunk for chunk in chunks if chunk.metadata["content_kind"] == "figure")
    text = next(chunk for chunk in chunks if chunk.metadata["content_kind"] == "text")
    assert figure.metadata["figure_text_source"] == expected
    assert "figure_text_source" not in text.metadata
    images = json.loads(str(figure.metadata["engine_metadata_json"]))["image_evidence"]
    assert images[0]["page"] == 1
    assert images[0]["bbox"] == [100, 200, 700, 900]


def test_small_to_big_strategy_requires_docling_layout() -> None:
    assert normalize_chunking_strategy("small_to_big") == "small_to_big"
    plain = StructuredExtraction(raw_text="本文")
    assert not has_layout_records(plain)
    with pytest.raises(LayoutRecordsMissingError):
        build_parent_child_chunks(plain)


def test_small_to_big_first_page_context_is_kept_once_without_picture_ocr_and_truncated() -> None:
    """1 ページ目の本文だけを、Picture・OCR を除き上限 8000 文字で、先頭の chunk に 1 つだけ載せる。

    chunk ごとの metadata(engine_metadata_json)と検索の文には入れない(#557)。
    """
    cover = "受注管理規程 第3版 営業本部"
    long_text = "規程の目的と適用範囲を定める。" * 600
    records = [
        _record(1, "Title", cover, [50, 40, 900, 90]),
        {
            **_record(2, "Picture", "OCR抽出テキスト:\n氏名\n住所", [50, 100, 900, 300]),
            "raw_type": "picture_ocr_text",
        },
        _record(3, "Text", long_text, [50, 320, 900, 1300]),
        {
            **_record(4, "Section-header", "受注入力画面", [50, 40, 600, 90]),
            "id": "docling-p2-4",
            "page": 2,
        },
        {
            **_record(5, "Text", "受注番号を入力し、登録ボタンを押します。", [50, 120, 900, 200]),
            "id": "docling-p2-5",
            "page": 2,
        },
    ]
    extraction = StructuredExtraction(
        raw_text=cover,
        parser_artifacts={
            "layout_records": {
                "version": 1,
                "pages": [{"page": page, "width": 1000, "height": 1400} for page in (1, 2)],
                "records": records,
            }
        },
    )

    chunks = build_parent_child_chunks(extraction, source_name="規程.pdf")

    first_page = json.loads(str(chunks[0].metadata[FIRST_PAGE_CONTEXT_KEY]))
    assert first_page["page"] == 1
    assert first_page["status"] == "available"
    assert first_page["text"].startswith(cover)
    assert "OCR抽出テキスト" not in first_page["text"]
    assert "登録ボタン" not in first_page["text"]
    assert len(first_page["text"]) == 8000
    assert first_page["truncated"] is True
    assert "docling-p1-2" not in first_page["record_ids"]
    assert all(FIRST_PAGE_CONTEXT_KEY not in chunk.metadata for chunk in chunks[1:])
    for chunk in chunks:
        document = json.loads(str(chunk.metadata["engine_metadata_json"]))["document"]
        assert "first_page_context" not in document
    # 検索の文は rag_engine の search_text。Oracle Text へは質問と同じ文字の形（NFKC）にして
    # 索引する（#1336。embedding の入力は ingestion が engine_search_text をそのまま使う）。
    assert _chunk_search_text(chunks[0]) == normalize_text_search_index_text(
        chunks[0].metadata[ENGINE_SEARCH_TEXT_KEY]
    )


def _long_extraction(sentences: int = 60) -> StructuredExtraction:
    body = "".join(
        f"手順{index}では受注番号を入力して登録ボタンを押します。" for index in range(sentences)
    )
    return _extraction([_record(3, "Text", body, [50, 180, 900, 1300])])


def test_small_to_big_chunks_apply_configured_child_target_chars() -> None:
    """設定の子 chunk 目標文字数を rag_engine の分割へ渡す(既定は rag_poc の 1000 字)。"""
    extraction = _long_extraction()
    default_chunks = build_parent_child_chunks(extraction, source_name="manual.pdf")
    small_chunks = build_parent_child_chunks(
        extraction,
        source_name="manual.pdf",
        params=SmallToBigParams(child_target_chars=300),
    )

    assert len(small_chunks) > len(default_chunks)
    assert max(len(chunk.text) for chunk in small_chunks) < max(
        len(chunk.text) for chunk in default_chunks
    )


def test_small_to_big_chunking_config_maps_all_params() -> None:
    config = small_to_big_chunking_config(SmallToBigParams(600, 1200, 3000, 2, 6))

    assert config.child_target_chars == 600
    assert config.table_child_target_chars == 1200
    assert config.parent_target_chars == 3000
    assert config.parent_max_pages == 2
    assert config.parent_max_children == 6
    # 検索用テキストの 3 項目は rag_poc の既定のまま。
    assert config.contextual_search_text_enabled is True
    assert small_to_big_chunking_config() == small_to_big_chunking_config(SmallToBigParams())


def test_small_to_big_parent_limits_split_parents() -> None:
    """親の子数上限を下げると、同じ文書でも親が増える。"""
    extraction = _long_extraction()

    def parent_count(params: SmallToBigParams) -> int:
        chunks = build_parent_child_chunks(extraction, params=params)
        return len({chunk.metadata["parent_chunk_id"] for chunk in chunks})

    many = parent_count(SmallToBigParams(child_target_chars=300, parent_max_children=3))
    few = parent_count(SmallToBigParams(child_target_chars=300, parent_max_children=20))
    assert many > few


def test_missing_layout_error_is_shown_to_user() -> None:
    """Docling 以外の解析結果で選んだときの失敗理由は、取込ジョブにそのまま表示する。"""
    with pytest.raises(LayoutRecordsMissingError) as info:
        build_parent_child_chunks(StructuredExtraction(raw_text="本文"))

    assert getattr(info.value, "safe_for_user", False) is True
    assert "Docling" in str(info.value)


def test_drift_compares_small_to_big_params_only_for_small_to_big() -> None:
    """親子階層（small-to-big）の 5 項目の差分は、その方式のときだけ見る。

    旧 snapshot は既定値とみなす。
    """
    effective = {
        "chunking_strategy": "small_to_big",
        "chunk_child_target_chars": 1000,
        "chunk_table_child_target_chars": 3000,
        "chunk_parent_target_chars": 6000,
        "chunk_parent_max_pages": 3,
        "chunk_parent_max_children": 12,
    }
    old_snapshot = {"chunking_strategy": "small_to_big"}
    assert _processing_config_drift_groups(old_snapshot, effective) == []

    tuned = {**effective, "chunk_child_target_chars": 600}
    assert _processing_config_drift_groups(old_snapshot, tuned) == ["chunking_strategy"]

    structure = {"chunking_strategy": "structure_aware", "chunk_child_target_chars": 600}
    assert (
        _processing_config_drift_groups({"chunking_strategy": "structure_aware"}, structure) == []
    )


def test_snapshot_built_with_removed_strategy_is_reported_as_drift() -> None:
    row = {
        "recipe_subset": {
            "effective_processing_config": {"chunking_strategy": "hierarchical_parent_child"}
        }
    }
    assert _snapshot_used_removed_chunking_strategy(row)
    assert not _snapshot_used_removed_chunking_strategy(
        {"recipe_subset": {"effective_processing_config": {"chunking_strategy": "small_to_big"}}}
    )
    assert not _snapshot_used_removed_chunking_strategy(None)


def _pipeline(strategy: str) -> IngestionPipeline:
    return IngestionPipeline(
        vlm=cast(Any, object()),
        genai=cast(Any, object()),
        oracle=cast(Any, object()),
        object_storage=cast(Any, object()),
        settings=Settings(rag_chunking_strategy=strategy),
    )


async def _ingestion_chunks(strategy: str, extraction: StructuredExtraction) -> list[Any]:
    return await _pipeline(strategy)._build_chunks_for_extraction(
        trace_id="trace-300",
        extraction=extraction,
        quality_report=build_ingestion_quality_report(extraction),
        parser_profile="unstructured",
        source_name="policy.pdf",
    )


def _plain_extraction() -> StructuredExtraction:
    """Docling 以外の解析結果(layout_records なし)。"""
    body = "\n\n".join(
        f"第{index}条 申請者は所定の様式で申請書を提出し、承認を受ける。" for index in range(1, 6)
    )
    return StructuredExtraction(raw_text=f"就業規則\n\n{body}")


def test_small_to_big_fallback_needed_only_for_small_to_big_without_layout() -> None:
    assert small_to_big_fallback_needed("small_to_big", _plain_extraction()) is True
    assert small_to_big_fallback_needed("small_to_big", _extraction()) is False
    assert small_to_big_fallback_needed("structure_aware", _plain_extraction()) is False


async def test_ingestion_falls_back_to_structure_aware_without_docling_layout() -> None:
    """Docling 以外の解析結果で親子階層（small-to-big）を選んでも失敗させず、構造認識で分割する
    (#300)。

    以前は LayoutRecordsMissingError で Chunk 作成のジョブが失敗していた。
    どの方式で分割したかは chunk の metadata に残す。
    """
    chunks = await _ingestion_chunks("small_to_big", _plain_extraction())

    assert chunks
    for chunk in chunks:
        assert chunk.metadata["chunk_strategy"] == "structure_aware"
        assert chunk.metadata[CHUNK_STRATEGY_REQUESTED_KEY] == "small_to_big"
        assert chunk.metadata[CHUNK_STRATEGY_FALLBACK_REASON_KEY] == "layout_missing"
        assert ENGINE_SEARCH_TEXT_KEY not in chunk.metadata
    # 縮退後の分割は、同じ設定で構造認識を選んだときと同じ本文になる。
    direct = await _ingestion_chunks("structure_aware", _plain_extraction())
    assert [chunk.text for chunk in chunks] == [chunk.text for chunk in direct]
    assert CHUNK_STRATEGY_FALLBACK_REASON_KEY not in direct[0].metadata


async def test_ingestion_uses_small_to_big_when_docling_layout_exists() -> None:
    """Docling の解析結果があれば従来どおり親子階層（small-to-big）で分割し、縮退の印は付けない。"""
    chunks = await _ingestion_chunks("small_to_big", _extraction())

    assert chunks
    assert all(chunk.metadata["chunk_strategy"] == "small_to_big" for chunk in chunks)
    assert all(CHUNK_STRATEGY_FALLBACK_REASON_KEY not in chunk.metadata for chunk in chunks)


class _PreviewOracle:
    """分割プレビュー route が読む OracleClient の部分集合(保存済みの抽出結果だけを返す)。"""

    def __init__(self, extraction: StructuredExtraction) -> None:
        self._extraction = extraction

    async def get_document(self, document_id: str) -> DocumentDetail:
        return DocumentDetail(
            id=document_id,
            file_name="policy.pdf",
            status=FileStatus.REVIEW,
            content_sha256="a" * 64,
            uploaded_at=datetime.now(UTC),
        )

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
        _ = document_id
        return {
            "recipe_id": recipe_id,
            "status": "REVIEW",
            "active_extraction_recipe_id": "er-1",
            "processing_config": {},
        }

    async def get_document_extraction_artifact(self, **_: object) -> dict[str, object]:
        return {"extraction_json": self._extraction.model_dump(mode="json")}


@pytest.mark.parametrize(
    ("extraction", "expected_strategy", "fallback"),
    [
        (_plain_extraction(), "structure_aware", True),
        (_extraction(), "small_to_big", False),
    ],
)
def test_chunk_preview_small_to_big_falls_back_without_docling_layout(
    monkeypatch: pytest.MonkeyPatch,
    extraction: StructuredExtraction,
    expected_strategy: str,
    fallback: bool,
) -> None:
    """分割プレビューも取込と同じく、Docling の解析結果がなければ構造認識で分割する(#300)。

    以前は「親子階層（small-to-big）には Docling の解析結果が必要です。」の 422 だった。
    """
    monkeypatch.setattr(documents_route, "OracleClient", lambda: _PreviewOracle(extraction))

    response = AsgiTestClient(app).post(
        "/api/documents/doc-1/recipes/recipe-1/chunk-preview",
        json={"chunking_strategy": "small_to_big"},
    )

    assert response.status_code == 200, response.text
    chunks = response.json()["data"]["chunks"]
    assert chunks
    for chunk in chunks:
        metadata = chunk["metadata"]
        assert metadata["chunk_strategy"] == expected_strategy
        if fallback:
            assert metadata[CHUNK_STRATEGY_REQUESTED_KEY] == "small_to_big"
            assert metadata[CHUNK_STRATEGY_FALLBACK_REASON_KEY] == "layout_missing"
        else:
            assert CHUNK_STRATEGY_FALLBACK_REASON_KEY not in metadata
