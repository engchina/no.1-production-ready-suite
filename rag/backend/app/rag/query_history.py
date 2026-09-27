"""質問履歴の記録と、よく聞かれる質問の候補(rag_poc の docrag.knowledge.query_history)。

rag_poc は JSONL へ追記したが、rag では業務ビュー単位で `rag_query_history` に保存する。
候補の規則(保持期間・最小回数・類似度・分類条件・除外リスト)は rag_poc の関数をそのまま使う。
記録するかどうかは global の設定で、既定は無効(質問の本文を保存するため)。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from docrag.knowledge.query_history import (
    QueryHistoryRecord,
    QueryHistorySuggestion,
    suggest_query_history_questions,
)

# ponytail: 正規化・除外の判定は rag_poc の非公開関数を使う(同じ規則で記録と候補を揃えるため)。
from docrag.knowledge.query_history import (
    _clean_question as clean_question,
)
from docrag.knowledge.query_history import (
    _matches_blocklist as matches_blocklist,
)
from docrag.knowledge.query_history import (
    _normalize_key as normalize_question,
)

from app.config import Settings

logger = logging.getLogger(__name__)

_CLASSIFICATION_KEYS = ("large_category", "middle_category", "small_category")


class QueryHistoryStore(Protocol):
    async def append_query_history(self, record: Mapping[str, object]) -> None: ...

    async def purge_query_history(self, retention_days: int) -> int: ...

    async def list_query_history(
        self, business_view_id: str, *, retention_days: int, limit: int = 5000
    ) -> list[dict[str, object]]: ...


def classification_from_filters(filters: Mapping[str, str]) -> dict[str, str]:
    return {key: filters[key] for key in _CLASSIFICATION_KEYS if filters.get(key)}


async def record_query_history(
    store: QueryHistoryStore,
    settings: Settings,
    *,
    business_view_id: str | None,
    question: str,
    surface: str,
    filters: Mapping[str, str],
) -> None:
    """回答に成功した質問を記録する。無効・業務ビューなし・除外語は記録しない。失敗しても例外にしない。"""
    if not settings.rag_query_history_enabled or not business_view_id:
        return
    cleaned = clean_question(question)
    if not cleaned or matches_blocklist(cleaned, settings.rag_query_history_blocklist):
        return
    try:
        await store.append_query_history(
            {
                "business_view_id": business_view_id,
                "surface": surface,
                "question": cleaned,
                "normalized_question": normalize_question(cleaned),
                "classification_filter": classification_from_filters(filters),
            }
        )
        if settings.rag_query_history_retention_days > 0:
            await store.purge_query_history(settings.rag_query_history_retention_days)
    except Exception:  # noqa: BLE001 - 履歴は補助。回答の返却を止めない。
        logger.warning("query history record failed", exc_info=True)


async def query_history_suggestions(
    store: QueryHistoryStore,
    settings: Settings,
    *,
    business_view_id: str,
    question: str,
    classification: Mapping[str, str],
) -> list[QueryHistorySuggestion]:
    """よく聞かれる質問を、入力中の質問との類似度順に返す。無効なら空。"""
    if not settings.rag_query_history_enabled:
        return []
    rows = await store.list_query_history(
        business_view_id, retention_days=settings.rag_query_history_retention_days
    )
    records = [_record(row) for row in rows]
    return suggest_query_history_questions(
        question,
        records,
        classification_filter=dict(classification),
        min_count=settings.rag_query_history_min_count,
        limit=settings.rag_query_history_suggestion_limit,
        blocklist=settings.rag_query_history_blocklist,
    )


def _record(row: Mapping[str, Any]) -> QueryHistoryRecord:
    raw_created_at = row.get("created_at")
    created_at = (
        raw_created_at.isoformat() if isinstance(raw_created_at, datetime) else str(raw_created_at)
    )
    classification = row.get("classification_filter")
    query_id = str(row.get("query_id") or "")
    return QueryHistoryRecord(
        query_id=query_id,
        # rag_poc は run_id の種類数を最小ユニーク数の判定に使う。rag の履歴は 1 件ずつ数える。
        run_id=query_id,
        created_at=created_at,
        question=str(row.get("question") or ""),
        normalized_question=str(row.get("normalized_question") or ""),
        classification_filter=dict(classification) if isinstance(classification, Mapping) else None,
    )
