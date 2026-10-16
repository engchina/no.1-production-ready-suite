"""質問文から、後続処理が実際に使う情報だけを読み取る。

以前は回答方針（executor）・タスク・質問タイプも判定して表示していたが、検索戦略にも
回答フローにも分岐がなく、表示のためだけのラベルになっていた。後続が使うのは、
原画像を回答へ添付するかどうかと、質問理解（検索の絞り込み・補助検索文）だけである。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from rag_engine.retrieval.evidence_cues import evidence_cues
from rag_engine.retrieval.inquiry_conditions import InquiryConditionParse, parse_inquiry_conditions

# 原画像を回答へ添付する語は言語資源（evidence_cues.visual_context_words。#2181）。


@dataclass(frozen=True)
class QuestionPlan:
    """画像添付の要否と質問理解を保持します。"""

    original_question: str
    requires_visual_context: bool = False
    inquiry_conditions: InquiryConditionParse | None = None

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {"original_question": self.original_question, "requires_visual_context": self.requires_visual_context}


def plan_question(question: str) -> QuestionPlan:
    """質問文を解析します。日本語の問い合わせ規則を持たない profile では質問理解だけを返します。"""
    from rag_engine.resources.runtime import current_profile

    text = unicodedata.normalize("NFKC", str(question or "")).strip()
    conditions = parse_inquiry_conditions(text)
    visual = current_profile().japanese_inquiry_rules and any(word in text for word in evidence_cues().words["visual_context_words"])
    return QuestionPlan(text, requires_visual_context=bool(visual), inquiry_conditions=conditions)
