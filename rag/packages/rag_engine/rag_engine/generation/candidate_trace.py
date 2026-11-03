"""候補処理の実入出力。短い概要とは分け、本文記録の保存口だけへ渡す。"""

from collections.abc import Sequence
from typing import Any

from rag_engine.generation.answer_models import AnswerRecord
from rag_engine.retrieval.candidate_budget import record_id


def candidate_snapshot(records: Sequence[AnswerRecord]) -> list[dict[str, Any]]:
    """渡された順序・原文と版の識別情報・実際の採点値を写す。選別理由は推測しない。"""
    return [
        {
            "ID": record_id(record),
            "文書ID": record.metadata.get("document_id"),
            "文書": record.source,
            "原文の版": record.source_run_id,
            "レシピID": record.metadata.get("recipe_id"),
            "Chunk集合ID": record.metadata.get("chunk_set_id"),
            "頁": record.page,
            "終了頁": record.page_end,
            "本文": record.text,
            "追加経路": record.metadata.get("retrieval_admission_stage", "fusion"),
            "採点": dict(record.metadata["rerank"]) if isinstance(record.metadata.get("rerank"), dict) else None,
        }
        for record in records
    ]
