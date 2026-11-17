"""システムテーブル（運用設定 > システムテーブル）の API（#751。RAG / NL2SQL と同じ契約）。

- `GET /settings/database/system-tables`: DDL を実行せずに状態を返す。
- `POST /settings/database/system-tables/initialize`: 作成・更新、または確認語付きの全再作成を
  後台の操作として始め、202 と開始した時点の状態を返す（#5278）。データを消す未適用の migration は
  `allow_destructive` が無ければ 409（#619）。保存済みのデータを書き換える migration・全再作成の
  後は、このプロセスのメモリを保存先から読み直し、読み直せないプロセスを状態の
  `last_operation.restart_required` で返す（#1844）。

権限はどちらも `menu.settings_system_tables`（API の権限 manifest。NL2SQL と同じ）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pr_backend_core import ApiResponse
from pr_system_settings.system_schema import (
    SystemTableColumnData,
    SystemTableForeignKeyData,
    SystemTableLastOperationData,
    SystemTableOperationState,
    SystemTablesInitializeRequest,
    system_tables_status_error,
)
from pydantic import BaseModel, Field

from app.system_schema import SystemSchemaError, system_schema_manager

router = APIRouter(prefix="/settings/database/system-tables", tags=["system-tables"])


class SystemObjectRef(BaseModel):
    name: str
    object_type: str


class SystemTableMetadata(BaseModel):
    name: str
    exists: bool
    estimated_rows: int | None = None
    created_at: str | None = None
    last_analyzed_at: str | None = None


class SystemObjectMetadata(SystemTableMetadata):
    object_type: str


class SystemTableDestructiveMigration(BaseModel):
    name: str
    description: str


class SystemTablesStatusData(BaseModel):
    status: str
    schema_head: str
    applied_versions: list[str]
    pending_versions: list[str]
    pending_destructive_migrations: list[SystemTableDestructiveMigration] = Field(
        default_factory=list
    )
    expected_object_count: int
    existing_object_count: int
    expected_table_count: int
    existing_table_count: int
    missing_objects: list[SystemObjectRef]
    retired_objects: list[SystemObjectRef] = Field(default_factory=list)
    # 既存の表に無い正本の列（台帳が適用済みでも欠けた列。状態は outdated。#2458）。
    missing_columns: list[SystemTableColumnData] = Field(default_factory=list)
    missing_foreign_keys: list[SystemTableForeignKeyData] = Field(default_factory=list)
    orphaned_foreign_keys: list[SystemTableForeignKeyData] = Field(default_factory=list)
    mismatched_foreign_keys: list[SystemTableForeignKeyData] = Field(default_factory=list)
    disabled_foreign_keys: list[SystemTableForeignKeyData] = Field(default_factory=list)
    tables: list[SystemTableMetadata]
    objects: list[SystemObjectMetadata] = Field(default_factory=list)
    operation_state: SystemTableOperationState
    # このプロセスが後台で終えた直近の操作の要約（無ければ画面は状態の差から求める。#5278）。
    last_operation: SystemTableLastOperationData | None = None


@router.get("", response_model=ApiResponse[SystemTablesStatusData])
async def get_system_tables_status() -> ApiResponse[SystemTablesStatusData]:
    """Agent のシステムテーブルの状態を DDL なしで取得する。"""
    try:
        data = await asyncio.to_thread(system_schema_manager.screen_status)
    except Exception as exc:
        raise system_tables_status_error(exc) from exc
    return ApiResponse(data=SystemTablesStatusData.model_validate(data))


@router.post("/initialize", response_model=ApiResponse[SystemTablesStatusData], status_code=202)
async def initialize_system_tables(
    payload: SystemTablesInitializeRequest,
) -> ApiResponse[SystemTablesStatusData] | JSONResponse:
    """管理者の明示操作として作成・更新、または全再作成を始める（後台の操作。#5278）。

    lease を取ったら 202 と開始した時点の状態（running）を返す。保存済みのデータの読み直しは
    後台の操作の中で行い、再起動が要るプロセス（#1844）は状態の `last_operation` で返す。
    """
    try:
        data = await asyncio.to_thread(
            system_schema_manager.start_initialize_in_background,
            recreate=payload.recreate,
            confirmation=payload.confirmation,
            allow_destructive=payload.allow_destructive,
        )
    except SystemSchemaError as exc:
        return _system_schema_error_response(exc)
    return ApiResponse(data=SystemTablesStatusData.model_validate(data))


def _system_schema_error_response(exc: SystemSchemaError) -> JSONResponse:
    """システムテーブル操作の業務エラー（公開してよい文言と ORA コードだけ）。"""
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.retry_headers,
        content={
            "data": None,
            "error_messages": [exc.public_message],
            "warning_messages": [],
            "error_code": exc.code,
        },
    )
