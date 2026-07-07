"""variant 層別 keying(variant_keys)の決定論ユニットテスト。"""

import pytest

from app.config import get_settings
from app.rag.variant_keys import (
    compute_chunk_set_id,
    compute_document_recipe_extraction_id,
    compute_extraction_recipe_id,
    compute_graph_layer_id,
    compute_layer_ids,
    compute_metadata_layer_id,
    compute_nav_layer_id,
    extraction_recipe_subset,
)

SRC = "a" * 64
SRC2 = "b" * 64


def test_chunk_set_id_is_deterministic() -> None:
    """同一原本 + 同一設定なら chunk_set_id は安定。"""
    settings = get_settings()
    assert compute_chunk_set_id(SRC, settings) == compute_chunk_set_id(SRC, settings)


def test_chunk_set_id_changes_with_chunk_axis() -> None:
    """chunk 軸(chunk_size)が違えば別 chunk 集合になる。"""
    base = get_settings()
    other = base.model_copy(update={"rag_chunk_size": base.rag_chunk_size + 256})
    assert compute_chunk_set_id(SRC, base) != compute_chunk_set_id(SRC, other)


def test_chunk_set_id_changes_with_context_header_axis() -> None:
    """索引対象テキストが変わるため context header の有無は別 chunk_set。"""
    enabled = get_settings().model_copy(update={"rag_chunk_context_header_enabled": True})
    disabled = enabled.model_copy(update={"rag_chunk_context_header_enabled": False})

    assert compute_chunk_set_id(SRC, enabled) != compute_chunk_set_id(SRC, disabled)


def test_extraction_recipe_id_is_deterministic() -> None:
    """同一原本 + 同一設定なら extraction_recipe_id は安定。prefix は er_。"""
    settings = get_settings()
    recipe_id = compute_extraction_recipe_id(SRC, settings)
    assert recipe_id == compute_extraction_recipe_id(SRC, settings)
    assert recipe_id.startswith("er_")


def test_document_recipe_extraction_id_is_isolated_by_recipe_and_revision() -> None:
    """同じ抽出設定でも recipe/revision が違えば成果物を共有しない。"""
    base = compute_extraction_recipe_id(SRC, get_settings())
    first = compute_document_recipe_extraction_id(base, "recipe-1", 2)

    assert first == compute_document_recipe_extraction_id(base, "recipe-1", 2)
    assert first != compute_document_recipe_extraction_id(base, "recipe-2", 2)
    assert first != compute_document_recipe_extraction_id(base, "recipe-1", 3)


def test_extraction_recipe_id_ignores_chunk_axis() -> None:
    """chunking だけ違う場合は同じ extraction を再利用できる。"""
    base = get_settings()
    other = base.model_copy(update={"rag_chunk_size": base.rag_chunk_size + 256})

    assert compute_extraction_recipe_id(SRC, base) == compute_extraction_recipe_id(SRC, other)
    assert compute_chunk_set_id(SRC, base) != compute_chunk_set_id(SRC, other)


def test_extraction_recipe_id_changes_with_parser_axis() -> None:
    """Parser 軸が違えば保存済み extraction は共有しない。"""
    base = get_settings().model_copy(update={"rag_parser_adapter_backend": "local"})
    other = get_settings().model_copy(update={"rag_parser_adapter_backend": "docling"})

    assert compute_extraction_recipe_id(SRC, base) != compute_extraction_recipe_id(SRC, other)
    assert compute_chunk_set_id(SRC, base) != compute_chunk_set_id(SRC, other)


@pytest.mark.parametrize(
    ("backend", "field", "value"),
    [
        ("unlimited_ocr", "rag_parser_unlimited_ocr_model", "unlimited-v2"),
        ("unlimited_ocr", "rag_parser_unlimited_ocr_dpi", 600),
        ("unlimited_ocr", "rag_parser_unlimited_ocr_pdf_batch_size", 8),
        ("mineru", "rag_parser_mineru_language", "english"),
        ("dots_ocr", "rag_parser_dots_ocr_model", "dots-v2"),
        ("dots_ocr", "rag_parser_dots_ocr_dpi", 400),
        ("glm_ocr", "rag_parser_glm_ocr_model", "glm-v2"),
        ("glm_ocr", "rag_parser_glm_ocr_dpi", 500),
    ],
)
def test_extraction_recipe_id_changes_with_output_affecting_parser_setting(
    backend: str,
    field: str,
    value: object,
) -> None:
    base = get_settings().model_copy(update={"rag_parser_adapter_backend": backend})
    changed = base.model_copy(update={field: value})
    base_id = compute_extraction_recipe_id(SRC, base)
    changed_id = compute_extraction_recipe_id(SRC, changed)

    assert base_id != changed_id
    assert compute_document_recipe_extraction_id(
        base_id, "recipe-1", 1
    ) != compute_document_recipe_extraction_id(changed_id, "recipe-1", 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rag_parser_unlimited_ocr_model", "unlimited-v2"),
        ("rag_parser_mineru_language", "english"),
        ("rag_parser_dots_ocr_dpi", 400),
        ("rag_parser_glm_ocr_model", "glm-v2"),
    ],
)
def test_extraction_recipe_id_ignores_unselected_parser_setting(
    field: str,
    value: object,
) -> None:
    base = get_settings().model_copy(update={"rag_parser_adapter_backend": "unstructured"})
    changed = base.model_copy(update={field: value})

    assert compute_extraction_recipe_id(SRC, base) == compute_extraction_recipe_id(SRC, changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rag_parser_glm_ocr_api_host", "https://new.example.com"),
        ("rag_parser_glm_ocr_api_key", "new-secret"),
        ("rag_parser_dots_ocr_pdf_workers", 12),
    ],
)
def test_extraction_recipe_id_ignores_parser_deployment_setting(
    field: str,
    value: object,
) -> None:
    base = get_settings()
    changed = base.model_copy(update={field: value})

    assert compute_extraction_recipe_id(SRC, base) == compute_extraction_recipe_id(SRC, changed)


def test_extraction_recipe_diagnostics_match_hashed_non_secret_settings() -> None:
    settings = get_settings().model_copy(
        update={
            "rag_parser_adapter_backend": "glm_ocr",
            "rag_parser_glm_ocr_model": "glm-diagnostic",
            "rag_parser_glm_ocr_api_key": "never-persist-this",
        }
    )

    subset = extraction_recipe_subset(settings)

    assert subset["rag_parser_glm_ocr_model"] == "glm-diagnostic"
    assert "rag_parser_glm_ocr_api_key" not in subset
    assert "rag_parser_glm_ocr_api_host" not in subset
    assert "rag_parser_dots_ocr_pdf_workers" not in subset
    assert "rag_parser_dots_ocr_model" not in subset


def test_extraction_recipe_id_changes_with_preprocess_axis_and_source() -> None:
    """前処理軸・原本が違えば別 extraction recipe。"""
    base = get_settings()
    pre = base.model_copy(update={"rag_preprocess_profile": "office_to_pdf"})
    assert compute_extraction_recipe_id(SRC, base) != compute_extraction_recipe_id(SRC, pre)
    assert compute_extraction_recipe_id(SRC, base) != compute_extraction_recipe_id(SRC2, base)


def test_chunk_set_id_changes_with_source() -> None:
    """原本が違えば chunk 集合も別。"""
    settings = get_settings()
    assert compute_chunk_set_id(SRC, settings) != compute_chunk_set_id(SRC2, settings)


def test_graph_axis_shares_chunk_set_but_splits_graph_layer() -> None:
    """graph_profile だけ違うとき chunk_set は共有、graph 層だけ別になる。"""
    off = get_settings().model_copy(update={"rag_graph_profile": "off"})
    entities = get_settings().model_copy(update={"rag_graph_profile": "entities"})

    cs_off = compute_chunk_set_id(SRC, off)
    cs_entities = compute_chunk_set_id(SRC, entities)
    assert cs_off == cs_entities
    assert compute_graph_layer_id(cs_off, off) != compute_graph_layer_id(cs_entities, entities)


def test_field_extraction_axis_shares_chunk_set_but_splits_metadata_layer() -> None:
    """field_extraction だけ違うとき chunk_set は共有、metadata 層だけ別になる。"""
    off = get_settings().model_copy(update={"rag_field_extraction_enabled": False})
    on = get_settings().model_copy(update={"rag_field_extraction_enabled": True})

    cs = compute_chunk_set_id(SRC, off)
    assert cs == compute_chunk_set_id(SRC, on)
    assert compute_metadata_layer_id(cs, off) != compute_metadata_layer_id(cs, on)


def test_nav_axis_shares_chunk_set_but_splits_nav_layer() -> None:
    """navigation_summary だけ違うとき chunk_set は共有、nav 層だけ別になる。"""
    off = get_settings().model_copy(update={"rag_navigation_summary_enabled": False})
    on = get_settings().model_copy(update={"rag_navigation_summary_enabled": True})

    cs = compute_chunk_set_id(SRC, off)
    assert cs == compute_chunk_set_id(SRC, on)
    assert compute_nav_layer_id(cs, off) != compute_nav_layer_id(cs, on)


def test_layer_ids_are_consistent_bundle() -> None:
    """compute_layer_ids は個別計算と一致し、5 層キーを返す。"""
    settings = get_settings()
    bundle = compute_layer_ids(SRC, settings)
    er = compute_extraction_recipe_id(SRC, settings)
    cs = compute_chunk_set_id(SRC, settings)

    assert bundle["extraction_recipe_id"] == er
    assert bundle["chunk_set_id"] == cs
    assert bundle["metadata_layer_id"] == compute_metadata_layer_id(cs, settings)
    assert bundle["graph_layer_id"] == compute_graph_layer_id(cs, settings)
    assert bundle["nav_layer_id"] == compute_nav_layer_id(cs, settings)
    assert set(bundle) == {
        "extraction_recipe_id",
        "chunk_set_id",
        "metadata_layer_id",
        "graph_layer_id",
        "nav_layer_id",
    }


def test_ids_carry_layer_prefixes() -> None:
    """各層 ID は層を識別する prefix を持つ。"""
    bundle = compute_layer_ids(SRC, get_settings())
    assert bundle["extraction_recipe_id"].startswith("er_")
    assert bundle["chunk_set_id"].startswith("cs_")
    assert bundle["metadata_layer_id"].startswith("md_")
    assert bundle["graph_layer_id"].startswith("gr_")
    assert bundle["nav_layer_id"].startswith("nv_")
