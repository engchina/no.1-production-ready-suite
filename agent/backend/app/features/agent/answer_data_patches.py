"""不正な参照の範囲だけを修正し、それ以外の回答を保持する。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from .answer_data import DataReferenceError
from .answer_data_tables import table_spans

_MARKER = re.compile(
    r"\[\[(?:cell|quantity|table|cite):[^\r\n]*?(?:\]\]|(?=\r?$))"
    r"|【根拠:[^】\r\n]*】",
    re.MULTILINE,
)


class ReferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str
    replacement: str


class ReferencePatches(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patches: list[ReferencePatch]


@dataclass(frozen=True)
class PatchTarget:
    target_id: str
    start: int
    end: int
    text: str
    error: str


@dataclass(frozen=True)
class PatchPlan:
    answer: str
    targets: tuple[PatchTarget, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.answer.encode("utf-8")).hexdigest()

    def payload(self) -> dict[str, Any]:
        return {
            "targets": [
                {"target_id": t.target_id, "text": t.text, "error": t.error} for t in self.targets
            ],
        }

    def apply(self, response: ReferencePatches, *, current_answer: str) -> str:
        if hashlib.sha256(current_answer.encode("utf-8")).hexdigest() != self.sha256:
            raise DataReferenceError("参照修正の対象の回答が一致しません。")
        patches = {item.target_id: item.replacement for item in response.patches}
        if len(patches) != len(response.patches) or set(patches) != {
            target.target_id for target in self.targets
        }:
            raise DataReferenceError("参照修正の対象を重複・追加・省略できません。")
        cursor = 0
        parts: list[str] = []
        for target in self.targets:
            if (
                not 0 <= target.start < target.end <= len(self.answer)
                or target.start < cursor
                or self.answer[target.start : target.end] != target.text
            ):
                raise DataReferenceError("参照修正の対象の範囲が一致しません。")
            parts.extend((self.answer[cursor : target.start], patches[target.target_id]))
            cursor = target.end
        return "".join([*parts, self.answer[cursor:]])


def plan_patches(answer: str, validate: Callable[[str], Any]) -> PatchPlan:
    """既存の形式検証を各構文領域へ適用する。意味の判定や参照先の推測はしない。"""
    invalid: list[tuple[int, int, str]] = []
    tables = list(table_spans(answer))
    for start, end in tables:
        try:
            validate(answer[start:end])
        except DataReferenceError as exc:
            invalid.append((start, end, str(exc)))
    for marker in _MARKER.finditer(answer):
        start, end = marker.span()
        if any(left <= start < right for left, right in tables):
            continue
        # 配置の誤りもmarkerだけを編集する。正常な同じ行の本文は対象に含めない。
        if marker[0].startswith("[[table:"):
            left = answer.rfind("\n", 0, start) + 1
            newline = answer.find("\n", end)
            right = len(answer) if newline < 0 else newline
            if answer[left:right].strip() != marker[0]:
                invalid.append((start, end, "table参照は単独の行に置いてください。"))
                continue
        try:
            validate(answer[start:end])
        except DataReferenceError as exc:
            invalid.append((start, end, str(exc)))
    ranges: list[tuple[int, int, str]] = []
    for start, end, error in sorted(invalid):
        if ranges and start < ranges[-1][1]:
            left, right, previous = ranges[-1]
            ranges[-1] = (left, max(right, end), previous)
        else:
            ranges.append((start, end, error))
    if not ranges:
        # 対応表・集約上限など、編集対象を特定できない失敗を本文の再生成で隠さない。
        raise DataReferenceError("不正な参照の修正範囲を特定できませんでした。")
    return PatchPlan(
        answer,
        tuple(
            PatchTarget(f"P{number}", start, end, answer[start:end], error)
            for number, (start, end, error) in enumerate(ranges, start=1)
        ),
    )


PATCH_INSTRUCTIONS = (
    "## Step 1: 指定された範囲の参照形式を直す\n"
    "targetsのtextだけを、取得済みの参照を使う形式に直してください。"
    "表は投影参照、または値をquantity/cellで参照する箇条書きにできます。\n"
    "## Step 2: 修正箇所を返す\n"
    "全target_idをそのまま返し、各replacementに置換文を入れてください。"
)


def applied_result(original: Any, repair: Any, answer: str) -> Any:
    """修正JSONを履歴に足さず、元の回答メッセージを適用済み本文へ替える。

    モデルのraw response/traceは変更しない。SDKのcontinuation/checkpointは
    new_itemsと_model_input_itemsの回答を使うため、両方を同じ本文へ束縛する。
    """
    from agents.items import MessageOutputItem
    from openai.types.responses import ResponseOutputText

    result = copy(original)
    result.final_output = answer
    result.context_wrapper = repair.context_wrapper

    def replace_message(items: list[Any]) -> list[Any]:
        updated = list(items)
        for position in range(len(updated) - 1, -1, -1):
            item = updated[position]
            if isinstance(item, MessageOutputItem):
                message = item.raw_item.model_copy(
                    update={
                        "content": [
                            ResponseOutputText(text=answer, annotations=[], type="output_text")
                        ]
                    }
                )
                updated[position] = copy(item)
                updated[position].raw_item = message
                return updated
        raise DataReferenceError("適用済み回答を保存するメッセージがありません。")

    result.new_items = replace_message(original.new_items)
    model_items = getattr(original, "_model_input_items", None)
    if isinstance(model_items, list):
        result._model_input_items = (
            replace_message(model_items) if model_items else list(result.new_items)
        )
    processed = getattr(original, "_last_processed_response", None)
    if processed is not None:
        result._last_processed_response = copy(processed)
        result._last_processed_response.new_items = replace_message(processed.new_items)
    return result
