"""モデル設定の API（3製品共通。NL2SQL の実装を基準に移設。#103）。

- OCI Enterprise AI（回答生成 / Vision）と OCI Generative AI（埋め込み / リランク）の設定
- API key は `backend/.env` の `OCI_ENTERPRISE_AI_API_KEY` だけに保存し、JSON には書かない
- それ以外は `model-settings.json`（`version: 3`）へ保存し、起動時と mtime の変化時に読み込む
- 製品固有の節（RAG の `parser_adapters`）は `ModelSettingsSection` で読み書きする
- モデル単位の接続テスト。実際の呼び出しは製品が `run_model_test` で渡す
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from dotenv import dotenv_values
from fastapi import APIRouter, HTTPException, params
from pr_backend_core.schemas import ApiResponse
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from .env_file import write_env_values

logger = logging.getLogger(__name__)

ENTERPRISE_AI_API_KEY_ENV = "OCI_ENTERPRISE_AI_API_KEY"
MODEL_SETTINGS_DOCUMENT_VERSION = 3
MODEL_SETTINGS_FILE_MODE = 0o600
MODEL_SETTINGS_DIRECTORY_MODE = 0o700

EnterpriseAiVlmInputMode = Literal["auto", "files_api", "inline_image"]
ModelSettingsSecretSource = Literal["environment", "legacy_json", "missing"]
ModelSettingsTestStatus = Literal["success", "failed"]
ModelSettingsTestTargetType = Literal["enterprise_text", "enterprise_vision", "embedding", "rerank"]
ModelTestDetails = dict[str, str | int | float | bool | None]


# --------------------------------------------------------------------------- schemas


class EnterpriseAiConfiguredModel(BaseModel):
    """OCI Enterprise AI provider に登録する LLM。"""

    model_id: str = Field(default="", max_length=256)
    display_name: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


# API schema 上の名前（NL2SQL / RAG の既存名）。中身は同じ。
EnterpriseAiModelEntrySettings = EnterpriseAiConfiguredModel


class EnterpriseAiModelSettings(BaseModel):
    """OCI Enterprise AI モデル provider 設定。"""

    endpoint: str = Field(default="", max_length=2048)
    project_ocid: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=4096)
    has_api_key: bool = False
    clear_api_key: bool = False
    models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list, max_length=20)
    default_model_id: str = Field(default="", max_length=256)
    api_path: str = Field(default="/responses", max_length=512)
    vlm_input_mode: EnterpriseAiVlmInputMode = "auto"
    text_payload_template: str = Field(default="", max_length=20000)
    vision_payload_template: str = Field(default="", max_length=20000)
    text_response_path: str = Field(default="", max_length=1024)
    vision_response_path: str = Field(default="", max_length=1024)
    timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=3, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=1200, ge=1, le=65536)
    vlm_max_output_tokens: int = Field(default=65536, ge=1, le=65536)

    @field_validator(
        "endpoint",
        "project_ocid",
        "api_key",
        "default_model_id",
        "api_path",
        "text_payload_template",
        "vision_payload_template",
        "text_response_path",
        "vision_response_path",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()

    @field_validator("text_payload_template", "vision_payload_template")
    @classmethod
    def validate_payload_template(cls, value: str) -> str:
        """payload template は空または JSON object 文字列だけを許可する。"""
        if not value:
            return value
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise ValueError("payload template は JSON object で入力してください。") from exc
        if not isinstance(parsed, dict):
            raise ValueError("payload template は JSON object で入力してください。")
        return value

    @field_validator("text_response_path", "vision_response_path")
    @classmethod
    def validate_response_path(cls, value: str) -> str:
        """response path は空または JSON Pointer 形式だけを許可する。"""
        if value and not value.startswith("/"):
            raise ValueError("response path は / で始まる JSON Pointer で入力してください。")
        return value


class GenerativeAiModelSettings(BaseModel):
    """OCI Generative AI（embedding/rerank）モデル設定。"""

    embedding_model: str = Field(default="cohere.embed-v4.0", max_length=256)
    embedding_dim: int = Field(
        default=1536,
        ge=1536,
        le=1536,
        description="Oracle VECTOR(1536, FLOAT32) と互換にするため 1536 固定。",
    )
    rerank_model: str = Field(default="cohere.rerank-v4.0-fast", max_length=256)

    @field_validator("embedding_model", "rerank_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class ModelSettingsPayload(BaseModel):
    """モデル設定の読み書き payload。"""

    enterprise_ai: EnterpriseAiModelSettings
    generative_ai: GenerativeAiModelSettings


class ModelSettingsData(BaseModel):
    """モデル設定 API のレスポンス data。"""

    settings: ModelSettingsPayload
    model_settings_file: str
    source: Literal["runtime"]
    secret_source: ModelSettingsSecretSource
    legacy_secret_detected: bool = False


class ModelSettingsTestRequest(BaseModel):
    """保存前のモデル設定で特定モデルを実 API に対してテストする request。"""

    settings: ModelSettingsPayload
    target_type: ModelSettingsTestTargetType
    model_id: str = Field(default="", max_length=256)
    vision_enabled: bool = False

    @field_validator("model_id")
    @classmethod
    def strip_model_id(cls, value: str) -> str:
        """前後空白を設定値へ混入させない。"""
        return value.strip()


class ModelSettingsTestResult(BaseModel):
    """モデル単位の実接続テスト結果。"""

    status: ModelSettingsTestStatus
    target_type: ModelSettingsTestTargetType
    model_id: str
    message: str
    troubleshooting: list[str] = Field(default_factory=list)
    raw_error: str | None = None
    error_type: str | None = None
    elapsed_ms: int
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: ModelTestDetails = Field(default_factory=dict)


# --------------------------------------------------------------------------- secret state


class ModelSecretStateMixin(BaseModel):
    """製品の Settings に混ぜる、Enterprise AI API key の取得元の状態。

    `class Settings(ModelSecretStateMixin, BaseSettings)` のように先頭に置く。
    """

    _environment_enterprise_ai_api_key: str = PrivateAttr(default="")
    _model_secret_source: ModelSettingsSecretSource = PrivateAttr(default="missing")
    _legacy_model_secret_detected: bool = PrivateAttr(default=False)
    _model_secret_state_initialized: bool = PrivateAttr(default=False)

    @property
    def model_secret_source(self) -> ModelSettingsSecretSource:
        """Enterprise AI API key の実効的な取得元。"""
        return self._model_secret_source

    @property
    def legacy_model_secret_detected(self) -> bool:
        """model-settings.json に旧 secret field が残っているか。"""
        return self._legacy_model_secret_detected

    def prepare_model_secret_state(self, environment_api_key: str | None = None) -> None:
        """JSON 再読込前に環境由来 secret を基準値へ戻す。

        `environment_api_key` を渡すと新しい基準値にする（別 worker が `.env` を更新した場合）。
        """
        if not self._model_secret_state_initialized:
            self._environment_enterprise_ai_api_key = _api_key_of(self).strip()
            self._model_secret_state_initialized = True
        if environment_api_key is not None:
            self._environment_enterprise_ai_api_key = environment_api_key.strip()
        self._set_api_key(self._environment_enterprise_ai_api_key)
        self._model_secret_source = (
            "environment" if self._environment_enterprise_ai_api_key else "missing"
        )
        self._legacy_model_secret_detected = False

    def set_runtime_enterprise_ai_api_key(self, api_key: str) -> None:
        """`.env` 更新後の secret 状態を現在プロセスへ反映する。"""
        normalized = api_key.strip()
        self._environment_enterprise_ai_api_key = normalized
        self._model_secret_state_initialized = True
        self._set_api_key(normalized)
        self._model_secret_source = "environment" if normalized else "missing"
        self._legacy_model_secret_detected = False

    def apply_legacy_enterprise_ai_api_key(self, api_key: str, *, detected: bool) -> None:
        """旧 JSON の secret を、環境に key がないときだけ一時的に runtime へ適用する。"""
        normalized = api_key.strip()
        self._legacy_model_secret_detected = detected
        if not self._environment_enterprise_ai_api_key and normalized:
            self._set_api_key(normalized)
            self._model_secret_source = "legacy_json"  # nosec B105

    def _set_api_key(self, value: str) -> None:
        # Settings 側のフィールド（mixin 自体は持たない）。
        setattr(self, "oci_enterprise_ai_api_key", value)  # noqa: B010


def _api_key_of(settings: Any) -> str:
    return str(getattr(settings, "oci_enterprise_ai_api_key", "") or "")


# --------------------------------------------------------------------------- catalog


def _coerce_model(value: object) -> EnterpriseAiConfiguredModel:
    if isinstance(value, EnterpriseAiConfiguredModel):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return EnterpriseAiConfiguredModel.model_validate(value)


def enterprise_ai_model_catalog(settings: Any) -> list[EnterpriseAiConfiguredModel]:
    """Enterprise AI の登録モデル一覧を返す。旧 LLM/VLM 設定からも補完する。"""
    configured = [
        model
        for model in (
            _coerce_model(item) for item in getattr(settings, "oci_enterprise_ai_models", [])
        )
        if model.model_id
    ]
    if configured:
        return configured
    llm_model = str(getattr(settings, "oci_enterprise_ai_llm_model", "")).strip()
    vlm_model = str(getattr(settings, "oci_enterprise_ai_vlm_model", "")).strip()
    models: list[EnterpriseAiConfiguredModel] = []
    if llm_model:
        models.append(
            EnterpriseAiConfiguredModel(
                model_id=llm_model,
                display_name=llm_model,
                vision_enabled=bool(vlm_model and vlm_model == llm_model),
            )
        )
    if vlm_model and vlm_model != llm_model:
        models.append(
            EnterpriseAiConfiguredModel(
                model_id=vlm_model, display_name=vlm_model, vision_enabled=True
            )
        )
    return models


def enterprise_ai_default_model_id(settings: Any) -> str:
    """通常の LLM 呼び出しで使う既定モデル ID を返す。"""
    for name in ("oci_enterprise_ai_default_model", "oci_enterprise_ai_llm_model"):
        value = str(getattr(settings, name, "")).strip()
        if value:
            return value
    catalog = enterprise_ai_model_catalog(settings)
    return catalog[0].model_id if catalog else ""


def enterprise_ai_vision_model_id(settings: Any) -> str:
    """Vision/OCR 呼び出しで使うモデル ID を返す。"""
    catalog = enterprise_ai_model_catalog(settings)
    selected = _vision_model_id(catalog, enterprise_ai_default_model_id(settings))
    return selected or str(getattr(settings, "oci_enterprise_ai_vlm_model", "")).strip()


def _vision_model_id(models: Sequence[EnterpriseAiConfiguredModel], default_model: str) -> str:
    """Vision/OCR 用 model を default 優先で選ぶ。"""
    for model in models:
        if model.model_id == default_model and model.vision_enabled:
            return model.model_id
    for model in models:
        if model.model_id and model.vision_enabled:
            return model.model_id
    return ""


# --------------------------------------------------------------------------- payload <-> Settings


def model_payload(settings: Any) -> ModelSettingsPayload:
    """Settings から UI 用 payload を組み立てる（secret は含めない）。"""
    models = [model.model_copy() for model in enterprise_ai_model_catalog(settings)]
    if not models:
        models.append(EnterpriseAiConfiguredModel())
    api_path = settings.oci_enterprise_ai_llm_path or settings.oci_enterprise_ai_vlm_path
    return ModelSettingsPayload(
        enterprise_ai=EnterpriseAiModelSettings(
            endpoint=settings.oci_enterprise_ai_endpoint,
            project_ocid=settings.oci_enterprise_ai_project_ocid,
            api_key="",
            has_api_key=bool(_api_key_of(settings).strip()),
            models=models,
            default_model_id=enterprise_ai_default_model_id(settings),
            api_path=api_path or "/responses",
            vlm_input_mode=settings.oci_enterprise_ai_vlm_input_mode,
            text_payload_template=settings.oci_enterprise_ai_llm_payload_template,
            vision_payload_template=settings.oci_enterprise_ai_vlm_payload_template,
            text_response_path=settings.oci_enterprise_ai_llm_response_path,
            vision_response_path=settings.oci_enterprise_ai_vlm_response_path,
            timeout_seconds=settings.oci_enterprise_ai_timeout_seconds,
            max_retries=settings.oci_enterprise_ai_max_retries,
            llm_max_output_tokens=settings.oci_enterprise_ai_llm_max_output_tokens,
            vlm_max_output_tokens=settings.oci_enterprise_ai_vlm_max_output_tokens,
        ),
        generative_ai=GenerativeAiModelSettings(
            embedding_model=settings.oci_genai_embedding_model,
            embedding_dim=settings.oci_genai_embedding_dim,
            rerank_model=settings.oci_genai_rerank_model,
        ),
    )


def apply_model_settings(settings: Any, payload: ModelSettingsPayload) -> None:
    """payload（secret 以外）を Settings へ反映する。API key は呼出側で扱う。"""
    enterprise = payload.enterprise_ai
    generative = payload.generative_ai
    models = [model.model_copy() for model in enterprise.models if model.model_id]
    default_model = enterprise.default_model_id
    settings.oci_enterprise_ai_endpoint = enterprise.endpoint
    settings.oci_enterprise_ai_project_ocid = enterprise.project_ocid
    settings.oci_enterprise_ai_models = models
    settings.oci_enterprise_ai_default_model = default_model
    settings.oci_enterprise_ai_llm_model = default_model
    settings.oci_enterprise_ai_vlm_model = _vision_model_id(models, default_model) or default_model
    settings.oci_enterprise_ai_llm_path = enterprise.api_path
    settings.oci_enterprise_ai_vlm_path = enterprise.api_path
    settings.oci_enterprise_ai_vlm_input_mode = enterprise.vlm_input_mode
    settings.oci_enterprise_ai_llm_payload_template = enterprise.text_payload_template
    settings.oci_enterprise_ai_vlm_payload_template = enterprise.vision_payload_template
    settings.oci_enterprise_ai_llm_response_path = enterprise.text_response_path
    settings.oci_enterprise_ai_vlm_response_path = enterprise.vision_response_path
    settings.oci_enterprise_ai_timeout_seconds = enterprise.timeout_seconds
    settings.oci_enterprise_ai_max_retries = enterprise.max_retries
    settings.oci_enterprise_ai_llm_max_output_tokens = enterprise.llm_max_output_tokens
    settings.oci_enterprise_ai_vlm_max_output_tokens = enterprise.vlm_max_output_tokens
    settings.oci_genai_embedding_model = generative.embedding_model
    settings.oci_genai_embedding_dim = generative.embedding_dim
    settings.oci_genai_rerank_model = generative.rerank_model
    # NL2SQL は旧名の model ID も読んでいる。
    _set_if_field(settings, "oci_genai_embed_model_id", generative.embedding_model)
    _set_if_field(settings, "oci_genai_rerank_model_id", generative.rerank_model)


def _set_if_field(settings: Any, name: str, value: object) -> None:
    if name in getattr(type(settings), "model_fields", {}):
        setattr(settings, name, value)


def _secret_value(*, current: str, update: str | None, clear: bool) -> str:
    """secret の保持・更新・削除を判定する。"""
    if clear:
        return ""
    if update:
        return update
    return current


def resolve_api_key(settings: Any, payload: ModelSettingsPayload) -> str:
    """保存後の API key（空欄は現在値を保持、`clear_api_key` は削除）。"""
    enterprise = payload.enterprise_ai
    return _secret_value(
        current=_api_key_of(settings),
        update=enterprise.api_key,
        clear=enterprise.clear_api_key,
    )


def model_settings_data(settings: Any) -> ModelSettingsData:
    """現在の Settings を API data へ変換する（secret を含めない）。"""
    return ModelSettingsData(
        settings=model_payload(settings),
        model_settings_file=settings.model_settings_file,
        source="runtime",
        secret_source=settings.model_secret_source,
        legacy_secret_detected=settings.legacy_model_secret_detected,
    )


# --------------------------------------------------------------------------- persistence


class _PersistedEnterpriseAiSettings(BaseModel):
    endpoint: str = ""
    project_ocid: str = ""
    # v1 / v2（RAG・Agent）の読込互換専用。v3 の writer は secret を JSON へ出力しない。
    api_key: str | None = None
    models: list[EnterpriseAiConfiguredModel] = Field(default_factory=list)
    default_model_id: str = ""
    api_path: str = "/responses"
    vlm_input_mode: EnterpriseAiVlmInputMode = "auto"
    text_payload_template: str = ""
    vision_payload_template: str = ""
    text_response_path: str = ""
    vision_response_path: str = ""
    timeout_seconds: float = 600.0
    max_retries: int = 3
    llm_max_output_tokens: int = 1200
    vlm_max_output_tokens: int = 65536

    @field_validator("vlm_input_mode", mode="before")
    @classmethod
    def normalize_vlm_input_mode(cls, value: object) -> object:
        return str(value).strip().casefold() or "auto"


class _PersistedModelSettings(BaseModel):
    version: int = 1
    enterprise_ai: _PersistedEnterpriseAiSettings = Field(
        default_factory=_PersistedEnterpriseAiSettings
    )
    generative_ai: GenerativeAiModelSettings = Field(default_factory=GenerativeAiModelSettings)


@dataclass(frozen=True)
class ModelSettingsSection:
    """model-settings.json に同居させる製品固有の節（RAG の `parser_adapters` など）。

    - `load(settings, raw, version)`：読込時に呼ぶ。節がなければ `raw` は None。
    - `dump(settings)`：保存時に呼び、節の中身を返す。モデル設定の保存でも消さない。
    """

    name: str
    load: Callable[[Any, Mapping[str, Any] | None, int], None]
    dump: Callable[[Any], Mapping[str, Any]]


class ModelSettingsStore:
    """model-settings.json と `.env` の API key の読み書き。製品ごとに 1 つ作る。"""

    def __init__(
        self,
        *,
        resolve_path: Callable[[Any], Path],
        env_file: Callable[[Any], Path],
        sections: Sequence[ModelSettingsSection] = (),
    ) -> None:
        self._resolve_path = resolve_path
        self._env_file = env_file
        self._sections = tuple(sections)
        self._loaded: tuple[str, int | None] | None = None

    def path(self, settings: Any) -> Path:
        return self._resolve_path(settings)

    def env_file(self, settings: Any) -> Path:
        """API key を保存する `.env`。"""
        return self._env_file(settings)

    def reset(self) -> None:
        """テストや明示的な再初期化のため、最後に読んだファイルの記録を消す。"""
        self._loaded = None

    def load(self, settings: Any, *, refresh_secret: bool = False) -> None:
        """保存済みの JSON があれば Settings へ上書き適用する。

        `.env` に API key があればそれを使う。`refresh_secret`（別 worker が保存した後の再読込）の
        ときは、`.env` から key が消えていれば未設定にする。
        """
        settings.prepare_model_secret_state(
            self._environment_api_key(settings, refresh=refresh_secret)
        )
        path = self.path(settings)
        if not path.is_file():
            self._loaded = (str(path), None)
            return
        try:
            mtime_ns = path.stat().st_mtime_ns
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("JSON object ではありません。")
            persisted = _PersistedModelSettings.model_validate(raw)
            self._apply(settings, persisted)
            for section in self._sections:
                section_raw = raw.get(section.name)
                section.load(
                    settings,
                    section_raw if isinstance(section_raw, Mapping) else None,
                    persisted.version,
                )
        except (OSError, ValueError) as exc:
            raise ValueError(f"モデル設定ファイルを読み込めません: {path}") from exc
        if settings.legacy_model_secret_detected:
            logger.warning(
                "legacy_model_secret_detected",
                extra={"warning_code": "MODEL_SETTINGS_LEGACY_SECRET"},
            )
        self._loaded = (str(path), mtime_ns)

    def reload_if_changed(self, settings: Any) -> None:
        """別 worker が保存したモデル設定を次回リクエストで取り込む。"""
        path = self.path(settings)
        try:
            mtime_ns = path.stat().st_mtime_ns if path.is_file() else None
        except OSError:
            mtime_ns = None
        if self._loaded == (str(path), mtime_ns):
            return
        if mtime_ns is None:
            self._loaded = (str(path), None)
            return
        self.load(settings, refresh_secret=True)

    @contextmanager
    def lock(self, settings: Any) -> Iterator[None]:
        """model-settings.json の read-modify-write を worker 間で直列化する。"""
        path = self.path(settings)
        lock_path = path.with_name(f"{path.name}.lock")
        _ensure_directory(lock_path.parent)
        with lock_path.open("a", encoding="utf-8") as lock_file:
            lock_path.chmod(MODEL_SETTINGS_FILE_MODE)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def save(self, settings: Any, payload: ModelSettingsPayload, *, api_key: str) -> None:
        """`.env` の API key → JSON の順に保存する。JSON に失敗したら `.env` を元に戻す。

        `.env` を先に書くのは、JSON の mtime を見て再読込する別 worker が新しい key を読めるように
        するため。`lock()` の中で呼ぶ。失敗時は OSError を送出する。
        """
        env_file = self.env_file(settings)
        previous = _dotenv_value(env_file)
        self._write_api_key(env_file, api_key.strip() or None)
        try:
            self.write_document(settings, payload)
        except OSError:
            self._write_api_key(env_file, previous or None)
            raise

    def write_document(self, settings: Any, payload: ModelSettingsPayload) -> None:
        """JSON を atomic に保存する（secret は書かない）。`lock()` の中で呼ぶ。"""
        path = self.path(settings)
        document = _model_settings_document(payload)
        for section in self._sections:
            document[section.name] = dict(section.dump(settings))
        _ensure_directory(path.parent)
        tmp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
        try:
            tmp_path.write_text(
                json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp_path.chmod(MODEL_SETTINGS_FILE_MODE)
            tmp_path.replace(path)
            path.chmod(MODEL_SETTINGS_FILE_MODE)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _environment_api_key(self, settings: Any, *, refresh: bool) -> str | None:
        """`.env` の API key。None は「分からないので現在の基準値を使う」。"""
        # プロセス環境変数は `.env` より優先される（pydantic-settings と同じ）。
        if ENTERPRISE_AI_API_KEY_ENV in os.environ:
            return os.environ[ENTERPRISE_AI_API_KEY_ENV]
        env_file = self.env_file(settings)
        value = _dotenv_value(env_file)
        if value is not None:
            return value
        return "" if refresh and env_file.is_file() else None

    @staticmethod
    def _write_api_key(env_file: Path, value: str | None) -> None:
        write_env_values(
            env_file,
            {ENTERPRISE_AI_API_KEY_ENV: value},
            section_comment="# OCI Enterprise AI secret",
        )

    @staticmethod
    def _apply(settings: Any, persisted: _PersistedModelSettings) -> None:
        enterprise = persisted.enterprise_ai
        models = [model for model in enterprise.models if model.model_id]
        default_model = enterprise.default_model_id or (models[0].model_id if models else "")
        apply_model_settings(
            settings,
            ModelSettingsPayload.model_construct(
                enterprise_ai=EnterpriseAiModelSettings.model_construct(
                    **enterprise.model_dump(exclude={"api_key", "models", "default_model_id"}),
                    models=models,
                    default_model_id=default_model,
                ),
                generative_ai=persisted.generative_ai,
            ),
        )
        # 保存済み JSON では Vision モデルがなければ空にする（default へ fallback しない）。
        settings.oci_enterprise_ai_vlm_model = _vision_model_id(models, default_model)
        legacy_secret = (enterprise.api_key or "").strip()
        settings.apply_legacy_enterprise_ai_api_key(
            legacy_secret if persisted.version < MODEL_SETTINGS_DOCUMENT_VERSION else "",
            detected=bool(legacy_secret),
        )


def _model_settings_document(payload: ModelSettingsPayload) -> dict[str, Any]:
    enterprise = payload.enterprise_ai
    generative = payload.generative_ai
    return {
        "version": MODEL_SETTINGS_DOCUMENT_VERSION,
        "enterprise_ai": {
            "endpoint": enterprise.endpoint,
            "project_ocid": enterprise.project_ocid,
            "models": [
                model.model_dump(include={"model_id", "display_name", "vision_enabled"})
                for model in enterprise.models
                if model.model_id
            ],
            "default_model_id": enterprise.default_model_id,
            "api_path": enterprise.api_path,
            "vlm_input_mode": enterprise.vlm_input_mode,
            "text_payload_template": enterprise.text_payload_template,
            "vision_payload_template": enterprise.vision_payload_template,
            "text_response_path": enterprise.text_response_path,
            "vision_response_path": enterprise.vision_response_path,
            "timeout_seconds": enterprise.timeout_seconds,
            "max_retries": enterprise.max_retries,
            "llm_max_output_tokens": enterprise.llm_max_output_tokens,
            "vlm_max_output_tokens": enterprise.vlm_max_output_tokens,
        },
        "generative_ai": generative.model_dump(),
    }


def _dotenv_value(env_file: Path) -> str | None:
    if not env_file.is_file():
        return None
    return dotenv_values(env_file).get(ENTERPRISE_AI_API_KEY_ENV)


def _ensure_directory(path: Path) -> None:
    existed = path.exists()
    path.mkdir(mode=MODEL_SETTINGS_DIRECTORY_MODE, parents=True, exist_ok=True)
    if not existed:
        path.chmod(MODEL_SETTINGS_DIRECTORY_MODE)


# --------------------------------------------------------------------------- model test


def model_test_candidate(settings: Any, request: ModelSettingsTestRequest) -> Any:
    """保存前 payload を、対象モデルだけを呼ぶ一時 Settings へ変換する。"""
    candidate = settings.model_copy(deep=True)
    apply_model_settings(candidate, request.settings)
    candidate.oci_enterprise_ai_api_key = resolve_api_key(settings, request.settings)
    model_id = request.model_id
    if request.target_type in {"enterprise_text", "enterprise_vision"}:
        candidate.oci_enterprise_ai_default_model = model_id
        candidate.oci_enterprise_ai_llm_model = model_id
    if request.target_type == "enterprise_vision":
        candidate.oci_enterprise_ai_vlm_model = model_id
        candidate.oci_enterprise_ai_models = [
            model.model_copy(
                update={"vision_enabled": model.model_id == model_id or model.vision_enabled}
            )
            for model in enterprise_ai_model_catalog(candidate)
        ]
    elif request.target_type == "embedding":
        candidate.oci_genai_embedding_model = model_id
        _set_if_field(candidate, "oci_genai_embed_model_id", model_id)
    elif request.target_type == "rerank":
        candidate.oci_genai_rerank_model = model_id
        _set_if_field(candidate, "oci_genai_rerank_model_id", model_id)
    return candidate


def _model_test_success_message(target_type: ModelSettingsTestTargetType, model_id: str) -> str:
    if target_type == "enterprise_text":
        return f"Enterprise AI の回答生成モデル「{model_id}」から応答を取得しました。"
    if target_type == "enterprise_vision":
        return (
            f"Enterprise AI の Vision モデル「{model_id}」から構造化抽出レスポンスを取得しました。"
        )
    if target_type == "embedding":
        return f"Embedding モデル「{model_id}」で 1536 次元ベクトルを取得しました。"
    return f"Rerank モデル「{model_id}」から順位スコアを取得しました。"


def _model_test_failure_message(target_type: ModelSettingsTestTargetType, model_id: str) -> str:
    if target_type in {"enterprise_text", "enterprise_vision"}:
        return f"Enterprise AI モデル「{model_id or '未入力'}」のテストに失敗しました。"
    if target_type == "embedding":
        return f"Embedding モデル「{model_id or '未入力'}」のテストに失敗しました。"
    return f"Rerank モデル「{model_id or '未入力'}」のテストに失敗しました。"


def _model_test_troubleshooting(
    target_type: ModelSettingsTestTargetType,
    raw_error: str,
    error_type: str,
) -> list[str]:
    """実エラーからユーザーが次に確認しやすい項目を返す。"""
    lowered = f"{raw_error} {error_type}".lower()
    tips: list[str] = []
    if target_type in {"enterprise_text", "enterprise_vision"}:
        tips.extend(
            [
                "Endpoint URL、API パス、Project OCID、API key が Enterprise AI の"
                " OpenAI-compatible gateway と一致しているか確認してください。",
                "モデル ID が Enterprise AI 側の model deployment / gateway で"
                "利用可能か確認してください。",
            ]
        )
        if "response path" in lowered or "回答 text" in raw_error or "構造化抽出" in raw_error:
            tips.append(
                "独自 gateway の場合は payload template と response path が"
                "実レスポンスの JSON 構造に合っているか確認してください。"
            )
    else:
        tips.extend(
            [
                "OCI config file、profile、region、compartment OCID が"
                "バックエンド実行環境から参照できるか確認してください。",
                "モデル ID と IAM policy が OCI Generative AI Inference の"
                " embedding/rerank 呼び出しを許可しているか確認してください。",
            ]
        )
    if any(token in lowered for token in ("401", "unauthorized", "authentication")):
        tips.append(
            "認証エラーです。API key / OCI config の資格情報を再発行または再保存してください。"
        )
    if any(token in lowered for token in ("403", "notauthorized", "not authorized", "forbidden")):
        tips.append(
            "権限エラーです。Project / compartment / IAM policy の対象が"
            "このモデル呼び出しを許可しているか確認してください。"
        )
    if any(token in lowered for token in ("404", "not found")):
        tips.append(
            "Endpoint、API パス、model ID のいずれかが見つかっていません。"
            "リージョンと model deployment 名も確認してください。"
        )
    if any(token in lowered for token in ("timeout", "timed out")):
        tips.append(
            "タイムアウトです。ネットワーク経路を確認し、"
            "必要ならタイムアウト秒数を一時的に長くしてください。"
        )
    if any(token in lowered for token in ("429", "quota", "rate")):
        tips.append(
            "レート制限または quota の可能性があります。"
            "しばらく待つか service limit を確認してください。"
        )
    if any(token in lowered for token in ("500", "502", "503", "504")):
        tips.append(
            "サービス側または gateway 側の一時障害の可能性があります。"
            "少し待って再試行し、OCI 側の稼働状況を確認してください。"
        )
    return list(dict.fromkeys(tips))


def sanitize_model_test_error(raw_error: str, secrets: Sequence[str]) -> str:
    """実エラーは残しつつ、既知の secret だけを伏せる。"""
    sanitized = raw_error.strip() or "詳細メッセージは返されませんでした。"
    for secret in secrets:
        cleaned = secret.strip()
        if cleaned:
            sanitized = sanitized.replace(cleaned, "<secret>")
    return sanitized[:2000]


async def run_model_settings_test(
    settings: Any,
    request: ModelSettingsTestRequest,
    run_model_test: RunModelTest,
) -> ModelSettingsTestResult:
    """対象モデルを実 API で呼び、表示用の結果へ変換する（例外は結果に含める）。"""
    started = time.perf_counter()
    candidate = model_test_candidate(settings, request)
    try:
        if not request.model_id:
            raise ValueError("テストするモデル ID を入力してください。")
        details = await run_model_test(candidate, request)
    except Exception as exc:  # noqa: BLE001 - 外部 SDK/API の多様な例外を表示用に握る
        raw_error = sanitize_model_test_error(str(exc), [_api_key_of(candidate)])
        return ModelSettingsTestResult(
            status="failed",
            target_type=request.target_type,
            model_id=request.model_id,
            message=_model_test_failure_message(request.target_type, request.model_id),
            troubleshooting=_model_test_troubleshooting(
                request.target_type, raw_error, type(exc).__name__
            ),
            raw_error=raw_error,
            error_type=type(exc).__name__,
            elapsed_ms=_elapsed_ms(started),
        )
    return ModelSettingsTestResult(
        status="success",
        target_type=request.target_type,
        model_id=request.model_id,
        message=_model_test_success_message(request.target_type, request.model_id),
        elapsed_ms=_elapsed_ms(started),
        details=details,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


# --------------------------------------------------------------------------- router

RunModelTest = Callable[[Any, ModelSettingsTestRequest], Awaitable[ModelTestDetails]]


def save_model_settings(
    settings: Any, store: ModelSettingsStore, payload: ModelSettingsPayload
) -> None:
    """保存が成功してから runtime へ反映する。失敗時は HTTPException(500)。"""
    try:
        with store.lock(settings):
            store.reload_if_changed(settings)
            api_key = resolve_api_key(settings, payload)
            store.save(settings, payload, api_key=api_key)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail="モデル設定を永続化ファイルへ保存できませんでした。"
        ) from exc
    apply_model_settings(settings, payload)
    settings.set_runtime_enterprise_ai_api_key(api_key)


def build_model_router(
    *,
    get_settings: Callable[[], Any],
    store: ModelSettingsStore,
    run_model_test: RunModelTest,
    write_dependencies: Sequence[params.Depends] = (),
    action_dependencies: Sequence[params.Depends] = (),
) -> APIRouter:
    """`/model*` の router を作る。製品側で `/settings` 配下に include する。

    - `run_model_test(candidate_settings, request)`：対象モデルを実際に呼び、表示用 details を返す
    - `write_dependencies`：PATCH に付ける依存関係
    - `action_dependencies`：外部へ通信する接続テストに付ける依存関係
    """
    router = APIRouter()

    @router.get("/model", response_model=ApiResponse[ModelSettingsData])
    def get_model_settings() -> ApiResponse[ModelSettingsData]:
        return ApiResponse(data=model_settings_data(get_settings()))

    @router.patch(
        "/model",
        response_model=ApiResponse[ModelSettingsData],
        dependencies=list(write_dependencies),
    )
    def update_model_settings(payload: ModelSettingsPayload) -> ApiResponse[ModelSettingsData]:
        settings = get_settings()
        save_model_settings(settings, store, payload)
        return ApiResponse(data=model_settings_data(settings))

    @router.post(
        "/model/test",
        response_model=ApiResponse[ModelSettingsTestResult],
        dependencies=list(action_dependencies),
    )
    async def test_model_settings(
        request: ModelSettingsTestRequest,
    ) -> ApiResponse[ModelSettingsTestResult]:
        result = await run_model_settings_test(get_settings(), request, run_model_test)
        return ApiResponse(data=result)

    return router
