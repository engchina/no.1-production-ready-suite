"""元の段落と文の終端だけを使い、生成用の原文を欠けなく分ける。"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Paragraph:
    start: int
    end: int
    sentence_ends: tuple[int, ...] = ()
    source: str = ""


class SentenceTooLarge(ValueError):
    def __init__(self, paragraph: Paragraph, start: int, end: int) -> None:
        self.source, self.start, self.end = paragraph.source, start, end
        super().__init__(f"完全な文が入力予算を超えています ({self.source}, {start}..{end})")


def pack(
    text: str, paragraphs: Sequence[Paragraph], fits: Callable[[int, int], bool]
) -> list[tuple[int, int]]:
    """fits は指示や schema も含む、その範囲を送るときの完全な要求で判定する。"""
    chunks = []
    start = end = 0
    for p in paragraphs:
        if p.start != end or not end < p.end <= len(text):
            raise ValueError("段落の範囲に空白、重複、または範囲外の位置があります。")
        if (
            not all(p.start < x < p.end for x in p.sentence_ends)
            or tuple(sorted(set(p.sentence_ends))) != p.sentence_ends
        ):
            raise ValueError("文の終端が段落の範囲または原文順と一致しません。")
        if fits(start, p.end):
            end = p.end
            continue
        if start < end:
            chunks.append((start, end))
        start = end
        if fits(start, p.end):
            end = p.end
            continue
        for boundary in (*p.sentence_ends, p.end):
            if not fits(start, boundary):
                if end > start:
                    chunks.append((start, end))
                    start = end
                if not fits(start, boundary):
                    raise SentenceTooLarge(p, start, boundary)
            end = boundary
    if end != len(text):
        raise ValueError("原文の末尾まで段落が特定されていません。")
    if start < end:
        chunks.append((start, end))
    return chunks
