"""候補処理の実入出力。短い概要とは分け、本文記録の保存口だけへ渡す。"""

from collections.abc import Sequence
from typing import Any

from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
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


def context_snapshot(context: AnswerContext, **fields: Any) -> dict[str, Any]:
    """工程へ渡した文脈と親子・候補の関係を、記録時に写す。採否理由は推測しない。"""
    return {
        **fields,
        "根拠レコード": candidate_snapshot(context.records),
        "文脈テキスト": context.text,
        "文脈状態": context.status,
        "不足理由": context.insufficient_reason,
        "優先ID": list(context.preferred_child_ids),
        "検索候補": candidate_snapshot(context.ranked_candidates),
        "後回し候補": candidate_snapshot(context.deferred_records),
        "展開用候補": candidate_snapshot(context.expansion_records),
        "文書選択": dict(context.document_selection),
        "根拠の親子関係": [
            {
                "親": candidate_snapshot([parent.record]),
                "role": parent.role,
                "reason": parent.reason,
                "子": [{"根拠": candidate_snapshot([child.record]), "role": child.role} for child in parent.children],
            }
            for parent in context.evidence_tree
        ],
    }
