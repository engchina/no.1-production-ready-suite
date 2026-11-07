"""退役した版（chunk_set）の清掃の Oracle の読み書き（#4168）。

切替は旧版に退役の時刻を付けるだけで、行は消さない。ここでは保持を超えた版を、レシピの行を
lock した transaction の中で ``_delete_chunk_set_outputs`` で消す。何度実行しても同じ結果になる。

消さない版:

- 新しい順に ``retained_versions`` 番目までで、退役から ``retention_days`` 日を超えていない版。
- 同じレシピに QUEUED / RUNNING の取込 job がある版（lock の中で確かめる。実行中の job が読む）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import uuid4

from app.clients.document_cleanup_store import (
    DocumentCleanupTargets,
    DocumentCleanupTask,
    insert_document_cleanup_record,
)
from app.clients.oracle import (
    OracleClient,
    OracleConnectionProtocol,
    _delete_chunk_set_outputs,
    _fetch_all,
    _fetch_one,
    _optional_str,
)
from app.rag.request_context import current_audit_request_context

# 保持を超えた版だけを選ぶ条件（``retained`` は退役の新しい順の番号の入った内側の問い合わせ）。
_DUE_SQL = """(
    retired_rank > :retained_versions
    OR retired_at <= SYSTIMESTAMP - NUMTODSINTERVAL(:retention_days, 'DAY')
)"""
# 版が解析したファイル。正本は抽出の系譜（#4166）。
_DERIVED_PATH_SQL = (
    "JSON_VALUE(e.extraction_json, '$.parser_artifacts.source_derivation.derived_object_path')"
)


@dataclass(frozen=True, slots=True)
class ExpiredRecipeVersions:
    """1 つのレシピの清掃の結果。"""

    recipe_id: str
    chunk_set_ids: tuple[str, ...] = ()
    cleanup: DocumentCleanupTask | None = None
    # lock を取れなかった・実行中の job がある（次の回に回す）。
    skipped: bool = False
    orphan_paths: tuple[str, ...] = field(default=())


class ChunkSetRetentionStore:
    """清掃の読み書き（OracleClient の接続と transaction を使う）。"""

    def __init__(self, oracle: OracleClient | None = None) -> None:
        self._oracle = oracle or OracleClient()

    async def due_recipe_ids(
        self, *, retention_days: int, retained_versions: int, limit: int
    ) -> list[str]:
        """保持を超えた版を持ち、実行中の取込 job の無いレシピ。tenant では絞らない。"""
        rows = await self._oracle._fetch_all(
            f"""
            SELECT DISTINCT retained.recipe_id
            FROM (
                SELECT cs.recipe_id, cs.retired_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY cs.recipe_id ORDER BY cs.retired_at DESC, cs.chunk_set_id
                    ) AS retired_rank
                FROM rag_chunk_sets cs
                WHERE cs.retired_at IS NOT NULL AND cs.is_active = 0
            ) retained
            WHERE {_DUE_SQL}
              AND NOT EXISTS (
                  SELECT 1 FROM rag_ingestion_jobs j
                  WHERE j.recipe_id = retained.recipe_id AND j.status IN ('QUEUED', 'RUNNING')
              )
            FETCH FIRST :limit ROWS ONLY
            """,
            {
                "retention_days": int(retention_days),
                "retained_versions": int(retained_versions),
                "limit": max(1, int(limit)),
            },
        )
        return [str(row["recipe_id"]) for row in rows]

    async def expire_recipe(
        self,
        recipe_id: str,
        *,
        retention_days: int,
        retained_versions: int,
        canonical_prefix: str,
    ) -> ExpiredRecipeVersions:
        """レシピの保持を超えた版を消し、どこからも参照されなくなったファイルの後始末を記録する。

        ファイルの削除は commit の後に行う（DB の参照が残ったままファイルだけ消えないように）。
        記録を同じ transaction に残すので、途中で落ちても定期の後始末がやり直す。
        """
        context = current_audit_request_context()

        def operation(connection: OracleConnectionProtocol) -> ExpiredRecipeVersions:
            # 切替と同じ行の lock。切替の途中なら次の回に回す（切替を待たせない）。
            recipe = _fetch_one(
                connection,
                """
                SELECT document_id FROM rag_document_recipes
                WHERE recipe_id = :recipe_id
                FOR UPDATE SKIP LOCKED
                """,
                {"recipe_id": recipe_id},
            )
            if recipe is None:
                return ExpiredRecipeVersions(recipe_id=recipe_id, skipped=True)
            # job の投入も同じレシピの行を lock して確かめるので、この確認と清掃の間に入らない。
            if _fetch_one(
                connection,
                """
                SELECT 1 AS running FROM rag_ingestion_jobs
                WHERE recipe_id = :recipe_id AND status IN ('QUEUED', 'RUNNING')
                FETCH FIRST 1 ROWS ONLY
                """,
                {"recipe_id": recipe_id},
            ):
                return ExpiredRecipeVersions(recipe_id=recipe_id, skipped=True)
            document_id = str(recipe["document_id"])
            due = _fetch_all(
                connection,
                f"""
                SELECT chunk_set_id, extraction_recipe_id
                FROM (
                    SELECT cs.chunk_set_id, cs.extraction_recipe_id, cs.retired_at,
                        ROW_NUMBER() OVER (
                            ORDER BY cs.retired_at DESC, cs.chunk_set_id
                        ) AS retired_rank
                    FROM rag_chunk_sets cs
                    WHERE cs.recipe_id = :recipe_id
                      AND cs.retired_at IS NOT NULL
                      AND cs.is_active = 0
                )
                WHERE {_DUE_SQL}
                """,
                {
                    "recipe_id": recipe_id,
                    "retention_days": int(retention_days),
                    "retained_versions": int(retained_versions),
                },
            )
            if not due:
                return ExpiredRecipeVersions(recipe_id=recipe_id)
            paths = _derived_paths(connection, document_id, due)
            _delete_chunk_set_outputs(connection, document_id=document_id, chunk_sets=due)
            orphans = _unreferenced_paths(
                connection,
                document_id,
                paths,
                prefix=f"{canonical_prefix.strip('/')}/{document_id}/",
            )
            task: DocumentCleanupTask | None = None
            if orphans:
                file_row = _fetch_one(
                    connection,
                    "SELECT file_name FROM rag_documents WHERE document_id = :document_id",
                    {"document_id": document_id},
                )
                task = DocumentCleanupTask(
                    cleanup_id=uuid4().hex,
                    document_id=document_id,
                    file_name=_optional_str((file_row or {}).get("file_name")) or document_id,
                    targets=DocumentCleanupTargets(artifact_uris=tuple(orphans)),
                )
                insert_document_cleanup_record(connection, task, context=context)
            return ExpiredRecipeVersions(
                recipe_id=recipe_id,
                chunk_set_ids=tuple(str(row["chunk_set_id"]) for row in due),
                cleanup=task,
                orphan_paths=tuple(orphans),
            )

        return await self._oracle._run_transaction(operation)


def _derived_paths(
    connection: OracleConnectionProtocol,
    document_id: str,
    chunk_sets: Sequence[Mapping[str, object]],
) -> list[str]:
    """消す版の抽出が解析したファイルの参照（消す前に読む）。"""
    paths: set[str] = set()
    for extraction_id in {
        str(row["extraction_recipe_id"]) for row in chunk_sets if row.get("extraction_recipe_id")
    }:
        row = _fetch_one(
            connection,
            f"""
            SELECT {_DERIVED_PATH_SQL} AS derived_path
            FROM rag_document_extractions e
            WHERE e.document_id = :document_id AND e.extraction_recipe_id = :extraction_recipe_id
            """,
            {"document_id": document_id, "extraction_recipe_id": extraction_id},
        )
        if row and row.get("derived_path"):
            paths.add(str(row["derived_path"]))
    return sorted(paths)


def _unreferenced_paths(
    connection: OracleConnectionProtocol,
    document_id: str,
    paths: Sequence[str],
    *,
    prefix: str,
) -> list[str]:
    """文書の正規化原本の置き場の中で、残った行のどこからも参照されないファイルだけを返す。

    抽出は別の版からも指されていれば残るので、残った抽出の系譜も確かめる。
    """
    orphans: list[str] = []
    for path in paths:
        if prefix not in path:
            continue
        referenced = _fetch_one(
            connection,
            f"""
            SELECT 1 AS referenced FROM dual
            WHERE EXISTS (
                    SELECT 1 FROM rag_document_extractions e
                    WHERE e.document_id = :document_id AND {_DERIVED_PATH_SQL} = :path
                )
               OR EXISTS (
                    SELECT 1 FROM rag_document_recipes r
                    WHERE r.document_id = :document_id
                      AND JSON_VALUE(r.preprocess_artifact, '$.object_storage_path') = :path
                )
               OR EXISTS (
                    SELECT 1 FROM rag_documents d
                    WHERE d.document_id = :document_id
                      AND d.object_storage_path = :path
                )
            """,
            {"document_id": document_id, "path": path},
        )
        if referenced is None:
            orphans.append(path)
    return orphans
