"""チャンク分割。日本語テキストと章節構造を考慮した分割を行う。"""

import hashlib
import json
import re
from dataclasses import dataclass, field

from rag_parser_core.extraction import DocumentElement, StructuredExtraction

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
    parent_id: str | None
    source_parser: str | None
    link_urls: tuple[str, ...]
    link_texts: tuple[str, ...]
    page_width: float | None
    page_height: float | None
    page_rotation: int | None
    bbox_json: str | None
    bbox_coordinate_mode: str | None
    bbox_unit: str | None
    chunk_template: str | None
    code_language: str | None
    equation_delimiter: str | None
    equation_format: str | None
    formula_count: int | None
    formula_cell_refs: tuple[str, ...]
    formula_cells: tuple[str, ...]
    formula_cell_row: int | None
    formula_cell_col: int | None
    formula_value: str | None
    table_id: str | None
    table_caption: str | None
    table_row_count: int | None
    table_column_count: int | None
    asset_id: str | None
    asset_kind: str | None


@dataclass(frozen=True)
class _TablePart:
    """長い表を行グループ単位にした chunk 入力。"""

    text: str
    row_start: int | None
    row_end: int | None
    header_repeated: bool


STRUCTURE_CHUNK_PROFILE = "structure_v1"
TEXT_CHUNK_PROFILE = "text_v1"
TABLE_PRESERVE_ROWS_TEMPLATE = "table_preserve_rows"
NON_INDEXED_ELEMENT_KINDS = {"header", "footer"}
FIGURE_ELEMENT_KINDS = {"figure", "figure_caption"}
BBOX_COORDINATE_MODE_KEYS = (
    "bbox_coordinate_mode",
    "bbox_mode",
    "bbox_format",
    "coordinate_mode",
)
BBOX_UNIT_KEYS = ("bbox_unit", "coordinate_unit")


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
        final_chunks = [_with_chunk_metadata(chunk) for chunk in chunks]
    else:
        final_chunks = [_with_chunk_metadata(chunk) for chunk in _apply_overlap(chunks, overlap)]
    return _with_chunk_size_compliance_metadata(
        final_chunks,
        chunk_size=chunk_size,
        overlap=overlap,
    )


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
            chunks.extend(_chunk_table_span(span, chunk_size=chunk_size, start_index=len(chunks)))
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
    return _with_chunk_size_compliance_metadata(
        _with_table_row_tree_metadata(
            _with_table_continuity_metadata([_with_chunk_metadata(chunk) for chunk in chunks])
        ),
        chunk_size=chunk_size,
        overlap=overlap,
    )


CHUNKING_STRATEGIES: tuple[str, ...] = (
    "structure_aware",
    "recursive_character",
    "sentence_window",
    "hierarchical_parent_child",
    "markdown_heading",
    "page_level",
    "fixed_size",
)
_DEFAULT_CHUNKING_STRATEGY = "structure_aware"


def chunk_extraction_with_strategy(
    extraction: StructuredExtraction,
    *,
    strategy: str = _DEFAULT_CHUNKING_STRATEGY,
    chunk_size: int = 800,
    overlap: int = 120,
    child_size: int = 320,
    sentence_window_size: int = 3,
    min_chars: int = 0,
) -> list[Chunk]:
    """選択された chunking 戦略(Chunking アダプター)で構造化抽出を分割する。

    業界の代表的な chunking 手法(LangChain / LlamaIndex / PageIndex 等)を外部依存なしで
    本プロジェクトの `StructuredExtraction` に再マップし、chunks 段階で手動選択できるようにする。
    未知 / 既定の戦略は structure_aware へ安全に fallback する。
    """
    _validate_chunk_settings(chunk_size, overlap)
    normalized_strategy = (
        strategy if strategy in CHUNKING_STRATEGIES else _DEFAULT_CHUNKING_STRATEGY
    )
    if normalized_strategy == "recursive_character":
        chunks = _chunk_recursive_character(extraction, chunk_size=chunk_size, overlap=overlap)
    elif normalized_strategy == "sentence_window":
        chunks = _chunk_sentence_window(
            extraction,
            chunk_size=chunk_size,
            overlap=overlap,
            window=max(1, sentence_window_size),
        )
    elif normalized_strategy == "hierarchical_parent_child":
        chunks = _chunk_hierarchical_parent_child(
            extraction,
            chunk_size=chunk_size,
            overlap=overlap,
            child_size=max(1, min(child_size, chunk_size - 1)),
        )
    elif normalized_strategy == "markdown_heading":
        chunks = _chunk_markdown_heading(extraction, chunk_size=chunk_size, overlap=overlap)
    elif normalized_strategy == "page_level":
        chunks = _chunk_page_level(extraction, chunk_size=chunk_size, overlap=overlap)
    elif normalized_strategy == "fixed_size":
        chunks = _chunk_fixed_size(extraction, chunk_size=chunk_size, overlap=overlap)
    else:
        chunks = chunk_extraction(extraction, chunk_size=chunk_size, overlap=overlap)
    absorbed = _absorb_small_chunks(chunks, min_chars=min_chars, chunk_size=chunk_size)
    return _with_chunk_strategy_metadata(absorbed, normalized_strategy)


def _chunk_recursive_character(
    extraction: StructuredExtraction,
    *,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """LangChain RecursiveCharacterTextSplitter 風に raw_text を固定長で分割する。"""
    return chunk_text(extraction.raw_text, chunk_size=chunk_size, overlap=overlap)


def _chunk_fixed_size(
    extraction: StructuredExtraction,
    *,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """RAGFlow / Dify の "General(固定長)" 風に、章節・文境界を無視して全文を固定長窓で分割する。

    `recursive_character` が章節・文境界を尊重するのに対し、本戦略は決定論的に
    `chunk_size` 文字ごと(`overlap` 文字ぶん戻して再開)へ純粋に切る。KB 単位で
    chunk_size / overlap を固定したい運用(機械的に揃った chunk 長が欲しい場合)向け。
    トークン化ライブラリに依存しないため CI でも安定する。
    """
    source = extraction.raw_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s+", " ", source).strip()
    if not normalized:
        return []
    metadata: ChunkMetadata = {
        "chunk_template": TEXT_CHUNK_PROFILE,
        "chunk_fixed_size": True,
    }
    step = max(1, chunk_size - overlap)
    chunks: list[Chunk] = []
    cursor = 0
    length = len(normalized)
    while cursor < length:
        piece = normalized[cursor : cursor + chunk_size].strip()
        if piece:
            chunks.append(
                Chunk(
                    text=piece,
                    index=len(chunks),
                    start_offset=cursor,
                    end_offset=cursor + len(piece),
                    metadata=dict(metadata),
                )
            )
        cursor += step
    final_chunks = [_with_chunk_metadata(chunk) for chunk in chunks]
    return _with_chunk_size_compliance_metadata(
        final_chunks,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _chunk_markdown_heading(
    extraction: StructuredExtraction,
    *,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """章節(見出し)単位で 1 chunk にまとめ、超過時のみ size 分割する。"""
    source = extraction.raw_text.replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return []
    chunks: list[Chunk] = []
    for segment in _section_segments(source):
        normalized = re.sub(r"\s+", " ", segment.text).strip()
        if not normalized:
            continue
        metadata = _segment_metadata(segment)
        if len(normalized) <= chunk_size:
            segment_chunks = [
                Chunk(
                    text=normalized,
                    index=len(chunks),
                    start_offset=segment.start_offset,
                    end_offset=segment.start_offset + len(normalized),
                    metadata=dict(metadata),
                )
            ]
        else:
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
    final_chunks = [_with_chunk_metadata(chunk) for chunk in chunks]
    return _with_chunk_size_compliance_metadata(
        final_chunks,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _chunk_sentence_window(
    extraction: StructuredExtraction,
    *,
    chunk_size: int,
    overlap: int,
    window: int,
) -> list[Chunk]:
    """LlamaIndex SentenceWindow 風に章節内で固定文数ごとの小 chunk を作る。"""
    source = extraction.raw_text.replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return []
    chunks: list[Chunk] = []
    for segment in _section_segments(source):
        normalized = re.sub(r"\s+", " ", segment.text).strip()
        if not normalized:
            continue
        metadata = _segment_metadata(segment)
        metadata["sentence_window_size"] = window
        sentences = _split_sentences(normalized)
        segment_chunks: list[Chunk] = []
        cursor = segment.start_offset
        for start in range(0, len(sentences), window):
            text = " ".join(sentences[start : start + window]).strip()
            if not text:
                continue
            if len(text) <= chunk_size:
                segment_chunks.append(
                    Chunk(
                        text=text,
                        index=len(chunks) + len(segment_chunks),
                        start_offset=cursor,
                        end_offset=cursor + len(text),
                        metadata=dict(metadata),
                    )
                )
                cursor += len(text) + 1
                continue
            for part in _split_long_sentence(text, chunk_size, overlap):
                segment_chunks.append(
                    Chunk(
                        text=part,
                        index=len(chunks) + len(segment_chunks),
                        start_offset=cursor,
                        end_offset=cursor + len(part),
                        metadata=dict(metadata),
                    )
                )
                cursor += max(1, len(part) - overlap)
        chunks.extend(
            _with_chunk_group_metadata(
                segment_chunks,
                group_kind="section",
                group_text=normalized,
            )
        )
    final_chunks = [_with_chunk_metadata(chunk) for chunk in chunks]
    return _with_chunk_size_compliance_metadata(
        final_chunks,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _chunk_hierarchical_parent_child(
    extraction: StructuredExtraction,
    *,
    chunk_size: int,
    overlap: int,
    child_size: int,
) -> list[Chunk]:
    """LlamaIndex AutoMerging 風に親 chunk を子 chunk へ再分割し、子を索引する。"""
    parents = chunk_extraction(extraction, chunk_size=chunk_size, overlap=overlap)
    children: list[Chunk] = []
    for parent in parents:
        parent_sha = str(parent.metadata.get("text_sha256") or "")
        parent_id = (
            str(parent.metadata.get("chunk_group_id") or "")
            or parent_sha
            or f"parent-{parent.index:04d}"
        )[:32]
        if parent.metadata.get("content_kind") == "table" or len(parent.text) <= child_size:
            parts = [parent.text]
        else:
            normalized = re.sub(r"\s+", " ", parent.text).strip()
            sub_chunks = _chunk_normalized_text(
                normalized,
                chunk_size=child_size,
                overlap=min(overlap, child_size - 1),
                base_offset=parent.start_offset,
                metadata={},
                start_index=0,
            )
            parts = [chunk.text for chunk in sub_chunks] or [parent.text]
        part_count = len(parts)
        for part_index, part in enumerate(parts, start=1):
            metadata = {
                key: value
                for key, value in parent.metadata.items()
                if key not in {"text_sha256", "text_chars"}
            }
            metadata.update(
                {
                    "chunk_level": "child",
                    "parent_chunk_id": parent_id,
                    "parent_chunk_chars": len(parent.text),
                    "chunk_group_id": parent_id,
                    "chunk_group_kind": "parent_child",
                    "chunk_part_index": part_index,
                    "chunk_part_count": part_count,
                }
            )
            children.append(
                Chunk(
                    text=part,
                    index=len(children),
                    start_offset=parent.start_offset,
                    end_offset=parent.start_offset + len(part),
                    metadata=metadata,
                )
            )
    final_chunks = [_with_chunk_metadata(chunk) for chunk in children]
    return _with_chunk_size_compliance_metadata(
        final_chunks,
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _chunk_page_level(
    extraction: StructuredExtraction,
    *,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """PageIndex 風にページ単位で 1 chunk にまとめる。無ページ文書は章節へ fallback する。"""
    spans = [
        span
        for span in _element_spans(extraction.elements)
        if span.kind not in NON_INDEXED_ELEMENT_KINDS
    ]
    if not spans:
        return _chunk_markdown_heading(extraction, chunk_size=chunk_size, overlap=overlap)
    if all(span.page_number is None for span in spans):
        return _chunk_markdown_heading(extraction, chunk_size=chunk_size, overlap=overlap)

    chunks: list[Chunk] = []
    buffer: list[_ElementSpan] = []
    buffer_page: int | None = None

    def flush_buffer() -> None:
        nonlocal buffer, buffer_page
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
        buffer_page = None

    for span in spans:
        if span.content_kind == "table":
            flush_buffer()
            chunks.extend(_chunk_table_span(span, chunk_size=chunk_size, start_index=len(chunks)))
            continue
        if buffer and span.page_number != buffer_page:
            flush_buffer()
        if not buffer:
            buffer_page = span.page_number
        buffer.append(span)
    flush_buffer()
    return _with_chunk_size_compliance_metadata(
        _with_table_row_tree_metadata(
            _with_table_continuity_metadata([_with_chunk_metadata(chunk) for chunk in chunks])
        ),
        chunk_size=chunk_size,
        overlap=overlap,
    )


def _absorb_small_chunks(
    chunks: list[Chunk],
    *,
    min_chars: int,
    chunk_size: int,
) -> list[Chunk]:
    """min_chars 未満の微小 chunk を、同一 chunk group 内の直前 chunk へ吸収する。"""
    if min_chars <= 0 or len(chunks) <= 1:
        return chunks
    merged: list[Chunk] = []
    for chunk in chunks:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and len(chunk.text) < min_chars
            and chunk.metadata.get("content_kind") != "table"
            and previous.metadata.get("content_kind") != "table"
            and previous.metadata.get("chunk_group_id") == chunk.metadata.get("chunk_group_id")
            and previous.metadata.get("chunk_group_id") is not None
        ):
            joined = f"{previous.text}\n{chunk.text}".strip()
            merged[-1] = _restamp_merged_chunk(
                Chunk(
                    text=joined,
                    index=previous.index,
                    start_offset=previous.start_offset,
                    end_offset=chunk.end_offset,
                    metadata=dict(previous.metadata),
                ),
                chunk_size=chunk_size,
            )
            continue
        merged.append(chunk)
    return [
        Chunk(
            text=chunk.text,
            index=index,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            metadata=chunk.metadata,
        )
        for index, chunk in enumerate(merged)
    ]


def _restamp_merged_chunk(chunk: Chunk, *, chunk_size: int) -> Chunk:
    """吸収で本文が変わった chunk の sha / 文字数 / size compliance を再計算する。"""
    metadata = dict(chunk.metadata)
    metadata["text_sha256"] = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
    metadata["text_chars"] = len(chunk.text)
    limit = _metadata_int(metadata.get("chunk_size_limit"), chunk_size)
    if len(chunk.text) <= limit:
        metadata["chunk_size_compliance"] = "within_limit"
        metadata.pop("chunk_size_overflow_reason", None)
    elif reason := _chunk_size_overflow_reason(chunk):
        metadata["chunk_size_compliance"] = "overflow_justified"
        metadata["chunk_size_overflow_reason"] = reason
    else:
        metadata["chunk_size_compliance"] = "overflow"
        metadata.pop("chunk_size_overflow_reason", None)
    return Chunk(
        text=chunk.text,
        index=chunk.index,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        metadata=metadata,
    )


def _with_chunk_strategy_metadata(chunks: list[Chunk], strategy: str) -> list[Chunk]:
    """全 chunk に選択された chunking 戦略名を刻む。"""
    return [
        Chunk(
            text=chunk.text,
            index=chunk.index,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            metadata={**chunk.metadata, "chunk_strategy": strategy},
        )
        for chunk in chunks
    ]


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
        content_kind = _element_content_kind(element)
        element_id = _element_id(element)
        table_id = _metadata_label(element.metadata.get("table_id"), max_length=80)
        if not table_id and content_kind == "table":
            table_id = element_id
        spans.append(
            _ElementSpan(
                text=text,
                start_offset=start_offset,
                end_offset=end_offset,
                kind=element.kind,
                content_kind=content_kind,
                page_number=element.page_number,
                section_path=section_path,
                section_level=section_level,
                section_title=section_path[-1] if section_path else None,
                element_id=element_id,
                parent_id=element.parent_id
                or _metadata_label(element.metadata.get("parent_id"), max_length=80),
                source_parser=element.source_parser
                or _metadata_label(
                    element.metadata.get("source_parser"),
                    max_length=80,
                ),
                link_urls=_metadata_text_tuple(element.metadata.get("link_urls")),
                link_texts=_metadata_text_tuple(element.metadata.get("link_texts")),
                page_width=_metadata_float(element.metadata.get("page_width")),
                page_height=_metadata_float(element.metadata.get("page_height")),
                page_rotation=_metadata_optional_int(element.metadata.get("page_rotation")),
                bbox_json=_bbox_json(element.bbox),
                bbox_coordinate_mode=_bbox_coordinate_mode(element.metadata),
                bbox_unit=_bbox_unit(element.bbox, element.metadata),
                chunk_template=_metadata_label(
                    element.metadata.get("chunk_template"),
                    max_length=80,
                ),
                code_language=_metadata_label(
                    element.metadata.get("code_language"),
                    max_length=40,
                ),
                equation_delimiter=_metadata_label(
                    element.metadata.get("equation_delimiter"),
                    max_length=40,
                ),
                equation_format=_metadata_label(
                    element.metadata.get("equation_format"),
                    max_length=40,
                ),
                formula_count=_metadata_positive_int(element.metadata.get("formula_count")),
                formula_cell_refs=_formula_cell_refs(element.metadata),
                formula_cells=_metadata_text_tuple(element.metadata.get("formula_cells")),
                formula_cell_row=_metadata_optional_int(
                    element.metadata.get("formula_cell_row")
                ),
                formula_cell_col=_metadata_optional_int(
                    element.metadata.get("formula_cell_col")
                ),
                formula_value=_metadata_label(
                    element.metadata.get("formula_value"),
                    max_length=500,
                ),
                table_id=table_id,
                table_caption=_metadata_label(
                    element.metadata.get("table_caption"),
                    max_length=500,
                ),
                table_row_count=_metadata_positive_int(element.metadata.get("row_count")),
                table_column_count=_metadata_positive_int(
                    element.metadata.get("column_count")
                ),
                asset_id=_metadata_label(element.metadata.get("asset_id"), max_length=80),
                asset_kind=_metadata_label(element.metadata.get("asset_kind"), max_length=40),
            )
        )
    return spans


def _element_content_kind(element: DocumentElement) -> str:
    """検索 metadata に保存する低 cardinality の content kind。"""
    if element.content_kind:
        return element.content_kind
    if element.kind in {"table", "table_caption"}:
        return "table"
    if element.kind in FIGURE_ELEMENT_KINDS:
        return "figure"
    if element.kind == "equation":
        return "equation"
    if element.kind == "code":
        return "code"
    if element.kind == "list" or _content_kind(element.text) == "list":
        return "list"
    return "text"


def _element_id(element: DocumentElement) -> str:
    """metadata 用の安定した element id を作る。"""
    if element.element_id:
        return element.element_id[:80]
    for key in ("element_id", "id"):
        value = element.metadata.get(key)
        if isinstance(value, str | int):
            cleaned = str(value).strip()
            if cleaned:
                return cleaned[:80]
    return f"el-{element.order:04d}"


def _metadata_label(value: object, *, max_length: int) -> str | None:
    """metadata の短いラベル値を読む。"""
    if isinstance(value, str | int):
        cleaned = str(value).strip()
        return cleaned[:max_length] if cleaned else None
    return None


def _metadata_text_tuple(value: object) -> tuple[str, ...]:
    """metadata の改行区切り文字列を短い tuple として読む。"""
    if not isinstance(value, str):
        return ()
    items: list[str] = []
    for raw in value.splitlines():
        cleaned = raw.strip()
        if cleaned:
            items.append(cleaned[:500])
    return tuple(_ordered_unique(items))


def _formula_cell_refs(metadata: ChunkMetadata) -> tuple[str, ...]:
    """table / equation metadata から Excel formula cell refs を集約する。"""
    refs = list(_metadata_text_tuple(metadata.get("formula_cell_refs")))
    single_ref = _metadata_label(metadata.get("formula_cell_ref"), max_length=80)
    if single_ref is not None:
        refs.append(single_ref)
    return tuple(_ordered_unique(refs))


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _bbox_json(value: list[float] | None) -> str | None:
    """bbox は JSON 文字列として chunk metadata に保存する。"""
    if not value:
        return None
    return json.dumps([round(float(item), 6) for item in value], separators=(",", ":"))


def _bbox_coordinate_mode(metadata: ChunkMetadata) -> str | None:
    """metadata から明示 bbox coordinate mode を低 cardinality に寄せる。"""
    for key in BBOX_COORDINATE_MODE_KEYS:
        label = _metadata_label(metadata.get(key), max_length=40)
        if not label:
            continue
        normalized = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        if normalized in {"xyxy", "x1_y1_x2_y2"}:
            return "xyxy"
        if normalized in {"xywh", "x_y_width_height", "left_top_width_height"}:
            return "xywh"
    return None


def _bbox_unit(value: list[float] | None, metadata: ChunkMetadata) -> str | None:
    """bbox 座標単位を metadata または値域から低 cardinality に寄せる。"""
    for key in BBOX_UNIT_KEYS:
        label = _metadata_label(metadata.get(key), max_length=40)
        if not label:
            continue
        normalized = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        if normalized in {"ratio", "normalized", "relative"}:
            return "ratio"
        if normalized in {"percent", "percentage"}:
            return "percent"
        if normalized in {"absolute", "pixel", "pixels", "point", "points"}:
            return "absolute"
    if not value:
        return None
    max_value = max(abs(float(item)) for item in value)
    if max_value <= 1:
        return "ratio"
    if max_value <= 100:
        return "percent"
    return "absolute"


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


def _metadata_positive_int(value: object) -> int | None:
    """metadata の正の整数値を読む。"""
    parsed = _metadata_int(value, default=0)
    return parsed if parsed > 0 else None


def _metadata_optional_int(value: object) -> int | None:
    """metadata の任意整数値を読む。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _metadata_float(value: object) -> float | None:
    """metadata の正の数値を読む。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if number > 0 else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if number > 0 else None
    return None


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
    source_template = metadata.get("chunk_template")
    if source_template and source_template != TABLE_PRESERVE_ROWS_TEMPLATE:
        metadata["source_chunk_template"] = source_template
    metadata["chunk_template"] = TABLE_PRESERVE_ROWS_TEMPLATE
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
        _chunks_from_table_parts(
            _split_table_rows_by_size(span.text, chunk_size),
            start_index=start_index,
            start_offset=span.start_offset,
            metadata=metadata,
        ),
        group_kind="table",
        group_text=span.text,
    )


def _chunks_from_table_parts(
    parts: list[_TablePart],
    *,
    start_index: int,
    start_offset: int,
    metadata: ChunkMetadata,
) -> list[Chunk]:
    """表の行グループ分割結果を Chunk にする。"""
    chunks: list[Chunk] = []
    cursor = start_offset
    for part in parts:
        if not part.text.strip():
            continue
        part_metadata = dict(metadata)
        if part.row_start is not None:
            part_metadata["table_data_row_start"] = part.row_start
        if part.row_end is not None:
            part_metadata["table_data_row_end"] = part.row_end
        part_metadata["table_header_repeated"] = part.header_repeated
        chunks.append(
            Chunk(
                text=part.text,
                index=start_index + len(chunks),
                start_offset=cursor,
                end_offset=cursor + len(part.text),
                metadata=part_metadata,
            )
        )
        cursor += len(part.text) + 1
    return chunks


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


def _with_table_continuity_metadata(chunks: list[Chunk]) -> list[Chunk]:
    """同一 table_id が複数ページにまたがる場合、table continuity metadata を付ける。"""
    table_chunks_by_id: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        if chunk.metadata.get("content_kind") != "table":
            continue
        table_id = chunk.metadata.get("table_id")
        if not isinstance(table_id, str) or not table_id.strip():
            continue
        table_chunks_by_id.setdefault(table_id, []).append(chunk)

    replacements: dict[int, Chunk] = {}
    for table_id, table_chunks in table_chunks_by_id.items():
        pages = sorted(
            {
                page
                for chunk in table_chunks
                for page in _chunk_page_range(chunk)
            }
        )
        if len(pages) <= 1:
            continue
        ordered = sorted(
            table_chunks,
            key=lambda chunk: (
                _metadata_int(chunk.metadata.get("page_start"), chunk.index),
                _metadata_int(chunk.metadata.get("table_data_row_start"), chunk.index),
                chunk.index,
            ),
        )
        group_id = _table_continuity_group_id(table_id, ordered, pages=pages)
        row_offsets = _table_row_offsets_by_page(ordered)
        part_count = len(ordered)
        for part_index, chunk in enumerate(ordered, start=1):
            metadata = dict(chunk.metadata)
            page_start = _metadata_int(metadata.get("page_start"), pages[0])
            row_offset = row_offsets.get(page_start, 0)
            row_start = _metadata_positive_int(metadata.get("table_data_row_start"))
            row_end = _metadata_positive_int(metadata.get("table_data_row_end"))
            inferred_body_rows = _table_body_row_count(chunk.text)
            effective_row_start = row_start or (1 if inferred_body_rows else None)
            effective_row_end = row_end or inferred_body_rows or effective_row_start
            if effective_row_start is not None:
                metadata["table_data_row_start"] = row_offset + effective_row_start
            if effective_row_end is not None:
                metadata["table_data_row_end"] = row_offset + effective_row_end
            metadata.update(
                {
                    "chunk_group_id": group_id,
                    "chunk_group_kind": "table_continuity",
                    "chunk_part_index": part_index,
                    "chunk_part_count": part_count,
                    "table_continuity_group_id": group_id,
                    "table_cross_page": True,
                    "table_page_start": pages[0],
                    "table_page_end": pages[-1],
                    "table_page_count": len(pages),
                    "table_continuation_index": part_index,
                    "table_continuation_count": part_count,
                    "table_header_repeated": bool(part_index > 1)
                    or metadata.get("table_header_repeated") is True,
                }
            )
            replacements[chunk.index] = Chunk(
                text=chunk.text,
                index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                metadata=metadata,
            )
    if not replacements:
        return chunks
    return [replacements.get(chunk.index, chunk) for chunk in chunks]


def _with_table_row_tree_metadata(chunks: list[Chunk]) -> list[Chunk]:
    """表 chunk に row-level key-value lineage metadata を付ける。"""
    annotated: list[Chunk] = []
    for chunk in chunks:
        if chunk.metadata.get("content_kind") != "table":
            annotated.append(chunk)
            continue
        row_tree = _table_row_tree_metadata(chunk)
        if not row_tree:
            annotated.append(chunk)
            continue
        annotated.append(
            Chunk(
                text=chunk.text,
                index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                metadata={**chunk.metadata, **row_tree},
            )
        )
    return annotated


def _table_row_tree_metadata(chunk: Chunk) -> ChunkMetadata:
    rows = _table_rows_without_separator(chunk.text)
    if len(rows) < 2:
        return {}
    column_count = max(
        _metadata_positive_int(chunk.metadata.get("table_column_count")) or 0,
        *(len(row) for row in rows),
    )
    if column_count <= 0:
        return {}
    column_keys = _table_column_keys(rows[0], column_count=column_count)
    data_rows = rows[1:]
    row_blocks = _table_key_value_rows(column_keys, data_rows)
    if not row_blocks:
        return {}
    row_start = _metadata_positive_int(chunk.metadata.get("table_data_row_start")) or 1
    row_end = _metadata_positive_int(chunk.metadata.get("table_data_row_end")) or (
        row_start + len(row_blocks) - 1
    )
    header_json = json.dumps(
        column_keys,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    row_hashes = [
        _stable_sha256({"columns": column_keys, "row": row_block}) for row_block in row_blocks
    ]
    cell_refs = _table_cell_refs_for_row_group(
        row_start=row_start,
        row_count=len(row_blocks),
        column_count=len(column_keys),
    )
    return {
        "table_row_tree_version": "row_tree_v1",
        "table_row_tree_format": "key_value_rows",
        "table_row_tree_column_count": len(column_keys),
        "table_row_tree_row_count": len(row_blocks),
        "table_row_tree_row_start": row_start,
        "table_row_tree_row_end": row_end,
        "table_row_tree_column_keys": header_json,
        "table_row_tree_header_sha256": hashlib.sha256(
            header_json.encode("utf-8")
        ).hexdigest(),
        "table_row_tree_row_hashes": json.dumps(
            row_hashes,
            separators=(",", ":"),
        ),
        "table_row_tree_kv_sha256": _stable_sha256(
            {"columns": column_keys, "rows": row_blocks}
        ),
        "table_row_tree_dense": all(len(row) == len(column_keys) for row in data_rows),
        "table_cell_ref_format": "a1",
        "table_cell_ref_count": len(cell_refs),
        "table_cell_refs": "\n".join(cell_refs)[:4000],
    }


def _table_cell_refs_for_row_group(
    *,
    row_start: int,
    row_count: int,
    column_count: int,
) -> list[str]:
    """table data row range から A1 形式 cell refs を生成する。"""
    refs: list[str] = []
    for data_row_index in range(row_start, row_start + row_count):
        sheet_row = data_row_index + 1
        for col_index in range(column_count):
            refs.append(f"{_spreadsheet_column_label(col_index)}{sheet_row}")
    return refs


def _spreadsheet_column_label(col_index: int) -> str:
    index = max(0, col_index)
    letters: list[str] = []
    while True:
        index, remainder = divmod(index, 26)
        letters.append(chr(ord("A") + remainder))
        if index == 0:
            break
        index -= 1
    return "".join(reversed(letters))


def _table_rows_without_separator(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not TABLE_LINE.match(stripped) or _is_markdown_table_separator(stripped):
            continue
        cells = _table_cells(stripped)
        if cells:
            rows.append(cells)
    return rows


def _table_cells(line: str) -> list[str]:
    body = line.strip().strip("|")
    return [
        _clean_table_cell(cell.replace("\\|", "|"))
        for cell in re.split(r"(?<!\\)\|", body)
    ]


def _clean_table_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _table_column_keys(header_cells: list[str], *, column_count: int) -> list[str]:
    raw_keys = [
        _clean_table_column_key(header_cells[index] if index < len(header_cells) else "")
        for index in range(column_count)
    ]
    keys: list[str] = []
    seen: dict[str, int] = {}
    for index, key in enumerate(raw_keys, start=1):
        base = key or f"column_{index}"
        seen[base] = seen.get(base, 0) + 1
        keys.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return keys


def _clean_table_column_key(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:80]


def _table_key_value_rows(
    column_keys: list[str],
    data_rows: list[list[str]],
) -> list[dict[str, str]]:
    row_blocks: list[dict[str, str]] = []
    for row in data_rows:
        values = [*row, *([""] * max(0, len(column_keys) - len(row)))]
        row_blocks.append(
            {
                column_key: values[index] if index < len(values) else ""
                for index, column_key in enumerate(column_keys)
            }
        )
    return row_blocks


def _stable_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _with_chunk_size_compliance_metadata(
    chunks: list[Chunk],
    *,
    chunk_size: int,
    overlap: int,
) -> list[Chunk]:
    """Adaptive chunking 評価向けに size compliance metadata を付ける。"""
    limit = chunk_size + max(0, overlap)
    annotated: list[Chunk] = []
    for chunk in chunks:
        metadata = {
            **chunk.metadata,
            "chunk_size_target": chunk_size,
            "chunk_size_limit": limit,
        }
        text_chars = len(chunk.text)
        if text_chars <= limit:
            metadata["chunk_size_compliance"] = "within_limit"
        elif reason := _chunk_size_overflow_reason(chunk):
            metadata["chunk_size_compliance"] = "overflow_justified"
            metadata["chunk_size_overflow_reason"] = reason
        else:
            metadata["chunk_size_compliance"] = "overflow"
        annotated.append(
            Chunk(
                text=chunk.text,
                index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                metadata=metadata,
            )
        )
    return annotated


def _chunk_size_overflow_reason(chunk: Chunk) -> str | None:
    """構造を壊さないために chunk_size を超えることを許容できる理由を返す。"""
    content_kind = chunk.metadata.get("content_kind")
    if content_kind in {"table", "code", "equation", "figure"}:
        return "atomic_block"
    return None


def _chunk_page_range(chunk: Chunk) -> range:
    page_start = _metadata_positive_int(chunk.metadata.get("page_start"))
    page_end = _metadata_positive_int(chunk.metadata.get("page_end")) or page_start
    if page_start is None or page_end is None or page_end < page_start:
        return range(0)
    return range(page_start, page_end + 1)


def _table_continuity_group_id(
    table_id: str,
    chunks: list[Chunk],
    *,
    pages: list[int],
) -> str:
    payload = {
        "group_kind": "table_continuity",
        "table_id": table_id,
        "pages": pages,
        "element_ids": [
            chunk.metadata.get("element_ids")
            for chunk in chunks
            if chunk.metadata.get("element_ids")
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _table_row_offsets_by_page(chunks: list[Chunk]) -> dict[int, int]:
    """各ページの table_data_row_* を table 全体の行番号へ寄せる offset を返す。"""
    row_max_by_page: dict[int, int] = {}
    for chunk in chunks:
        page_start = _metadata_positive_int(chunk.metadata.get("page_start"))
        row_end = _metadata_positive_int(chunk.metadata.get("table_data_row_end")) or (
            _table_body_row_count(chunk.text) or None
        )
        if page_start is None or row_end is None:
            continue
        row_max_by_page[page_start] = max(row_max_by_page.get(page_start, 0), row_end)
    offsets: dict[int, int] = {}
    offset = 0
    for page in sorted(row_max_by_page):
        offsets[page] = offset
        offset += row_max_by_page[page]
    return offsets


def _table_body_row_count(text: str) -> int:
    """Markdown table の body row 数を数える。"""
    lines = [line.strip() for line in text.splitlines() if TABLE_LINE.match(line.strip())]
    if not lines:
        return 0
    body_lines = lines[1:]
    if body_lines and _is_markdown_table_separator(body_lines[0]):
        body_lines = body_lines[1:]
    return len(body_lines)


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


def _split_table_rows_by_size(text: str, chunk_size: int) -> list[_TablePart]:
    """表は行を壊さず、分割後の各 chunk に表頭を繰り返す。"""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    prefix_lines: list[str] = []
    while lines and not TABLE_LINE.match(lines[0]):
        prefix_lines.append(lines.pop(0))
    if not lines and prefix_lines:
        return [
            _TablePart(text=part, row_start=None, row_end=None, header_repeated=False)
            for part in _split_lines_by_size(text, chunk_size)
        ]
    if not TABLE_LINE.match(lines[0]):
        return [
            _TablePart(text=part, row_start=None, row_end=None, header_repeated=False)
            for part in _split_lines_by_size(text, chunk_size)
        ]

    header_line_count = _table_header_line_count(lines)
    header_lines = [*prefix_lines, *lines[:header_line_count]]
    body_lines = lines[header_line_count:]
    if not body_lines:
        return [
            _TablePart(
                text="\n".join([*prefix_lines, *lines]),
                row_start=None,
                row_end=None,
                header_repeated=False,
            )
        ]

    parts: list[_TablePart] = []
    current = list(header_lines)
    current_start: int | None = None

    def flush_current() -> None:
        nonlocal current, current_start
        if current_start is None:
            return
        parts.append(
            _TablePart(
                text="\n".join(current),
                row_start=current_start,
                row_end=current_start + len(current) - len(header_lines) - 1,
                header_repeated=bool(parts),
            )
        )
        current = list(header_lines)
        current_start = None

    for row_index, line in enumerate(body_lines, start=1):
        if current_start is None:
            current_start = row_index
        projected = "\n".join([*current, line])
        if len(projected) <= chunk_size or len(current) == len(header_lines):
            current.append(line)
            continue
        flush_current()
        current_start = row_index
        current.append(line)
    flush_current()
    return parts


def _table_header_line_count(lines: list[str]) -> int:
    """Markdown 表の header + separator を検出する。"""
    if len(lines) >= 2 and _is_markdown_table_separator(lines[1]):
        return 2
    return 1


def _is_markdown_table_separator(line: str) -> bool:
    """`| --- | :---: |` のような separator 行かを判定する。"""
    if not TABLE_LINE.match(line):
        return False
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


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
    source_parsers = sorted({span.source_parser for span in group if span.source_parser})
    templates = sorted({span.chunk_template for span in group if span.chunk_template})
    link_urls = _ordered_unique([url for span in group for url in span.link_urls])
    link_texts = _ordered_unique([text for span in group for text in span.link_texts])
    bboxes = {span.bbox_json for span in group if span.bbox_json}
    bbox_modes = {span.bbox_coordinate_mode for span in group if span.bbox_coordinate_mode}
    bbox_units = {span.bbox_unit for span in group if span.bbox_unit}
    page_widths = {span.page_width for span in group if span.page_width is not None}
    page_heights = {span.page_height for span in group if span.page_height is not None}
    page_rotations = {
        span.page_rotation for span in group if span.page_rotation is not None
    }
    code_languages = {span.code_language for span in group if span.code_language}
    equation_delimiters = {
        span.equation_delimiter for span in group if span.equation_delimiter
    }
    equation_formats = {span.equation_format for span in group if span.equation_format}
    formula_counts = {
        span.formula_count for span in group if span.formula_count is not None
    }
    formula_cell_refs = _ordered_unique(
        [cell_ref for span in group for cell_ref in span.formula_cell_refs]
    )
    formula_cells = _ordered_unique([cell for span in group for cell in span.formula_cells])
    formula_cell_rows = {
        span.formula_cell_row for span in group if span.formula_cell_row is not None
    }
    formula_cell_cols = {
        span.formula_cell_col for span in group if span.formula_cell_col is not None
    }
    formula_values = _ordered_unique(
        [span.formula_value for span in group if span.formula_value]
    )
    table_ids = sorted({span.table_id for span in group if span.table_id})
    table_captions = _ordered_unique([span.table_caption for span in group if span.table_caption])
    asset_ids = sorted({span.asset_id for span in group if span.asset_id})
    asset_kinds = sorted({span.asset_kind for span in group if span.asset_kind})
    table_row_counts = {
        span.table_row_count for span in group if span.table_row_count is not None
    }
    table_column_counts = {
        span.table_column_count
        for span in group
        if span.table_column_count is not None
    }
    parent_ids = sorted({span.parent_id for span in group if span.parent_id})
    dependency_edges = _span_dependency_edges(group)
    metadata: ChunkMetadata = {
        "chunk_profile": STRUCTURE_CHUNK_PROFILE,
        "content_kind": first.content_kind,
        "section_level": first.section_level,
        "element_kinds": ",".join(sorted({span.kind for span in group})),
        "element_ids": ",".join(span.element_id for span in group),
    }
    if parent_ids:
        metadata["parent_element_ids"] = ",".join(parent_ids)
    if dependency_edges:
        metadata["dependency_edges"] = json.dumps(
            dependency_edges,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        metadata["dependency_edge_count"] = len(dependency_edges)
    if source_parsers:
        metadata["source_parser"] = source_parsers[0]
    if templates:
        metadata["chunk_template"] = templates[0]
    if link_urls:
        metadata["link_count"] = len(link_urls)
        metadata["link_urls"] = "\n".join(link_urls)[:1000]
    if link_texts:
        metadata["link_texts"] = "\n".join(link_texts)[:1000]
    if len(bboxes) == 1:
        metadata["bbox"] = next(iter(bboxes))
        if len(bbox_modes) == 1:
            metadata["bbox_coordinate_mode"] = next(iter(bbox_modes))
        if len(bbox_units) == 1:
            metadata["bbox_unit"] = next(iter(bbox_units))
        if len(page_widths) == 1:
            metadata["page_width"] = next(iter(page_widths))
        if len(page_heights) == 1:
            metadata["page_height"] = next(iter(page_heights))
        if len(page_rotations) == 1:
            metadata["page_rotation"] = next(iter(page_rotations))
    if len(code_languages) == 1:
        metadata["code_language"] = next(iter(code_languages))
    if len(equation_delimiters) == 1:
        metadata["equation_delimiter"] = next(iter(equation_delimiters))
    if len(equation_formats) == 1:
        metadata["equation_format"] = next(iter(equation_formats))
    if len(formula_counts) == 1:
        metadata["formula_count"] = next(iter(formula_counts))
    elif formula_cell_refs:
        metadata["formula_count"] = len(formula_cell_refs)
    if formula_cell_refs:
        metadata["formula_cell_count"] = len(formula_cell_refs)
        metadata["formula_cell_refs"] = "\n".join(formula_cell_refs)[:1000]
    if formula_cells:
        metadata["formula_cells"] = "\n".join(formula_cells)[:4000]
    if len(formula_cell_rows) == 1:
        metadata["formula_cell_row"] = next(iter(formula_cell_rows))
    if len(formula_cell_cols) == 1:
        metadata["formula_cell_col"] = next(iter(formula_cell_cols))
    if len(formula_values) == 1:
        metadata["formula_value"] = formula_values[0]
    if len(table_ids) == 1:
        metadata["table_id"] = table_ids[0]
    if len(table_captions) == 1:
        metadata["table_caption"] = table_captions[0]
    if len(asset_ids) == 1:
        metadata["asset_id"] = asset_ids[0]
    if len(asset_kinds) == 1:
        metadata["asset_kind"] = asset_kinds[0]
    if len(table_row_counts) == 1:
        metadata["table_row_count"] = next(iter(table_row_counts))
    if len(table_column_counts) == 1:
        metadata["table_column_count"] = next(iter(table_column_counts))
    if first.section_title:
        metadata["section_title"] = first.section_title
    if first.section_path:
        metadata["section_path"] = " > ".join(first.section_path)
    if pages:
        metadata["page_start"] = pages[0]
        metadata["page_end"] = pages[-1]
    return metadata


def _span_dependency_edges(group: list[_ElementSpan]) -> list[dict[str, str]]:
    """同一 chunk 内に閉じた parent-child dependency を citation metadata 化する。"""
    element_ids = {span.element_id for span in group}
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for span in group:
        if not span.parent_id or span.parent_id not in element_ids:
            continue
        key = (span.parent_id, span.element_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"parent_id": span.parent_id, "child_id": span.element_id})
    return edges


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
            current_path = tuple(path_by_level[level_key] for level_key in sorted(path_by_level))
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
