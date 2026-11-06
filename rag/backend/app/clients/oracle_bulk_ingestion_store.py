"""文書の一括の投入(#2674)の Oracle の保存先。

1 文書ずつの往復をせず、文書の状態は IN の条件(最大 ``_IN_CHUNK`` 件)でまとめて読み、job は
batch ごとの 1 transaction で ``executemany`` で登録する。新しい表・列は持たない(既存の
``rag_documents`` / ``rag_document_recipes`` / ``rag_ingestion_jobs`` と、前回の取込の出力の
有無を見るレシピの層の ``rag_document_extractions`` / ``rag_chunk_sets`` だけを使う)。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import uuid4

from app.clients.oracle import (
    INGESTION_JOB_INSERT_SQL,
    OracleClient,
    OracleConnectionProtocol,
    _executemany,
    _fetch_all,
    _file_status,
    _ingestion_job_binds,
    _json_input_sizes,
    _json_loads,
    _optional_int,
    _optional_str,
    _oracle_access_predicate_sql,
    _oracle_in_predicate,
    _render_sql,
    _with_tenant_bind,
    default_document_recipe_id,
)
from app.rag.bulk_ingestion import (
    BulkIngestionDocumentState,
    BulkInsertResult,
    BulkJobDraft,
)
from app.schemas.document import (
    FileStatus,
    IngestionJob,
    IngestionJobPhase,
    IngestionJobStatus,
)

# 1 つの SQL の IN に入れる ID の数(Oracle の IN の上限 1000 より小さく)。
_IN_CHUNK = 500

_ACTIVE_JOB_STATUSES_SQL = "('QUEUED', 'RUNNING')"


def _chunks(values: Sequence[str], size: int = _IN_CHUNK) -> list[Sequence[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


class OracleBulkIngestionStore:
    """一括の投入の読み書き(OracleClient の接続と transaction を使う)。"""

    def __init__(self, oracle: OracleClient | None = None) -> None:
        self._oracle = oracle or OracleClient()

    async def load_states(
        self, document_ids: Sequence[str]
    ) -> Mapping[str, BulkIngestionDocumentState]:
        """利用者から見える文書の状態を返す(見えない・無い文書は含めない)。"""
        unique_ids = list(dict.fromkeys(document_ids))
        states: dict[str, BulkIngestionDocumentState] = {}
        for chunk in _chunks(unique_ids):
            documents = await self._oracle._fetch_all(*_document_state_query(chunk))
            if not documents:
                continue
            visible_ids = [str(row["document_id"]) for row in documents]
            jobs = await self._oracle._fetch_all(*_job_state_query(visible_ids))
            history: set[str] = set()
            active: set[str] = set()
            for job in jobs:
                document_id = str(job["document_id"])
                history.add(document_id)
                recipe_id = _optional_str(job.get("recipe_id"))
                if int(str(job.get("active") or 0)) == 1 and recipe_id in {
                    None,
                    default_document_recipe_id(document_id),
                }:
                    active.add(document_id)
            for row in documents:
                state = _document_state(row, history=history, active=active)
                states[state.document_id] = state
        return states

    async def insert_queued_jobs(
        self,
        drafts: Sequence[BulkJobDraft],
        *,
        queued_at: datetime,
        max_attempts: int,
    ) -> BulkInsertResult:
        """1 つの transaction で既存レシピ1を lock して確かめ直し、QUEUED の job を登録する。"""
        if not drafts:
            return BulkInsertResult(created={})
        if len(drafts) > _IN_CHUNK:
            raise ValueError(f"1 回に登録できる job は {_IN_CHUNK} 件までです。")

        def operation(connection: OracleConnectionProtocol) -> BulkInsertResult:
            return _insert_queued_jobs(
                connection, drafts, queued_at=queued_at, max_attempts=max_attempts
            )

        return await self._oracle._run_transaction(operation)

    async def list_document_ids(
        self,
        *,
        status: FileStatus | None,
        query: str | None,
        knowledge_base_id: str | None,
        limit: int,
    ) -> tuple[list[str], int]:
        """一覧と同じ条件・順(新しい順)の文書の ID を最大 ``limit`` 件と、条件に一致する件数。"""
        return await self._oracle.list_document_ids(
            status=status, query=query, knowledge_base_id=knowledge_base_id, limit=limit
        )


def _document_state_query(document_ids: Sequence[str]) -> tuple[str, dict[str, object]]:
    in_sql, in_binds = _oracle_in_predicate("d.document_id", "bulk_document_id", document_ids)
    statement = _render_sql(
        """
        SELECT
            d.document_id,
            d.file_name,
            d.status AS document_status,
            d.content_type,
            d.file_size_bytes,
            d.content_sha256,
            d.duplicate_of_document_id,
            CASE WHEN d.object_storage_path IS NULL THEN 0 ELSE 1 END AS has_source,
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM rag_document_recipes o
                    WHERE o.document_id = d.document_id AND o.preprocess_artifact IS NOT NULL
                )
                OR EXISTS (
                    SELECT 1 FROM rag_document_extractions e WHERE e.document_id = d.document_id
                )
                OR EXISTS (
                    SELECT 1 FROM rag_chunk_sets cs WHERE cs.document_id = d.document_id
                ) THEN 1
                ELSE 0
            END AS has_outputs,
            (
                SELECT MIN(s.status) KEEP (DENSE_RANK FIRST ORDER BY s.slot_no)
                FROM rag_document_recipes s
                WHERE s.document_id = d.document_id
            ) AS list_status,
            r.recipe_id AS default_recipe_id,
            r.processing_config AS recipe_processing_config
        FROM rag_documents d
        LEFT JOIN rag_document_recipes r
          ON r.document_id = d.document_id
         AND r.slot_no = 1
        WHERE {in_sql}
          AND {access_predicate}
        """,
        in_sql=in_sql,
        access_predicate=_oracle_access_predicate_sql(alias="d"),
    )
    return statement, _with_tenant_bind(in_binds)


def _job_state_query(document_ids: Sequence[str]) -> tuple[str, dict[str, object]]:
    """文書・レシピごとの job の有無と、待機中・実行中の job があるか。"""
    in_sql, in_binds = _oracle_in_predicate("j.document_id", "bulk_job_document_id", document_ids)
    statement = _render_sql(
        f"""
        SELECT
            j.document_id,
            j.recipe_id,
            MAX(CASE WHEN j.status IN {_ACTIVE_JOB_STATUSES_SQL} THEN 1 ELSE 0 END) AS active
        FROM rag_ingestion_jobs j
        WHERE {{in_sql}}
        GROUP BY j.document_id, j.recipe_id
        """,
        in_sql=in_sql,
    )
    return statement, in_binds


def _document_state(
    row: Mapping[str, object], *, history: set[str], active: set[str]
) -> BulkIngestionDocumentState:
    document_id = str(row["document_id"])
    document_status = _file_status(row.get("document_status") or FileStatus.UPLOADED.value)
    has_recipe = row.get("default_recipe_id") is not None
    # 一覧の状態(最も小さい slot のレシピの状態。レシピの行が無ければ文書の状態。#2012)。
    list_status = row.get("list_status")
    status = _file_status(list_status) if list_status else document_status
    raw_config = row.get("recipe_processing_config") if has_recipe else None
    return BulkIngestionDocumentState(
        document_id=document_id,
        file_name=str(row.get("file_name") or ""),
        status=status,
        document_status=document_status,
        has_source=int(str(row.get("has_source") or 0)) == 1,
        content_type=_optional_str(row.get("content_type")),
        file_size_bytes=_optional_int(row.get("file_size_bytes")),
        content_sha256=_optional_str(row.get("content_sha256")),
        duplicate_of_document_id=_optional_str(row.get("duplicate_of_document_id")),
        processing_config=_json_loads(raw_config),
        has_default_recipe=has_recipe,
        has_job_history=document_id in history,
        has_active_job=document_id in active,
        has_outputs=int(str(row.get("has_outputs") or 0)) == 1,
    )


def _insert_queued_jobs(
    connection: OracleConnectionProtocol,
    drafts: Sequence[BulkJobDraft],
    *,
    queued_at: datetime,
    max_attempts: int,
) -> BulkInsertResult:
    document_ids = [draft.document_id for draft in drafts]
    # 1. 正式レシピ1の行を lock する(1 件の投入・レシピの編集と同じ行の lock で直列にする)。
    in_sql, in_binds = _oracle_in_predicate("r.document_id", "bulk_lock_document_id", document_ids)
    locked = _fetch_all(
        connection,
        _render_sql(
            """
            SELECT r.document_id, r.recipe_id, r.config_revision
            FROM rag_document_recipes r
            JOIN rag_documents d ON d.document_id = r.document_id
            WHERE r.slot_no = 1
              AND {in_sql}
              AND {access_predicate}
            FOR UPDATE OF r.recipe_id
            """,
            in_sql=in_sql,
            access_predicate=_oracle_access_predicate_sql(alias="d"),
        ),
        _with_tenant_bind(in_binds),
    )
    recipes = {
        str(row["document_id"]): (str(row["recipe_id"]), int(str(row.get("config_revision") or 1)))
        for row in locked
    }
    # 2. lock の後に、待機中・実行中の job を確かめ直す(別の要求・1 件の投入が先に登録した)。
    active_rows: list[dict[str, object]] = []
    if recipes:
        active_sql, active_binds = _oracle_in_predicate(
            "document_id", "bulk_active_document_id", list(recipes)
        )
        active_rows = _fetch_all(
            connection,
            f"""
            SELECT document_id, recipe_id
            FROM rag_ingestion_jobs
            WHERE {active_sql}
              AND status IN {_ACTIVE_JOB_STATUSES_SQL}
            """,
            active_binds,
        )
    active = {
        str(row["document_id"])
        for row in active_rows
        if _optional_str(row.get("recipe_id")) in {None, recipes[str(row["document_id"])][0]}
    }
    # 3. まとめて登録する。
    jobs: list[IngestionJob] = []
    for draft in drafts:
        recipe = recipes.get(draft.document_id)
        if recipe is None or draft.document_id in active:
            continue
        recipe_id, revision = recipe
        jobs.append(
            IngestionJob(
                id=uuid4().hex,
                document_id=draft.document_id,
                recipe_id=recipe_id,
                recipe_revision=revision,
                status=IngestionJobStatus.QUEUED,
                phase=IngestionJobPhase.PREPROCESS,
                parser_profile=draft.parser_profile,
                quality_warnings=list(draft.quality_warnings),
                max_attempts=max_attempts,
                queued_at=queued_at,
            )
        )
    if jobs:
        _executemany(
            connection,
            INGESTION_JOB_INSERT_SQL,
            [_ingestion_job_binds(job) for job in jobs],
            input_sizes=_json_input_sizes("quality_warnings", "settings_overrides"),
        )
    return BulkInsertResult(
        created={job.document_id: job.id for job in jobs},
        active=frozenset(active),
        missing=frozenset(set(document_ids) - set(recipes)),
    )
