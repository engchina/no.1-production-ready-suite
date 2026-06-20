"""Chunking アダプター(分割戦略)のテスト。"""

from app.config import Settings
from app.rag.chunking import CHUNKING_STRATEGIES, chunk_extraction_with_strategy
from app.rag.chunking_strategy import (
    CHUNKING_STRATEGY_ORDER,
    chunking_runtime_settings,
    normalize_chunking_strategy,
    resolve_chunking_params,
)
from app.schemas.extraction import DocumentElement, StructuredExtraction


def _sample_extraction() -> StructuredExtraction:
    return StructuredExtraction(
        raw_text=(
            "# 概要\n本文の一文目です。本文の二文目です。本文の三文目です。\n"
            "## 詳細\n詳細の一文目です。詳細の二文目です。詳細の三文目です。"
        ),
        elements=[
            DocumentElement(kind="title", text="第1章 概要", page_number=1),
            DocumentElement(
                kind="text",
                text="本文の一文目です。本文の二文目です。本文の三文目です。",
                page_number=1,
            ),
            DocumentElement(kind="title", text="第2章 詳細", page_number=2),
            DocumentElement(
                kind="text",
                text="詳細の一文目です。詳細の二文目です。詳細の三文目です。",
                page_number=2,
            ),
        ],
    )


def test_all_strategies_stamp_chunk_strategy_metadata() -> None:
    """全戦略が chunk_strategy metadata と text 計量を一貫して付ける。"""
    extraction = _sample_extraction()
    for strategy in CHUNKING_STRATEGIES:
        chunks = chunk_extraction_with_strategy(
            extraction,
            strategy=strategy,
            chunk_size=60,
            overlap=10,
            child_size=30,
            sentence_window_size=2,
        )
        assert chunks, strategy
        assert all(chunk.metadata["chunk_strategy"] == strategy for chunk in chunks), strategy
        assert all(chunk.metadata["text_chars"] == len(chunk.text) for chunk in chunks), strategy
        assert [chunk.index for chunk in chunks] == list(range(len(chunks))), strategy


def test_unknown_strategy_falls_back_to_structure_aware() -> None:
    """未知の戦略名は structure_aware へ安全に fallback する。"""
    extraction = _sample_extraction()
    chunks = chunk_extraction_with_strategy(extraction, strategy="does_not_exist")
    assert chunks
    assert all(chunk.metadata["chunk_strategy"] == "structure_aware" for chunk in chunks)


def test_sentence_window_records_window_size() -> None:
    """sentence_window は窓幅 metadata を付け、文単位の小 chunk を作る。"""
    extraction = _sample_extraction()
    chunks = chunk_extraction_with_strategy(
        extraction,
        strategy="sentence_window",
        chunk_size=400,
        overlap=0,
        sentence_window_size=1,
    )
    assert all(chunk.metadata.get("sentence_window_size") == 1 for chunk in chunks)
    # 文単位なので structure_aware より chunk 数が多い。
    structure = chunk_extraction_with_strategy(
        extraction,
        strategy="structure_aware",
        chunk_size=400,
        overlap=0,
    )
    assert len(chunks) > len(structure)


def test_hierarchical_parent_child_links_children_to_parent() -> None:
    """hierarchical 戦略は子 chunk を親へ連結し、子は child_size に収まる。"""
    extraction = _sample_extraction()
    chunks = chunk_extraction_with_strategy(
        extraction,
        strategy="hierarchical_parent_child",
        chunk_size=200,
        overlap=0,
        child_size=30,
    )
    assert chunks
    assert all(chunk.metadata.get("chunk_level") == "child" for chunk in chunks)
    assert all(chunk.metadata.get("chunk_group_kind") == "parent_child" for chunk in chunks)
    assert all(chunk.metadata.get("parent_chunk_id") for chunk in chunks)
    # 子 chunk は overlap を除いて child_size を大きく超えない。
    assert all(len(chunk.text) <= 30 for chunk in chunks)


def test_page_level_groups_by_page() -> None:
    """page_level はページ単位でまとめ、page_start/page_end をページごとに保つ。"""
    extraction = _sample_extraction()
    chunks = chunk_extraction_with_strategy(
        extraction,
        strategy="page_level",
        chunk_size=400,
        overlap=0,
    )
    pages = {(chunk.metadata.get("page_start"), chunk.metadata.get("page_end")) for chunk in chunks}
    assert pages == {(1, 1), (2, 2)}


def test_page_level_falls_back_without_pages() -> None:
    """ページ情報がない文書では章節単位へ fallback する。"""
    extraction = StructuredExtraction(
        raw_text="# 章\n本文の一文目です。本文の二文目です。",
    )
    chunks = chunk_extraction_with_strategy(extraction, strategy="page_level", chunk_size=400)
    assert chunks
    assert all(chunk.metadata["chunk_strategy"] == "page_level" for chunk in chunks)


def test_markdown_heading_keeps_one_chunk_per_section() -> None:
    """markdown_heading は十分大きい chunk_size で章節ごとに 1 chunk にまとめる。"""
    extraction = _sample_extraction()
    chunks = chunk_extraction_with_strategy(
        extraction,
        strategy="markdown_heading",
        chunk_size=2000,
        overlap=0,
    )
    sections = {chunk.metadata.get("section_title") for chunk in chunks}
    assert "概要" in sections
    assert "詳細" in sections
    assert len(chunks) == 2


def test_min_chars_absorbs_small_chunks() -> None:
    """min_chars 未満の微小 chunk は同一 group 内の隣接 chunk へ吸収する。"""
    extraction = _sample_extraction()
    without_absorb = chunk_extraction_with_strategy(
        extraction,
        strategy="sentence_window",
        chunk_size=400,
        overlap=0,
        sentence_window_size=1,
        min_chars=0,
    )
    with_absorb = chunk_extraction_with_strategy(
        extraction,
        strategy="sentence_window",
        chunk_size=400,
        overlap=0,
        sentence_window_size=1,
        min_chars=200,
    )
    assert len(with_absorb) < len(without_absorb)
    assert all(chunk.metadata["text_chars"] == len(chunk.text) for chunk in with_absorb)


def test_resolve_chunking_params_reads_settings() -> None:
    """Settings から chunking パラメータを解決する。"""
    settings = Settings(
        rag_chunking_strategy="recursive_character",
        rag_chunk_size=1000,
        rag_chunk_overlap=80,
        rag_chunk_child_size=250,
        rag_chunk_sentence_window_size=2,
        rag_chunk_min_chars=30,
    )
    params = resolve_chunking_params(settings)
    assert params.strategy == "recursive_character"
    assert params.chunk_size == 1000
    assert params.overlap == 80
    assert params.child_size == 250
    assert params.sentence_window_size == 2
    assert params.min_chars == 30


def test_chunking_runtime_settings_orders_and_marks_selected() -> None:
    """runtime snapshot は戦略を既定順で並べ、選択戦略を 1 件だけ marked にする。"""
    settings = Settings(rag_chunking_strategy="page_level")
    runtime = chunking_runtime_settings(settings)
    assert tuple(status.name for status in runtime.strategies) == CHUNKING_STRATEGY_ORDER
    selected = [status.name for status in runtime.strategies if status.selected]
    assert selected == ["page_level"]


def test_normalize_chunking_strategy_defaults_to_structure_aware() -> None:
    assert normalize_chunking_strategy("nope") == "structure_aware"
    assert normalize_chunking_strategy("page_level") == "page_level"
