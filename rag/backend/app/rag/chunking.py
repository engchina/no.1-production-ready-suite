"""チャンク分割。日本語テキストを考慮した分割を行う。"""

import re
from dataclasses import dataclass

SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?])\s*")


@dataclass
class Chunk:
    """分割後のチャンク。"""

    text: str
    index: int
    start_offset: int
    end_offset: int


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    """テキストを重複付きで分割する。

    日本語帳票では OCR 結果が改行・句点・全角記号を含むため、まず文境界を尊重し、
    長すぎる文だけを文字数で分割する。トークン化ライブラリに依存しないため CI でも安定する。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size は 1 以上である必要があります。")
    if overlap < 0:
        raise ValueError("overlap は 0 以上である必要があります。")
    if overlap >= chunk_size:
        raise ValueError("overlap は chunk_size より小さい必要があります。")

    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    sentences = _split_sentences(normalized)
    chunks: list[Chunk] = []
    cursor = 0
    buffer = ""
    buffer_start = 0

    for sentence in sentences:
        if not buffer:
            buffer_start = cursor
        projected = f"{buffer} {sentence}".strip()
        if len(projected) <= chunk_size:
            buffer = projected
            cursor += len(sentence) + 1
            continue

        if buffer:
            chunks.append(
                Chunk(
                    text=buffer,
                    index=len(chunks),
                    start_offset=buffer_start,
                    end_offset=buffer_start + len(buffer),
                )
            )
        for part in _split_long_sentence(sentence, chunk_size, overlap):
            chunks.append(
                Chunk(
                    text=part,
                    index=len(chunks),
                    start_offset=cursor,
                    end_offset=cursor + len(part),
                )
            )
            cursor += max(1, len(part) - overlap)
        buffer = ""

    if buffer:
        chunks.append(
            Chunk(
                text=buffer,
                index=len(chunks),
                start_offset=buffer_start,
                end_offset=buffer_start + len(buffer),
            )
        )

    if overlap == 0 or len(chunks) <= 1:
        return chunks
    return _apply_overlap(chunks, overlap)


def _split_sentences(text: str) -> list[str]:
    """句点・疑問符・感嘆符を優先して文に分ける。"""
    parts = [part.strip() for part in SENTENCE_BOUNDARY.split(text)]
    return [part for part in parts if part]


def _split_long_sentence(sentence: str, chunk_size: int, overlap: int) -> list[str]:
    """文単位で収まらない場合だけ文字数で分割する。"""
    parts: list[str] = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(sentence):
        parts.append(sentence[start : start + chunk_size])
        start += step
    return parts


def _apply_overlap(chunks: list[Chunk], overlap: int) -> list[Chunk]:
    """隣接チャンクの前方に前チャンク末尾を重ねる。"""
    overlapped: list[Chunk] = []
    previous_tail = ""
    for chunk in chunks:
        text = f"{previous_tail} {chunk.text}".strip() if previous_tail else chunk.text
        overlapped.append(
            Chunk(
                text=text,
                index=chunk.index,
                start_offset=max(0, chunk.start_offset - len(previous_tail)),
                end_offset=chunk.end_offset,
            )
        )
        previous_tail = chunk.text[-overlap:]
    return overlapped
