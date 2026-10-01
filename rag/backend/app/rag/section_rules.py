"""章節の抽出規則(#715)。

保存済みの抽出結果の要素に規則を当てて、章節(並び順 + 階層の一覧)を作る。抽出のやり直しは
要らず、規則を変えるとすぐ章節ナビゲーションに出る。chunk・検索・抽出結果は変えない。

- 方式: ``parser``(解析エンジンの見出し。今までどおり抽出結果の章節を使う)/ プリセット
  ``legal``(法令)・``official``(公用文)・``numbered``(番号付き)/ ``custom``(独自の規則)。
- 照合: 各要素の 1 行目を NFKC で全角・半角をそろえ、規則の正規表現を行頭から当てる(並びが優先
  順位)。長すぎる行・文で終わる行は見出しにしない。
- 全体の既定は ``section-rules.json``(``RAG_SECTION_RULES_FILE`` で場所を変えられる。抽出項目の
  定義と同じ形)。処理レシピの ``section_rules_mode`` で方式を上書きできる。
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import BACKEND_ROOT
from app.rag.document_sections import sections_from_navigation
from app.schemas.document import DocumentSection, SectionRulesMode
from app.schemas.extraction import DocumentElement, StructuredExtraction

SECTION_RULES_MODES: tuple[SectionRulesMode, ...] = (
    "parser",
    "legal",
    "official",
    "numbered",
    "custom",
)
SECTION_RULES_FILE_ENV = "RAG_SECTION_RULES_FILE"
DEFAULT_SECTION_RULES_FILE = "section-rules.json"
MAX_SECTION_RULES = 30
# 見出しとみなす行の長さの上限(本文の 1 文を見出しにしない)。
MAX_HEADING_CHARS = 80
# 図・表など、見出しにしない要素の種類。
_NON_HEADING_KINDS = frozenset({"table", "figure", "image", "section_summary", "formula", "code"})
_SENTENCE_END = re.compile(r"[。．！？!?]$")


class SectionRule(BaseModel):
    """章節の抽出規則 1 件。``pattern`` を要素の 1 行目の行頭から当て、合えば ``level`` の章節。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=80)
    pattern: str = Field(min_length=1, max_length=300)
    level: int = Field(ge=1, le=6)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("規則の名前を入力してください。")
        return cleaned

    @field_validator("pattern")
    @classmethod
    def _compilable(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"正規表現として読めません（{exc.msg}）。") from exc
        return value


class SectionRulesSettingsData(BaseModel):
    """章節の抽出規則の全体の既定と、プリセットの中身(画面の表示用)。"""

    mode: SectionRulesMode
    rules: list[SectionRule]
    presets: dict[str, list[SectionRule]]


class SectionRulesPreviewRequest(BaseModel):
    """見本の文書に規則を当てた章節を返す(保存しない)。"""

    model_config = ConfigDict(extra="forbid")

    mode: SectionRulesMode
    rules: list[SectionRule] = Field(default_factory=list, max_length=MAX_SECTION_RULES)
    recipe_id: str | None = Field(default=None, max_length=128)


class SectionRulesStore(BaseModel):
    """章節の抽出規則の全体の既定。"""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    mode: SectionRulesMode = "parser"
    rules: list[SectionRule] = Field(default_factory=list, max_length=MAX_SECTION_RULES)


_NUM = "[0-9一二三四五六七八九十百千〇零]+"

# プリセット(rag_engine/chunking/headings.py・rag_parser_core の見出しの規則を下敷きにした)。
PRESET_RULES: dict[SectionRulesMode, list[SectionRule]] = {
    "legal": [
        SectionRule(name="編", pattern=rf"^第{_NUM}編", level=1),
        SectionRule(name="章", pattern=rf"^第{_NUM}章", level=2),
        SectionRule(name="節", pattern=rf"^第{_NUM}節", level=3),
        SectionRule(name="款", pattern=rf"^第{_NUM}款", level=4),
        SectionRule(name="目", pattern=rf"^第{_NUM}目", level=5),
        SectionRule(name="条", pattern=rf"^第{_NUM}条(の{_NUM})*", level=6),
    ],
    "official": [
        SectionRule(name="第1", pattern=r"^第[0-9]+(\s|$)", level=1),
        SectionRule(name="1", pattern=r"^[0-9]+(\s|[.、]\s*)\S", level=2),
        SectionRule(name="(1)", pattern=r"^\([0-9]+\)", level=3),
        SectionRule(name="ア", pattern=r"^[ア-ン](\s|[.、])", level=4),
        SectionRule(name="(ア)", pattern=r"^\([ア-ン]\)", level=5),
    ],
    "numbered": [
        SectionRule(name="1.1.1", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(\s|\.)", level=3),
        SectionRule(name="1.1", pattern=r"^[0-9]+\.[0-9]+(\s|\.)", level=2),
        SectionRule(name="1.", pattern=r"^[0-9]+\.\s", level=1),
    ],
}


def _section_rules_path() -> Path:
    raw = os.environ.get(SECTION_RULES_FILE_ENV, "").strip() or DEFAULT_SECTION_RULES_FILE
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def load_section_rules() -> SectionRulesStore:
    """全体の既定。保存していない・読めないときは「解析エンジンの見出し」。"""
    try:
        data = _section_rules_path().read_text(encoding="utf-8")
    except OSError:
        return SectionRulesStore()
    try:
        return SectionRulesStore.model_validate_json(data)
    except ValueError:
        return SectionRulesStore()


def save_section_rules(store: SectionRulesStore) -> SectionRulesStore:
    path = _section_rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    return store


def reset_section_rules() -> None:
    """保存した全体の既定を消し、「解析エンジンの見出し」に戻す。"""
    _section_rules_path().unlink(missing_ok=True)


def rules_for_mode(mode: SectionRulesMode, store: SectionRulesStore) -> list[SectionRule] | None:
    """方式の規則。``parser`` は None(抽出結果の章節を使う)。"""
    if mode == "parser":
        return None
    rules = store.rules if mode == "custom" else PRESET_RULES[mode]
    return [rule for rule in rules if rule.enabled]


def _heading_line(element: DocumentElement) -> str:
    if (element.kind or "").lower() in _NON_HEADING_KINDS:
        return ""
    if (element.content_kind or "").lower() in _NON_HEADING_KINDS:
        return ""
    line = element.text.strip().split("\n", 1)[0].strip()
    # Markdown の見出しの印は、規則を当てる前に外す。
    return re.sub(r"^#{1,6}\s+", "", line)


def sections_from_rules(
    elements: Sequence[DocumentElement], rules: Sequence[SectionRule]
) -> list[DocumentSection]:
    """要素に規則を当てて章節を作る。ページ範囲は次の同じか浅い階層の章節の直前まで。"""
    compiled = [(re.compile(rule.pattern), rule.level) for rule in rules]
    ordered = sorted(elements, key=lambda element: element.order)
    headings: list[tuple[int, str, int, int | None]] = []  # (要素の位置, 名前, 階層, ページ)
    for position, element in enumerate(ordered):
        line = _heading_line(element)
        if not line or len(line) > MAX_HEADING_CHARS or _SENTENCE_END.search(line):
            continue
        normalized = unicodedata.normalize("NFKC", line)
        level = next((lvl for pattern, lvl in compiled if pattern.match(normalized)), None)
        if level is not None:
            headings.append((position, line, level, element.page_number))

    sections: list[DocumentSection] = []
    # 規則の階層(1〜6)を、章節の木の深さに写す。浅い・同じ階層が来たら戻り、規則の階層が飛んで
    # いても 1 段ずつにする(例: 法令の「章(2)」「条(6)」は 1・2)。
    stack: list[int] = []
    for index, (position, title, raw_level, page) in enumerate(headings):
        end_position = next(
            (later[0] for later in headings[index + 1 :] if later[2] <= raw_level), len(ordered)
        )
        pages = [
            element.page_number
            for element in ordered[position:end_position]
            if element.page_number is not None
        ]
        while stack and stack[-1] >= raw_level:
            stack.pop()
        stack.append(raw_level)
        level = min(len(stack), 6)
        section_id = (
            "rule-"
            + hashlib.sha1(f"{index}:{title}".encode(), usedforsecurity=False).hexdigest()[:16]
        )
        sections.append(
            DocumentSection(
                id=section_id,
                title=title[:200],
                level=level,
                page_start=page if page is not None else (min(pages) if pages else None),
                page_end=max(pages) if pages else None,
                origin="extraction",
                source_section_id=section_id,
            )
        )
    return sections


def extraction_sections(
    extraction: StructuredExtraction | None,
    mode: SectionRulesMode,
    store: SectionRulesStore | None = None,
) -> list[DocumentSection]:
    """抽出結果の章節(方式が ``parser`` なら解析エンジンの見出し、それ以外は規則を当てた章節)。"""
    if extraction is None:
        return []
    rules = rules_for_mode(mode, store or load_section_rules())
    if rules is None:
        return sections_from_navigation(extraction.navigation)
    return sections_from_rules(extraction.elements, rules)


def section_rules_settings(store: SectionRulesStore | None = None) -> SectionRulesSettingsData:
    current = store or load_section_rules()
    return SectionRulesSettingsData(
        mode=current.mode,
        rules=current.rules,
        presets={mode: rules for mode, rules in PRESET_RULES.items()},
    )
