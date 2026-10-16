"""回答の本文の見出し・定型文・出典の表示（言語資源の answer_labels。#2181 段 5）。

回答の組み立て（render）が書く語と、回答の検査・評価・指標が回答の本文を読み分ける語を、同じ言語資源から作る。
組み立ては言語資源の hash ごとに 1 回だけ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rag_parser_core.language import LanguagePack, per_pack


@dataclass(frozen=True)
class AnswerLabels:
    """回答の本文の見出し・定型文・出典の表示。"""

    rules_section: str
    quote_only_section: str
    quote_only_note: str
    gaps_section: str
    unverified_section: str
    deadline_section: str
    confirmations_section: str
    confirmation_only_summary: str
    neutral_summary: str
    citation_prefix: str
    deadline_summary: str
    off_goal_gap: str
    cause_gap: str
    operation_steps_heading: str
    confirmation_steps_heading: str
    page_location: str
    sheet_location: str
    item_bullet: str
    steps_heading_template: str
    quote_template: str
    condition_template: str
    condition_suffix: str
    condition_link: str
    # 用言の終わり（「有効期限を過ぎた」）。条件がこれで終わるときは「の」を挟まない (#644)。
    predicate_endings: tuple[str, ...]
    # 観点名（task_contract.required_aspects）→ 利用者向けの語 (#986)。
    aspect_labels: dict[str, str]
    # render() が決定的に出力する見出し行と出典行、「参考情報」の節の原文の行と節の箇条の行。
    structural_line: re.Pattern[str]
    quote_only_line: re.Pattern[str]
    section_item_line: re.Pattern[str]

    def page(self, page: object) -> str:
        return self.page_location.format(page=page)

    def sheet(self, sheet: str, cell_range: str) -> str:
        return self.sheet_location.format(sheet=sheet, cell_range=cell_range)

    def quote(self, text: str) -> str:
        return self.quote_template.format(quote=text)

    def condition(self, text: str) -> str:
        return self.condition_template.format(condition=text)

    def steps_heading(self, heading: str, title: str) -> str:
        return self.steps_heading_template.format(heading=heading, title=title) if title else heading


def _build(pack: LanguagePack) -> AnswerLabels:
    def text(key: str) -> str:
        return pack.text("answer_labels", key)

    note = text("quote_only_note")
    note_sentences = tuple(sentence for sentence in re.split(r"(?<=。)", note) if sentence)
    page_head = re.escape(text("page_location").split("{", 1)[0])
    sheet_head = re.escape(text("sheet_location").split("{", 1)[0])
    steps = "|".join(re.escape(text(key)) for key in ("operation_steps_heading", "confirmation_steps_heading"))
    # 見出しの型の「{title}」の前後（「（」「）」）。
    title_open, _sep, title_close = text("steps_heading_template").partition("{title}")
    title_open = title_open.replace("{heading}", "")
    quote_open, _sep, quote_close = text("quote_template").partition("{quote}")
    item = rf"(?:{re.escape(text('item_bullet'))}|[0-9]+\.\s)"
    titles = (
        text("rules_section"),
        text("quote_only_section"),
        note,
        *note_sentences,
        text("gaps_section"),
        text("unverified_section"),
        text("deadline_section"),
    )
    return AnswerLabels(
        rules_section=text("rules_section"),
        quote_only_section=text("quote_only_section"),
        quote_only_note=note,
        gaps_section=text("gaps_section"),
        unverified_section=text("unverified_section"),
        deadline_section=text("deadline_section"),
        confirmations_section=text("confirmations_section"),
        confirmation_only_summary=text("confirmation_only_summary"),
        neutral_summary=text("neutral_summary"),
        citation_prefix=text("citation_prefix"),
        deadline_summary=text("deadline_summary"),
        off_goal_gap=text("off_goal_gap"),
        cause_gap=text("cause_gap"),
        operation_steps_heading=text("operation_steps_heading"),
        confirmation_steps_heading=text("confirmation_steps_heading"),
        page_location=text("page_location"),
        sheet_location=text("sheet_location"),
        item_bullet=text("item_bullet"),
        steps_heading_template=text("steps_heading_template"),
        quote_template=text("quote_template"),
        condition_template=text("condition_template"),
        condition_suffix=text("condition_suffix"),
        condition_link=text("condition_link"),
        predicate_endings=pack.words("answer_labels", "predicate_endings"),
        aspect_labels={aspect: label for label, aspect in pack.mapping("answer_labels", "aspect_labels").items()},
        structural_line=re.compile(
            "|".join(re.escape(title) for title in titles)
            + rf"|(?:{steps})(?:{re.escape(title_open)}.+{re.escape(title_close)})?|"
            + re.escape(text("citation_prefix"))
            + rf".*(?: {page_head}.*| {sheet_head}.+)"
        ),
        quote_only_line=re.compile(rf"{item}{re.escape(quote_open)}.*{re.escape(quote_close)}"),
        section_item_line=re.compile(item),
    )


answer_labels = per_pack(_build)
