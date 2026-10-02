"""Agent Runtime API。

業務 RAG / NL2SQL は外部ツールとして扱い、このサービスは実行管理・
権限・監査・表示用の標準契約を提供する。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import stat
from asyncio import sleep, wait_for
from collections.abc import Callable, Iterable, Mapping
from csv import DictWriter
from datetime import UTC, datetime
from importlib import import_module
from io import StringIO
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any, cast

import httpx
from anyio import fail_after
from anyio import to_thread as anyio_to_thread
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response, StreamingResponse
from pr_backend_core import ApiResponse
from pr_backend_core.mcp import mcp_http_response
from pr_system_settings.database import build_database_router
from pr_system_settings.database_status import (
    DatabaseSchemaProbeResult,
    build_database_status_router,
)
from pr_system_settings.model import (
    EnterpriseAiConnection,
    EnterpriseAiModelSettings,
    GenerativeAiModelSettings,
    ModelSettingsTestRequest,
    build_model_router,
    enterprise_ai_connection_for_model,
    model_payload,
)
from pr_system_settings.model_test_input import (
    MODEL_TEST_EMBEDDING_INPUT,
    MODEL_TEST_RERANK_DOCUMENTS,
    MODEL_TEST_RERANK_QUERY,
    MODEL_TEST_TEXT_PROMPT,
    MODEL_TEST_VISION_PROMPT,
    model_test_image_data_url,
    model_test_text_context,
)
from pr_system_settings.oci import build_oci_router
from pr_system_settings.oci_auth import (
    load_oci_config_without_prompt as _load_oci_config_without_prompt,
)
from pr_system_settings.upload_storage import (
    build_upload_storage_router,
)
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

import app.settings as app_settings
from app.features.agent import builtin_runtime, control_plane_store
from app.features.agent.api_keys import (
    ApiKeyCreated,
    ApiKeyCreateRequest,
    ApiKeysListData,
    api_key_registry,
    key_view,
)
from app.features.agent.config import McpAuthMode, McpConnectionConfig, runtime_config_store
from app.features.agent.control_plane_store import (
    ControlPlaneStoreError,
    delete_api_key,
    get_control_plane_store,
    save_api_key,
)
from app.features.agent.mcp_server import build_agent_mcp_server
from app.features.agent.plugins import (
    MarketplaceListing,
    MarketplaceSource,
    MarketplaceSourcesOutput,
    PluginListOutput,
    PluginManifest,
    PluginRecord,
    PluginSummary,
    marketplace_registry,
    plugin_registry,
    reload_declared_plugins,
)
from app.features.agent.runtime import (
    BUILTIN_RUNTIME_ID,
    AgentProfile,
    AgentProfilePatch,
    AgentRuntimeSnapshot,
    AgentRuntimeSnapshotValidation,
    AgentsData,
    ApprovalDecisionRequest,
    Artifact,
    ArtifactsData,
    RunCreateRequest,
    RunEvent,
    RunsData,
    RunState,
    RunStatus,
    RuntimeToolCallAuditData,
    builtin_resume_pending,
    runtime_repository,
)
from app.features.agent.skills import (
    AgentSkillDefinition,
    AgentSkillListOutput,
    SkillMcpRequirement,
    reload_declared_skills,
    skill_registry,
)
from app.features.agent.tools import (
    MCP_TOOL_SEPARATOR,
    ExternalMcpToolsData,
    ExternalToolError,
    ToolCall,
    ToolDefinitionsData,
    ToolInvocationContext,
    ToolPolicy,
    ToolResult,
    list_mcp_connection_tools,
    tool_registry,
)
from app.features.agent.user_names import user_display_names
from app.observability import (
    ObservabilityStatus,
    TraceEventsData,
    TraceExportRetryData,
    TracePolicyPatch,
    TracePolicySettings,
    flush_trace_export_retry_queue,
    get_trace_policy,
    list_trace_events,
    patch_trace_policy,
    trace_exporter_status,
)
from app.oracle_connection import oracle_connect_kwargs
from app.security.dependencies import (
    WebSocketAuthRejected,
    actor_roles_for_principal,
    authenticate_websocket,
    permission_route_path,
)
from app.security.domain import Principal
from app.security.permissions import UNCLASSIFIED_PERMISSION, permission_for_route
from app.security.service import get_security_service
from app.settings import MODEL_SETTINGS_STORE, get_settings
from app.system_schema import system_schema_manager

router = APIRouter(tags=["agent-runtime"])
logger = logging.getLogger(__name__)

PASSPHRASE_CONFIG_KEYS = frozenset({"pass_phrase", "passphrase", "key_password"})
_WEBSOCKET_COMMAND_DEDUPE_TTL_SECONDS = 300.0
_WEBSOCKET_COMMAND_DEDUPE_MAX_ENTRIES = 2000
_websocket_command_dedupe: dict[tuple[str, str], tuple[str, float]] = {}


_MCP_TIMEOUT_MAX_SECONDS = 600


def _validate_mcp_timeout(value: float | None) -> float | None:
    """外部 MCP のタイムアウト秒（画面の「タイムアウト秒」と同じ規則・文言。#540）。

    省略（None）は既定値（作成）・変更なし（更新）。
    0 以下を保存すると呼び出しがすぐ失敗するため受け付けない。
    """
    if value is None:
        return None
    if not 0 < value <= _MCP_TIMEOUT_MAX_SECONDS:
        raise ValueError(
            f"タイムアウト秒は 0 より大きく {_MCP_TIMEOUT_MAX_SECONDS} 以下の"
            "数値を入力してください。"
        )
    return value


_MCP_CONNECTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


def _validate_mcp_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if value and not re.match(r"^https?://[^\s/]+", value):
        raise ValueError("MCP の URL は http:// または https:// で始めてください。")
    return value


class McpConnectionSettings(BaseModel):
    """MCP 接続の公開設定（#757。資格情報の値は返さず、設定済みかだけを返す）。"""

    server_id: str
    label: str | None = None
    base_url: str | None = None
    auth_mode: McpAuthMode
    service_audience: str | None = None
    timeout_seconds: float
    # 由来: builtin(RAG / NL2SQL) / env / plugin:<id> / runtime。runtime だけ削除できる。
    source: str
    removable: bool
    # URL と認証方式に必要な資格情報がそろい、呼び出せる状態か。
    configured: bool
    api_key_configured: bool = False
    oauth_configured: bool = False
    session_configured: bool = False
    # サービストークン: 共通 `.env` の PLATFORM_SERVICE_TOKEN_SECRET と、利用者のいない呼び出しの
    # AGENT_MCP_SERVICE_USER_LOGIN_ID（値は返さない）。
    service_token_configured: bool = False
    service_user_configured: bool = False


class McpConnectionsData(BaseModel):
    connections: list[McpConnectionSettings] = Field(default_factory=list)


class McpConnectionPatch(BaseModel):
    label: str | None = None
    base_url: str | None = None
    auth_mode: McpAuthMode | None = None
    api_key: str | None = None
    timeout_seconds: float | None = None
    session_id: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scope: str | None = None
    service_audience: str | None = None

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: float | None) -> float | None:
        return _validate_mcp_timeout(value)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        return _validate_mcp_url(value)


class McpConnectionCreate(McpConnectionPatch):
    server_id: str

    @field_validator("server_id")
    @classmethod
    def _validate_server_id(cls, value: str) -> str:
        value = value.strip()
        # ID はモデルに渡すツール名（`<接続>__<ツール>`）の先頭になる。
        if not _MCP_CONNECTION_ID_PATTERN.match(value) or "__" in value or value == "control-plane":
            raise ValueError(
                "接続 ID は英数字で始まる 40 文字以内の英数字・「-」・「_」で入力してください"
                "（「__」と control-plane は使えません）。"
            )
        return value


class AgentSkillCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    instructions: str = ""
    mcp_requirements: list[SkillMcpRequirement] = Field(default_factory=list)
    resource_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class AgentSkillPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    mcp_requirements: list[SkillMcpRequirement] | None = None
    resource_ids: list[str] | None = None
    enabled: bool | None = None
    tags: list[str] | None = None


class PluginInstallRequest(BaseModel):
    manifest: PluginManifest | None = None
    marketplace_id: str | None = None
    plugin_id: str | None = None


class PluginPatch(BaseModel):
    enabled: bool | None = None


class MarketplaceAddRequest(BaseModel):
    id: str
    name: str = ""
    url: str | None = None
    listing: MarketplaceListing | None = None


class ToolPolicySettings(BaseModel):
    default_mode: str = "approval"
    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class ToolPolicySettingsPatch(BaseModel):
    default_mode: str | None = None
    allow: list[str] | None = None
    ask: list[str] | None = None
    deny: list[str] | None = None


class ToolAuditRecord(BaseModel):
    step_id: str
    tool_name: str
    status: str
    approval_id: str | None = None
    approval_status: str | None = None
    policy_decision: str | None = None
    permission_level: str | None = None
    side_effects: bool | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None
    success: bool | None = None
    error: str | None = None
    error_code: str | None = None
    guardrail_warnings: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    audit_metadata: dict[str, object] = Field(default_factory=dict)


class RunAuditData(BaseModel):
    run_id: str
    goal: str
    status: str
    records: list[ToolAuditRecord]


class ToolCallAuditRecord(ToolAuditRecord):
    run_id: str
    run_goal: str
    run_status: str
    agent_id: str
    run_created_at: str
    run_updated_at: str


class ToolCallAuditData(BaseModel):
    total: int
    offset: int
    limit: int
    filters: dict[str, object] = Field(default_factory=dict)
    records: list[ToolCallAuditRecord]


class RuntimeSnapshotImportRequest(BaseModel):
    snapshot: AgentRuntimeSnapshot
    dry_run: bool = True
    confirm_replace: bool = False
    reason: str | None = None


class RuntimeSnapshotImportResult(BaseModel):
    imported: bool
    dry_run: bool
    validation: AgentRuntimeSnapshotValidation
    reason: str | None = None


class ActorPolicy(BaseModel):
    roles: set[str] = Field(default_factory=set)
    agent_ids: set[str] | None = None


async def require_viewer(request: Request) -> None:
    _require_actor_roles(request, {"viewer", "operator", "approver", "auditor"})


async def require_operator(request: Request) -> None:
    _require_actor_roles(request, {"operator"})


async def require_approver(request: Request) -> None:
    _require_actor_roles(request, {"approver"})


async def require_auditor(request: Request) -> None:
    _require_actor_roles(request, {"auditor"})


async def require_admin(request: Request) -> None:
    _require_actor_roles(request, {"admin"})


async def require_system_settings_write(request: Request) -> None:
    """共通のシステム設定の保存・操作（OCI 認証・アップロード保存先・モデル・データベース。#215）。

    RAG / NL2SQL と同じくメニュー権限（manifest の同じ判定）で許可する。local のローカル利用者は
    全権限を持つ。
    """
    principal = _request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="ログインしてください。")
    permissions = permission_for_route(request.method, permission_route_path(request))
    if (
        permissions is None
        or UNCLASSIFIED_PERMISSION in permissions
        or not principal.has_any_permission(set(permissions))
    ):
        raise HTTPException(
            status_code=403,
            detail=f"actor {principal.login_user_id} cannot change system settings",
        )


# アップロード保存先は3製品共通の実装（platform の pr_system_settings。#97）。
# 保存先は3製品共通の `.env`（app.settings.PLATFORM_ENV_FILE。#211）。
# テストで get_settings / PLATFORM_ENV_FILE を差し替えられるよう、呼出時に module の値を参照する。
router.include_router(
    build_upload_storage_router(
        get_settings=lambda: get_settings(),
        env_file=lambda: app_settings.PLATFORM_ENV_FILE,
        write_dependencies=[Depends(require_system_settings_write)],
    ),
    prefix="/settings",
)
# OCI 認証も3製品共通の実装（pr_system_settings.oci。#100）。
# config 読込・接続テスト・namespace 取得もローカルの認証ファイルを読むため管理者に限定する。
router.include_router(
    build_oci_router(
        get_settings=lambda: get_settings(),
        env_file=lambda: app_settings.PLATFORM_ENV_FILE,
        write_dependencies=[Depends(require_system_settings_write)],
        action_dependencies=[Depends(require_system_settings_write)],
    ),
    prefix="/settings",
)
# データベース設定も3製品共通の実装（pr_system_settings.database。#108）。
# 接続は Agent の Thin mode 実装を渡す。接続テストも管理者に限定する。
router.include_router(
    build_database_router(
        get_settings=lambda: get_settings(),
        env_file=lambda: app_settings.PLATFORM_ENV_FILE,
        test_connection=lambda candidate: _test_database_connection(candidate),
        write_dependencies=[Depends(require_system_settings_write)],
        action_dependencies=[Depends(require_system_settings_write)],
    ),
    prefix="/settings",
)
# DB の状態 API（`GET /api/ready/database`。3製品共通の判定と契約。#325）。画面の DB ゲートが使う。
# ログイン不要の公開 path（`app.security.permissions.PUBLIC_API_PATHS`）。RAG / NL2SQL と同じく、
# 設定の判定・接続確認に加えて Agent のシステムテーブルの状態を確かめる（#751）。local でも
# 共通認証のユーザー・ロールは共通 DB にあるため、短絡しない。
router.include_router(
    build_database_status_router(
        get_settings=lambda: get_settings(),
        test_connection=lambda settings: _test_database_connection(settings),
        schema_probe=lambda settings: _system_schema_probe(settings),
    )
)


async def _system_schema_probe(_settings: object) -> DatabaseSchemaProbeResult:
    """Agent のシステムテーブルの状態（例外は共通部品が setup_required へ正規化する）。"""
    schema = await run_in_threadpool(system_schema_manager.status)
    schema_status = schema["status"]
    if schema_status != "ready" or schema["operation_state"]["status"] == "running":
        return DatabaseSchemaProbeResult(status="setup_required", schema_status=schema_status)
    return DatabaseSchemaProbeResult(status="ok", schema_status="ready")


# モデル設定も3製品共通の実装（pr_system_settings.model。#103）。
# 接続テストは外部へ通信するため管理者に限定する。
router.include_router(
    build_model_router(
        get_settings=lambda: get_settings(),
        store=MODEL_SETTINGS_STORE,
        run_model_test=lambda settings, request: _run_model_settings_test(settings, request),
        write_dependencies=[Depends(require_system_settings_write)],
        action_dependencies=[Depends(require_system_settings_write)],
    ),
    prefix="/settings",
)


def _settings_attr(name: str, default: object) -> object:
    return getattr(get_settings(), name, default)


def _settings_str_from(settings: object, name: str, default: str = "") -> str:
    value = getattr(settings, name, default)
    return str(default if value is None else value)


def _settings_str(name: str, default: str = "") -> str:
    return _settings_str_from(get_settings(), name, default)


def _settings_int_from(settings: object, name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return _coerce_int(value, default)


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _settings_int(name: str, default: int) -> int:
    return _settings_int_from(get_settings(), name, default)


def _settings_float_from(settings: object, name: str, default: float) -> float:
    value = getattr(settings, name, default)
    return _coerce_float(value, default)


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _settings_float(name: str, default: float) -> float:
    return _settings_float_from(get_settings(), name, default)


def _is_present(value: str) -> bool:
    return bool(value.strip())


def _json_pointer_or_empty(value: str) -> str:
    normalized = value.strip()
    return normalized if not normalized or normalized.startswith("/") else ""


async def _run_model_settings_test(
    settings: Any,
    request: ModelSettingsTestRequest,
) -> dict[str, str | int | float | bool | None]:
    """対象モデルの実 API 呼び出しを行い、表示用 details を返す（共有 router の hook）。

    `settings` は保存前の入力値と対象モデルを反映した一時 Settings（#103）。
    Enterprise AI はテストするモデルの接続（Endpoint・Project・API key）で呼ぶ（#533）。
    """
    payload = model_payload(settings)
    enterprise_ai = payload.enterprise_ai
    connection = enterprise_ai_connection_for_model(settings, request.model_id)
    if request.target_type == "enterprise_text":
        text = await _run_enterprise_text_model_test(enterprise_ai, connection)
        return {"response_chars": len(text), "surface": "llm"}
    if request.target_type == "enterprise_vision":
        text = await _run_enterprise_vision_model_test(enterprise_ai, connection)
        return {"response_chars": len(text), "surface": "vision"}
    if request.target_type == "embedding":
        vector_dim = await _run_oci_embedding_model_test(settings, payload.generative_ai)
        return {"vector_dim": vector_dim, "input_count": 1}
    ranked_count, top_score = await _run_oci_rerank_model_test(settings, payload.generative_ai)
    return {"ranked_count": ranked_count, "top_score": top_score}


async def _run_enterprise_text_model_test(
    settings: EnterpriseAiModelSettings, connection: EnterpriseAiConnection
) -> str:
    payload = _enterprise_text_payload(
        settings,
        prompt=MODEL_TEST_TEXT_PROMPT,
        context=model_test_text_context("Production Ready Agent"),
        project_ocid=connection.project_ocid,
    )
    response = await _post_enterprise_ai(settings, connection, settings.api_path, payload)
    return _parse_enterprise_text_response(response, settings.text_response_path)


async def _run_enterprise_vision_model_test(
    settings: EnterpriseAiModelSettings, connection: EnterpriseAiConnection
) -> str:
    payload = _enterprise_vision_payload(
        settings,
        prompt=MODEL_TEST_VISION_PROMPT,
        project_ocid=connection.project_ocid,
    )
    response = await _post_enterprise_ai(settings, connection, settings.api_path, payload)
    return _parse_enterprise_text_response(response, settings.vision_response_path)


def _enterprise_text_model_id(settings: EnterpriseAiModelSettings) -> str:
    """画像を扱わない呼び出しのモデル（既定のテキストモデル、未設定なら既定の Vision モデル）。"""
    return settings.default_text_model_id or settings.default_vision_model_id


def _enterprise_text_payload(
    settings: EnterpriseAiModelSettings,
    *,
    prompt: str,
    context: str,
    project_ocid: str = "",
) -> dict[str, object]:
    system_prompt = "根拠に基づいて日本語で簡潔に回答してください。"
    user_message = f"{context}\n\n質問: {prompt}" if context else prompt
    values = {
        "model": _enterprise_text_model_id(settings),
        "project": project_ocid,
        "project_ocid": project_ocid,
        "prompt": prompt,
        "context": context,
        "system_prompt": system_prompt,
        "user_message": user_message,
        "input": [{"role": "user", "content": user_message}],
        "instructions": system_prompt,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_output_tokens": settings.llm_max_output_tokens,
    }
    if settings.text_payload_template.strip():
        return _render_model_payload_template(settings.text_payload_template, values)
    return {
        "model": _enterprise_text_model_id(settings),
        "instructions": system_prompt,
        "input": [{"role": "user", "content": user_message}],
        "temperature": 0,
        "max_output_tokens": settings.llm_max_output_tokens,
    }


def _enterprise_vision_payload(
    settings: EnterpriseAiModelSettings,
    *,
    prompt: str,
    project_ocid: str = "",
) -> dict[str, object]:
    content = [
        {"type": "input_text", "text": prompt},
        {"type": "input_image", "image_url": model_test_image_data_url()},
    ]
    values = {
        "model": settings.default_vision_model_id,
        "project": project_ocid,
        "project_ocid": project_ocid,
        "prompt": prompt,
        "input": [{"role": "user", "content": content}],
        "messages": [{"role": "user", "content": content}],
        "max_output_tokens": settings.vlm_max_output_tokens,
    }
    if settings.vision_payload_template.strip():
        return _render_model_payload_template(settings.vision_payload_template, values)
    return {
        "model": settings.default_vision_model_id,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": settings.vlm_max_output_tokens,
    }


def _render_model_payload_template(
    template: str,
    values: Mapping[str, object],
) -> dict[str, object]:
    try:
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{{" + key + "}}", json.dumps(value, ensure_ascii=False))
            rendered = rendered.replace("{" + key + "}", str(value))
        parsed = json.loads(rendered)
    except ValueError as exc:
        raise ValueError("payload template は JSON object で入力してください。") from exc
    if not isinstance(parsed, dict):
        raise ValueError("payload template は JSON object で入力してください。")
    return parsed


async def _post_enterprise_ai(
    settings: EnterpriseAiModelSettings,
    connection: EnterpriseAiConnection,
    api_path: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    endpoint = connection.endpoint.rstrip("/")
    url = (
        api_path
        if api_path.startswith(("http://", "https://"))
        else endpoint + "/" + api_path.lstrip("/")
    )
    api_key = _require_non_empty(connection.api_key, "OCI Enterprise AI API key")
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if connection.project_ocid:
        headers["OpenAI-Project"] = connection.project_ocid
    async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
        response = await client.post(url, headers=headers, json=dict(payload))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, Mapping):
        raise ValueError("Enterprise AI response は JSON object である必要があります。")
    return data


def _parse_enterprise_text_response(
    response: Mapping[str, object],
    response_path: str,
) -> str:
    if response_path.strip():
        value = _json_pointer_value(response, response_path)
        if isinstance(value, str) and value.strip():
            return value
        raise ValueError("Enterprise AI response path から回答 text を取得できませんでした。")
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, Mapping):
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        return text
    raise ValueError("Enterprise AI response から回答 text を取得できませんでした。")


def _json_pointer_value(document: Mapping[str, object], pointer: str) -> object:
    current: object = document
    for raw_part in pointer.strip().split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            current = current.get(part)
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
            continue
        return None
    return current


async def _run_oci_embedding_model_test(
    settings: object,
    model_settings: GenerativeAiModelSettings,
) -> int:
    models = import_module("oci.generative_ai_inference.models")
    client = cast(Any, _oci_genai_inference_client(settings))
    details = models.EmbedTextDetails(
        inputs=[MODEL_TEST_EMBEDDING_INPUT],
        serving_mode=models.OnDemandServingMode(model_id=model_settings.embedding_model),
        compartment_id=_require_non_empty(
            _settings_str_from(settings, "oci_compartment_id"),
            "OCI compartment OCID",
        ),
        input_type="SEARCH_QUERY",
        output_dimensions=model_settings.embedding_dim,
    )
    response = await anyio_to_thread.run_sync(lambda: client.embed_text(details))
    embeddings = getattr(response.data, "embeddings", None)
    if not isinstance(embeddings, list) or not embeddings:
        raise ValueError("OCI embedding response に embeddings がありません。")
    vector = embeddings[0]
    if not isinstance(vector, list):
        raise ValueError("OCI embedding response の vector が不正です。")
    if len(vector) != model_settings.embedding_dim:
        raise ValueError(
            "embedding の次元数が不正です。"
            f"expected={model_settings.embedding_dim}, actual={len(vector)}"
        )
    return len(vector)


async def _run_oci_rerank_model_test(
    settings: object,
    model_settings: GenerativeAiModelSettings,
) -> tuple[int, float | None]:
    models = import_module("oci.generative_ai_inference.models")
    client = cast(Any, _oci_genai_inference_client(settings))
    details = models.RerankTextDetails(
        input=MODEL_TEST_RERANK_QUERY,
        documents=list(MODEL_TEST_RERANK_DOCUMENTS),
        serving_mode=models.OnDemandServingMode(model_id=model_settings.rerank_model),
        compartment_id=_require_non_empty(
            _settings_str_from(settings, "oci_compartment_id"),
            "OCI compartment OCID",
        ),
        top_n=1,
    )
    response = await anyio_to_thread.run_sync(lambda: client.rerank_text(details))
    document_ranks = getattr(response.data, "document_ranks", None)
    if not isinstance(document_ranks, list):
        raise ValueError("OCI rerank response に document_ranks がありません。")
    top_score = None
    if document_ranks:
        raw_score = getattr(document_ranks[0], "relevance_score", None)
        top_score = float(raw_score) if raw_score is not None else None
    return len(document_ranks), top_score


def _oci_genai_inference_client(settings: object) -> object:
    oci_config = import_module("oci.config")
    genai = import_module("oci.generative_ai_inference")
    config = _load_oci_config_without_prompt(
        oci_config,
        _settings_str_from(settings, "oci_config_file", "~/.oci/config"),
        _settings_str_from(settings, "oci_config_profile", "DEFAULT"),
    )
    return genai.GenerativeAiInferenceClient(config)


def _require_non_empty(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} を設定してください。")
    return cleaned


async def _test_database_connection(candidate: Any) -> None:
    """共有 router の接続 hook。Agent は python-oracledb の Thin mode だけで接続する。"""
    await _test_oracle_connection(
        SimpleNamespace(
            oracle_user=_settings_str_from(candidate, "oracle_user"),
            oracle_dsn=_settings_str_from(candidate, "oracle_dsn"),
            oracle_password=_settings_str_from(candidate, "oracle_password"),
            oracle_wallet_dir=_settings_str_from(candidate, "resolved_oracle_wallet_dir"),
            oracle_wallet_password=_settings_str_from(candidate, "oracle_wallet_password"),
            oracle_tcp_connect_timeout_seconds=getattr(
                candidate, "oracle_tcp_connect_timeout_seconds", 10.0
            ),
            oracle_db_test_timeout_seconds=getattr(
                candidate, "oracle_db_test_timeout_seconds", 15.0
            ),
        )
    )


async def _test_oracle_connection(settings: SimpleNamespace) -> None:
    timeout_seconds = float(settings.oracle_db_test_timeout_seconds)
    try:
        with fail_after(timeout_seconds):
            await anyio_to_thread.run_sync(_test_oracle_connection_sync, settings)
    except TimeoutError as exc:
        raise OracleConnectionTimeoutError(
            f"Oracle AI Database の接続テストが {timeout_seconds:g} 秒でタイムアウトしました。"
            "データベースの起動状態、Wallet サービス名、ネットワーク到達性を確認してください。"
        ) from exc


def _test_oracle_connection_sync(settings: SimpleNamespace) -> None:
    oracledb = import_module("oracledb")
    connection = oracledb.connect(**_oracle_connect_kwargs(settings))
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1 FROM DUAL")
            cursor.fetchone()
        finally:
            cursor.close()
    finally:
        connection.close()


def _oracle_connect_kwargs(settings: SimpleNamespace) -> dict[str, object]:
    """接続テストの引数（共通の `app.oracle_connection` と同じ規則。#215）。"""
    return oracle_connect_kwargs(settings)


class OracleConnectionTimeoutError(RuntimeError):
    safe_for_user = True


def _pem_file_is_encrypted(path: Path) -> bool:
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    text = head.decode("utf-8", errors="ignore").upper()
    return "BEGIN ENCRYPTED PRIVATE KEY" in text or "PROC-TYPE: 4,ENCRYPTED" in text


def _has_pass_phrase(config: Mapping[str, object]) -> bool:
    return any(str(config.get(key, "") or "").strip() for key in PASSPHRASE_CONFIG_KEYS)


def _zip_member_is_symlink(external_attr: int) -> bool:
    mode = external_attr >> 16
    return bool(mode and stat.S_ISLNK(mode))


@router.get("/runtime/snapshot", response_model=ApiResponse[AgentRuntimeSnapshot])
async def export_runtime_snapshot(
    _: None = Depends(require_admin),
) -> ApiResponse[AgentRuntimeSnapshot]:
    return ApiResponse(data=runtime_repository.export_snapshot())


@router.post(
    "/runtime/snapshot/import",
    response_model=ApiResponse[RuntimeSnapshotImportResult],
)
async def import_runtime_snapshot(
    request: RuntimeSnapshotImportRequest,
    _: None = Depends(require_admin),
) -> ApiResponse[RuntimeSnapshotImportResult]:
    validation = runtime_repository.validate_snapshot(request.snapshot)
    if request.dry_run:
        return ApiResponse(
            data=RuntimeSnapshotImportResult(
                imported=False,
                dry_run=True,
                validation=validation,
                reason=request.reason,
            )
        )
    if not validation.valid:
        raise HTTPException(status_code=400, detail="snapshot validation failed")
    if not request.confirm_replace:
        raise HTTPException(
            status_code=400,
            detail="confirm_replace=true is required when dry_run=false",
        )
    try:
        runtime_repository.replace_snapshot(request.snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(
        data=RuntimeSnapshotImportResult(
            imported=True,
            dry_run=False,
            validation=validation,
            reason=request.reason,
        )
    )


@router.get("/observability/status", response_model=ApiResponse[ObservabilityStatus])
async def get_observability_status() -> ApiResponse[ObservabilityStatus]:
    settings = get_settings()
    policy = get_trace_policy()
    exporter = trace_exporter_status()
    return ApiResponse(
        data=ObservabilityStatus(
            metrics_enabled=settings.agent_metrics_enabled,
            prometheus_metrics_path="/metrics",
            trace_events_enabled=policy.trace_events_enabled,
            trace_events_buffer_size=policy.trace_events_buffer_size,
            trace_events_retention_seconds=policy.trace_events_retention_seconds,
            trace_sample_rate=policy.trace_sample_rate,
            trace_exporter_configured=exporter.configured,
            trace_exporter_last_success_at=exporter.last_success_at,
            trace_exporter_last_error=exporter.last_error,
            retry_queue_size=exporter.retry_queue_size,
            retry_queue_max_size=exporter.retry_queue_max_size,
            retry_max_attempts=exporter.retry_max_attempts,
            retry_worker_enabled=exporter.retry_worker_enabled,
            retry_worker_running=exporter.retry_worker_running,
            retry_worker_interval_seconds=exporter.retry_worker_interval_seconds,
            langfuse_configured=bool(
                settings.agent_langfuse_host
                and settings.agent_langfuse_public_key
                and settings.agent_langfuse_secret_key
            ),
            opentelemetry_configured=bool(settings.agent_opentelemetry_endpoint),
        )
    )


@router.get("/settings/trace-policy", response_model=ApiResponse[TracePolicySettings])
async def get_trace_policy_settings() -> ApiResponse[TracePolicySettings]:
    return ApiResponse(data=get_trace_policy())


@router.patch("/settings/trace-policy", response_model=ApiResponse[TracePolicySettings])
async def patch_trace_policy_settings(
    patch: TracePolicyPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[TracePolicySettings]:
    try:
        policy = patch_trace_policy(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ApiResponse(data=policy)


@router.get("/observability/events", response_model=ApiResponse[TraceEventsData])
async def get_observability_events(
    request: Request,
    event_type: str | None = None,
    run_id: str | None = None,
    tool_name: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    _: None = Depends(require_auditor),
) -> ApiResponse[TraceEventsData]:
    data = list_trace_events(
        event_type=event_type,
        run_id=run_id,
        tool_name=tool_name,
        limit=limit,
    )
    if _request_agent_ids(request) is not None:
        # 対象範囲が制限された利用者には、範囲内の Run の event だけを返す（#215）。
        allowed_run_ids = {
            run.id for run in _filter_runs_for_actor(request, runtime_repository.list_runs())
        }
        events = [event for event in data.events if event.run_id in allowed_run_ids]
        data = TraceEventsData(total=len(events), events=events)
    return ApiResponse(data=data)


@router.post(
    "/observability/export-retry/flush",
    response_model=ApiResponse[TraceExportRetryData],
)
async def flush_observability_export_retry_queue(
    limit: int = Query(default=100, ge=1, le=1000),
    force: bool = False,
    _: None = Depends(require_admin),
) -> ApiResponse[TraceExportRetryData]:
    return ApiResponse(data=flush_trace_export_retry_queue(limit=limit, force=force))


@router.get("/tools", response_model=ApiResponse[ToolDefinitionsData])
async def list_tool_definitions() -> ApiResponse[ToolDefinitionsData]:
    """登録済みツールの v2 schema 一覧を返す。"""
    return ApiResponse(data=ToolDefinitionsData(tools=tool_registry.definitions()))


@router.get("/skills", response_model=ApiResponse[AgentSkillListOutput])
async def list_agent_skills(
    _: None = Depends(require_viewer),
) -> ApiResponse[AgentSkillListOutput]:
    skills = skill_registry.list()
    return ApiResponse(data=AgentSkillListOutput(skills=skills, metadata={"count": len(skills)}))


@router.post("/skills/reload", response_model=ApiResponse[AgentSkillListOutput])
async def reload_agent_skills(
    _: None = Depends(require_admin),
) -> ApiResponse[AgentSkillListOutput]:
    """中立ディレクトリ / JSON 宣言を再読込し、project / env 層を更新する。"""
    counts = reload_declared_skills()
    skills = skill_registry.list()
    return ApiResponse(
        data=AgentSkillListOutput(
            skills=skills, metadata={"count": len(skills), "reloaded": counts}
        )
    )


@router.get("/skills/{skill_id}", response_model=ApiResponse[AgentSkillDefinition])
async def get_agent_skill(
    skill_id: str,
    _: None = Depends(require_viewer),
) -> ApiResponse[AgentSkillDefinition]:
    """単一 skill の詳細(instructions / mcp_requirements)を返す(progressive disclosure)。"""
    skill = skill_registry.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return ApiResponse(data=skill)


@router.post("/skills", response_model=ApiResponse[AgentSkillDefinition])
async def create_agent_skill(
    payload: AgentSkillCreate,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentSkillDefinition]:
    skill_id = payload.id.strip()
    if not skill_id:
        raise HTTPException(status_code=400, detail="id is required")
    if skill_registry.get(skill_id) is not None:
        raise HTTPException(status_code=409, detail="skill already exists")
    skill = AgentSkillDefinition(
        id=skill_id,
        name=payload.name,
        description=payload.description,
        instructions=payload.instructions,
        mcp_requirements=payload.mcp_requirements,
        resource_ids=payload.resource_ids,
        enabled=payload.enabled,
        tags=payload.tags,
        source="runtime",
    )
    try:
        saved = skill_registry.upsert_custom(skill)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _persist(lambda: control_plane_store.save_skill(saved))
    return ApiResponse(data=saved)


@router.patch("/skills/{skill_id}", response_model=ApiResponse[AgentSkillDefinition])
async def patch_agent_skill(
    skill_id: str,
    patch: AgentSkillPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentSkillDefinition]:
    current = skill_registry.get(skill_id)
    if current is None:
        raise HTTPException(status_code=404, detail="skill not found")
    if current.source != "runtime":
        raise HTTPException(
            status_code=400,
            detail=f"{current.source} skill is read-only; edit the source definition",
        )
    updated = current.model_copy(
        update={
            "name": current.name if patch.name is None else patch.name,
            "description": (
                current.description if patch.description is None else patch.description
            ),
            "instructions": (
                current.instructions if patch.instructions is None else patch.instructions
            ),
            "mcp_requirements": (
                current.mcp_requirements
                if patch.mcp_requirements is None
                else patch.mcp_requirements
            ),
            "resource_ids": (
                current.resource_ids if patch.resource_ids is None else patch.resource_ids
            ),
            "enabled": current.enabled if patch.enabled is None else patch.enabled,
            "tags": current.tags if patch.tags is None else patch.tags,
        }
    )
    try:
        saved = skill_registry.upsert_custom(updated)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _persist(lambda: control_plane_store.save_skill(saved))
    return ApiResponse(data=saved)


@router.delete("/skills/{skill_id}", response_model=ApiResponse[AgentSkillListOutput])
async def delete_agent_skill(
    skill_id: str,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentSkillListOutput]:
    try:
        skill_registry.remove(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="skill not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _persist(lambda: control_plane_store.delete_skill(skill_id))
    skills = skill_registry.list()
    return ApiResponse(data=AgentSkillListOutput(skills=skills, metadata={"count": len(skills)}))


def _plugin_summary(record: PluginRecord) -> PluginSummary:
    return PluginSummary(
        id=record.id,
        name=record.name,
        version=record.version,
        description=record.description,
        author=record.author,
        enabled=record.enabled,
        marketplace_id=record.marketplace_id,
        skill_count=record.skill_count,
        mcp_count=record.mcp_count,
        resource_count=record.resource_count,
        warnings=record.warnings,
        agent_count=record.agent_count,
    )


def _plugin_list_response(metadata: dict[str, object] | None = None) -> PluginListOutput:
    plugins = [_plugin_summary(record) for record in plugin_registry.list()]
    meta: dict[str, object] = {"count": len(plugins)}
    if metadata:
        meta.update(metadata)
    return PluginListOutput(plugins=plugins, metadata=meta)


@router.get("/plugins", response_model=ApiResponse[PluginListOutput])
async def list_plugins(_: None = Depends(require_viewer)) -> ApiResponse[PluginListOutput]:
    return ApiResponse(data=_plugin_list_response())


@router.post("/plugins", response_model=ApiResponse[PluginRecord])
async def install_plugin(
    payload: PluginInstallRequest,
    _: None = Depends(require_admin),
) -> ApiResponse[PluginRecord]:
    """plugin を install する。body は manifest 直指定 or (marketplace_id, plugin_id)。"""
    manifest = payload.manifest
    if manifest is None:
        if not payload.marketplace_id or not payload.plugin_id:
            raise HTTPException(
                status_code=400,
                detail="manifest or (marketplace_id, plugin_id) is required",
            )
        manifest = marketplace_registry.find_manifest(payload.marketplace_id, payload.plugin_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="plugin not found in marketplace")
    if plugin_registry.get(manifest.id) is not None:
        raise HTTPException(status_code=409, detail="plugin already installed")
    try:
        record = plugin_registry.install(manifest, marketplace_id=payload.marketplace_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _persist(lambda: control_plane_store.save_plugin(record))
    return ApiResponse(data=record)


@router.post("/plugins/reload", response_model=ApiResponse[PluginListOutput])
async def reload_plugins(_: None = Depends(require_admin)) -> ApiResponse[PluginListOutput]:
    counts = reload_declared_plugins()
    return ApiResponse(data=_plugin_list_response({"reloaded": counts}))


@router.get("/plugins/marketplaces", response_model=ApiResponse[MarketplaceSourcesOutput])
async def list_plugin_marketplaces(
    _: None = Depends(require_viewer),
) -> ApiResponse[MarketplaceSourcesOutput]:
    return ApiResponse(data=MarketplaceSourcesOutput(marketplaces=marketplace_registry.list()))


@router.post("/plugins/marketplaces", response_model=ApiResponse[MarketplaceSource])
async def add_plugin_marketplace(
    payload: MarketplaceAddRequest,
    _: None = Depends(require_admin),
) -> ApiResponse[MarketplaceSource]:
    try:
        source = MarketplaceSource(id=payload.id, name=payload.name or payload.id, url=payload.url)
        added = marketplace_registry.add(source, payload.listing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _persist(lambda: control_plane_store.save_marketplace(added, payload.listing))
    return ApiResponse(data=added)


@router.post(
    "/plugins/marketplaces/{marketplace_id}/refresh",
    response_model=ApiResponse[MarketplaceSource],
)
async def refresh_plugin_marketplace(
    marketplace_id: str,
    _: None = Depends(require_admin),
) -> ApiResponse[MarketplaceSource]:
    """marketplace の url から plugin 一覧を HTTP 取得して更新する(url 無しは no-op)。"""
    try:
        return ApiResponse(data=marketplace_registry.refresh(marketplace_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="marketplace not found") from exc


@router.get(
    "/plugins/marketplaces/{marketplace_id}/plugins",
    response_model=ApiResponse[MarketplaceListing],
)
async def list_marketplace_plugins(
    marketplace_id: str,
    _: None = Depends(require_viewer),
) -> ApiResponse[MarketplaceListing]:
    try:
        return ApiResponse(data=marketplace_registry.get_listing(marketplace_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="marketplace not found") from exc


@router.delete(
    "/plugins/marketplaces/{marketplace_id}",
    response_model=ApiResponse[MarketplaceSourcesOutput],
)
async def delete_plugin_marketplace(
    marketplace_id: str,
    _: None = Depends(require_admin),
) -> ApiResponse[MarketplaceSourcesOutput]:
    try:
        marketplace_registry.remove(marketplace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="marketplace not found") from exc
    _persist(lambda: control_plane_store.delete_marketplace(marketplace_id))
    return ApiResponse(data=MarketplaceSourcesOutput(marketplaces=marketplace_registry.list()))


@router.get("/plugins/{plugin_id}", response_model=ApiResponse[PluginRecord])
async def get_plugin(
    plugin_id: str,
    _: None = Depends(require_viewer),
) -> ApiResponse[PluginRecord]:
    record = plugin_registry.get(plugin_id)
    if record is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    return ApiResponse(data=record)


@router.patch("/plugins/{plugin_id}", response_model=ApiResponse[PluginRecord])
async def patch_plugin(
    plugin_id: str,
    patch: PluginPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[PluginRecord]:
    if patch.enabled is None:
        record = plugin_registry.get(plugin_id)
        if record is None:
            raise HTTPException(status_code=404, detail="plugin not found")
        return ApiResponse(data=record)
    try:
        record = plugin_registry.set_enabled(plugin_id, patch.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="plugin not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _persist(lambda: control_plane_store.save_plugin(record))
    return ApiResponse(data=record)


@router.delete("/plugins/{plugin_id}", response_model=ApiResponse[PluginListOutput])
async def uninstall_plugin(
    plugin_id: str,
    _: None = Depends(require_admin),
) -> ApiResponse[PluginListOutput]:
    try:
        plugin_registry.uninstall(plugin_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="plugin not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _persist(lambda: control_plane_store.delete_plugin(plugin_id))
    return ApiResponse(data=_plugin_list_response())


@router.post("/tools/invoke", response_model=ApiResponse[ToolResult])
async def invoke_tool(
    call: ToolCall,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[ToolResult]:
    """単発ツール呼び出し。承認が必要な場合は approval_required を返す。

    RAG / NL2SQL の MCP のツールは、呼び出したログイン中の利用者として呼ぶ（#233）。
    """
    context = ToolInvocationContext(
        trace_id=call.trace_id, user_uuid=_run_creator_user_uuid(request)
    )
    return ApiResponse(
        data=await run_in_threadpool(
            tool_registry.invoke, call, policy=_configured_tool_policy(), context=context
        )
    )


def _control_plane_agent(agent_id: str) -> AgentProfile:
    agent = next((item for item in runtime_repository.list_agents() if item.id == agent_id), None)
    if agent is None:
        raise KeyError(agent_id)
    return agent


class BuiltinRuntimeModel(BaseModel):
    model_id: str
    display_name: str


class BuiltinRuntimeStatus(BaseModel):
    """組み込み Runtime の状態（Runtime 画面。API key は出さない。#754）。"""

    id: str
    name: str
    sdk: str
    sdk_version: str
    model_provider: str
    model_id: str
    ready: bool
    error_code: str | None = None
    message: str | None = None
    models: list[BuiltinRuntimeModel] = Field(default_factory=list)


@router.get("/runtime/status", response_model=ApiResponse[BuiltinRuntimeStatus])
async def get_builtin_runtime_status(
    _: None = Depends(require_viewer),
) -> ApiResponse[BuiltinRuntimeStatus]:
    """組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI）の SDK の版と使うモデル。"""
    return ApiResponse(data=BuiltinRuntimeStatus.model_validate(builtin_runtime.runtime_status()))


# 実行中の組み込み Runtime の task（GC で消えないよう参照を持つ）。
_builtin_tasks: set[asyncio.Task[None]] = set()


def _schedule_builtin_run(run: RunState) -> None:
    """組み込み Runtime の Run を、このプロセスで実行・再開する（dispatcher のときは何もしない）。

    承認がすべて決まって queued に戻った Run（再開の状態を持つ）は再開し、それ以外は
    最初から実行する。
    本番の別プロセス（`runtime_dispatcher`）は同じ判定で Run を claim する。
    """
    if run.runtime_id != BUILTIN_RUNTIME_ID or run.status != RunStatus.QUEUED:
        return
    if get_settings().agent_runtime_dispatch_mode.strip().lower() != "in_process":
        return
    coroutine = (
        builtin_runtime.resume_run(run.id)
        if builtin_resume_pending(run)
        else builtin_runtime.execute_run(run.id)
    )
    task = asyncio.get_running_loop().create_task(coroutine)
    _builtin_tasks.add(task)
    task.add_done_callback(_builtin_tasks.discard)


@router.get("/runs", response_model=ApiResponse[RunsData])
async def list_runs(
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[RunsData]:
    runs = _filter_runs_for_actor(request, runtime_repository.list_runs())
    return ApiResponse(data=RunsData(runs=runs))


@router.post("/runs", response_model=ApiResponse[RunState])
async def create_run(
    run_request: RunCreateRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        _require_agent_access(request, run_request.agent_id)
        # 旧エンジンの v1 の Run（`X-Agent-API-Version: 1`）は #756 で削除した。
        agent = _control_plane_agent(run_request.agent_id)
        if agent.migration_required:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "agent_migration_required",
                    "message": "Skill を選択して Agent の移行を完了してください。",
                },
            )
        if not agent.enabled:
            raise HTTPException(
                status_code=409,
                detail={"code": "agent_disabled", "message": "無効な業務 Agent は実行できません。"},
            )
        # 実行は組み込み Runtime（OpenAI Agents SDK + OCI Enterprise AI。#754）。
        run = runtime_repository.create_builtin_run(
            run_request, created_by_user_uuid=_run_creator_user_uuid(request)
        )
        _schedule_builtin_run(run)
        return ApiResponse(data=run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/runs/{run_id}", response_model=ApiResponse[RunState])
async def get_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    _require_agent_access(request, run.agent_id)
    return ApiResponse(data=run)


@router.get("/runs/{run_id}/audit", response_model=ApiResponse[RunAuditData])
async def get_run_audit(
    run_id: str,
    request: Request,
    _: None = Depends(require_auditor),
) -> ApiResponse[RunAuditData]:
    try:
        run = runtime_repository.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    _require_agent_access(request, run.agent_id)
    return ApiResponse(data=_run_audit_data(run))


@router.get("/audit/tool-calls", response_model=ApiResponse[ToolCallAuditData])
async def list_tool_call_audit(
    request: Request,
    run_id: str | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    approval_status: str | None = None,
    error_code: str | None = None,
    has_guardrail_warnings: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    _: None = Depends(require_auditor),
) -> ApiResponse[ToolCallAuditData]:
    return ApiResponse(
        data=_tool_call_audit_data(
            request=request,
            run_id=run_id,
            tool_name=tool_name,
            status=status,
            approval_status=approval_status,
            error_code=error_code,
            has_guardrail_warnings=has_guardrail_warnings,
            offset=offset,
            limit=limit,
        )
    )


@router.get("/audit/tool-calls.csv", response_class=Response)
async def export_tool_call_audit_csv(
    request: Request,
    run_id: str | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    approval_status: str | None = None,
    error_code: str | None = None,
    has_guardrail_warnings: bool | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
    _: None = Depends(require_auditor),
) -> Response:
    data = _tool_call_audit_data(
        request=request,
        run_id=run_id,
        tool_name=tool_name,
        status=status,
        approval_status=approval_status,
        error_code=error_code,
        has_guardrail_warnings=has_guardrail_warnings,
        offset=offset,
        limit=limit,
    )
    return Response(
        _tool_call_audit_csv(data.records),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="agent-tool-call-audit.csv"'},
    )


@router.get("/runs/{run_id}/artifacts", response_model=ApiResponse[ArtifactsData])
async def list_run_artifacts(
    run_id: str,
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[ArtifactsData]:
    try:
        run = runtime_repository.get_run(run_id)
        artifacts = runtime_repository.list_artifacts(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    _require_agent_access(request, run.agent_id)
    return ApiResponse(data=ArtifactsData(artifacts=artifacts))


@router.get("/runs/{run_id}/artifacts/{artifact_id}", response_model=ApiResponse[Artifact])
async def get_run_artifact(
    run_id: str,
    artifact_id: str,
    request: Request,
    _: None = Depends(require_viewer),
) -> ApiResponse[Artifact]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        return ApiResponse(data=runtime_repository.get_artifact(run_id, artifact_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    follow: bool = Query(default=False),
    after_event_id: str | None = Query(default=None),
    _: None = Depends(require_viewer),
) -> Response:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        events = runtime_repository.iter_events(
            run_id,
            after_event_id=after_event_id,
            follow=follow,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    if not follow:
        return Response("".join(_sse_events(events)), media_type="text/event-stream")
    return StreamingResponse(_sse_events(events), media_type="text/event-stream")


@router.websocket("/runs/{run_id}/events/ws")
async def stream_run_events_websocket(
    websocket: WebSocket,
    run_id: str,
    after_event_id: str | None = None,
    heartbeat_interval_seconds: float = 15.0,
    max_events_per_tick: int = 50,
) -> None:
    # production の Cookie セッション（Origin 必須）は accept 前に確認する（#215）。
    # local はローカル利用者（全権限）を state.principal に入れる（#750）。
    try:
        await authenticate_websocket(websocket, "/runs/{run_id}/events/ws")
    except WebSocketAuthRejected:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if not _websocket_has_roles(websocket, {"viewer", "operator", "approver", "auditor"}):
        await websocket.send_json(
            {
                "type": "error",
                "error_code": "rbac.forbidden",
                "message": "required role: viewer/operator/approver/auditor/admin",
            }
        )
        await websocket.close(code=1008)
        return
    try:
        run = runtime_repository.get_run(run_id)
    except KeyError:
        await websocket.send_json(
            {
                "type": "error",
                "error_code": "run.not_found",
                "message": "run not found",
            }
        )
        await websocket.close(code=1008)
        return
    if not _websocket_has_agent_access(websocket, run.agent_id):
        await websocket.send_json(
            {
                "type": "error",
                "error_code": "rbac.agent_forbidden",
                "message": "agent access denied",
            }
        )
        await websocket.close(code=1008)
        return

    next_index = _event_index_after_ws(run.events, after_event_id)
    last_heartbeat_at = monotonic()
    event_batch_size = max(1, min(max_events_per_tick, 500))
    try:
        while True:
            run = runtime_repository.get_run(run_id)
            events_sent = 0
            while next_index < len(run.events) and events_sent < event_batch_size:
                event = run.events[next_index]
                next_index += 1
                events_sent += 1
                await websocket.send_json(_websocket_event_payload(event))

            has_backlog = next_index < len(run.events)
            if run.status in {"completed", "failed", "cancelled"} and not has_backlog:
                await websocket.close(code=1000)
                return

            now = monotonic()
            if now - last_heartbeat_at >= max(0.0, heartbeat_interval_seconds):
                await websocket.send_json(_websocket_heartbeat_payload(run))
                last_heartbeat_at = now

            await _handle_websocket_command(websocket, run_id)
            await sleep(0.05 if has_backlog else 0.25)
    except WebSocketDisconnect:
        return


@router.post("/runs/{run_id}/cancel", response_model=ApiResponse[RunState])
async def cancel_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        return ApiResponse(data=runtime_repository.cancel_run(run_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/resume", response_model=ApiResponse[RunState])
async def resume_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        # 組み込み Runtime は承認がすべて決まると自動で再開する。止まっている Run だけ再開を
        # 起動し直す。旧エンジンの Run（#756 で削除）は読み取りだけ。
        if not builtin_resume_pending(run):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "run_not_resumable",
                    "message": "承認待ちが残っているか、再開できる状態ではありません。",
                },
            )
        _schedule_builtin_run(run)
        return ApiResponse(data=run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/runs/{run_id}/replay", response_model=ApiResponse[RunState])
async def replay_run(
    run_id: str,
    request: Request,
    _: None = Depends(require_operator),
) -> ApiResponse[RunState]:
    try:
        run = runtime_repository.get_run(run_id)
        _require_agent_access(request, run.agent_id)
        # 同じ Agent・ゴールの新しい Run として組み込み Runtime で実行する
        # （再実行を指示した利用者として。旧エンジンの Run も同じ）。
        replayed = runtime_repository.create_builtin_run(
            RunCreateRequest(
                goal=run.goal,
                agent_id=run.agent_id,
                metadata={
                    **{k: v for k, v in run.metadata.items() if not k.startswith("_")},
                    "replayed_from_run_id": run.id,
                },
            ),
            created_by_user_uuid=_run_creator_user_uuid(request),
        )
        _schedule_builtin_run(replayed)
        return ApiResponse(data=replayed)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/approvals/{approval_id}/decision", response_model=ApiResponse[RunState])
async def decide_approval(
    approval_id: str,
    request: ApprovalDecisionRequest,
    http_request: Request,
    _: None = Depends(require_approver),
) -> ApiResponse[RunState]:
    try:
        run = _run_for_approval(approval_id)
        _require_agent_access(http_request, run.agent_id)
        principal = _request_principal(http_request)
        if principal is not None:
            # 決定者はログイン中の利用者（body の decided_by は使わない。なりすまし防止。#215）。
            request = request.model_copy(update={"decided_by": principal.login_user_id})
        decided = runtime_repository.decide_approval(approval_id, request)
        _schedule_builtin_run(decided)
        return ApiResponse(data=decided)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="approval not found") from exc


@router.get("/agents", response_model=ApiResponse[AgentsData])
async def list_agents(request: Request) -> ApiResponse[AgentsData]:
    """業務 Agent の一覧。RBAC が有効なら利用者が使えるエージェントだけに絞る（#215）。"""
    agents = [
        agent for agent in runtime_repository.list_agents() if _agent_allowed(request, agent.id)
    ]
    return ApiResponse(data=AgentsData(agents=agents))


@router.post("/agents", response_model=ApiResponse[AgentProfile])
async def create_agent(
    agent: AgentProfile,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentProfile]:
    try:
        return ApiResponse(data=runtime_repository.create_agent(agent))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/agents/{agent_id}", response_model=ApiResponse[AgentProfile])
async def patch_agent(
    agent_id: str,
    patch: AgentProfilePatch,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentProfile]:
    try:
        return ApiResponse(data=runtime_repository.patch_agent(agent_id, patch))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/agents/{agent_id}", response_model=ApiResponse[AgentsData])
async def delete_agent(
    agent_id: str,
    _: None = Depends(require_admin),
) -> ApiResponse[AgentsData]:
    try:
        runtime_repository.delete_agent(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="agent not found") from exc
    # 削除したエージェントをロールの対象範囲（AGENT_ROLE_AGENTS）から外す（#750）。
    # 失敗しても削除は成功のまま（権限管理の保存は、削除済みのエージェントを黙って外す）。
    try:
        await run_in_threadpool(get_security_service().remove_agent_assignments, agent_id)
    except Exception:
        logger.warning("agent_role_assignment_cleanup_failed", extra={"agent_id": agent_id})
    return ApiResponse(data=AgentsData(agents=runtime_repository.list_agents()))


def _mcp_connection_settings(config: McpConnectionConfig) -> McpConnectionSettings:
    settings = get_settings()
    mode = config.effective_auth_mode()
    oauth_configured = _mcp_oauth_configured(config)
    service_token_configured = len(settings.app_service_token_secret.strip()) >= 32
    credentials_ready = {
        "none": True,
        "api_key": bool(config.api_key),
        "oauth_client_credentials": oauth_configured,
        "service_token": service_token_configured,
    }[mode]
    return McpConnectionSettings(
        server_id=config.server_id,
        label=config.label,
        base_url=config.base_url,
        auth_mode=mode,
        service_audience=config.audience() if mode == "service_token" else None,
        timeout_seconds=config.timeout_seconds,
        source=config.source,
        removable=config.source == "runtime",
        configured=bool(config.base_url) and credentials_ready,
        api_key_configured=bool(config.api_key),
        oauth_configured=oauth_configured,
        session_configured=bool(config.session_id),
        service_token_configured=service_token_configured,
        service_user_configured=bool(settings.agent_mcp_service_user_login_id.strip()),
    )


def _mcp_connections_response() -> McpConnectionsData:
    return McpConnectionsData(
        connections=[
            _mcp_connection_settings(config) for config in runtime_config_store.list_mcp_servers()
        ]
    )


def _upsert_mcp_connection(server_id: str, payload: McpConnectionPatch) -> McpConnectionConfig:
    config = runtime_config_store.upsert_mcp_server(
        server_id,
        label=payload.label,
        base_url=payload.base_url,
        auth_mode=payload.auth_mode,
        api_key=payload.api_key,
        timeout_seconds=payload.timeout_seconds,
        session_id=payload.session_id,
        oauth_token_url=payload.oauth_token_url,
        oauth_client_id=payload.oauth_client_id,
        oauth_client_secret=payload.oauth_client_secret,
        oauth_scope=payload.oauth_scope,
        service_audience=payload.service_audience,
    )
    _persist(lambda: control_plane_store.save_mcp_connection(config))
    return config


@router.post("/mcp", response_class=Response)
async def mcp_endpoint(request: Request) -> Response:
    """業務 Agent の MCP（#778）。`initialize` / `ping` / `tools/list` / `tools/call` を処理する。

    認証はサービストークン（audience `agent`）か Agent の API キー（`authorize_api_request`）。
    ツールごとの権限は、ここで呼び出し元の利用者の権限から判定する。
    """
    principal = getattr(request.state, "principal", None)

    def has_any_permission(permissions: frozenset[str]) -> bool:
        return principal is not None and bool(principal.has_any_permission(set(permissions)))

    return await mcp_http_response(
        request, build_agent_mcp_server(principal), has_any_permission=has_any_permission
    )


@router.get("/settings/api-keys", response_model=ApiResponse[ApiKeysListData])
async def list_api_keys() -> ApiResponse[ApiKeysListData]:
    """API キー（#778。秘密と hash は返さない）。"""
    records = api_key_registry.list()
    names = await run_in_threadpool(
        user_display_names, sorted({record.owner_user_uuid for record in records})
    )
    return ApiResponse(
        data=ApiKeysListData(
            keys=[
                key_view(record, owner_display_name=names.get(record.owner_user_uuid, ""))
                for record in records
            ],
            persistent=get_control_plane_store().persistent,
        )
    )


@router.post("/settings/api-keys", response_model=ApiResponse[ApiKeyCreated])
async def create_api_key(
    payload: ApiKeyCreateRequest,
    request: Request,
    _: None = Depends(require_admin),
) -> ApiResponse[ApiKeyCreated]:
    """API キーを作る。キーは作った利用者として動く。秘密（`token`）はこの応答で 1 回だけ返す。"""
    owner = _run_creator_user_uuid(request)
    if owner is None:
        raise HTTPException(status_code=401, detail="ログインしてください。")
    known_agents = {agent.id for agent in runtime_repository.list_agents()}
    unknown = sorted(set(payload.agent_ids or []) - known_agents)
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"業務 Agent が見つかりません: {', '.join(unknown)}"
        )
    record, token = api_key_registry.create(payload, owner_user_uuid=owner)
    try:
        _persist(lambda: save_api_key(record))
    except HTTPException:
        api_key_registry.delete(record.id)
        raise
    principal = _request_principal(request)
    display = principal.display_name if principal is not None else ""
    return ApiResponse(
        data=ApiKeyCreated(key=key_view(record, owner_display_name=display), token=token)
    )


@router.delete("/settings/api-keys/{key_id}", response_model=ApiResponse[None])
async def delete_api_key_endpoint(
    key_id: str,
    _: None = Depends(require_admin),
) -> ApiResponse[None]:
    """API キーを削除する（失効。すぐに使えなくなる）。"""
    try:
        record = api_key_registry.get(key_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="API キーが見つかりません。") from exc
    _persist(lambda: delete_api_key(record.id))
    api_key_registry.delete(record.id)
    return ApiResponse(data=None)


@router.get("/settings/mcp-connections", response_model=ApiResponse[McpConnectionsData])
async def list_mcp_connections() -> ApiResponse[McpConnectionsData]:
    """MCP 接続（RAG / NL2SQL / 外部 MCP。#757）を一覧する（資格情報は返さない）。"""
    return ApiResponse(data=_mcp_connections_response())


@router.post("/settings/mcp-connections", response_model=ApiResponse[McpConnectionSettings])
async def create_mcp_connection(
    payload: McpConnectionCreate,
    _: None = Depends(require_admin),
) -> ApiResponse[McpConnectionSettings]:
    existing = {config.server_id for config in runtime_config_store.list_mcp_servers()}
    if payload.server_id in existing:
        raise HTTPException(status_code=409, detail="MCP 接続の ID はすでに使われています。")
    config = _upsert_mcp_connection(payload.server_id, payload)
    return ApiResponse(data=_mcp_connection_settings(config))


@router.patch(
    "/settings/mcp-connections/{server_id}", response_model=ApiResponse[McpConnectionSettings]
)
async def patch_mcp_connection(
    server_id: str,
    patch: McpConnectionPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[McpConnectionSettings]:
    try:
        runtime_config_store.get_mcp(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MCP 接続が見つかりません。") from exc
    config = _upsert_mcp_connection(server_id, patch)
    return ApiResponse(data=_mcp_connection_settings(config))


@router.delete(
    "/settings/mcp-connections/{server_id}", response_model=ApiResponse[McpConnectionsData]
)
async def delete_mcp_connection(
    server_id: str,
    _: None = Depends(require_admin),
) -> ApiResponse[McpConnectionsData]:
    try:
        runtime_config_store.remove_mcp_server(server_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MCP 接続が見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="RAG / NL2SQL・宣言・連携機能の MCP 接続は削除できません。",
        ) from exc
    _persist(lambda: control_plane_store.delete_mcp_connection(server_id))
    return ApiResponse(data=_mcp_connections_response())


@router.get(
    "/settings/mcp-connections/{server_id}/tools",
    response_model=ApiResponse[ExternalMcpToolsData],
)
async def list_mcp_connection_tool_definitions(
    server_id: str,
    request: Request,
) -> ApiResponse[ExternalMcpToolsData]:
    """接続のツール一覧（`tools/list`）。接続の確認を兼ね、ログイン中の利用者として呼ぶ。"""
    context = ToolInvocationContext(user_uuid=_run_creator_user_uuid(request))
    try:
        data = await asyncio.to_thread(list_mcp_connection_tools, server_id, context=context)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MCP 接続が見つかりません。") from exc
    except ExternalToolError as exc:
        status_code = 400 if exc.code.endswith("_not_configured") else 502
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    return ApiResponse(data=data)


@router.get("/settings/tool-policy", response_model=ApiResponse[ToolPolicySettings])
async def get_tool_policy_settings() -> ApiResponse[ToolPolicySettings]:
    return ApiResponse(data=_tool_policy_settings_response())


@router.patch("/settings/tool-policy", response_model=ApiResponse[ToolPolicySettings])
async def patch_tool_policy_settings(
    patch: ToolPolicySettingsPatch,
    _: None = Depends(require_admin),
) -> ApiResponse[ToolPolicySettings]:
    try:
        _validate_tool_policy_patch(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    policy = runtime_config_store.patch_tool_policy(
        default_mode=patch.default_mode,
        allow=patch.allow,
        ask=patch.ask,
        deny=patch.deny,
    )
    _persist(lambda: control_plane_store.save_tool_policy(policy))
    return ApiResponse(data=_tool_policy_settings_response())


def _sse_events(events: Iterable[RunEvent | None]) -> Iterable[str]:
    for event in events:
        if event is None:
            yield ": keepalive\n\n"
            continue
        payload = event.model_dump(mode="json")
        yield f"event: {event.type}\n"
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_index_after_ws(events: list[RunEvent], event_id: str | None) -> int:
    if event_id is None:
        return 0
    for index, event in enumerate(events):
        if event.id == event_id:
            return index + 1
    return 0


def _websocket_event_payload(event: RunEvent) -> dict[str, object]:
    return {
        "type": event.type.value,
        "event": event.model_dump(mode="json"),
    }


def _websocket_heartbeat_payload(run: RunState) -> dict[str, object]:
    return {
        "type": "heartbeat",
        "run_id": run.id,
        "run_status": run.status.value,
        "server_time": datetime.now(UTC).isoformat(),
    }


async def _handle_websocket_command(websocket: WebSocket, run_id: str) -> None:
    try:
        message = await wait_for(websocket.receive_json(), timeout=0.01)
    except TimeoutError:
        return
    if not isinstance(message, dict):
        await websocket.send_json(
            _websocket_error_payload(
                "websocket.invalid_message",
                "message must be a JSON object",
            )
        )
        return
    command = message.get("type")
    command_id = message.get("command_id")
    normalized_command_id = command_id if isinstance(command_id, str) and command_id else None
    if command == "ping":
        await websocket.send_json(
            {
                "type": "pong",
                "ok": True,
                "command": "ping",
                "command_id": normalized_command_id,
                "server_time": datetime.now(UTC).isoformat(),
            }
        )
        return
    if command == "cancel":
        if not _websocket_has_roles(websocket, {"operator"}):
            await websocket.send_json(
                _websocket_error_payload(
                    "rbac.forbidden",
                    "cancel requires operator/admin role",
                    command="cancel",
                    command_id=normalized_command_id,
                )
            )
            return
        dedupe = _websocket_command_dedupe_state(run_id, "cancel", normalized_command_id)
        if dedupe == "duplicate":
            await websocket.send_json(
                _websocket_command_accepted("cancel", normalized_command_id, duplicate=True)
            )
            return
        if dedupe == "conflict":
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.command_id_conflict",
                    "command_id was already used for another command",
                    command="cancel",
                    command_id=normalized_command_id,
                )
            )
            return
        try:
            runtime_repository.cancel_run(run_id)
        except KeyError:
            await websocket.send_json(
                _websocket_error_payload(
                    "run.not_found",
                    "run not found",
                    command="cancel",
                    command_id=normalized_command_id,
                )
            )
            return
        await websocket.send_json(_websocket_command_accepted("cancel", normalized_command_id))
        return
    if command == "resume":
        if not _websocket_has_roles(websocket, {"operator"}):
            await websocket.send_json(
                _websocket_error_payload(
                    "rbac.forbidden",
                    "resume requires operator/admin role",
                    command="resume",
                    command_id=normalized_command_id,
                )
            )
            return
        dedupe = _websocket_command_dedupe_state(run_id, "resume", normalized_command_id)
        if dedupe == "duplicate":
            await websocket.send_json(
                _websocket_command_accepted("resume", normalized_command_id, duplicate=True)
            )
            return
        if dedupe == "conflict":
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.command_id_conflict",
                    "command_id was already used for another command",
                    command="resume",
                    command_id=normalized_command_id,
                )
            )
            return
        try:
            # 組み込み Runtime は、承認の決定の後に止まっている Run だけを再開する（#756）。
            resumable = runtime_repository.get_run(run_id)
            if builtin_resume_pending(resumable):
                _schedule_builtin_run(resumable)
        except KeyError:
            await websocket.send_json(
                _websocket_error_payload(
                    "run.not_found",
                    "run not found",
                    command="resume",
                    command_id=normalized_command_id,
                )
            )
            return
        await websocket.send_json(_websocket_command_accepted("resume", normalized_command_id))
        return
    if command == "approval_decision":
        if not _websocket_has_roles(websocket, {"approver"}):
            await websocket.send_json(
                _websocket_error_payload(
                    "rbac.forbidden",
                    "approval_decision requires approver/admin role",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        approval_id = message.get("approval_id")
        approved = message.get("approved")
        if not isinstance(approval_id, str) or not approval_id:
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.invalid_command",
                    "approval_decision requires approval_id",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        if not isinstance(approved, bool):
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.invalid_command",
                    "approval_decision requires boolean approved",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        try:
            approval_run = _run_for_approval(approval_id)
        except KeyError:
            await websocket.send_json(
                _websocket_error_payload(
                    "approval.not_found",
                    "approval not found",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        if approval_run.id != run_id:
            await websocket.send_json(
                _websocket_error_payload(
                    "approval.run_mismatch",
                    "approval does not belong to this run",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        dedupe = _websocket_command_dedupe_state(
            run_id,
            "approval_decision",
            normalized_command_id,
        )
        if dedupe == "duplicate":
            await websocket.send_json(
                _websocket_command_accepted(
                    "approval_decision",
                    normalized_command_id,
                    duplicate=True,
                )
            )
            return
        if dedupe == "conflict":
            await websocket.send_json(
                _websocket_error_payload(
                    "websocket.command_id_conflict",
                    "command_id was already used for another command",
                    command="approval_decision",
                    command_id=normalized_command_id,
                )
            )
            return
        decided_by = message.get("decided_by")
        principal = _request_principal(websocket)
        if principal is not None:
            # 決定者はログイン中の利用者（message の decided_by は使わない。#215）。
            decided_by = principal.login_user_id
        comment = message.get("comment")
        decided = runtime_repository.decide_approval(
            approval_id,
            ApprovalDecisionRequest(
                approved=approved,
                decided_by=(
                    decided_by if isinstance(decided_by, str) and decided_by else "websocket"
                ),
                comment=comment if isinstance(comment, str) and comment else None,
            ),
        )
        _schedule_builtin_run(decided)
        await websocket.send_json(
            _websocket_command_accepted("approval_decision", normalized_command_id)
        )
        return
    await websocket.send_json(
        _websocket_error_payload(
            "websocket.unknown_command",
            f"unknown command: {command}",
            command=str(command) if command is not None else None,
            command_id=normalized_command_id,
        )
    )


def _websocket_command_dedupe_state(
    run_id: str,
    command: str,
    command_id: str | None,
) -> str:
    if command_id is None:
        return "new"
    now = monotonic()
    expired_keys = [
        key
        for key, (_, recorded_at) in _websocket_command_dedupe.items()
        if now - recorded_at > _WEBSOCKET_COMMAND_DEDUPE_TTL_SECONDS
    ]
    for key in expired_keys:
        _websocket_command_dedupe.pop(key, None)

    key = (run_id, command_id)
    existing = _websocket_command_dedupe.get(key)
    if existing is not None:
        existing_command, _ = existing
        return "duplicate" if existing_command == command else "conflict"

    if len(_websocket_command_dedupe) >= _WEBSOCKET_COMMAND_DEDUPE_MAX_ENTRIES:
        oldest_key = min(
            _websocket_command_dedupe,
            key=lambda item: _websocket_command_dedupe[item][1],
        )
        _websocket_command_dedupe.pop(oldest_key, None)
    _websocket_command_dedupe[key] = (command, now)
    return "new"


def _websocket_command_accepted(
    command: str,
    command_id: str | None,
    *,
    duplicate: bool = False,
) -> dict[str, object]:
    return {
        "type": "command.accepted",
        "ok": True,
        "command": command,
        "command_id": command_id,
        "duplicate": duplicate,
    }


def _websocket_error_payload(
    error_code: str,
    message: str,
    *,
    command: str | None = None,
    command_id: str | None = None,
) -> dict[str, object]:
    return {
        "type": "error",
        "ok": False,
        "error_code": error_code,
        "message": message,
        "command": command,
        "command_id": command_id,
    }


def _configured_tool_policy() -> ToolPolicy:
    config = runtime_config_store.get_tool_policy()
    return ToolPolicy(
        default_mode=config.default_mode,
        allow=config.allow,
        ask=config.ask,
        deny=config.deny,
    )


def _request_principal(connection: object) -> Principal | None:
    """認証済みの利用者（Cookie のセッション、または local のローカル利用者）。

    `/api` の HTTP は `app.security.dependencies.authorize_api_request`、WebSocket は
    `authenticate_websocket` が `state.principal` に入れる。無ければ未認証。
    """
    state = getattr(connection, "state", None)
    principal = getattr(state, "principal", None) if state is not None else None
    return principal if isinstance(principal, Principal) else None


def _actor_policy(connection: object) -> ActorPolicy:
    """利用者から作る ActorPolicy（#215 / #750）。

    capability → 従来のロール、対象範囲は利用者の許可（None は制限なし）。RAG / NL2SQL と同じく
    共通認証の利用者だけで判定し、利用者がいなければ何も許可しない。
    """
    principal = _request_principal(connection)
    if principal is None:
        return ActorPolicy(roles=set(), agent_ids=set())
    return ActorPolicy(
        roles=actor_roles_for_principal(principal),
        agent_ids=None if principal.allowed_agent_ids is None else set(principal.allowed_agent_ids),
    )


def _policy_allows_agent(policy: ActorPolicy, agent_id: str) -> bool:
    allowed = policy.agent_ids
    return allowed is None or agent_id in allowed


def _policy_has_roles(policy: ActorPolicy, allowed_roles: set[str]) -> bool:
    return "admin" in policy.roles or bool(policy.roles.intersection(allowed_roles))


def _actor_display_name(connection: object) -> str:
    principal = _request_principal(connection)
    return principal.login_user_id if principal is not None else "anonymous"


def _require_actor_roles(request: Request, allowed_roles: set[str]) -> None:
    if _request_principal(request) is None:
        raise HTTPException(status_code=401, detail="ログインしてください。")
    if _policy_has_roles(_actor_policy(request), allowed_roles):
        return
    actor = _actor_display_name(request)
    required = ", ".join(sorted(allowed_roles | {"admin"}))
    raise HTTPException(
        status_code=403,
        detail=f"actor {actor} requires one of roles: {required}",
    )


def _run_creator_user_uuid(request: Request) -> str | None:
    """Run を作る利用者（#233）。Cookie のセッションの利用者か、local のローカル利用者。"""
    principal = _request_principal(request)
    return principal.user_uuid if principal is not None else None


def _filter_runs_for_actor(request: Request, runs: list[RunState]) -> list[RunState]:
    policy = _actor_policy(request)
    if policy.agent_ids is None:
        return runs
    return [run for run in runs if _policy_allows_agent(policy, run.agent_id)]


def _require_agent_access(request: Request, agent_id: str) -> None:
    if _agent_allowed(request, agent_id):
        return
    actor = _actor_display_name(request)
    raise HTTPException(
        status_code=403,
        detail=f"actor {actor} cannot access agent_id={agent_id}",
    )


def _agent_allowed(request: Request, agent_id: str) -> bool:
    return _policy_allows_agent(_actor_policy(request), agent_id)


def _websocket_has_roles(websocket: WebSocket, allowed_roles: set[str]) -> bool:
    return _policy_has_roles(_actor_policy(websocket), allowed_roles)


def _websocket_has_agent_access(websocket: WebSocket, agent_id: str) -> bool:
    return _policy_allows_agent(_actor_policy(websocket), agent_id)


def _request_agent_ids(request: Request) -> set[str] | None:
    """利用者が使えるエージェント（None は制限なし）。"""
    return _actor_policy(request).agent_ids


def _run_for_approval(approval_id: str) -> RunState:
    for run in runtime_repository.list_runs():
        if any(approval.id == approval_id for approval in run.approvals):
            return run
    raise KeyError(approval_id)


def _tool_policy_settings_response() -> ToolPolicySettings:
    config = runtime_config_store.get_tool_policy()
    return ToolPolicySettings(
        default_mode=config.default_mode,
        allow=sorted(config.allow),
        ask=sorted(config.ask),
        deny=sorted(config.deny),
    )


def _validate_tool_policy_patch(patch: ToolPolicySettingsPatch) -> None:
    if patch.default_mode is not None and patch.default_mode not in {"approval", "deny"}:
        raise ValueError("default_mode must be approval or deny")
    registered_tools = set(tool_registry.names())
    connections = {config.server_id for config in runtime_config_store.list_mcp_servers()}
    unknown_tools = sorted(
        {
            name
            for names in (patch.allow, patch.ask, patch.deny)
            if names is not None
            for name in names
            if name not in registered_tools and not _is_mcp_function_name(name, connections)
        }
    )
    if unknown_tools:
        raise ValueError(f"unknown tool: {', '.join(unknown_tools)}")


def _persist(action: Callable[[], None]) -> None:
    """変更を保存する（#764）。保存できなければ 503（変更は再起動で失われる）。"""
    try:
        action()
    except ControlPlaneStoreError as exc:
        logger.warning("agent_control_plane_persist_failed", extra={"reason": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _is_mcp_function_name(name: str, connections: set[str]) -> bool:
    """MCP 接続のツール（`<接続>__<ツール>`。#757）の名前か（ツールの存在は呼び先が決める）。"""
    server_id, separator, tool_name = name.partition(MCP_TOOL_SEPARATOR)
    return bool(separator and tool_name) and server_id in connections


def _mcp_oauth_configured(config: object) -> bool:
    return bool(
        getattr(config, "oauth_token_url", None)
        and getattr(config, "oauth_client_id", None)
        and getattr(config, "oauth_client_secret", None)
    )


def _normalized_command_prefixes(prefixes: list[str]) -> list[str]:
    return sorted({prefix.strip() for prefix in prefixes if prefix.strip()})


def _normalized_tool_names(tool_names: list[str]) -> list[str]:
    return sorted({name.strip() for name in tool_names if name.strip()})


def _run_audit_data(run: RunState) -> RunAuditData:
    approvals = {approval.id: approval for approval in run.approvals}
    artifact_ids_by_step: dict[str, list[str]] = {}
    for event in run.events:
        if event.type != "artifact.created":
            continue
        step_id = event.payload.get("step_id")
        artifact_id = event.payload.get("artifact_id")
        if isinstance(step_id, str) and isinstance(artifact_id, str):
            artifact_ids_by_step.setdefault(step_id, []).append(artifact_id)

    records: list[ToolAuditRecord] = []
    for step in run.steps:
        tool_name = step.tool_call.name if step.tool_call else step.kind
        result = step.tool_result
        approval = approvals.get(step.approval_id or "")
        definition = tool_registry.get(tool_name)
        audit_metadata = result.audit_metadata if result is not None else {}
        records.append(
            ToolAuditRecord(
                step_id=step.id,
                tool_name=tool_name,
                status=step.status.value,
                approval_id=step.approval_id,
                approval_status=approval.status.value if approval else None,
                policy_decision=result.policy_decision.value if result else None,
                permission_level=(
                    definition.permission_level.value
                    if definition is not None
                    else _audit_text(audit_metadata, "permission_level")
                ),
                side_effects=(
                    definition.side_effects
                    if definition is not None
                    else _audit_bool(audit_metadata, "side_effects")
                ),
                started_at=step.started_at.isoformat() if step.started_at else None,
                completed_at=step.completed_at.isoformat() if step.completed_at else None,
                duration_ms=result.duration_ms if result else None,
                success=result.success if result else None,
                error=result.error if result else None,
                error_code=result.error_code if result else None,
                guardrail_warnings=result.guardrail_warnings if result else [],
                trace_id=step.tool_call.trace_id if step.tool_call else None,
                artifact_ids=artifact_ids_by_step.get(step.id, []),
                audit_metadata=audit_metadata,
            )
        )
    return RunAuditData(
        run_id=run.id,
        goal=run.goal,
        status=run.status.value,
        records=records,
    )


def _tool_call_audit_data(
    *,
    request: Request,
    run_id: str | None,
    tool_name: str | None,
    status: str | None,
    approval_status: str | None,
    error_code: str | None,
    has_guardrail_warnings: bool | None,
    offset: int,
    limit: int,
) -> ToolCallAuditData:
    filters = _audit_filters(
        run_id=run_id,
        tool_name=tool_name,
        status=status,
        approval_status=approval_status,
        error_code=error_code,
        has_guardrail_warnings=has_guardrail_warnings,
    )
    projection_reader = getattr(runtime_repository, "list_tool_call_audit_projection", None)
    if callable(projection_reader) and _request_agent_ids(request) is None:
        try:
            projection = projection_reader(
                run_id=run_id,
                tool_name=tool_name,
                status=status,
                approval_status=approval_status,
                error_code=error_code,
                has_guardrail_warnings=has_guardrail_warnings,
                offset=offset,
                limit=limit,
            )
            if isinstance(projection, RuntimeToolCallAuditData):
                return ToolCallAuditData(
                    total=projection.total,
                    offset=projection.offset,
                    limit=projection.limit,
                    filters=filters,
                    records=[
                        ToolCallAuditRecord.model_validate(record.model_dump())
                        for record in projection.records
                    ],
                )
        except RuntimeError:
            pass

    records: list[ToolCallAuditRecord] = []
    for run in _filter_runs_for_actor(request, runtime_repository.list_runs()):
        if run_id is not None and run.id != run_id:
            continue
        audit = _run_audit_data(run)
        for record in audit.records:
            enriched = ToolCallAuditRecord(
                **record.model_dump(),
                run_id=run.id,
                run_goal=run.goal,
                run_status=run.status.value,
                agent_id=run.agent_id,
                run_created_at=run.created_at.isoformat(),
                run_updated_at=run.updated_at.isoformat(),
            )
            if _audit_record_matches(
                enriched,
                tool_name=tool_name,
                status=status,
                approval_status=approval_status,
                error_code=error_code,
                has_guardrail_warnings=has_guardrail_warnings,
            ):
                records.append(enriched)
    total = len(records)
    return ToolCallAuditData(
        total=total,
        offset=offset,
        limit=limit,
        filters=filters,
        records=records[offset : offset + limit],
    )


def _audit_filters(
    *,
    run_id: str | None,
    tool_name: str | None,
    status: str | None,
    approval_status: str | None,
    error_code: str | None,
    has_guardrail_warnings: bool | None,
) -> dict[str, object]:
    filters: dict[str, object] = {}
    if run_id is not None:
        filters["run_id"] = run_id
    if tool_name is not None:
        filters["tool_name"] = tool_name
    if status is not None:
        filters["status"] = status
    if approval_status is not None:
        filters["approval_status"] = approval_status
    if error_code is not None:
        filters["error_code"] = error_code
    if has_guardrail_warnings is not None:
        filters["has_guardrail_warnings"] = has_guardrail_warnings
    return filters


def _audit_record_matches(
    record: ToolCallAuditRecord,
    *,
    tool_name: str | None,
    status: str | None,
    approval_status: str | None,
    error_code: str | None,
    has_guardrail_warnings: bool | None,
) -> bool:
    if tool_name is not None and record.tool_name != tool_name:
        return False
    if status is not None and record.status != status:
        return False
    if approval_status is not None and record.approval_status != approval_status:
        return False
    if error_code is not None and record.error_code != error_code:
        return False
    return not (
        has_guardrail_warnings is not None
        and bool(record.guardrail_warnings) != has_guardrail_warnings
    )


def _tool_call_audit_csv(records: list[ToolCallAuditRecord]) -> str:
    fieldnames = [
        "run_id",
        "run_goal",
        "run_status",
        "agent_id",
        "step_id",
        "tool_name",
        "status",
        "approval_id",
        "approval_status",
        "policy_decision",
        "permission_level",
        "side_effects",
        "started_at",
        "completed_at",
        "duration_ms",
        "success",
        "error_code",
        "error",
        "guardrail_warnings",
        "trace_id",
        "artifact_ids",
        "run_created_at",
        "run_updated_at",
    ]
    output = StringIO()
    writer = DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        row = record.model_dump(mode="json")
        row["guardrail_warnings"] = "|".join(record.guardrail_warnings)
        row["artifact_ids"] = "|".join(record.artifact_ids)
        writer.writerow(row)
    return output.getvalue()


def _audit_text(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str):
        return value
    return None


def _audit_bool(metadata: dict[str, object], key: str) -> bool | None:
    value = metadata.get(key)
    if isinstance(value, bool):
        return value
    return None
