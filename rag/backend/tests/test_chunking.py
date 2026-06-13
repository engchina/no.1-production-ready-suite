"""チャンク分割のテスト。"""

import pytest

from app.rag.chunking import chunk_text


def test_chunk_text_respects_overlap_and_offsets() -> None:
    text = "これは一つ目の文です。これは二つ目の文です。これは三つ目の文です。"
    chunks = chunk_text(text, chunk_size=18, overlap=4)
    assert len(chunks) >= 2
    assert chunks[0].index == 0
    assert chunks[1].text.startswith(chunks[0].text[-4:])


def test_chunk_text_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("テスト", chunk_size=10, overlap=10)
