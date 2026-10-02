"""Run・業務 Agent と画面で変えた定義の保存先の状態（#839）。

保存先は `AGENT_RUNTIME_REPOSITORY_BACKEND`（Run の repository と定義の store が同じ値に従う。
#764）。`memory` はプロセスの中だけに持ち、バックエンドの再起動で消える。画面はこの状態を見て、
消えることと直す場所（保存先の設定か、データベースの設定か）を案内する。

- `persistent` は定義の store（`control_plane_store`）が保存するか。Run だけを file に保存する
  構成（`memory` + `AGENT_RUNTIME_SNAPSHOT_PATH`）でも、定義は消えるので `False` にする。
- `database_configured` は共通の `PLATFORM_ORACLE_*` の設定がそろっているか（接続は試さない。
  システム設定と同じ `database_readiness` の判定）。
- 接続先・資格情報は返さない。
"""

from __future__ import annotations

import logging
from typing import Literal

from pr_system_settings.database import database_readiness
from pydantic import BaseModel

from app.features.agent.control_plane_store import get_control_plane_store
from app.settings import get_settings

logger = logging.getLogger(__name__)

StorageBackend = Literal["memory", "file", "oracle_checkpoint", "oracle_normalized"]
# 保存されない理由。`memory_backend` は DB は設定済みで保存先の設定が memory（または file の
# 保存先のパスが無い）、`database_not_configured` は DB の設定もそろっていない。
NotPersistentReason = Literal["memory_backend", "database_not_configured"]

_ORACLE_BACKENDS = {"oracle", "oracle_checkpoint", "oracle_normalized"}
_FILE_BACKENDS = {"file", "file_snapshot"}


class RuntimeStorageStatus(BaseModel):
    """保存先の状態（`GET /api/runtime/storage`）。"""

    # 実際に使っている保存先（`oracle` は `oracle_checkpoint` に、保存先のパスの無い file は
    # memory にそろえる）。
    backend: StorageBackend
    # 業務 Agent・スキル・MCP 接続・実行・自動実行・API キー・品質評価などが再起動の後も残るか。
    persistent: bool
    # 共通の PLATFORM_ORACLE_* の設定がそろっているか（接続は試さない）。
    database_configured: bool
    # persistent が False の理由（True のときは None）。
    reason: NotPersistentReason | None = None


def effective_backend() -> StorageBackend:
    """設定の値を、実際に使う保存先の名前にそろえる。"""
    settings = get_settings()
    backend = settings.agent_runtime_repository_backend.strip().lower()
    if backend in _ORACLE_BACKENDS:
        return "oracle_normalized" if backend == "oracle_normalized" else "oracle_checkpoint"
    if backend in _FILE_BACKENDS and settings.agent_runtime_snapshot_path:
        return "file"
    return "memory"


def database_configured() -> bool:
    """共通の DB の設定がそろっているか（システム設定・DB ゲートと同じ判定。接続は試さない）。"""
    try:
        return database_readiness(get_settings()) == "ok"
    except Exception:  # 設定の読み取りの失敗は「未設定」として案内する（画面を止めない）。
        logger.warning("agent_storage_database_readiness_failed", exc_info=True)
        return False


def runtime_storage_status() -> RuntimeStorageStatus:
    persistent = bool(get_control_plane_store().persistent)
    configured = database_configured()
    reason: NotPersistentReason | None = None
    if not persistent:
        reason = "memory_backend" if configured else "database_not_configured"
    return RuntimeStorageStatus(
        backend=effective_backend(),
        persistent=persistent,
        database_configured=configured,
        reason=reason,
    )
