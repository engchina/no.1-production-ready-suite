"""完成した回答本文と引用位置を受け、本文の意味で公開を判定しない。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from types import GenericAlias
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, create_model

from rag_engine.generation.answer_envelope import AnswerOutcome
from rag_engine.generation.citation_unit_ids import BoundUnitRange, SentUnitCatalog
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem, ResponseLanguage

# モデルと表示の間だけの引用マーカー。自然言語の意味は解析しない。
CITATION_MARKER = re.compile(r"\[C([1-9][0-9]*)\]")


class FullAnswerWire(BaseModel):
    """本文はモデルが完成させ、種別は同じ呼び出しの付帯情報にする。"""

    model_config = ConfigDict(extra="forbid")
    _provider_output_text: str | None = PrivateAttr(default=None)
    _uses_unit_ids = True
    _uses_full_answer = True

    response_language: ResponseLanguage = Field(default_factory=ResponseLanguage)
    answer_body: str = Field(
        min_length=1,
        description=(
            "利用者へ返す完成した回答本文。必要に応じて Markdown を使う。"
            "引用する箇所に [C1] の形式の印を置く。番号は citations の順（1始まり）。"
        ),
    )
    response_kind: AnswerOutcome = Field(
        description="本文の回答種別。付帯情報であり、本文の正しさを保証しない。answered=回答、conditional=条件付き、needs_environment_data=実環境の確認が必要、needs_clarification=質問の確認が必要、insufficient_evidence=資料不足、needs_human=人の判断が必要。"
    )
    confidence: Literal["high", "medium", "low"] = Field(description="回答の確かさの自己申告。")


def full_answer_model(catalog: SentUnitCatalog) -> type[BaseModel]:
    """引用の選択肢は、この入力に実際に提示した原文単位だけ。"""
    fields: dict[str, Any] = {
        "citations": (
            GenericAlias(list, catalog.selection_model()),
            Field(default_factory=list, description="本文で引用する原文の単位。本文の [Cn] はこの配列の n 番目を指す。"),
        ),
    }
    return create_model("FullAnswerWire", __base__=FullAnswerWire, **fields)


def canonical_full_answer(value: BaseModel, catalog: SentUnitCatalog) -> GroundedDraft:
    """既存の保存形式へ引用を結ぶ。分類・引用数による本文の削除や書換えはしない。"""
    raw = value.model_dump()
    body = str(raw["answer_body"])
    selections = raw.get("citations", [])
    used = set(int(match.group(1)) for match in CITATION_MARKER.finditer(body))
    bindings: list[BoundUnitRange] = []
    groups: dict[int, tuple[int, ...]] = {}
    for number, selection in enumerate(selections, 1):
        if number not in used:
            continue
        selected = catalog.bind(selection["unit_ids"])
        groups[number] = tuple(range(len(bindings), len(bindings) + len(selected)))
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
    draft._full_answer = {"body": body, "groups": groups}
    return draft


def render_full_answer(draft: GroundedDraft, checked: Sequence[Any]) -> Any:
    """引用マーカーだけを既存の根拠行へ変換する。本文の主張はそのまま返す。"""
    from rag_engine.generation import grounded

    full = draft._full_answer
    assert full is not None
    entries = {entry.draft_index: entry for entry in checked if entry.span is not None}
    lines: list[dict[str, Any]] = []

    body = cast(str, full["body"])
    if not CITATION_MARKER.search(body):
        return grounded.RenderedAnswer(body, [])
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
            pending.extend(entries[index] for index in full["groups"].get(int(match.group(1)), ()) if index in entries)
            return ""

        # 表の行や文末の句読点を途中で割らず、原文参照だけを行・表の後ろへ出す。
        output.append(CITATION_MARKER.sub(replace_marker, line))
        if not table_line:
            append_references()
    append_references()
    return grounded.RenderedAnswer("\n".join(output), lines)
