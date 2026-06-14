"""チャンク分割。日本語テキストと章節構造を考慮した分割を行う。"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from app.schemas.extraction import DocumentElement, StructuredExtraction

SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?])\s*")
MARKDOWN_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+)$")
NUMBERED_HEADING = re.compile(
    r"^(?P<prefix>(?:\d+(?:\.\d+)*|第[一二三四五六七八九十百千\d]+[章節部]|"
    r"[（(]?\d+[）)]))[\s:：.．、-]+(?P<title>.+)$"
)
BULLET_LINE = re.compile(r"^\s*(?:[-*・]|\d+[.)）]|[（(]\d+[）)])\s+")
TABLE_LINE = re.compile(r"^\s*\|.+\|\s*$")

type ChunkMetadata = dict[str, str | int | float | bool | None]


@dataclass
class Chunk:
    """分割後のチャンク。"""

    text: str
    index: int
    start_offset: int
    end_offset: int
    metadata: ChunkMetadata = field(default_factory=dict)


@dataclass(frozen=True)
class _SectionSegment:
    """章節単位の入力断片。"""

    text: str
    start_offset: int
    level: int
    title: str | None
    path: tuple[str, ...]


@dataclass(frozen=True)
class _ElementSpan:
    """構造化抽出 element を chunking しやすい形にしたもの。"""

    text: str
    start_offset: int
    end_offset: int
    kind: str
    content_kind: str
    page_number: int | None
    section_path: tuple[str, ...]
    section_level: int
    section_title: str | None
    element_id: str


STRUCTURE_CHUNK_PROFILE = "structure_v1"
TEXT_CHUNK_PROFILE = "text_v1"
NON_INDEXED_ELEMENT_KINDS = {"header", "footer"}
FIGURE_ELEMENT_KINDS = {"figure", "figure_caption"}


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    """テキストを重複付きで分割する。

    OCR / Markdown / Office 由来の文書では改行・句点・見出し・表を含むため、まず章節境界を
    推定し、その中で文境界を尊重する。長すぎる文だけを文字数で分割する。トークン化
    ライブラリに依存しないため CI でも安定する。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size は 1 以上である必要があります。")
    if overlap < 0:
        raise ValueError("overlap は 0 以上である必要があります。")
    if overlap >= chunk_size:
        raise ValueError("overlap は chunk_size より小さい必要があります。")

    source = text.replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return []

    chunks: list[Chunk] = []
    for segment in _section_segments(source):
        normalized = re.sub(r"\s+", " ", segment.text).strip()
        if not normalized:
            continue
        metadata = _segment_metadata(segment)
        segment_chunks = _chunk_normalized_text(
            normalized,
            chunk_size=chunk_size,
            overlap=overlap,
            base_offset=segment.start_offset,
            metadata=metadata,
            start_index=len(chunks),
        )
        chunks.extend(
            _with_chunk_group_metadata(
                segment_chunks,
                group_kind="section",
                group_text=normalized,
            )
        )

    if overlap == 0 or len(chunks) <= 1:
        return [_with_chunk_metadata(chunk) for chunk in chunks]
    return [_with_chunk_metadata(chunk) for chunk in _apply_overlap(chunks, overlap)]


def chunk_extraction(
    extraction: StructuredExtraction,
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[Chunk]:
    """構造化抽出 element を優先して分割する。

    Docling / Marker / Unstructured / RAGFlow 系の「要素単位を壊さずに RAG index 化する」
    方針を、外部 parser 依存なしで本プロジェクトの `StructuredExtraction` に再マップする。
    """
    _validate_chunk_settings(chunk_size, overlap)
    spans = [
        span
        for span in _element_spans(extraction.elements)
        if span.kind not in NON_INDEXED_ELEMENT_KINDS
    ]
    if not spans:
        return chunk_text(extraction.raw_text, chunk_size=chunk_size, overlap=overlap)

    chunks: list[Chunk] = []
    buffer: list[_ElementSpan] = []

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        chunks.extend(
            _chunk_span_group(
                buffer,
                chunk_size=chunk_size,
                overlap=overlap,
                start_index=len(chunks),
            )
        )
        buffer = []

    for span in spans:
        if span.content_kind == "table":
            flush_buffer()
            chunks.extend(
                _chunk_table_span(span, chunk_size=chunk_size, start_index=len(chunks))
            )
            continue
        if not buffer:
            buffer = [span]
            continue
        if _can_merge_spans(buffer, span, chunk_size):
            buffer.append(span)
            continue
        flush_buffer()
        buffer = [span]

    flush_buffer()
    return [_with_chunk_metadata(chunk) for chunk in chunks]


def _validate_chunk_settings(chunk_size: int, overlap: int) -> None:
    """chunk size / overlap の共通検証。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size は 1 以上である必要があります。")
    if overlap < 0:
        raise ValueError("overlap は 0 以上である必要があります。")
    if overlap >= chunk_size:
        raise ValueError("overlap は chunk_size より小さい必要があります。")


def _chunk_normalized_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    base_offset: int,
    metadata: ChunkMetadata,
    start_index: int,
) -> list[Chunk]:
    """正規化済みテキストを文境界優先で chunk 化する。"""
    sentences = _split_sentences(text)
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
                    index=start_index + len(chunks),
                    start_offset=base_offset + buffer_start,
                    end_offset=base_offset + buffer_start + len(buffer),
                    metadata=dict(metadata),
                )
            )
        for part in _split_long_sentence(sentence, chunk_size, overlap):
            chunks.append(
                Chunk(
                    text=part,
                    index=start_index + len(chunks),
                    start_offset=base_offset + cursor,
                    end_offset=base_offset + cursor + len(part),
                    metadata=dict(metadata),
                )
            )
            cursor += max(1, len(part) - overlap)
        buffer = ""

    if buffer:
        chunks.append(
            Chunk(
                text=buffer,
                index=start_index + len(chunks),
                start_offset=base_offset + buffer_start,
                end_offset=base_offset + buffer_start + len(buffer),
                metadata=dict(metadata),
            )
        )

    return chunks


def _element_spans(elements: list[DocumentElement]) -> list[_ElementSpan]:
    """DocumentElement を offset / section metadata 付き span に変換する。"""
    spans: list[_ElementSpan] = []
    cursor = 0
    for element in elements:
        text = element.text.strip()
        if not text:
            continue
        start_offset = _metadata_int(element.metadata.get("raw_start"), cursor)
        end_offset = _metadata_int(
            element.metadata.get("raw_end"),
            start_offset + len(text),
        )
        if end_offset < start_offset:
            end_offset = start_offset + len(text)
        cursor = max(cursor + len(text) + 1, end_offset + 1)
        section_path = tuple(element.section_path)
        section_level = _metadata_int(
            element.metadata.get("section_level"),
            len(section_path) if section_path else 0,
        )
        spans.append(
            _ElementSpan(
                text=text,
                start_offset=start_offset,
                end_offset=end_offset,
                kind=element.kind,
                content_kind=_element_content_kind(element),
                page_number=element.page_number,
                section_path=section_path,
                section_level=section_level,
                section_title=section_path[-1] if section_path else None,
                element_id=_element_id(element),
            )
        )
    return spans


def _element_content_kind(element: DocumentElement) -> str:
    """検索 metadata に保存する低 cardinality の content kind。"""
    if element.kind == "table":
        return "table"
    if element.kind in FIGURE_ELEMENT_KINDS:
        return "figure"
    if element.kind == "list" or _content_kind(element.text) == "list":
        return "list"
    return "text"


def _element_id(element: DocumentElement) -> str:
    """metadata 用の安定した element id を作る。"""
    for key in ("element_id", "id"):
        value = element.metadata.get(key)
        if isinstance(value, str | int):
            cleaned = str(value).strip()
            if cleaned:
                return cleaned[:80]
    return f"el-{element.order:04d}"


def _metadata_int(value: object, default: int) -> int:
    """metadata の整数値を安全に読む。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return default


def _can_merge_spans(group: list[_ElementSpan], span: _ElementSpan, chunk_size: int) -> bool:
    """同じ章節・同じ content kind の要素だけを chunk 内で結合する。"""
    first = group[0]
    if first.section_path != span.section_path:
        return False
    if first.content_kind != span.content_kind:
        return False
    return len(_join_span_text([*group, span])) <= chunk_size


def _chunk_span_group(
    group: list[_ElementSpan],
    *,
    chunk_size: int,
    overlap: int,
    start_index: int,
) -> list[Chunk]:
    """非 table element group を chunk 化する。"""
    text = _join_span_text(group)
    metadata = _span_group_metadata(group)
    start_offset = min(span.start_offset for span in group)
    end_offset = max(span.end_offset for span in group)
    if len(text) <= chunk_size:
        return _with_chunk_group_metadata(
            [
                Chunk(
                    text=text,
                    index=start_index,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    metadata=metadata,
                )
            ],
            group_kind="element_group",
            group_text=text,
        )
    if metadata["content_kind"] == "list":
        return _with_chunk_group_metadata(
            _chunks_from_parts(
                _split_lines_by_size(text, chunk_size),
                start_index=start_index,
                start_offset=start_offset,
                metadata=metadata,
            ),
            group_kind="element_group",
            group_text=text,
        )
    normalized = re.sub(r"\s+", " ", text).strip()
    return _with_chunk_group_metadata(
        _chunk_normalized_text(
            normalized,
            chunk_size=chunk_size,
            overlap=overlap,
            base_offset=start_offset,
            metadata=metadata,
            start_index=start_index,
        ),
        group_kind="element_group",
        group_text=normalized,
    )


def _chunk_table_span(
    span: _ElementSpan,
    *,
    chunk_size: int,
    start_index: int,
) -> list[Chunk]:
    """表は他要素と結合せず、必要な場合だけ行単位で分割する。"""
    metadata = _span_group_metadata([span])
    if len(span.text) <= chunk_size:
        return _with_chunk_group_metadata(
            [
                Chunk(
                    text=span.text,
                    index=start_index,
                    start_offset=span.start_offset,
                    end_offset=span.end_offset,
                    metadata=metadata,
                )
            ],
            group_kind="table",
            group_text=span.text,
        )
    return _with_chunk_group_metadata(
        _chunks_from_parts(
            _split_lines_by_size(span.text, chunk_size),
            start_index=start_index,
            start_offset=span.start_offset,
            metadata=metadata,
        ),
        group_kind="table",
        group_text=span.text,
    )


def _chunks_from_parts(
    parts: list[str],
    *,
    start_index: int,
    start_offset: int,
    metadata: ChunkMetadata,
) -> list[Chunk]:
    """行ベース分割結果を Chunk にする。"""
    chunks: list[Chunk] = []
    cursor = start_offset
    for part in parts:
        if not part.strip():
            continue
        chunks.append(
            Chunk(
                text=part,
                index=start_index + len(chunks),
                start_offset=cursor,
                end_offset=cursor + len(part),
                metadata=dict(metadata),
            )
        )
        cursor += len(part) + 1
    return chunks


def _with_chunk_group_metadata(
    chunks: list[Chunk],
    *,
    group_kind: str,
    group_text: str,
) -> list[Chunk]:
    """同一親要素/章節から分割された chunk に lineage metadata を付ける。"""
    if not chunks:
        return []
    group_id = _chunk_group_id(chunks, group_kind=group_kind, group_text=group_text)
    part_count = len(chunks)
    grouped: list[Chunk] = []
    for part_index, chunk in enumerate(chunks, start=1):
        grouped.append(
            Chunk(
                text=chunk.text,
                index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                metadata={
                    **chunk.metadata,
                    "chunk_group_id": group_id,
                    "chunk_group_kind": group_kind,
                    "chunk_part_index": part_index,
                    "chunk_part_count": part_count,
                },
            )
        )
    return grouped


def _chunk_group_id(
    chunks: list[Chunk],
    *,
    group_kind: str,
    group_text: str,
) -> str:
    """document 内で安定しやすい親 chunk group id を作る。"""
    first = chunks[0]
    last = chunks[-1]
    payload = {
        "group_kind": group_kind,
        "start_offset": first.start_offset,
        "end_offset": last.end_offset,
        "metadata": {
            key: value
            for key, value in sorted(first.metadata.items())
            if key not in {"text_sha256", "text_chars"}
        },
        "text_sha256": hashlib.sha256(group_text.encode("utf-8")).hexdigest(),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _split_lines_by_size(text: str, chunk_size: int) -> list[str]:
    """表・リストを行境界優先で分割する。"""
    parts: list[str] = []
    current: list[str] = []
    for line in [line for line in text.splitlines() if line.strip()]:
        if len(line) > chunk_size:
            if current:
                parts.append("\n".join(current))
                current = []
            parts.extend(_split_long_sentence(line, chunk_size, overlap=0))
            continue
        projected = "\n".join([*current, line]) if current else line
        if len(projected) <= chunk_size:
            current.append(line)
        else:
            parts.append("\n".join(current))
            current = [line]
    if current:
        parts.append("\n".join(current))
    return parts


def _join_span_text(group: list[_ElementSpan]) -> str:
    """複数 element のテキストを chunk text として結合する。"""
    return "\n".join(span.text for span in group if span.text.strip()).strip()


def _span_group_metadata(group: list[_ElementSpan]) -> ChunkMetadata:
    """構造化 element group から chunk metadata を作る。"""
    first = group[0]
    pages = sorted({span.page_number for span in group if span.page_number is not None})
    metadata: ChunkMetadata = {
        "chunk_profile": STRUCTURE_CHUNK_PROFILE,
        "content_kind": first.content_kind,
        "section_level": first.section_level,
        "element_kinds": ",".join(sorted({span.kind for span in group})),
        "element_ids": ",".join(span.element_id for span in group),
    }
    if first.section_title:
        metadata["section_title"] = first.section_title
    if first.section_path:
        metadata["section_path"] = " > ".join(first.section_path)
    if pages:
        metadata["page_start"] = pages[0]
        metadata["page_end"] = pages[-1]
    return metadata


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
                metadata=dict(chunk.metadata),
            )
        )
        previous_tail = chunk.text[-overlap:]
    return overlapped


def _section_segments(text: str) -> list[_SectionSegment]:
    """見出しらしい行で入力を章節単位に分ける。"""
    segments: list[_SectionSegment] = []
    path_by_level: dict[int, str] = {}
    current_lines: list[str] = []
    current_start = 0
    current_level = 0
    current_title: str | None = None
    current_path: tuple[str, ...] = ()
    offset = 0

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        heading = _parse_heading(stripped)
        if heading is not None:
            if current_lines:
                segments.append(
                    _SectionSegment(
                        text="".join(current_lines),
                        start_offset=current_start,
                        level=current_level,
                        title=current_title,
                        path=current_path,
                    )
                )
            level, title = heading
            path_by_level = {
                existing_level: existing_title
                for existing_level, existing_title in path_by_level.items()
                if existing_level < level
            }
            path_by_level[level] = title
            current_lines = [line]
            current_start = offset
            current_level = level
            current_title = title
            current_path = tuple(
                path_by_level[level_key] for level_key in sorted(path_by_level)
            )
        else:
            if not current_lines:
                current_start = offset
            current_lines.append(line)
        offset += len(line)

    if current_lines:
        segments.append(
            _SectionSegment(
                text="".join(current_lines),
                start_offset=current_start,
                level=current_level,
                title=current_title,
                path=current_path,
            )
        )
    return segments


def _parse_heading(line: str) -> tuple[int, str] | None:
    """Markdown / 番号付き / 日本語章節見出しを推定する。"""
    if not line or len(line) > 120:
        return None
    if match := MARKDOWN_HEADING.match(line):
        return len(match.group("marks")), _clean_heading_title(match.group("title"))
    if match := NUMBERED_HEADING.match(line):
        prefix = match.group("prefix")
        title = _clean_heading_title(match.group("title"))
        return _heading_level_from_prefix(prefix), title
    return None


def _heading_level_from_prefix(prefix: str) -> int:
    """番号形式から章節 level を推定する。"""
    if prefix.startswith("第") and prefix.endswith(("章", "部")):
        return 1
    if prefix.startswith("第") and prefix.endswith("節"):
        return 2
    dotted_depth = prefix.count(".") + 1
    return min(6, max(1, dotted_depth))


def _clean_heading_title(title: str) -> str:
    """metadata に入れる見出し文字列を短く正規化する。"""
    return re.sub(r"\s+", " ", title).strip()[:80]


def _segment_metadata(segment: _SectionSegment) -> ChunkMetadata:
    """章節断片から chunk 共通 metadata を作る。"""
    metadata: ChunkMetadata = {
        "content_kind": _content_kind(segment.text),
        "section_level": segment.level,
    }
    if segment.title:
        metadata["section_title"] = segment.title
    if segment.path:
        metadata["section_path"] = " > ".join(segment.path)
    return metadata


def _content_kind(text: str) -> str:
    """表・箇条書き・本文の簡易種別。"""
    lines = [line for line in text.splitlines() if line.strip()]
    if any(TABLE_LINE.match(line) for line in lines):
        return "table"
    if sum(1 for line in lines if BULLET_LINE.match(line)) >= 1:
        return "list"
    return "text"


def _with_chunk_metadata(chunk: Chunk) -> Chunk:
    """chunk 固有 metadata を付与する。"""
    metadata = {
        "chunk_profile": TEXT_CHUNK_PROFILE,
        **chunk.metadata,
        "text_sha256": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
        "text_chars": len(chunk.text),
    }
    return Chunk(
        text=chunk.text,
        index=chunk.index,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        metadata=metadata,
    )
