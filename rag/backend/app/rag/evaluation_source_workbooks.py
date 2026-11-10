"""品質評価にアップロードした Excel の原本の保存（#4212）。

結果を元のファイルに足してダウンロードするため、検証を通ったファイルをアップロードの保存先
（`ObjectStorageClient`）に置き、持ち主とメタ情報を `rag_evaluation_source_workbooks` に持つ。

- 読めるのは持ち主（tenant と user の hash）だけ。他人の ID は無いものとして扱う（API は 404）。
- 同じ持ち主の同じ内容（sha256）は 1 つだけ保存する（プロファイルを変えると検証し直すため）。
- ファイルの中身・ファイル名はログに書かない。
- 保存の失敗は検証を止めない（結果だけのファイルになる）。
- 評価に使わなかったファイルは、次の保存のときに期限を過ぎたものから消す。job が使うものは
  job と同じく残す（#3235）。job が消えれば参照が外れ（`ON DELETE SET NULL`）、同じように消える。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.clients.evaluation_workbook_store import OracleEvaluationWorkbookStore, SourceWorkbookRow
from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import OracleClient, oracle_error_log_fields
from app.rag.evaluation_excel import XLSX_MEDIA_TYPE
from app.rag.request_context import current_audit_request_context

logger = logging.getLogger(__name__)

# 評価に使わなかったファイルの保持時間（検証から評価の実行までの作業の間は残す）。
EVALUATION_SOURCE_WORKBOOK_UNUSED_RETENTION_HOURS = 24
_OBJECT_PREFIX = "evaluation-workbooks"
FILE_NAME_MAX_CHARS = 512


class EvaluationSourceWorkbookNotFoundError(LookupError):
    """元の Excel が無いか、持ち主ではない。"""

    safe_for_user = True


class EvaluationSourceWorkbookStore(Protocol):
    """元の Excel のメタ情報の保存先（Oracle / memory）。"""

    async def find_by_content(
        self, *, tenant_id_hash: str | None, user_id_hash: str | None, content_sha256: str
    ) -> SourceWorkbookRow | None: ...

    async def create(self, row: SourceWorkbookRow) -> None: ...

    async def get(
        self, workbook_id: str, *, tenant_id_hash: str | None, user_id_hash: str | None
    ) -> SourceWorkbookRow | None: ...

    async def list_unused(
        self, *, tenant_id_hash: str | None, user_id_hash: str | None, older_than_hours: int
    ) -> list[SourceWorkbookRow]: ...

    async def delete(self, workbook_id: str) -> bool: ...


class EvaluationSourceWorkbookStorage(Protocol):
    """ファイルの保存先（`ObjectStorageClient`）。"""

    async def put(self, key: str, data: bytes, content_type: str) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> bool: ...


class MemoryEvaluationSourceWorkbookStore:
    """Oracle の接続設定が無い開発環境・テストの保存先（プロセスの中だけ）。"""

    def __init__(self) -> None:
        self._rows: dict[str, SourceWorkbookRow] = {}
        self._used: set[str] = set()

    async def find_by_content(
        self, *, tenant_id_hash: str | None, user_id_hash: str | None, content_sha256: str
    ) -> SourceWorkbookRow | None:
        for row in self._rows.values():
            if (
                row.tenant_id_hash == tenant_id_hash
                and row.user_id_hash == user_id_hash
                and row.content_sha256 == content_sha256
            ):
                return row
        return None

    async def create(self, row: SourceWorkbookRow) -> None:
        self._rows[row.workbook_id] = row

    async def get(
        self, workbook_id: str, *, tenant_id_hash: str | None, user_id_hash: str | None
    ) -> SourceWorkbookRow | None:
        row = self._rows.get(workbook_id)
        if row is None or row.tenant_id_hash != tenant_id_hash or row.user_id_hash != user_id_hash:
            return None
        return row

    def mark_used(self, workbook_id: str) -> None:
        """job が使う（Oracle では `rag_evaluation_jobs.source_workbook_id` の参照）。"""
        self._used.add(workbook_id)

    async def list_unused(
        self, *, tenant_id_hash: str | None, user_id_hash: str | None, older_than_hours: int
    ) -> list[SourceWorkbookRow]:
        threshold = datetime.now(UTC) - timedelta(hours=older_than_hours)
        return [
            row
            for row in self._rows.values()
            if row.workbook_id not in self._used
            and row.tenant_id_hash == tenant_id_hash
            and row.user_id_hash == user_id_hash
            and (row.created_at or datetime.now(UTC)) <= threshold
        ]

    async def delete(self, workbook_id: str) -> bool:
        return self._rows.pop(workbook_id, None) is not None


@dataclass(frozen=True)
class EvaluationSourceWorkbook:
    """読み出した元の Excel。"""

    file_name: str
    content: bytes


class EvaluationSourceWorkbookService:
    """元の Excel の保存と読み出し（持ち主は要求の文脈から決める）。"""

    def __init__(
        self,
        store: EvaluationSourceWorkbookStore | None = None,
        *,
        storage: EvaluationSourceWorkbookStorage | None = None,
    ) -> None:
        self._store = store
        self._memory_store: MemoryEvaluationSourceWorkbookStore | None = None
        self._storage = storage

    @property
    def store(self) -> EvaluationSourceWorkbookStore:
        if self._store is not None:
            return self._store
        client = OracleClient()
        if client.is_connection_configured():
            return OracleEvaluationWorkbookStore(client)
        if self._memory_store is None:
            self._memory_store = MemoryEvaluationSourceWorkbookStore()
        return self._memory_store

    @property
    def storage(self) -> EvaluationSourceWorkbookStorage:
        return self._storage or ObjectStorageClient()

    @staticmethod
    def _owner() -> tuple[str | None, str | None]:
        context = current_audit_request_context()
        return context.tenant_id_hash, context.user_id_hash

    async def save(self, file_name: str, content: bytes) -> str | None:
        """ファイルを保存して ID を返す。保存できなければ None（検証は止めない）。"""
        tenant_id_hash, user_id_hash = self._owner()
        content_sha256 = hashlib.sha256(content).hexdigest()
        store = self.store
        object_path: str | None = None
        try:
            existing = await store.find_by_content(
                tenant_id_hash=tenant_id_hash,
                user_id_hash=user_id_hash,
                content_sha256=content_sha256,
            )
            if existing is not None:
                return existing.workbook_id
            workbook_id = uuid.uuid4().hex
            object_path = await self.storage.put(
                f"{_OBJECT_PREFIX}/{workbook_id}/source.xlsx", content, XLSX_MEDIA_TYPE
            )
            await store.create(
                SourceWorkbookRow(
                    workbook_id=workbook_id,
                    tenant_id_hash=tenant_id_hash,
                    user_id_hash=user_id_hash,
                    file_name=file_name[:FILE_NAME_MAX_CHARS],
                    content_sha256=content_sha256,
                    byte_size=len(content),
                    object_storage_path=object_path,
                    created_at=datetime.now(UTC),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 検証は続ける（結果だけのファイルになる）
            logger.warning(
                "evaluation_source_workbook_save_failed", extra=oracle_error_log_fields(exc)
            )
            if object_path is not None:
                with contextlib.suppress(Exception):
                    await self.storage.delete(object_path)
            return None
        with contextlib.suppress(Exception):
            # 期限を過ぎた未使用のファイルの削除は補助。失敗しても検証は止めない。
            await self.purge_unreferenced()
        return workbook_id

    async def load(
        self, workbook_id: str, *, owner: tuple[str | None, str | None] | None = None
    ) -> EvaluationSourceWorkbook:
        """持ち主のファイルを返す。無ければ `EvaluationSourceWorkbookNotFoundError`。

        `owner`（tenant と user の hash）を渡すと、その利用者のファイルを読む。見られることを
        確かめた品質評価の job の、実行した利用者のファイルを読むときだけ渡す（#4811）。
        """
        tenant_id_hash, user_id_hash = owner if owner is not None else self._owner()
        row = await self.store.get(
            workbook_id, tenant_id_hash=tenant_id_hash, user_id_hash=user_id_hash
        )
        if row is None:
            raise EvaluationSourceWorkbookNotFoundError(
                "アップロードした Excel が見つかりません。もう一度アップロードしてください。"
            )
        return EvaluationSourceWorkbook(
            file_name=row.file_name, content=await self.storage.get(row.object_storage_path)
        )

    async def purge_unreferenced(
        self, *, older_than_hours: int = EVALUATION_SOURCE_WORKBOOK_UNUSED_RETENTION_HOURS
    ) -> int:
        """持ち主の、評価に使わなかった期限切れのファイルを消す。消した数を返す。"""
        tenant_id_hash, user_id_hash = self._owner()
        store = self.store
        rows = await store.list_unused(
            tenant_id_hash=tenant_id_hash,
            user_id_hash=user_id_hash,
            older_than_hours=older_than_hours,
        )
        removed = 0
        for row in rows:
            # 表から先に消す（消す間に job が使い始めた行は残り、ファイルも残る）。
            if await store.delete(row.workbook_id):
                await self.storage.delete(row.object_storage_path)
                removed += 1
        return removed


_SERVICE = EvaluationSourceWorkbookService()


def get_evaluation_source_workbook_service() -> EvaluationSourceWorkbookService:
    """このプロセスの service を返す。"""
    return _SERVICE


def set_evaluation_source_workbook_service(
    service: EvaluationSourceWorkbookService | None,
) -> EvaluationSourceWorkbookService:
    """テスト用に service を差し替える（None なら新しい service にする）。"""
    global _SERVICE
    _SERVICE = service or EvaluationSourceWorkbookService()
    return _SERVICE
