"""完成した回答本文と引用位置を受け、本文の意味で公開を判定しない。"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, create_model, field_validator

from rag_engine.generation.answer_envelope import AnswerOutcome
from rag_engine.generation.citation_unit_ids import BoundUnitRange, SentUnitCatalog, unit_label
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem, ResponseLanguage
from rag_engine.retrieval.character_forms import format_characters


class FullAnswerWire(BaseModel):
    """本文はモデルが完成させ、種別は同じ呼び出しの付帯情報にする。"""

    model_config = ConfigDict(extra="forbid")
    _provider_output_text: str | None = PrivateAttr(default=None)
    _uses_unit_ids = True
    _uses_full_answer = True

    response_language: ResponseLanguage = Field(default_factory=ResponseLanguage)
    answer_body: str = Field(min_length=1, description="利用者へ返す完成した回答本文。必要に応じて Markdown を使う。")
    response_kind: AnswerOutcome = Field(
        description="本文の回答種別。付帯情報であり、本文の正しさを保証しない。answered=回答、conditional=条件付き、needs_environment_data=実環境の確認が必要、needs_clarification=質問の確認が必要、insufficient_evidence=資料不足、needs_human=人の判断が必要。"
    )
    confidence: Literal["high", "medium", "low"] = Field(description="回答の確かさの自己申告。")


# 印の角括弧として読む文字（半角・全角・隅付き括弧。#5091）。
_OPEN = "[\\[［【]"
_CLOSE = "[\\]］】]"


@functools.cache
def _padding() -> str:
    """括弧の内側と印の間に許す字: 改行を除く Unicode の空白（全角の空白を含む）と書式文字（ZWSP・BOM など）の連続（#5186）。"""
    return rf"(?:[^\S\n]|[{format_characters()}])*"


def citation_marker(catalog: SentUnitCatalog) -> re.Pattern[str] | None:
    """本文の引用の印: この入力の根拠のラベル（``[E2]``）か単位のラベル（``[E2.1]``）を角括弧で囲んだもの。

    印はモデルが入力の根拠で見たラベルそのもので、別の番号の体系を持たない（#5078）。今回送っていない根拠の
    ラベルは印にしない（本文の字として残す）。自然言語の意味は解析しない。

    角括弧は文字の種類だけを形式として揃え、全角の ``［］`` と ``【】`` も同じ印として読む（#5091）。括弧の内側の
    前後の空白と書式文字（``[ E2.1 ]``・ゼロ幅の空白の入った印）も形式として読み飛ばす（#5186）。ラベルの照合は変えない。
    """
    if not catalog.block_labels:
        return None
    blocks = "|".join(re.escape(label) for label in sorted(catalog.block_labels, key=len, reverse=True))
    pad = _padding()
    return re.compile(rf"{_OPEN}{pad}({blocks})(?:\.([1-9][0-9]*))?{pad}{_CLOSE}")


def _marker_run(marker: re.Pattern[str]) -> re.Pattern[str]:
    """続けて書いた印（``[E2.1][E2.2]``）は 1 か所の引用として、隣り合う単位を 1 つの出典の行にまとめる。"""
    return re.compile(rf"(?:{marker.pattern})(?:{_padding()}(?:{marker.pattern}))*")


def _cited_labels(run: str, marker: re.Pattern[str], catalog: SentUnitCatalog) -> tuple[str, ...]:
    """印の並びが指す単位のラベル。根拠のラベルはその根拠のすべての単位を指す。"""
    labels: list[str] = []
    for match in marker.finditer(run):
        block, index = match.group(1), match.group(2)
        labels.extend(catalog.block_units(block) if index is None else [unit_label(block, int(index))])
    return tuple(dict.fromkeys(labels))


def full_answer_model(catalog: SentUnitCatalog) -> type[BaseModel]:
    """引用は本文の印だけで受ける。印が指せるのは、この入力に実際に提示した根拠と単位だけ。"""
    marker = citation_marker(catalog)

    def known_units(value: str) -> str:
        if marker is None:
            return value
        unknown = [
            match.group(0)
            for match in marker.finditer(value)
            if match.group(2) is not None and unit_label(match.group(1), int(match.group(2))) not in catalog.unit_labels
        ]
        if unknown:
            raise ValueError(
                f"answer_body の {'、'.join(dict.fromkeys(unknown))} は今回の根拠に無い行のラベルです。"
                "引用には根拠の各行に示したラベルか、根拠のラベルをそのまま書いてください。"
            )
        return value

    validators: dict[str, Any] = {"known_units": field_validator("answer_body")(known_units)}
    return create_model("FullAnswerWire", __base__=FullAnswerWire, __validators__=validators)


def canonical_full_answer(value: BaseModel, catalog: SentUnitCatalog) -> GroundedDraft:
    """本文の印が指す単位を既存の保存形式へ結ぶ。分類・引用数による本文の削除や書換えはしない。"""
    raw = value.model_dump()
    body = str(raw["answer_body"])
    marker = citation_marker(catalog)
    bindings: list[BoundUnitRange] = []
    groups: dict[str, tuple[int, ...]] = {}
    if marker is not None:
        for run in _marker_run(marker).finditer(body):
            labels = _cited_labels(run.group(0), marker, catalog)
            key = ",".join(labels)
            if key in groups:
                continue
            selected = catalog.bind(labels)
            groups[key] = tuple(range(len(bindings), len(bindings) + len(selected)))
            bindings.extend(selected)
    items: list[GroundedItem] = []
    for binding in bindings:
        citation = GroundedCitation(evidence_id=binding.snapshot.span()["evidence_id"])
        citation._unit_binding = binding
        citation._wire_range = (binding.first + 1, binding.last + 1)
        citation.lines = str(binding.first + 1) if binding.first == binding.last else f"{binding.first + 1}-{binding.last + 1}"
        items.append(GroundedItem(kind="rule", text=binding.text, citations=[citation]))
    draft = GroundedDraft(items=items, summary="", confidence=raw["confidence"], response_language=raw["response_language"])
    draft._response_kind = draft._declared_response_kind = raw["response_kind"]
    draft._citation_snapshots = catalog.snapshots
    draft._provider_output_text = getattr(value, "_provider_output_text", None)
    draft._full_answer = {"body": body, "groups": groups, "catalog": catalog}
    return draft


def render_full_answer(draft: GroundedDraft, checked: Sequence[Any]) -> Any:
    """引用マーカーだけを既存の根拠行へ変換する。本文の主張はそのまま返す。"""
    from rag_engine.generation import grounded

    full = draft._full_answer
    assert full is not None
    entries = {entry.draft_index: entry for entry in checked if entry.span is not None}
    lines: list[dict[str, Any]] = []

    body = cast(str, full["body"])
    catalog = cast(SentUnitCatalog, full["catalog"])
    marker = citation_marker(catalog)
    if marker is None or not marker.search(body):
        return grounded.RenderedAnswer(body, [])
    run_pattern = _marker_run(marker)
    output: list[str] = []
    pending: list[Any] = []

    def append_references() -> None:
        for entry in pending:
            output.append(grounded._cited_line(entry))
            lines.append(grounded.citation_line_ref([entry]))
        pending.clear()

    in_table = False
    for line in body.splitlines():
        table_line = line.lstrip().startswith("|")
        if in_table and not table_line:
            append_references()
        in_table = table_line

        def replace_marker(match: re.Match[str]) -> str:
            key = ",".join(_cited_labels(match.group(0), marker, catalog))
            pending.extend(entries[index] for index in full["groups"].get(key, ()) if index in entries)
            return ""

        # 表の行や文末の句読点を途中で割らず、原文参照だけを行・表の後ろへ出す。
        displayed = run_pattern.sub(replace_marker, line)
        output.append(displayed)
        if displayed.strip().startswith(grounded.answer_labels().citation_prefix):
            # 本文に書かれた出典の見た目は、選択した ID に結ばない。後続の正式な引用番号だけを保つ。
            lines.append({})
        if not table_line:
            append_references()
    append_references()
    return grounded.RenderedAnswer("\n".join(output), lines)
