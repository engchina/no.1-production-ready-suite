"""DocRAG Small-to-Big 分割(docrag_small_to_big)の backend Chunk 変換。"""

import json

import pytest

from app.api.routes.documents import (
    _processing_config_drift_groups,
    _snapshot_used_removed_chunking_strategy,
)
from app.clients.oracle import _chunk_search_text
from app.rag.chunking_strategy import DocragChunkingParams, normalize_chunking_strategy
from app.rag.docrag_chunking import (
    DOCRAG_SEARCH_TEXT_KEY,
    DocragLayoutMissingError,
    build_docrag_chunks,
    docrag_chunking_config,
    has_docrag_layout,
)
from app.schemas.extraction import StructuredExtraction


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
            "docrag_layout": {
                "version": 1,
                "pages": [
                    {"page": 1, "width": 1000, "height": 1400, "pdf_width": 600, "pdf_height": 840}
                ],
                "records": records,
            }
        },
    )


def test_docrag_chunks_keep_parent_search_text_and_element_refs() -> None:
    chunks = build_docrag_chunks(_extraction(), source_name="manual.pdf")

    assert chunks
    body = next(chunk for chunk in chunks if "登録ボタン" in chunk.text)
    metadata = body.metadata
    search_text = metadata[DOCRAG_SEARCH_TEXT_KEY]
    assert isinstance(search_text, str) and "登録ボタン" in search_text
    assert "manual.pdf" in search_text
    assert metadata["chunk_group_id"] == metadata["parent_chunk_id"]
    assert "登録ボタン" in str(metadata["docrag_parent_text"])
    assert "docling-p1-3" in str(metadata["element_ids"]).split(",")
    assert metadata["page_start"] == 1
    assert json.loads(str(metadata["bbox"]))[0] == 50.0
    assert json.loads(str(metadata["docrag_metadata_json"]))["schema_version"] == 4
    table = next(chunk for chunk in chunks if chunk.metadata["content_kind"] == "table")
    assert "受注番号" in table.text
    # Oracle Text へは rag_poc の search_text を索引する。
    assert _chunk_search_text(body) == search_text


def test_docrag_strategy_requires_docling_layout() -> None:
    assert normalize_chunking_strategy("docrag_small_to_big") == "docrag_small_to_big"
    plain = StructuredExtraction(raw_text="本文")
    assert not has_docrag_layout(plain)
    with pytest.raises(DocragLayoutMissingError):
        build_docrag_chunks(plain)


def _long_extraction(sentences: int = 60) -> StructuredExtraction:
    body = "".join(
        f"手順{index}では受注番号を入力して登録ボタンを押します。" for index in range(sentences)
    )
    return _extraction([_record(3, "Text", body, [50, 180, 900, 1300])])


def test_docrag_chunks_apply_configured_child_target_chars() -> None:
    """設定の子 chunk 目標文字数を docrag の分割へ渡す(既定は rag_poc の 1000 字)。"""
    extraction = _long_extraction()
    default_chunks = build_docrag_chunks(extraction, source_name="manual.pdf")
    small_chunks = build_docrag_chunks(
        extraction,
        source_name="manual.pdf",
        params=DocragChunkingParams(child_target_chars=300),
    )

    assert len(small_chunks) > len(default_chunks)
    assert max(len(chunk.text) for chunk in small_chunks) < max(
        len(chunk.text) for chunk in default_chunks
    )


def test_docrag_chunking_config_maps_all_params() -> None:
    config = docrag_chunking_config(DocragChunkingParams(600, 1200, 3000, 2, 6))

    assert config.child_target_chars == 600
    assert config.table_child_target_chars == 1200
    assert config.parent_target_chars == 3000
    assert config.parent_max_pages == 2
    assert config.parent_max_children == 6
    # 検索用テキストの 3 項目は rag_poc の既定のまま。
    assert config.contextual_search_text_enabled is True
    assert docrag_chunking_config() == docrag_chunking_config(DocragChunkingParams())


def test_docrag_parent_limits_split_parents() -> None:
    """親の子数上限を下げると、同じ文書でも親が増える。"""
    extraction = _long_extraction()

    def parent_count(params: DocragChunkingParams) -> int:
        chunks = build_docrag_chunks(extraction, params=params)
        return len({chunk.metadata["parent_chunk_id"] for chunk in chunks})

    many = parent_count(DocragChunkingParams(child_target_chars=300, parent_max_children=3))
    few = parent_count(DocragChunkingParams(child_target_chars=300, parent_max_children=20))
    assert many > few


def test_missing_layout_error_is_shown_to_user() -> None:
    """Docling 以外の解析結果で選んだときの失敗理由は、取込ジョブにそのまま表示する。"""
    with pytest.raises(DocragLayoutMissingError) as info:
        build_docrag_chunks(StructuredExtraction(raw_text="本文"))

    assert getattr(info.value, "safe_for_user", False) is True
    assert "Docling" in str(info.value)


def test_drift_compares_docrag_params_only_for_docrag() -> None:
    """DocRAG の 5 項目の差分は DocRAG 親子階層のときだけ見る。旧 snapshot は既定値とみなす。"""
    effective = {
        "chunking_strategy": "docrag_small_to_big",
        "docrag_child_target_chars": 1000,
        "docrag_table_child_target_chars": 3000,
        "docrag_parent_target_chars": 6000,
        "docrag_parent_max_pages": 3,
        "docrag_parent_max_children": 12,
    }
    old_snapshot = {"chunking_strategy": "docrag_small_to_big"}
    assert _processing_config_drift_groups(old_snapshot, effective) == []

    tuned = {**effective, "docrag_child_target_chars": 600}
    assert _processing_config_drift_groups(old_snapshot, tuned) == ["chunking_strategy"]

    structure = {"chunking_strategy": "structure_aware", "docrag_child_target_chars": 600}
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
        {
            "recipe_subset": {
                "effective_processing_config": {"chunking_strategy": "docrag_small_to_big"}
            }
        }
    )
    assert not _snapshot_used_removed_chunking_strategy(None)
