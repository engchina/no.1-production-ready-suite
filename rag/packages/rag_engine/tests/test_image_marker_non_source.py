"""本文の無い図の chunk の画像の標識は原文ではない（#5793）。

本文の無い図（Vision の失敗など）の chunk は、画像の ID・ファイル・ページ・bbox の標識を本文にする。標識は分割の
段で原文に無い範囲として宣言し、原文の抜粋（生成が時間切れ・契約違反のときの fallback）・引用に出さない。
文書・図は合成。モデルは使わない。
"""

from __future__ import annotations

from dataclasses import replace

from rag_parser_core.non_source_text import NON_SOURCE_RANGES_KEY, load_ranges
from test_chunking import _record, _viewer_payload
from test_rag_evidence_recovery import rec

from rag_engine.chunking import CHILD_CHUNK_LEVEL, ChunkingConfig, build_small_to_big_chunks
from rag_engine.generation import grounded
from rag_engine.retrieval.evidence_selection import evidence_spans

BODY = "合成の手順の本文です。設定画面で値を入力します。"
QUESTION = "設定画面で値を入力する手順は？"


def _picture(seq_no: int):
    return _record(
        1,
        seq_no,
        "Picture",
        "",
        raw_type="picture",
        bbox=[0, 60, 100, 100],
        raw={"vision_crop": f"docling/vision/docling-p1-{seq_no}.png", "vision_error": "timeout"},
    )


def _chunks(records):
    return build_small_to_big_chunks(
        _viewer_payload(records), source_run_id="abcdef", selected_engine_ids=["docling"], config=ChunkingConfig()
    )


def _answer_record(chunk, key: str):
    record = rec(key, chunk.chunk_level, chunk.chunk_seq, chunk.text, page=chunk.page_start)
    return replace(record, metadata={**record.metadata, **chunk.metadata})


def test_image_marker_chunk_declares_the_whole_text_as_non_source():
    children = [chunk for chunk in _chunks([_picture(1)]) if chunk.chunk_level == CHILD_CHUNK_LEVEL]

    assert len(children) == 1
    marker = children[0]
    assert "Image evidence" in marker.text
    assert load_ranges(marker.metadata[NON_SOURCE_RANGES_KEY], len(marker.text)) == ((0, len(marker.text)),)


def test_parent_declares_only_the_marker_part():
    chunks = _chunks([_record(1, 1, "Text", BODY), _picture(2)])
    parents = [chunk for chunk in chunks if chunk.chunk_level != CHILD_CHUNK_LEVEL]

    assert len(parents) == 1
    parent = parents[0]
    ranges = load_ranges(parent.metadata.get(NON_SOURCE_RANGES_KEY), len(parent.text))
    assert ranges
    assert all("Image evidence" in parent.text[start:end] for start, end in ranges)
    assert BODY in grounded.source_text({"text": parent.text, NON_SOURCE_RANGES_KEY: [list(r) for r in ranges]}, 0, len(parent.text))


def test_fallback_quotes_do_not_show_the_image_marker():
    """生成の草稿が無いときの原文の抜粋（``deadline_document_quotes``）に、画像の標識を出さない。"""
    children = [chunk for chunk in _chunks([_picture(1)]) if chunk.chunk_level == CHILD_CHUNK_LEVEL]
    spans = evidence_spans(QUESTION, [_answer_record(children[0], "image-child")])
    assert spans

    quotes = grounded.deadline_document_quotes(QUESTION, spans)

    assert all("Image evidence" not in entry.item.text for entry in quotes)
    assert quotes == []
