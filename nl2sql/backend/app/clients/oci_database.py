"""OCI Autonomous Database 管理クライアント（起動 / 停止 / 情報取得）。"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.clients.oci_auth import load_oci_config_without_prompt
from app.settings import Settings, get_settings

type SdkCallRunner = Callable[[Callable[[], Any]], Awaitable[Any]]


@dataclass(frozen=True)
class AutonomousDatabaseInfo:
    """ADB 情報の表示用スナップショット。"""

    id: str | None
    display_name: str | None
    lifecycle_state: str | None
    db_name: str | None
    cpu_core_count: int | None
    data_storage_size_in_tbs: float | None


class DatabaseSdkClientProtocol(Protocol):
    """OCI Database control plane client の最小インターフェース。"""

    def get_autonomous_database(self, autonomous_database_id: str) -> Any:
        """ADB 情報を取得する。"""

    def start_autonomous_database(self, autonomous_database_id: str) -> Any:
        """ADB を起動する。"""

    def stop_autonomous_database(self, autonomous_database_id: str) -> Any:
        """ADB を停止する。"""


class OciDatabaseClient:
    """OCI Autonomous Database の制御プレーン操作クライアント。"""

    def __init__(
        self,
        settings: Settings | None = None,
        database_client: DatabaseSdkClientProtocol | None = None,
        sdk_call_runner: SdkCallRunner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._database_client = database_client
        self._sdk_call_runner = sdk_call_runner or _run_sdk_call_in_thread

    async def get_autonomous_database(self, adb_ocid: str) -> AutonomousDatabaseInfo:
        """ADB の現在情報を取得する。"""
        response = await self._sdk_call_runner(
            lambda: self._client().get_autonomous_database(adb_ocid)
        )
        return _to_info(getattr(response, "data", response))

    async def start_autonomous_database(self, adb_ocid: str) -> None:
        """ADB の起動をリクエストする。"""
        await self._sdk_call_runner(lambda: self._client().start_autonomous_database(adb_ocid))

    async def stop_autonomous_database(self, adb_ocid: str) -> None:
        """ADB の停止をリクエストする。"""
        await self._sdk_call_runner(lambda: self._client().stop_autonomous_database(adb_ocid))

    def _client(self) -> DatabaseSdkClientProtocol:
        """OCI Database client を遅延初期化する。"""
        if self._database_client is not None:
            return self._database_client

        oci_config = importlib.import_module("oci.config")
        database = importlib.import_module("oci.database")
        config = load_oci_config_without_prompt(
            oci_config,
            self._settings.oci_config_file,
            self._settings.oci_config_profile,
            region=self._settings.oci_region.strip() or None,
        )
        self._database_client = database.DatabaseClient(config)
        return self._database_client


def _to_info(data: Any) -> AutonomousDatabaseInfo:
    """OCI SDK の AutonomousDatabase model を表示用スナップショットへ変換する。"""
    return AutonomousDatabaseInfo(
        id=getattr(data, "id", None),
        display_name=getattr(data, "display_name", None),
        lifecycle_state=getattr(data, "lifecycle_state", None),
        db_name=getattr(data, "db_name", None),
        cpu_core_count=getattr(data, "cpu_core_count", None),
        data_storage_size_in_tbs=getattr(data, "data_storage_size_in_tbs", None),
    )


async def _run_sdk_call_in_thread(operation: Callable[[], Any]) -> Any:
    """同期 OCI SDK 呼び出しを event loop 外で実行する。"""
    return await asyncio.to_thread(operation)
