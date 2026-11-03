"""人が修正した章節の版どうしの比較と、基にした抽出の記録（#4171）。DB と入出力を持たない。

章節の修正の前提は「抽出の章節の一覧」。抽出の ID だけでは足りない（同じ原本・設定で解析し直すと
同じ ID のまま内容が変わり、章節の抽出規則を変えても ID は変わらない。逆に抽出の文の修正は ID を
変えるが章節は変わらない）ので、ID と一緒に抽出の章節の指紋を記録する。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from app.schemas.document import DocumentSection

SectionChangeKind = Literal["added", "removed", "changed", "moved"]


@dataclass(frozen=True)
class SectionExtractionBase:
    """章節の修正の基にした抽出（ID・抽出の章節の指紋・抽出の章節の内容）。"""

    extraction_recipe_id: str | None
    outline_sha256: str | None
    outline: dict[str, object]


@dataclass(frozen=True)
class SectionChange:
    """版の間で変わった章節 1 件。"""

    section_id: str
    change: SectionChangeKind
    before: DocumentSection | None
    after: DocumentSection | None


def section_revision_payload(
    sections: Sequence[DocumentSection], removed_source_ids: Sequence[str] = ()
) -> dict[str, object]:
    """版と今の修正の行に保存する内容。"""
    return {
        "sections": [section.model_dump() for section in sections],
        "removed_source_ids": sorted(removed_source_ids),
    }


def section_outline_fingerprint(extracted: Sequence[DocumentSection]) -> str:
    """抽出の章節の指紋（順序・階層・名前・ページ。修正の前提が変わったかを判定する）。"""
    canonical = json.dumps(
        [
            [
                section.source_section_id,
                section.title,
                section.level,
                section.page_start,
                section.page_end,
            ]
            for section in extracted
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extraction_base_of(
    extraction_recipe_id: str | None,
    extracted: Sequence[DocumentSection],
    *,
    has_extraction: bool,
) -> SectionExtractionBase:
    """今の抽出を、章節の修正の基として記録する形にする（抽出が無ければ ID と指紋は持たない）。"""
    return SectionExtractionBase(
        extraction_recipe_id=extraction_recipe_id if has_extraction else None,
        outline_sha256=section_outline_fingerprint(extracted) if has_extraction else None,
        outline=section_revision_payload(extracted),
    )


def base_change_flags(
    *,
    recorded_extraction_recipe_id: object,
    recorded_outline_sha256: object,
    current: SectionExtractionBase,
) -> tuple[bool, bool]:
    """記録した基と今の抽出の違い。(抽出が変わった, 抽出の章節が変わった)。

    記録の無い修正（履歴の前の修正）と、今の抽出が無いときは比べられないので変わっていない扱いにする。
    """
    if recorded_extraction_recipe_id is None or current.extraction_recipe_id is None:
        return False, False
    outline_changed = recorded_outline_sha256 != current.outline_sha256
    return (
        outline_changed or recorded_extraction_recipe_id != current.extraction_recipe_id,
        outline_changed,
    )


def _moved_section_ids(before: Sequence[str], after: Sequence[str]) -> set[str]:
    """両方にある章節のうち、並びが変わったもの（動かしていない多数を「移動」にしない）。"""
    matcher = SequenceMatcher(None, list(before), list(after), autojunk=False)
    kept: set[str] = set()
    for block in matcher.get_matching_blocks():
        kept.update(before[block.a : block.a + block.size])
    return set(before) - kept


def _content_changed(before: DocumentSection, after: DocumentSection) -> bool:
    return (before.title, before.level, before.page_start, before.page_end) != (
        after.title,
        after.level,
        after.page_start,
        after.page_end,
    )


def section_changes(
    before: Sequence[DocumentSection], after: Sequence[DocumentSection]
) -> list[SectionChange]:
    """``before`` から ``after`` への章節の差分（``after`` の順の後に、消えた章節）。"""
    before_by_id = {section.id: section for section in before}
    after_ids = {section.id for section in after}
    moved = _moved_section_ids(
        [section.id for section in before if section.id in after_ids],
        [section.id for section in after if section.id in before_by_id],
    )
    changes: list[SectionChange] = []
    for section in after:
        previous = before_by_id.get(section.id)
        if previous is None:
            changes.append(SectionChange(section.id, "added", None, section))
        elif _content_changed(previous, section):
            changes.append(SectionChange(section.id, "changed", previous, section))
        elif section.id in moved:
            changes.append(SectionChange(section.id, "moved", previous, section))
    changes.extend(
        SectionChange(section.id, "removed", section, None)
        for section in before
        if section.id not in after_ids
    )
    return changes
