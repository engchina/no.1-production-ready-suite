"""未確定の属性と事実が混在する表で、質問との対応が不明な行だけを既存の再試行で確かめる。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ModelWrapValidatorHandler, ValidationInfo, create_model, model_validator

from rag_engine.generation.full_answer_wire import AnswerFact, AnswerTableBody, EvidenceGap
from rag_engine.resources.structured_output_retry import FINAL_ATTEMPT_CONTEXT_KEY


class RequestRowRepair:
    def __init__(self, question: str):
        self.question = question
        self.original: dict[str, Any] | None = None
        self.validated: BaseModel | None = None
        self.reviewed_body: dict[str, Any] | None = None
        self.candidates: dict[str, dict[str, Any]] = {}
        self.removed: list[str] = []

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
                        if isinstance(row, dict) and isinstance(key := row.get("label"), str) and key in self.candidates:
                            votes.setdefault(key, []).append(row.get("cells") == [[]])
                self.removed = [key for key, values in votes.items() if values == [True]]
                original = self.original
                self.original = None
                original_body = original["answer_body"]
                rows = [row for i, row in enumerate(original_body["rows"], 1) if str(i) not in self.removed]
                return handler({**original, "answer_body": {**original_body, "rows": rows}})
            result = handler(data)
            if final or not isinstance(body, dict) or not self.question:
                return result
            self.candidates = self._candidates(AnswerTableBody.model_validate(body))
            if self.candidates:
                self.original = data
                self.validated = result
                self.reviewed_body = body
                raise ValueError("質問との対応を確かめる行があります。")
            return result

        validators: dict[str, Any] = {"review_request_rows": model_validator(mode="wrap")(review)}
        return create_model(base.__name__, __base__=base, __validators__=validators)

    def step(self) -> str:
        return (
            "## Step 1: 指定された行と原質問の対応を確かめる\n"
            "- 下の JSON は原質問と候補行のデータです。各行には原質問から選んだ語句、短答で解ける問い、短答の順に示します。\n"
            "- まとめて指す語句は、原質問で具体的に挙げた属性に戻って判断してください。"
            "その属性を直接答える行と、答えに必要な条件・例外・確認場所は残します。別の量や判断だけを説明する行は除外します。\n"
            "- answer_body の rows に候補ごとに一行を返します。label は JSON の番号をそのまま使い、"
            "除外する行は cells を [[]]、残す行は元の短答を含む一セルにしてください。ほかの行は出力不要です。\n"
            + json.dumps({"question": self.question, "rows": self.candidates}, ensure_ascii=False)
        )
