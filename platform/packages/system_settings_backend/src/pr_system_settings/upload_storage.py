"""アップロード保存先の設定 API（3製品共通。NL2SQL の実装を基準に移設。#97）。

保存の順序は「候補を作る → 検証する → `.env` へ保存する →
保存が成功したら runtime の Settings へ反映する」。
保存に失敗した場合は runtime の Settings を変えない。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, params
from pr_backend_core.schemas import ApiResponse
from pydantic import BaseModel, Field, field_validator

from .env_file import write_env_values

UploadStorageBackend = Literal["local", "oci"]

ENV_SECTION_COMMENT = "# アップロード保存先"
_OBJECT_STORAGE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


class UploadStorageSettingsData(BaseModel):
    """アップロード原本保存先の表示用データ。"""

    backend: UploadStorageBackend
    local_storage_dir: str
    object_storage_region: str
    object_storage_namespace: str
    object_storage_bucket: str
    readiness: str
    max_upload_bytes: int
    config_source: Literal["runtime"]


class UploadStorageSettingsUpdate(BaseModel):
    """アップロード原本保存先の更新 payload。"""

    backend: UploadStorageBackend
    local_storage_dir: str = Field(default="", max_length=1024)
    object_storage_region: str | None = Field(default=None, max_length=128)
    object_storage_namespace: str | None = Field(default=None, max_length=256)
    object_storage_bucket: str = Field(default="", max_length=256)

    @field_validator("local_storage_dir", "object_storage_bucket")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("object_storage_region", "object_storage_namespace")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        """省略時は既存の OCI Object Storage 設定を保持する。"""
        return value.strip() if value is not None else None

    @field_validator("object_storage_namespace", "object_storage_bucket")
    @classmethod
    def validate_object_storage_name(cls, value: str | None) -> str | None:
        """OCI Object Storage の namespace / bucket 名で危険な文字を拒否する。"""
        if value and not _OBJECT_STORAGE_NAME_RE.fullmatch(value):
            raise ValueError(
                "Object Storage の値は英数字、ハイフン、アンダースコア、ドットで入力してください。"
            )
        return value


def upload_storage_settings_data(settings: Any) -> UploadStorageSettingsData:
    """runtime の Settings から表示用データを作る。region が空なら OCI 認証の region を使う。"""
    backend = getattr(settings, "upload_storage_backend", "local")
    if backend not in {"local", "oci"}:
        backend = "local"
    namespace = getattr(settings, "object_storage_namespace", "") or ""
    bucket = getattr(settings, "object_storage_bucket", "") or ""
    local_dir = getattr(settings, "local_storage_dir", "") or ""
    region = getattr(settings, "object_storage_region", "") or getattr(settings, "oci_region", "")
    readiness = (
        "ok"
        if (backend == "local" and local_dir)
        or (backend == "oci" and region and namespace and bucket)
        else "missing"
    )
    return UploadStorageSettingsData(
        backend=backend,
        local_storage_dir=local_dir,
        object_storage_region=region or "",
        object_storage_namespace=namespace,
        object_storage_bucket=bucket,
        readiness=readiness,
        max_upload_bytes=int(getattr(settings, "max_upload_bytes", 104857600)),
        config_source="runtime",
    )


def upload_storage_candidate(base: Any, payload: UploadStorageSettingsUpdate) -> Any:
    """runtime の Settings を変えずに、更新後の Settings の候補を作る。"""
    updates = {
        "upload_storage_backend": payload.backend,
        "local_storage_dir": payload.local_storage_dir,
        "object_storage_region": (
            payload.object_storage_region
            if payload.object_storage_region is not None
            else getattr(base, "object_storage_region", "") or getattr(base, "oci_region", "")
        ),
        "object_storage_namespace": (
            payload.object_storage_namespace
            if payload.object_storage_namespace is not None
            else getattr(base, "object_storage_namespace", "")
        ),
        "object_storage_bucket": payload.object_storage_bucket,
    }
    if hasattr(base, "model_copy"):
        return base.model_copy(update=updates)
    # pydantic 以外の Settings（テスト用の SimpleNamespace など）も扱えるようにする。
    candidate = copy.copy(base)
    for key, value in updates.items():
        setattr(candidate, key, value)
    return candidate


def validate_upload_storage(settings: Any) -> None:
    """OCI を選んだときに region / namespace / bucket が空なら 422 を返す。"""
    if settings.upload_storage_backend != "oci":
        return
    missing_fields = [
        label
        for label, value in (
            ("Object Storage リージョン", settings.object_storage_region),
            ("Object Storage namespace", settings.object_storage_namespace),
            ("Object Storage bucket", settings.object_storage_bucket),
        )
        if not str(value or "").strip()
    ]
    if missing_fields:
        raise HTTPException(
            status_code=422,
            detail="OCI Object Storage 設定が不足しています: " + ", ".join(missing_fields),
        )


def persist_upload_storage(settings: Any, env_file: Path) -> None:
    """アップロード保存先を `.env` へ保存する。失敗は 500 に変換する。"""
    values: dict[str, str | None] = {
        "UPLOAD_STORAGE_BACKEND": settings.upload_storage_backend,
        "LOCAL_STORAGE_DIR": settings.local_storage_dir,
    }
    if settings.upload_storage_backend == "oci":
        values["OBJECT_STORAGE_REGION"] = settings.object_storage_region
        values["OBJECT_STORAGE_NAMESPACE"] = settings.object_storage_namespace
        values["OBJECT_STORAGE_BUCKET"] = settings.object_storage_bucket
    try:
        write_env_values(env_file, values, section_comment=ENV_SECTION_COMMENT)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="アップロード保存先設定を backend/.env へ保存できませんでした。",
        ) from exc


def apply_upload_storage(target: Any, source: Any) -> None:
    """保存が成功した後に、runtime の Settings へ反映する。"""
    for field in (
        "upload_storage_backend",
        "local_storage_dir",
        "object_storage_region",
        "object_storage_namespace",
        "object_storage_bucket",
    ):
        setattr(target, field, getattr(source, field))


def build_upload_storage_router(
    *,
    get_settings: Callable[[], Any],
    env_file: Callable[[], Path],
    write_dependencies: Sequence[params.Depends] = (),
) -> APIRouter:
    """`GET/PATCH /upload-storage` の router を作る。製品側で `/settings` 配下に include する。

    - `get_settings`：製品の runtime Settings（キャッシュされた同じインスタンス）を返す関数
    - `env_file`：保存先の `backend/.env` を返す関数（テストで差し替えられるよう呼出時に解決する）
    - `write_dependencies`：PATCH にだけ付ける依存関係（例: 管理者権限の確認）
    """
    router = APIRouter()

    @router.get("/upload-storage", response_model=ApiResponse[UploadStorageSettingsData])
    def get_upload_storage_settings() -> ApiResponse[UploadStorageSettingsData]:
        return ApiResponse(data=upload_storage_settings_data(get_settings()))

    @router.patch(
        "/upload-storage",
        response_model=ApiResponse[UploadStorageSettingsData],
        dependencies=list(write_dependencies),
    )
    def update_upload_storage_settings(
        payload: UploadStorageSettingsUpdate,
    ) -> ApiResponse[UploadStorageSettingsData]:
        settings = get_settings()
        candidate = upload_storage_candidate(settings, payload)
        validate_upload_storage(candidate)
        persist_upload_storage(candidate, env_file())
        apply_upload_storage(settings, candidate)
        return ApiResponse(data=upload_storage_settings_data(settings))

    return router
