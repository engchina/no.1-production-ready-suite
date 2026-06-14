"""VLM/LLM テキスト抽出スキーマ。"""

import math
import re
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

type ExtractionMetadataValue = str | int | float | bool | None

MARKDOWN_HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+)$")
NUMBERED_HEADING = re.compile(
    r"^(?P<prefix>(?:\d+(?:\.\d+)*|第[一二三四五六七八九十百千\d]+[章節部]|"
    r"[（(]?\d+[）)]))[\s:：.．、-]+(?P<title>.+)$"
)
BULLET_LINE = re.compile(r"^\s*(?:[-*・]|\d+[.)）]|[（(]\d+[）)])\s+")
TABLE_LINE = re.compile(r"^\s*\|.+\|\s*$")
PAGE_MARKER = re.compile(
    r"^\s*(?:-{2,}\s*)?(?:page|ページ|頁)\s*(?P<page>\d+)\s*(?:-{2,})?\s*$",
    re.IGNORECASE,
)

ELEMENT_KIND_ALIASES = {
    "paragraph": "text",
    "body": "text",
    "body_text": "text",
    "caption": "figure_caption",
    "figcaption": "figure_caption",
    "heading": "title",
    "header": "header",
    "footer": "footer",
    "list_item": "list",
    "bullet": "list",
    "bullet_list": "list",
    "ordered_list": "list",
    "table_chunk": "table",
    "tsr": "table",
    "formula": "equation",
    "image": "figure",
    "picture": "figure",
    "タイトル": "title",
    "見出し": "title",
    "本文": "text",
    "表": "table",
    "箇条書き": "list",
}

SEARCHABLE_ELEMENT_KINDS = {
    "title",
    "text",
    "list",
    "table",
    "table_caption",
    "figure",
    "figure_caption",
    "reference",
    "equation",
    "code",
    "other",
}


class DocumentElement(BaseModel):
    """ページ・見出し・表などを保持する文書構造要素。"""

    kind: str = "text"
    text: str = ""
    order: int = Field(default=0, ge=0)
    page_number: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None
    section_path: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, ExtractionMetadataValue] = Field(default_factory=dict)

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> str:
        """VLM/parser ごとの kind 表記を低 cardinality に寄せる。"""
        if value is None:
            return "text"
        normalized = re.sub(r"[\s-]+", "_", str(value).strip().casefold())
        if not normalized:
            return "text"
        return ELEMENT_KIND_ALIASES.get(normalized, normalized)[:40]

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        """要素本文の改行だけ正規化し、表・リストの構造は残す。"""
        return value.replace("\r\n", "\n").replace("\r", "\n").strip()

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float] | None) -> list[float] | None:
        """bbox は x1,y1,x2,y2 の 4 値だけを保存する。"""
        if value is None:
            return None
        if len(value) != 4 or not all(math.isfinite(item) for item in value):
            raise ValueError("bbox は有限数 4 個で指定してください。")
        return [float(item) for item in value]

    @field_validator("section_path")
    @classmethod
    def normalize_section_path(cls, value: list[str]) -> list[str]:
        """空の章節名を落とし、長すぎる値を切り詰める。"""
        return [re.sub(r"\s+", " ", item).strip()[:80] for item in value if item.strip()]

    @field_validator("metadata")
    @classmethod
    def normalize_metadata(
        cls,
        value: dict[str, ExtractionMetadataValue],
    ) -> dict[str, ExtractionMetadataValue]:
        """metadata は JSON scalar だけを残す。"""
        normalized: dict[str, ExtractionMetadataValue] = {}
        for key, item in value.items():
            clean_key = str(key).strip()[:80]
            if clean_key:
                normalized[clean_key] = item
        return normalized

    def to_payload(self) -> dict[str, object]:
        """DocumentDetail.extraction に入れる JSON 互換 payload を返す。"""
        return self.model_dump(exclude_none=True)


class StructuredExtraction(BaseModel):
    """OCI Enterprise AI の VLM/LLM 出力を検証して保存するための正規化形。"""

    raw_text: str = ""
    document_type: str = "ドキュメント"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    elements: list[DocumentElement] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_structure(self) -> Self:
        """raw_text と elements のどちらか片方だけでも検索可能な形へ補完する。"""
        self.raw_text = self.raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        self.elements = normalize_document_elements(self.elements, fallback_text=self.raw_text)
        if not self.raw_text and self.elements:
            self.raw_text = "\n".join(
                element.text
                for element in self.elements
                if element.kind in SEARCHABLE_ELEMENT_KINDS
            ).strip()
        return self

    def to_document_payload(self) -> dict[str, object]:
        """DocumentDetail.extraction に格納する JSON 互換 dict を返す。"""
        return {
            "raw_text": self.raw_text,
            "document_type": self.document_type,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "elements": [element.to_payload() for element in self.elements],
        }


def normalize_document_elements(
    elements: list[DocumentElement],
    *,
    fallback_text: str,
) -> list[DocumentElement]:
    """VLM が返した要素列を正規化し、なければ raw_text から軽量推定する。"""
    if not elements:
        return infer_document_elements(fallback_text)

    indexed = list(enumerate(element for element in elements if element.text.strip()))
    if any(element.order > 0 for _, element in indexed):
        indexed.sort(key=lambda item: (item[1].order, item[0]))

    normalized: list[DocumentElement] = []
    path_by_level: dict[int, str] = {}
    current_path: list[str] = []
    for order, (_, element) in enumerate(indexed):
        metadata = dict(element.metadata)
        kind = element.kind
        if heading := _parse_heading(element.text):
            level, title = heading
            kind = "title"
            path_by_level = {
                existing_level: existing_title
                for existing_level, existing_title in path_by_level.items()
                if existing_level < level
            }
            path_by_level[level] = title
            current_path = [path_by_level[key] for key in sorted(path_by_level)]
            metadata.setdefault("section_level", level)
        elif element.section_path:
            current_path = element.section_path

        normalized.append(
            element.model_copy(
                update={
                    "kind": kind,
                    "order": order,
                    "section_path": element.section_path or current_path,
                    "metadata": metadata,
                }
            )
        )
    return normalized


def infer_document_elements(text: str) -> list[DocumentElement]:
    """raw_text だけの抽出結果から Docling/Unstructured 風の block 列を推定する。"""
    source = text.replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return []

    elements: list[DocumentElement] = []
    path_by_level: dict[int, str] = {}
    current_page = 1
    offset = 0
    pending_kind: str | None = None
    pending_lines: list[str] = []
    pending_start = 0
    pending_page = current_page

    def flush_pending(end_offset: int) -> None:
        nonlocal pending_kind, pending_lines, pending_start, pending_page
        if not pending_kind or not pending_lines:
            return
        _append_element(
            elements,
            kind=pending_kind,
            text="\n".join(pending_lines),
            page_number=pending_page,
            section_path=[path_by_level[key] for key in sorted(path_by_level)],
            raw_start=pending_start,
            raw_end=end_offset,
        )
        pending_kind = None
        pending_lines = []

    for raw_line in source.splitlines(keepends=True):
        line_start = offset
        offset += len(raw_line)
        stripped = raw_line.strip()
        if not stripped:
            flush_pending(line_start)
            continue

        if page_match := PAGE_MARKER.match(stripped):
            flush_pending(line_start)
            current_page = max(1, int(page_match.group("page")))
            continue

        if heading := _parse_heading(stripped):
            flush_pending(line_start)
            level, title = heading
            path_by_level = {
                existing_level: existing_title
                for existing_level, existing_title in path_by_level.items()
                if existing_level < level
            }
            path_by_level[level] = title
            _append_element(
                elements,
                kind="title",
                text=stripped,
                page_number=current_page,
                section_path=[path_by_level[key] for key in sorted(path_by_level)],
                raw_start=line_start,
                raw_end=offset,
                extra_metadata={"section_level": level},
            )
            continue

        line_kind = _line_kind(stripped)
        if (
            pending_kind is not None
            and (pending_kind != line_kind or pending_page != current_page)
        ):
            flush_pending(line_start)
        if pending_kind is None:
            pending_kind = line_kind
            pending_start = line_start
            pending_page = current_page
        pending_lines.append(stripped)

    flush_pending(offset)
    return elements


def _append_element(
    elements: list[DocumentElement],
    *,
    kind: str,
    text: str,
    page_number: int,
    section_path: list[str],
    raw_start: int,
    raw_end: int,
    extra_metadata: dict[str, ExtractionMetadataValue] | None = None,
) -> None:
    """推定 element を順序と raw offset 付きで追加する。"""
    metadata: dict[str, ExtractionMetadataValue] = {
        "raw_start": raw_start,
        "raw_end": raw_end,
        **(extra_metadata or {}),
    }
    elements.append(
        DocumentElement(
            kind=kind,
            text=text,
            order=len(elements),
            page_number=page_number,
            section_path=section_path,
            metadata=metadata,
        )
    )


def _line_kind(line: str) -> str:
    """1 行から element kind を推定する。"""
    if TABLE_LINE.match(line):
        return "table"
    if BULLET_LINE.match(line):
        return "list"
    if line.casefold().startswith(("header:", "ヘッダー:")):
        return "header"
    if line.casefold().startswith(("footer:", "フッター:")):
        return "footer"
    return "text"


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
    return re.sub(r"\s+", " ", title).strip().strip("#")[:80]
