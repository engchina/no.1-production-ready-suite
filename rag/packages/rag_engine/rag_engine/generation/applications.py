"""回答の「当てはめ」: 規則の item を、質問に書かれた事実に当てはめた結果の検査（#2155）。

原文の引用で支える item（``grounded.GroundedItem``）は、規則を質問の事実に当てはめた結果（計算の結果・
該当するか）を書けない。当てはめは、参照・算式などの構造検査と、規則・質問・結論の意味検査を分ける。
推導結果の公開時は判定の欠落・重複・失敗も保留し、選んだ原文は残す。
原文だけの表示を確認済みの推導とみなさない。

特定の業務・文書の知識は持たない（規則は根拠の原文、事実は質問から取る）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answer_table import source_marks, table_cells, table_markdown
from rag_engine.generation.grounding_cues import grounding_cues
from rag_engine.models.llm import GroundedApplication

# 回答の本文の節の見出し。
APPLICATIONS_SECTION_TITLE = "資料をもとに導いた結論"
LEGACY_APPLICATIONS_SECTION_TITLES = ("質問の条件への当てはめ",)
PREMISES_LABEL = "前提："
REASONING_LABEL = "当てはめ："


@dataclass
class CheckedApplication:
    """検査後の当てはめ。``entries`` は指した規則の item（``grounded.CheckedItem``）。reasons は出さない理由。"""

    application: GroundedApplication
    entries: list[Any] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.reasons.append(reason)

    @property
    def published(self) -> bool:
        """出すか（指した規則の item がすべて本文に残っている）。"""
        return not self.reasons and bool(self.entries) and all(_usable(entry) for entry in self.entries)

    def to_payload(self) -> dict[str, Any]:
        return {**self.application.model_dump(), "published": self.published, "reasons": list(self.reasons)}


# 利用者向けの文に出してはいけない内部の番号（items の index・規則の番号・根拠の番号）。
def _internal_label() -> re.Pattern[str]:
    """内部の番号（「規則0」・items[0]・E12）。規則の語は言語資源（grounding_cues）。"""
    words = grounding_cues().alt["internal_label_words"]
    return re.compile(rf"{words}\s*\d+|items?\s*\[\s*\d+\s*\]|(?<![A-Za-z0-9])E\d{{1,3}}(?![0-9])")


def _usable(entry: Any) -> bool:
    """当てはめに使える（本文に言い換えが残る）規則の item か。"""
    return bool(entry.span) and not entry.quote_only and entry.item.kind != "gap"


def check_applications(applications: Sequence[GroundedApplication], items_by_index: Mapping[int, Any]) -> list[CheckedApplication]:
    """当てはめが指す規則の item の実在と、内部の番号の混入だけを確かめる。"""
    checked: list[CheckedApplication] = []
    for application in applications:
        entry = CheckedApplication(application)
        indexes = list(dict.fromkeys(application.item_indexes))
        if not indexes:
            entry.reject("当てはめる規則の item が無い")
        for index in indexes:
            item = items_by_index.get(index)
            if item is None or not item.span or item.item.kind == "gap":
                entry.reject(f"items[{index}] は引用のある規則ではない")
            else:
                entry.entries.append(item)
        if label := _internal_label().search("\n".join([application.reasoning, application.result, *table_cells(application.table)])):
            entry.reject(f"内部の番号『{label.group(0)}』を利用者向けの文に書いている")
        checked.append(entry)
    return checked


def render_lines(
    checked: Sequence[CheckedApplication],
    citation: Callable[[dict], str],
    cite: Callable[[Sequence[Any]], None],
) -> list[str]:
    """公開する当てはめの本文の行。指した規則の出典行を、本文の出典行と同じ形で続ける。

    同じ出典行になる規則はまとめて 1 行にし、``cite`` にはその行にまとめた規則（CheckedItem）を渡す。
    """
    lines: list[str] = []
    for entry in checked:
        if not entry.published:
            continue
        application = entry.application
        lines.append(f"・{application.result.strip()}")
        # 表の結論は冒頭の回答に表と行ごとの出典行を 1 回だけ出す（``table_block_lines``。#4812）。ここは題と案内だけ。
        if application.table is not None:
            lines.append(answer_labels().table_above_note)
            continue
        premises = "".join(f"「{premise.strip()}」" for premise in application.premises if premise.strip())
        if premises:
            lines.append(PREMISES_LABEL + premises)
        if application.reasoning.strip():
            lines.append(REASONING_LABEL + application.reasoning.strip())
        shown: dict[str, list[Any]] = {}
        for item in entry.entries:
            if item.span:
                shown.setdefault(citation(item.span), []).append(item)
        for line, items in shown.items():
            lines.append(line)
            cite(items)
    return lines


def table_block_lines(
    entry: CheckedApplication,
    citation: Callable[[dict], str],
    cite: Callable[[Sequence[Any]], None],
) -> list[str]:
    """冒頭の回答に出す表の結論の行（題・出典の列つきの表・表の下の出典行。#4812）。

    出典行は行の順に、同じ出典行になる規則をまとめて 1 行にする（``render_lines`` と同じ形）。出典の列の番号は表の下の
    出典行の順（1 始まり）で、番号はコードが振る。``cite`` にはその行にまとめた規則（CheckedItem）を出典行の順に渡す。
    """
    application = entry.application
    table = application.table
    if table is None:
        return []
    by_index = {item.draft_index: item for item in entry.entries}
    shown: dict[str, list[Any]] = {}
    marks: list[str] = []
    for row in table.rows:
        numbers: list[int] = []
        for index in row.item_indexes:
            item = by_index.get(index)
            if item is None or not item.span:
                continue
            line = citation(item.span)
            items = shown.setdefault(line, [])
            if all(item is not other for other in items):
                items.append(item)
            numbers.append(list(shown).index(line) + 1)
        marks.append(source_marks(numbers))
    # 行に結ばれていない規則（行の根拠の外の item）も、出典行として表の下に残す（番号の印は付かない）。
    for item in entry.entries:
        if item.span and all(item is not other for items in shown.values() for other in items):
            shown.setdefault(citation(item.span), []).append(item)
    lines = [
        application.result.strip(),
        *table_markdown(table, source_column=answer_labels().table_source_column, sources=marks).splitlines(),
    ]
    for line, items in shown.items():
        lines.append(line)
        cite(items)
    return lines
