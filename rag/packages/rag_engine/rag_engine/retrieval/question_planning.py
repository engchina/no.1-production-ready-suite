"""入力の正規化と原質問の保存。検索条件や種類を推定しない。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QuestionPlan:
    """正規化した原質問を保持します。"""

    original_question: str

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {"original_question": self.original_question}


def plan_question(question: str) -> QuestionPlan:
    """質問文の文字幅と外側の空白を正規化します。"""
    text = unicodedata.normalize("NFKC", str(question or "")).strip()
    return QuestionPlan(text)
