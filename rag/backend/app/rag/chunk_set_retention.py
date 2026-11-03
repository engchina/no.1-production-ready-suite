"""退役した版（chunk_set）の清掃（#4168）。切替（commit）と清掃（expire）を分ける。

取込ワーカーの定期の処理から呼ぶ。何度実行しても同じ結果になり、複数の worker が同時に動いても
レシピの行の lock で重ならない。ファイルの削除は文書の削除の後始末（``run_document_cleanup``）に
任せるので、保存先の失敗は記録に残って後からやり直される。
"""

from __future__ import annotations

import logging

from app.clients.chunk_set_retention_store import ChunkSetRetentionStore
from app.clients.document_cleanup_store import DocumentCleanupStore
from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import oracle_error_log_fields
from app.config import Settings, get_settings
from app.rag.document_cleanup import run_document_cleanup

logger = logging.getLogger(__name__)

# 1 回の清掃で扱うレシピの数の上限（残りは次の回。1 回の処理を長くしない）。
RETENTION_SWEEP_RECIPE_LIMIT = 20


async def expire_retired_chunk_sets(
    *,
    settings: Settings | None = None,
    store: ChunkSetRetentionStore | None = None,
    cleanup_store: DocumentCleanupStore | None = None,
    storage: ObjectStorageClient | None = None,
    limit: int = RETENTION_SWEEP_RECIPE_LIMIT,
) -> int:
    """保持を超えた版を消し、消した版の数を返す。1 つのレシピの失敗で残りを止めない。"""
    resolved = settings or get_settings()
    retention_days = resolved.rag_chunk_set_retention_days
    retained_versions = resolved.rag_chunk_set_retained_versions
    resolved_store = store or ChunkSetRetentionStore()
    recipe_ids = await resolved_store.due_recipe_ids(
        retention_days=retention_days, retained_versions=retained_versions, limit=limit
    )
    expired = 0
    for recipe_id in recipe_ids:
        try:
            result = await resolved_store.expire_recipe(
                recipe_id,
                retention_days=retention_days,
                retained_versions=retained_versions,
                canonical_prefix=resolved.rag_canonical_artifact_prefix,
            )
        except Exception as exc:  # noqa: BLE001 - 次の回にやり直す
            logger.warning(
                "chunk_set_retention_recipe_failed",
                extra={"recipe_id": recipe_id, **oracle_error_log_fields(exc)},
            )
            continue
        expired += len(result.chunk_set_ids)
        if result.chunk_set_ids:
            logger.info(
                "chunk_set_retention_expired",
                extra={
                    "recipe_id": recipe_id,
                    "chunk_set_count": len(result.chunk_set_ids),
                    "file_count": len(result.orphan_paths),
                },
            )
        if result.cleanup is not None:
            # 失敗は記録に残るので、ここでは止めない（定期の後始末がやり直す）。
            await run_document_cleanup(
                cleanup_store or DocumentCleanupStore(),
                storage or ObjectStorageClient(),
                result.cleanup,
            )
    return expired
