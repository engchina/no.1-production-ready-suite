"""表の行と数値の注記の採否だけを既存の再試行で確かめ、元の値と引用を保つ。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ModelWrapValidatorHandler, ValidationInfo, create_model, model_validator
from pydantic.functional_validators import ModelWrapValidator

from rag_engine.generation.full_answer_wire import (
    _ROW_NOTE,
    AnswerFact,
    AnswerTableBody,
    EvidenceGap,
    _value_under_row_note,
    number_list_note,
)
from rag_engine.resources.structured_output_retry import FINAL_ATTEMPT_CONTEXT_KEY


@dataclass(frozen=True)
class NumberNote:
    position: tuple[int, int, int]
    label: str
    text: str
    note: str
    values: str
    evidence: str


def _number_notes(body: AnswerTableBody, question: str) -> dict[str, NumberNote]:
    notes: dict[str, NumberNote] = {}
    if len(body.headers) < 2:
        return notes
    for i, row in enumerate(body.rows):
        scope = _ROW_NOTE.search(row.label.rstrip())
        if not scope:
            continue
        for j, cell in enumerate(row.cells):
            if not isinstance(cell, list):
                continue
            for k, fact in enumerate(cell):
                if isinstance(fact, AnswerFact):
                    text = _value_under_row_note(fact.requested_answer(question), (scope[1] or scope[2]).strip())
                    if parts := number_list_note(text):
                        key = f"{i + 1}.{j + 1}.{k + 1}"
                        notes[key] = NumberNote((i, j, k), row.label, text, *parts, fact.text)
    return notes


class RequestRowRepair:
    def __init__(self, question: str):
        self.question = question
        self.original: dict[str, Any] | None = None
        self.validated: BaseModel | None = None
        self.reviewed_body: dict[str, Any] | None = None
        self.candidates: dict[str, dict[str, Any]] = {}
        self.removed: list[str] = []
        self.notes: dict[str, NumberNote] = {}
        self.removed_notes: list[str] = []

    def _candidates(self, body: AnswerTableBody) -> dict[str, dict[str, Any]]:
        if len(body.headers) < 2 or not any(isinstance(cell, EvidenceGap) for row in body.rows for cell in row.cells):
            return {}
        omitted, _ = body.shared_scope_sources(self.question)
        candidates = {}
        for i, row in enumerate(body.rows, 1):
            if i - 1 in omitted or any(isinstance(cell, EvidenceGap) for cell in row.cells):
                continue
            facts = [fact for cell in row.cells if isinstance(cell, list) for fact in cell]
            if not facts or any(not isinstance(fact, AnswerFact) for fact in facts):
                continue
            linked = [fact for fact in facts if isinstance(fact, AnswerFact) and fact.requested_phrase]
            if linked and not any(
                (phrase := f.requested_phrase) and (phrase in row.label or phrase in f.answers_which_question) for f in linked
            ):
                candidates[str(i)] = {
                    "label": row.label,
                    "answers": [[f.requested_phrase, f.answers_which_question, f.short_answer] for f in linked],
                }
        return candidates

    def model(self, base: type[BaseModel]) -> type[BaseModel]:
        def review(cls: type[BaseModel], data: Any, handler: ModelWrapValidatorHandler[BaseModel], info: ValidationInfo) -> BaseModel:
            final = (info.context or {}).get(FINAL_ATTEMPT_CONTEXT_KEY)
            if final is None or not isinstance(data, dict):
                return handler(data)
            body = data.get("answer_body")
            if self.original is not None:
                # 除外を明示した番号だけを採用する。他のセルや出典を生成し直した文で上書きしない。
                votes: dict[str, list[bool]] = {}
                if isinstance(body, dict) and isinstance(body.get("rows"), list):
                    for row in body["rows"]:
                        if (
                            isinstance(row, dict)
                            and isinstance(key := row.get("label"), str)
                            and (key in self.candidates or key in self.notes)
                        ):
                            votes.setdefault(key, []).append(row.get("cells") == [[]])
                self.removed = [key for key in self.candidates if votes.get(key) == [True]]
                self.removed_notes = [key for key in self.notes if votes.get(key) == [True]]
                original = self.original
                self.original = None
                original_body = deepcopy(original["answer_body"]) if self.removed_notes else original["answer_body"]
                for key in self.removed_notes:
                    note = self.notes[key]
                    i, j, k = note.position
                    original_body["rows"][i]["cells"][j][k]["short_answer"] = note.values
                rows = [row for i, row in enumerate(original_body["rows"], 1) if str(i) not in self.removed]
                return handler({**original, "answer_body": {**original_body, "rows": rows}})
            result = handler(data)
            if final or not isinstance(body, dict) or not self.question:
                return result
            table = AnswerTableBody.model_validate(body)
            self.candidates = self._candidates(table)
            self.notes = _number_notes(table, self.question)
            if self.candidates or self.notes:
                self.original = data
                self.validated = result
                self.reviewed_body = body
                raise ValueError("採否を確かめる行または注記があります。")
            return result

        checked: ModelWrapValidator[BaseModel] = review
        validators: dict[str, Any] = {"review_request_rows": model_validator(mode="wrap")(checked)}
        return create_model(base.__name__, __base__=base, __validators__=validators)

    def step(self) -> str:
        return (
            "## Step 1: 指定された行と数値の注記の採否を返す\n"
            "- 下の JSON は判定対象のデータです。rows の各短答には原質問の語句、事実で解ける問い、短答を示します。"
            "まとめて指す語句は具体的な参照先に戻り、別の判断だけの行を除外します。"
            "ほかの行名に既に示した範囲だけを繰り返す行も、独立して尋ねられていなければ除外します。必要な条件・例外・確認場所は残します。\n"
            "- notes では指定された注記だけを判定します。行名か出典で分かる由来の反復は除外し、"
            "値の意味が変わる単位・条件・例外や、判断のつかない注記は残してください。元の数値と他のセルは保存されています。\n"
            "- answer_body の rows に各番号の判定を一行ずつ返してください。label は JSON の番号をそのまま使い、"
            "除外は cells を [[]]、保持は元の短答を含む一セルにします。\n"
            + json.dumps(
                {
                    "question": self.question,
                    "labels": [row["label"] for row in (self.reviewed_body or {}).get("rows", [])],
                    "rows": self.candidates,
                    "notes": {
                        key: {"label": n.label, "answer": n.text, "note": n.note, "evidence": n.evidence} for key, n in self.notes.items()
                    },
                },
                ensure_ascii=False,
            )
        )
