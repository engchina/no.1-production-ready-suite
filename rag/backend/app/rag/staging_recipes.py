"""staging 検証を通常のレシピjobと版に接続する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, StrictBool

from app.clients.oracle import OracleClient
from app.config import Settings, get_settings
from app.rag.ingestion_segment_receipts import read_segment_receipt
from app.rag.kb_adapter_config import (
    KnowledgeBaseAdapterConfig,
    resolve_effective_adapter_config,
    resolve_effective_settings,
)
from app.schemas.document import (
    DocumentDetail,
    DocumentProcessingConfig,
    FileStatus,
    IngestionJobPhase,
    IngestionJobStatus,
    SourceProfile,
)

logger = logging.getLogger(__name__)


class StagingRuntimeOptions(BaseModel):
    """private staging 入口だけが保存できるparser可用性の実行指定。"""

    model_config = ConfigDict(extra="forbid")
    parser_docling_enabled: StrictBool | None = None
    parser_unstructured_enabled: StrictBool | None = None
    parser_readiness_probe_enabled: StrictBool | None = None


def apply_staging_runtime(settings: Settings, overrides: Mapping[str, Any]) -> Settings:
    raw = overrides.get("staging_runtime")
    if raw is None:
        return settings
    options = StagingRuntimeOptions.model_validate(raw)
    return settings.model_copy(
        update={f"rag_{k}": v for k, v in options.model_dump(exclude_none=True).items()}
    )


@dataclass(frozen=True)
class StagingRecipeState:
    recipe_id: str
    status: FileStatus
    chunk_set_id: str | None
    extraction: dict[str, object]
    chunks: tuple[Any, ...]
    segments: tuple[Any, ...]


async def read_staging_recipe(oracle: Any, document_id: str) -> StagingRecipeState:
    identity = await oracle.ensure_default_document_recipe(document_id)
    recipe_id = str(identity["recipe_id"])
    row = await oracle.get_document_recipe(document_id, recipe_id)
    if row is None:
        raise RuntimeError("検証対象の処理レシピが見つかりません。")
    status = FileStatus(str(row["status"]))
    chunk_set_id = None
    chunks: tuple[Any, ...] = ()
    extraction_id = None
    if status == FileStatus.INDEXED:
        chunk_set_id = row.get("active_chunk_set_id")
        if not chunk_set_id:
            raise RuntimeError("索引済みの検証出力が見つかりません。")
        chunk_set_id = str(chunk_set_id)
        version = await oracle.get_chunk_set(chunk_set_id)
        if (
            not version
            or version.get("document_id") != document_id
            or version.get("recipe_id") != recipe_id
            or version.get("status") != "INDEXED"
        ):
            raise RuntimeError("検証出力の文書・処理レシピ・版が一致しません。")
        extraction_id = version.get("extraction_recipe_id")
        if not extraction_id:
            raise RuntimeError("検証出力の抽出結果が見つかりません。")
        chunks = tuple(await oracle.list_chunk_set_chunks(chunk_set_id))
    extraction: dict[str, object] = {}
    if extraction_id:
        artifact = await oracle.get_document_extraction_artifact(
            document_id=document_id, extraction_recipe_id=str(extraction_id)
        )
        if not artifact or not isinstance(artifact.get("extraction_json"), dict):
            raise RuntimeError("検証対象の抽出結果が見つかりません。")
        extraction = dict(artifact["extraction_json"])
    receipt = read_segment_receipt(extraction, document_id, recipe_id)
    segments = tuple(item.segment for item in receipt.segments) if receipt is not None else ()
    if status != FileStatus.INDEXED:
        job = getattr(oracle, "_staging_attempt_jobs", {}).get(document_id)
        if (
            job is not None
            and job.recipe_id == recipe_id
            and job.recipe_revision == int(row.get("config_revision") or 1)
        ):
            attempt = await oracle.read_ingestion_attempt_receipt(job)
            if attempt is not None:
                extraction = {
                    "parser_artifacts": {
                        "ingestion_segment_receipt": attempt.accepted.model_dump(mode="json")
                    }
                }
                segments = tuple(item.segment for item in attempt.accepted.segments) + tuple(
                    attempt.remaining_segments
                )
    return StagingRecipeState(recipe_id, status, chunk_set_id, extraction, chunks, segments)


class StagingRecipeOracle(OracleClient):
    async def read_staging_recipe(self, document_id: str) -> StagingRecipeState:
        return await read_staging_recipe(self, document_id)


class RecipeStagingIngestion:
    """候補構築・切替は正式jobへ委譲し、確認待ちを自動承認しない。"""

    def __init__(self, *, oracle: OracleClient, settings: Settings | None = None) -> None:
        self._oracle = oracle
        self._settings = settings or get_settings()
        self._initialized: set[str] = set()

    async def ingest(
        self,
        document_id: str,
        image_bytes: bytes,
        prompt: str,
        *,
        content_type: str = "application/octet-stream",
        source_profile: SourceProfile | None = None,
    ) -> DocumentDetail:
        # 原本はcreate_documentで保存済み。正式jobはそのURIから読む。
        _ = image_bytes, prompt, content_type, source_profile
        from app.api.routes.documents import _enqueue_ingestion_job_for_recipe, _run_ingestion_job

        row = await self._oracle.ensure_default_document_recipe(document_id)
        recipe_id = str(row["recipe_id"])
        previous_chunk_set_id = row.get("active_chunk_set_id")
        base = get_settings()
        requested = resolve_effective_adapter_config(
            self._settings, KnowledgeBaseAdapterConfig()
        ).ingestion.model_dump()
        inherited = resolve_effective_adapter_config(
            base, KnowledgeBaseAdapterConfig()
        ).ingestion.model_dump()
        if document_id not in self._initialized:
            changes = {k: v for k, v in requested.items() if v != inherited.get(k)}
            config = DocumentProcessingConfig.model_validate(changes)
            replayed = resolve_effective_settings(
                base, KnowledgeBaseAdapterConfig(ingestion=config), scope="ingestion"
            )
            runtime_fields = {f"rag_{key}" for key in StagingRuntimeOptions.model_fields}
            unsupported = [
                key
                for key in Settings.model_fields
                if key not in runtime_fields
                and getattr(self._settings, key) != getattr(replayed, key)
            ]
            if unsupported:
                raise ValueError(
                    "正式jobへ引き継げない明示設定があります: " + ", ".join(unsupported)
                )
            if changes:
                detail = await self._oracle.get_document(document_id)
                if (
                    detail is None
                    or detail.status != FileStatus.UPLOADED
                    or row.get("active_chunk_set_id")
                ):
                    raise RuntimeError("明示設定は新しい検証文書の処理レシピにだけ保存できます。")
                await self._oracle.update_document_recipe_config(document_id, recipe_id, config)
            self._initialized.add(document_id)
        runtime = StagingRuntimeOptions(
            **{
                key: getattr(self._settings, f"rag_{key}")
                for key in StagingRuntimeOptions.model_fields
            }
        )
        job = await _enqueue_ingestion_job_for_recipe(
            document_id, recipe_id, phase=IngestionJobPhase.PREPROCESS, staging_runtime=runtime
        )
        deadline = monotonic() + self._settings.ingestion_job_subprocess_timeout_seconds
        while True:
            attempts = getattr(self._oracle, "_staging_attempt_jobs", {})
            attempts.pop(document_id, None)
            cast(Any, self._oracle)._staging_attempt_jobs = attempts
            try:
                await _run_ingestion_job(job.id, propagate_errors=True)
            finally:
                # 明示したjobの受信snapshotだけを記録する。別jobや旧activeを探さない。
                try:
                    observed = await self._oracle.get_ingestion_job(job.id)
                    if observed is not None:
                        attempts = getattr(self._oracle, "_staging_attempt_jobs", {})
                        attempts[document_id] = observed
                        cast(Any, self._oracle)._staging_attempt_jobs = attempts
                except Exception as exc:  # noqa: BLE001 - 元の取込例外を優先する
                    logger.warning(
                        "staging_attempt_unrecorded", extra={"error_type": type(exc).__name__}
                    )
            current = await self._oracle.get_ingestion_job(job.id)
            if current is None:
                raise RuntimeError("検証対象の取込jobが見つかりません。")
            if current.status in {
                IngestionJobStatus.FAILED,
                IngestionJobStatus.CANCELLED,
                IngestionJobStatus.SKIPPED,
            }:
                raise RuntimeError(f"検証の取込jobは成功しませんでした: {current.status.value}")
            if current.status == IngestionJobStatus.SUCCEEDED:
                jobs = [
                    j
                    for j in await self._oracle.list_document_ingestion_jobs(document_id)
                    if j.recipe_id == recipe_id
                    and j.status in {IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING}
                ]
                if len(jobs) > 1:
                    raise RuntimeError("検証レシピに複数の実行中jobがあります。")
                if not jobs:
                    state = await read_staging_recipe(self._oracle, document_id)
                    if (
                        state.status == FileStatus.INDEXED
                        and state.chunk_set_id == previous_chunk_set_id
                    ):
                        raise RuntimeError("今回の取込による検証出力の切替を確認できません。")
                    detail = await self._oracle.get_document(document_id)
                    if detail is None:
                        raise RuntimeError("検証文書が見つかりません。")
                    return detail.model_copy(update={"status": state.status})
                job = jobs[0]
            if monotonic() >= deadline:
                raise TimeoutError("検証の取込jobの完了待ちが上限を超えました。")
            await asyncio.sleep(0.1)
