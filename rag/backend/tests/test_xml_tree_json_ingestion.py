"""XML ツリー JSON の取込の経路と分割(#2024)。

- 判定: アップロードの source profile が中身で ``source_format=xml_tree_json`` を付け、取込前の判定
  (既定の Docling では JSON を止める)を通す。ほかの JSON は今までどおり止める。
- 経路: 主の解析エンジン(Docling)を切り替えなくても、XML ツリー JSON は
  parser-xml-tree-json へ回す。
  無効にすると今までどおり主の解析エンジンで判定する。
- 分割: 親子階層(small-to-big)は番号つきの単位 = 親・段落 = 子。構造認識も段落ごとに分け、
  検索の文に引用ラベルの漢数字と算用数字の両方を入れる。
- 出典: 回答の根拠の場所を引用ラベルにし、原典の URL はひな形があるときだけ付ける。
fixture は合成の XML ツリー JSON(rag_parser_core の tests/fixtures/xml_tree_sample.json)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from rag_parser_core.registry import ParserRegistryResult
from rag_parser_core.xml_tree_json import xml_tree_json_extraction
from rag_pipeline_core.chunking import Chunk, chunk_extraction_with_strategy

from app.config import Settings
from app.rag.answer_engine import _xml_tree_location_labels
from app.rag.chunking_small_to_big import (
    build_parent_child_chunks,
    has_xml_tree_units,
    small_to_big_fallback_needed,
)
from app.rag.ingestion import IngestionPipeline
from app.rag.parser_source_guard import parser_source_block
from app.rag.source_format_routing import (
    apply_xml_tree_source_urls,
    source_format_parser_backend,
)
from app.rag.source_profile import build_source_profile
from app.rag.variant_keys import extraction_recipe_subset
from app.schemas.document import SourceProfile

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "rag_parser_core"
    / "tests"
    / "fixtures"
    / "xml_tree_sample.json"
)
TITLE = "サンプル規程"


def _settings(**updates: object) -> Settings:
    base = Settings().model_copy(
        update={
            "rag_parser_adapter_backend": "docling",
            "rag_parser_docling_enabled": True,
            "rag_parser_unstructured_enabled": False,
            "rag_preprocess_profile": "passthrough",
        }
    )
    return base.model_copy(update=updates)


def _profile(data: bytes, *, file_name: str = "sample.json") -> SourceProfile:
    return build_source_profile(
        original_file_name=file_name,
        sanitized_file_name=file_name,
        content_type="application/json",
        file_size_bytes=len(data),
        content_sha256="a" * 64,
        data=data,
    )


def test_source_profile_marks_supported_tree_json() -> None:
    assert _profile(FIXTURE.read_bytes()).source_format == "xml_tree_json"
    assert _profile(b'{"items": [1, 2]}').source_format is None
    # 中身を渡さない判定(拡張子だけ)では付けない。
    no_data = build_source_profile(
        original_file_name="sample.json",
        sanitized_file_name="sample.json",
        content_type="application/json",
        file_size_bytes=10,
        content_sha256="a" * 64,
    )
    assert no_data.source_format is None


def test_guard_does_not_block_tree_json_with_default_docling() -> None:
    settings = _settings()
    assert parser_source_block(settings, _profile(FIXTURE.read_bytes())) is None
    # ほかの JSON は今までどおり止める(Unstructured を案内する)。
    block = parser_source_block(settings, _profile(b'{"items": [1]}'))
    assert block is not None and block.backend == "docling"
    # 無効にすると、XML ツリー JSON も主の解析エンジンの判定に戻る。
    disabled = _settings(rag_parser_xml_tree_json_enabled=False)
    assert parser_source_block(disabled, _profile(FIXTURE.read_bytes())) is not None


def test_routing_uses_bytes_when_profile_has_no_source_format() -> None:
    data = FIXTURE.read_bytes()
    legacy_profile = _profile(data).model_copy(update={"source_format": None})
    settings = _settings()
    assert source_format_parser_backend(settings, source_profile=legacy_profile) is None
    assert (
        source_format_parser_backend(
            settings, source_profile=legacy_profile, data=data, content_type="application/json"
        )
        == "xml_tree_json"
    )
    disabled = _settings(rag_parser_xml_tree_json_enabled=False)
    assert source_format_parser_backend(disabled, source_profile=legacy_profile, data=data) is None


def test_partition_source_routes_tree_json_to_xml_tree_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    def _runner(
        self: object,
        backend: str,
        source_bytes: bytes,
        source_profile: SourceProfile | None,
        content_type: str,
        *,
        fail_fast: bool = False,
    ) -> ParserRegistryResult:
        called.append(backend)
        return ParserRegistryResult(
            extraction=xml_tree_json_extraction(source_bytes),
            parser_backend=backend,
            parser_version="test",
        )

    monkeypatch.setattr(
        "app.clients.parser_service.ParserServiceClient.runner", _runner, raising=True
    )
    pipeline = IngestionPipeline(
        vlm=cast(Any, object()),
        genai=cast(Any, object()),
        oracle=cast(Any, object()),
        object_storage=cast(Any, object()),
        document_understanding=cast(Any, object()),
        speech=cast(Any, object()),
        settings=_settings(),
    )
    data = FIXTURE.read_bytes()
    result = pipeline._partition_source(
        parse_bytes=data, source_profile=_profile(data), content_type="application/json"
    )
    assert called == ["xml_tree_json"]
    assert result.parser_backend == "xml_tree_json"
    # 主の解析エンジン(Docling)の選択とは比べない(別経路に縮退しない検査を通る)。
    pipeline._raise_if_selected_parser_was_not_used(result)


def _extraction() -> Any:
    extraction = xml_tree_json_extraction(FIXTURE.read_bytes())
    assert extraction is not None
    return extraction


def test_small_to_big_uses_units_as_parents_and_paragraphs_as_children() -> None:
    extraction = _extraction()
    assert has_xml_tree_units(extraction)
    assert not small_to_big_fallback_needed("small_to_big", extraction)
    chunks = build_parent_child_chunks(extraction, source_name="sample.json")
    by_label = {chunk.metadata["xml_tree_citation_label_arabic"]: chunk for chunk in chunks}
    first = by_label[f"{TITLE} 第7条第1項"]
    second = by_label[f"{TITLE} 第7条第2項"]
    # 同じ単位の段落は同じ親で、親の本文は両方の段落を持つ。
    assert first.metadata["parent_chunk_id"] == second.metadata["parent_chunk_id"]
    assert "第七条第一項" in str(first.metadata["parent_text"])
    assert "第七条第二項" in str(first.metadata["parent_text"])
    other = by_label[f"{TITLE} 第7条の2"]
    assert other.metadata["parent_chunk_id"] != first.metadata["parent_chunk_id"]
    search_text = str(first.metadata["engine_search_text"])
    assert "第七条第一項" in search_text and "第7条第1項" in search_text
    assert "第7条第1項第2号" in search_text
    assert "第7条第1項" in str(first.metadata["xml_tree_lookup_keys"]).splitlines()


def test_small_to_big_splits_long_paragraphs_at_line_boundaries() -> None:
    from app.rag.chunking_strategy import SmallToBigParams

    extraction = _extraction()
    chunks = build_parent_child_chunks(
        extraction, params=SmallToBigParams(child_target_chars=200, parent_target_chars=6000)
    )
    parts = [
        chunk
        for chunk in chunks
        if chunk.metadata["xml_tree_citation_label_arabic"] == f"{TITLE} 別表第1"
    ]
    assert parts
    # 各部分の先頭の行はラベルを繰り返す。
    assert all(chunk.text.splitlines()[0].startswith(f"{TITLE} 別表第一") for chunk in parts)


def test_structure_aware_keeps_one_chunk_per_paragraph() -> None:
    chunks = chunk_extraction_with_strategy(
        _extraction(), strategy="structure_aware", chunk_size=800, overlap=120, min_chars=50
    )
    labels = [chunk.metadata.get("xml_tree_citation_label_arabic") for chunk in chunks]
    assert f"{TITLE} 第7条第1項" in labels and f"{TITLE} 第7条第2項" in labels
    assert all(chunk.metadata.get("engine_search_text") for chunk in chunks)


def test_location_labels_for_answer_citations() -> None:
    paragraph = {
        "xml_tree_document_title": TITLE,
        "xml_tree_label_arabic": "第7条第1項",
        "xml_tree_parent_label_arabic": f"{TITLE} 第7条",
    }
    assert _xml_tree_location_labels(paragraph) == ("第7条第1項", "第7条")
    assert _xml_tree_location_labels({"section_path": "x"}) is None


def test_source_url_template_is_applied_only_when_https() -> None:
    chunks = [
        Chunk(
            text="a", index=0, start_offset=0, end_offset=1, metadata={"xml_tree_document_id": "D1"}
        ),
        Chunk(text="b", index=1, start_offset=0, end_offset=1, metadata={}),
    ]
    apply_xml_tree_source_urls(chunks, _settings())
    assert "xml_tree_source_url" not in chunks[0].metadata
    apply_xml_tree_source_urls(
        chunks,
        _settings(rag_xml_tree_json_source_url_template="https://example.test/doc/{document_id}"),
    )
    assert chunks[0].metadata["xml_tree_source_url"] == "https://example.test/doc/D1"
    assert "xml_tree_source_url" not in chunks[1].metadata
    plain = [
        Chunk(
            text="a", index=0, start_offset=0, end_offset=1, metadata={"xml_tree_document_id": "D1"}
        )
    ]
    apply_xml_tree_source_urls(
        plain, _settings(rag_xml_tree_json_source_url_template="http://x/{document_id}")
    )
    assert "xml_tree_source_url" not in plain[0].metadata


def test_disabling_tree_json_changes_extraction_recipe_only_when_not_default() -> None:
    assert "rag_parser_xml_tree_json_enabled" not in extraction_recipe_subset(_settings())
    subset = extraction_recipe_subset(_settings(rag_parser_xml_tree_json_enabled=False))
    assert subset["rag_parser_xml_tree_json_enabled"] is False
