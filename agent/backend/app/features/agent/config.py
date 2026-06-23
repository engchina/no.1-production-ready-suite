"""Agent Runtime 用の実効設定。

`.env` / pydantic-settings を初期値にし、UI からの PATCH はプロセス内 override として保持する。
本番では Secret / DB 永続化へ置き換える。
"""

from __future__ import annotations

import json
from threading import Lock

from pydantic import BaseModel, Field

from app.settings import get_settings


class ExternalRagRuntimeConfig(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 10.0


class ExternalNl2SqlRuntimeConfig(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 15.0
    default_limit: int = 100


class ExternalMcpRuntimeConfig(BaseModel):
    server_id: str = "default"
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    session_id: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scope: str | None = None
    timeout_seconds: float = 10.0
    # 由来層: env(legacy 単一 / JSON 宣言) または runtime(UI/API 追加)
    source: str = "runtime"


class PlannerRuntimeConfig(BaseModel):
    provider: str = "heuristic"
    oci_responses_base_url: str | None = None
    oci_responses_api_key: str | None = None
    oci_responses_model: str | None = None
    oci_responses_project: str | None = None
    oci_agent_endpoint: str | None = None
    oci_agent_api_key: str | None = None
    timeout_seconds: float = 8.0
    max_retries: int = 3
    fallback_to_heuristic: bool = True
    allowed_tool_names: list[str] = Field(default_factory=lambda: ["agent_skill_run"])
    allow_command_generation: bool = False


class ToolPolicyRuntimeConfig(BaseModel):
    default_mode: str = "approval"
    allow: set[str] = Field(default_factory=set)
    ask: set[str] = Field(default_factory=set)
    deny: set[str] = Field(default_factory=set)


class RuntimeSafetyConfig(BaseModel):
    max_tool_calls_per_run: int = 20
    max_pending_approvals_per_run: int = 5


class CommandPolicyRuntimeConfig(BaseModel):
    enabled: bool = False
    workspace_root: str = "."
    allowed_prefixes: list[str] = Field(default_factory=list)
    default_timeout_seconds: float = 10.0
    max_timeout_seconds: float = 30.0
    output_limit_bytes: int = 20_000
    sanitized_env_enabled: bool = True
    env_allowlist: list[str] = Field(
        default_factory=lambda: ["PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM"]
    )
    max_memory_mb: int = 512
    max_open_files: int = 64
    start_new_session: bool = True
    isolation_mode: str = "process"
    container_image: str | None = None
    container_network: str = "none"
    container_security_opts: list[str] = Field(default_factory=lambda: ["no-new-privileges:true"])
    container_userns: str | None = None
    container_user: str | None = None
    artifact_storage_backend: str = "inline"
    artifact_storage_path: str = ".agent-artifacts"


class AgentRuntimeConfigStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._lock = Lock()
        self._rag = ExternalRagRuntimeConfig(
            base_url=settings.agent_external_rag_base_url,
            api_key=settings.agent_external_rag_api_key,
            timeout_seconds=settings.agent_external_rag_timeout_seconds,
        )
        self._nl2sql = ExternalNl2SqlRuntimeConfig(
            base_url=settings.agent_external_nl2sql_base_url,
            api_key=settings.agent_external_nl2sql_api_key,
            timeout_seconds=settings.agent_external_nl2sql_timeout_seconds,
            default_limit=settings.agent_external_nl2sql_default_limit,
        )
        # MCP は named server registry。"default" は legacy 単一 env 由来で常駐し、
        # AGENT_EXTERNAL_MCP_SERVERS_JSON の宣言を後勝ちで重ねる。
        self._mcp_default_id = "default"
        self._mcp_servers: dict[str, ExternalMcpRuntimeConfig] = {
            "default": ExternalMcpRuntimeConfig(
                server_id="default",
                label="default",
                base_url=settings.agent_external_mcp_base_url,
                api_key=settings.agent_external_mcp_api_key,
                session_id=settings.agent_external_mcp_session_id,
                oauth_token_url=settings.agent_external_mcp_oauth_token_url,
                oauth_client_id=settings.agent_external_mcp_oauth_client_id,
                oauth_client_secret=settings.agent_external_mcp_oauth_client_secret,
                oauth_scope=settings.agent_external_mcp_oauth_scope,
                timeout_seconds=settings.agent_external_mcp_timeout_seconds,
                source="env",
            )
        }
        for declared in _mcp_servers_from_json(settings.agent_external_mcp_servers_json):
            self._mcp_servers[declared.server_id] = declared
        self._planner = PlannerRuntimeConfig(
            provider=_normalize_planner_provider(settings.agent_planner_provider),
            oci_responses_base_url=(
                settings.agent_planner_oci_responses_base_url
                or settings.agent_planner_enterprise_ai_endpoint
            ),
            oci_responses_api_key=(
                settings.agent_planner_oci_responses_api_key
                or settings.agent_planner_enterprise_ai_api_key
            ),
            oci_responses_model=settings.agent_planner_oci_responses_model,
            oci_responses_project=settings.agent_planner_oci_responses_project,
            oci_agent_endpoint=settings.agent_planner_oci_agent_endpoint,
            oci_agent_api_key=settings.agent_planner_oci_agent_api_key,
            timeout_seconds=settings.agent_planner_timeout_seconds,
            max_retries=settings.agent_planner_max_retries,
            fallback_to_heuristic=settings.agent_planner_fallback_to_heuristic,
            allowed_tool_names=_split_prefixes(settings.agent_planner_allowed_tool_names),
            allow_command_generation=settings.agent_planner_allow_command_generation,
        )
        self._tool_policy = ToolPolicyRuntimeConfig(
            default_mode=settings.agent_permission_default_mode
        )
        self._runtime_safety = RuntimeSafetyConfig(
            max_tool_calls_per_run=settings.agent_max_tool_calls_per_run,
            max_pending_approvals_per_run=settings.agent_max_pending_approvals_per_run,
        )
        self._command_policy = CommandPolicyRuntimeConfig(
            enabled=settings.agent_command_tools_enabled,
            workspace_root=settings.agent_command_workspace_root,
            allowed_prefixes=_split_prefixes(settings.agent_command_allowed_prefixes),
            default_timeout_seconds=settings.agent_command_default_timeout_seconds,
            max_timeout_seconds=settings.agent_command_max_timeout_seconds,
            output_limit_bytes=settings.agent_command_output_limit_bytes,
            sanitized_env_enabled=settings.agent_command_sanitized_env_enabled,
            env_allowlist=_split_prefixes(settings.agent_command_env_allowlist),
            max_memory_mb=settings.agent_command_max_memory_mb,
            max_open_files=settings.agent_command_max_open_files,
            start_new_session=settings.agent_command_start_new_session,
            isolation_mode=settings.agent_command_isolation_mode,
            container_image=settings.agent_command_container_image,
            container_network=settings.agent_command_container_network,
            container_security_opts=_split_prefixes(settings.agent_command_container_security_opts),
            container_userns=settings.agent_command_container_userns,
            container_user=settings.agent_command_container_user,
            artifact_storage_backend=settings.agent_artifact_storage_backend,
            artifact_storage_path=settings.agent_artifact_storage_path,
        )

    def get_rag(self) -> ExternalRagRuntimeConfig:
        with self._lock:
            return self._rag.model_copy(deep=True)

    def patch_rag(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ExternalRagRuntimeConfig:
        with self._lock:
            if base_url is not None:
                self._rag.base_url = base_url or None
            if timeout_seconds is not None:
                self._rag.timeout_seconds = timeout_seconds
            return self._rag.model_copy(deep=True)

    def get_nl2sql(self) -> ExternalNl2SqlRuntimeConfig:
        with self._lock:
            return self._nl2sql.model_copy(deep=True)

    def patch_nl2sql(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        default_limit: int | None = None,
    ) -> ExternalNl2SqlRuntimeConfig:
        with self._lock:
            if base_url is not None:
                self._nl2sql.base_url = base_url or None
            if timeout_seconds is not None:
                self._nl2sql.timeout_seconds = timeout_seconds
            if default_limit is not None:
                self._nl2sql.default_limit = default_limit
            return self._nl2sql.model_copy(deep=True)

    def get_mcp(self, server_id: str | None = None) -> ExternalMcpRuntimeConfig:
        with self._lock:
            # 登録済みの named server はその config を使う。未登録 server_id は
            # 既存の「単一 gateway が server_id でルーティングする」モデルを保つため
            # default config へ fallback する(両モデル併存)。
            if server_id:
                config = self._mcp_servers.get(server_id)
                if config is not None:
                    return config.model_copy(deep=True)
            config = self._mcp_servers.get(self._mcp_default_id) or self._mcp_servers.get(
                "default"
            )
            if config is None:
                raise KeyError(server_id or self._mcp_default_id)
            return config.model_copy(deep=True)

    def list_mcp_servers(self) -> list[ExternalMcpRuntimeConfig]:
        with self._lock:
            return [
                config.model_copy(deep=True)
                for config in sorted(
                    self._mcp_servers.values(), key=lambda item: item.server_id
                )
            ]

    def mcp_default_id(self) -> str:
        with self._lock:
            return self._mcp_default_id

    def upsert_mcp_server(
        self,
        server_id: str,
        *,
        label: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        session_id: str | None = None,
        oauth_token_url: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_scope: str | None = None,
    ) -> ExternalMcpRuntimeConfig:
        sid = server_id.strip()
        if not sid:
            raise ValueError("server_id is required")
        with self._lock:
            config = self._mcp_servers.get(sid)
            if config is None:
                config = ExternalMcpRuntimeConfig(server_id=sid, source="runtime")
                self._mcp_servers[sid] = config
            if label is not None:
                config.label = label or None
            if base_url is not None:
                config.base_url = base_url or None
            if timeout_seconds is not None:
                config.timeout_seconds = timeout_seconds
            if session_id is not None:
                config.session_id = session_id or None
            if oauth_token_url is not None:
                config.oauth_token_url = oauth_token_url or None
            if oauth_client_id is not None:
                config.oauth_client_id = oauth_client_id or None
            if oauth_client_secret is not None:
                config.oauth_client_secret = oauth_client_secret or None
            if oauth_scope is not None:
                config.oauth_scope = oauth_scope or None
            return config.model_copy(deep=True)

    def remove_mcp_server(self, server_id: str) -> None:
        with self._lock:
            if server_id == "default":
                raise ValueError("default server cannot be removed")
            if server_id not in self._mcp_servers:
                raise KeyError(server_id)
            del self._mcp_servers[server_id]
            if self._mcp_default_id == server_id:
                self._mcp_default_id = "default"

    def set_default_mcp_server(self, server_id: str) -> str:
        with self._lock:
            if server_id not in self._mcp_servers:
                raise KeyError(server_id)
            self._mcp_default_id = server_id
            return self._mcp_default_id

    def set_plugin_mcp_servers(
        self, source: str, configs: list[ExternalMcpRuntimeConfig]
    ) -> None:
        """指定 source(例: plugin:<id>)の MCP server を一括置換する。default は保護。"""
        with self._lock:
            self._remove_mcp_source_locked(source)
            for config in configs:
                sid = config.server_id.strip()
                if not sid or sid == "default":
                    continue
                self._mcp_servers[sid] = config.model_copy(deep=True, update={"source": source})

    def remove_mcp_servers_by_source(self, source: str) -> None:
        with self._lock:
            self._remove_mcp_source_locked(source)

    def _remove_mcp_source_locked(self, source: str) -> None:
        for sid in [
            server_id
            for server_id, config in self._mcp_servers.items()
            if config.source == source and server_id != "default"
        ]:
            del self._mcp_servers[sid]
            if self._mcp_default_id == sid:
                self._mcp_default_id = "default"

    def patch_mcp(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        session_id: str | None = None,
        oauth_token_url: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_scope: str | None = None,
    ) -> ExternalMcpRuntimeConfig:
        # 後方互換: 旧 API は default server を対象に patch する。
        return self.upsert_mcp_server(
            self.mcp_default_id(),
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            oauth_token_url=oauth_token_url,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
            oauth_scope=oauth_scope,
        )

    def get_planner(self) -> PlannerRuntimeConfig:
        with self._lock:
            return self._planner.model_copy(deep=True)

    def patch_planner(
        self,
        *,
        provider: str | None = None,
        oci_responses_base_url: str | None = None,
        oci_responses_model: str | None = None,
        oci_responses_project: str | None = None,
        oci_agent_endpoint: str | None = None,
        enterprise_ai_endpoint: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        fallback_to_heuristic: bool | None = None,
        allowed_tool_names: list[str] | None = None,
        allow_command_generation: bool | None = None,
    ) -> PlannerRuntimeConfig:
        with self._lock:
            if provider is not None:
                self._planner.provider = _normalize_planner_provider(provider)
            if oci_responses_base_url is not None:
                self._planner.oci_responses_base_url = oci_responses_base_url or None
            if enterprise_ai_endpoint is not None:
                self._planner.oci_responses_base_url = enterprise_ai_endpoint or None
            if oci_responses_model is not None:
                self._planner.oci_responses_model = oci_responses_model or None
            if oci_responses_project is not None:
                self._planner.oci_responses_project = oci_responses_project or None
            if oci_agent_endpoint is not None:
                self._planner.oci_agent_endpoint = oci_agent_endpoint or None
            if timeout_seconds is not None:
                self._planner.timeout_seconds = timeout_seconds
            if max_retries is not None:
                self._planner.max_retries = max_retries
            if fallback_to_heuristic is not None:
                self._planner.fallback_to_heuristic = fallback_to_heuristic
            if allowed_tool_names is not None:
                self._planner.allowed_tool_names = list(allowed_tool_names)
            if allow_command_generation is not None:
                self._planner.allow_command_generation = allow_command_generation
            return self._planner.model_copy(deep=True)

    def get_tool_policy(self) -> ToolPolicyRuntimeConfig:
        with self._lock:
            return self._tool_policy.model_copy(deep=True)

    def patch_tool_policy(
        self,
        *,
        default_mode: str | None = None,
        allow: list[str] | None = None,
        ask: list[str] | None = None,
        deny: list[str] | None = None,
    ) -> ToolPolicyRuntimeConfig:
        with self._lock:
            if default_mode is not None:
                self._tool_policy.default_mode = default_mode
            if allow is not None:
                self._tool_policy.allow = set(allow)
            if ask is not None:
                self._tool_policy.ask = set(ask)
            if deny is not None:
                self._tool_policy.deny = set(deny)
            return self._tool_policy.model_copy(deep=True)

    def get_runtime_safety(self) -> RuntimeSafetyConfig:
        with self._lock:
            return self._runtime_safety.model_copy(deep=True)

    def patch_runtime_safety(
        self,
        *,
        max_tool_calls_per_run: int | None = None,
        max_pending_approvals_per_run: int | None = None,
    ) -> RuntimeSafetyConfig:
        with self._lock:
            if max_tool_calls_per_run is not None:
                self._runtime_safety.max_tool_calls_per_run = max_tool_calls_per_run
            if max_pending_approvals_per_run is not None:
                self._runtime_safety.max_pending_approvals_per_run = max_pending_approvals_per_run
            return self._runtime_safety.model_copy(deep=True)

    def get_command_policy(self) -> CommandPolicyRuntimeConfig:
        with self._lock:
            return self._command_policy.model_copy(deep=True)

    def patch_command_policy(
        self,
        *,
        enabled: bool | None = None,
        workspace_root: str | None = None,
        allowed_prefixes: list[str] | None = None,
        default_timeout_seconds: float | None = None,
        max_timeout_seconds: float | None = None,
        output_limit_bytes: int | None = None,
        sanitized_env_enabled: bool | None = None,
        env_allowlist: list[str] | None = None,
        max_memory_mb: int | None = None,
        max_open_files: int | None = None,
        start_new_session: bool | None = None,
        isolation_mode: str | None = None,
        container_image: str | None = None,
        container_network: str | None = None,
        container_security_opts: list[str] | None = None,
        container_userns: str | None = None,
        container_user: str | None = None,
        artifact_storage_backend: str | None = None,
        artifact_storage_path: str | None = None,
    ) -> CommandPolicyRuntimeConfig:
        with self._lock:
            if enabled is not None:
                self._command_policy.enabled = enabled
            if workspace_root is not None:
                self._command_policy.workspace_root = workspace_root
            if allowed_prefixes is not None:
                self._command_policy.allowed_prefixes = list(allowed_prefixes)
            if default_timeout_seconds is not None:
                self._command_policy.default_timeout_seconds = default_timeout_seconds
            if max_timeout_seconds is not None:
                self._command_policy.max_timeout_seconds = max_timeout_seconds
            if output_limit_bytes is not None:
                self._command_policy.output_limit_bytes = output_limit_bytes
            if sanitized_env_enabled is not None:
                self._command_policy.sanitized_env_enabled = sanitized_env_enabled
            if env_allowlist is not None:
                self._command_policy.env_allowlist = list(env_allowlist)
            if max_memory_mb is not None:
                self._command_policy.max_memory_mb = max_memory_mb
            if max_open_files is not None:
                self._command_policy.max_open_files = max_open_files
            if start_new_session is not None:
                self._command_policy.start_new_session = start_new_session
            if isolation_mode is not None:
                self._command_policy.isolation_mode = isolation_mode
            if container_image is not None:
                self._command_policy.container_image = container_image or None
            if container_network is not None:
                self._command_policy.container_network = container_network
            if container_security_opts is not None:
                self._command_policy.container_security_opts = list(container_security_opts)
            if container_userns is not None:
                self._command_policy.container_userns = container_userns or None
            if container_user is not None:
                self._command_policy.container_user = container_user or None
            if artifact_storage_backend is not None:
                self._command_policy.artifact_storage_backend = artifact_storage_backend
            if artifact_storage_path is not None:
                self._command_policy.artifact_storage_path = artifact_storage_path
            return self._command_policy.model_copy(deep=True)


def _mcp_servers_from_json(raw: str | None) -> list[ExternalMcpRuntimeConfig]:
    """`AGENT_EXTERNAL_MCP_SERVERS_JSON` を named server の宣言として読む。

    list でも `{"servers": [...]}` でも受ける。不正項目は warning ではなく単に
    スキップして安全側に倒す(parser fallback 方針に倣う)。
    """
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items: object
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("servers")
    else:
        items = None
    if not isinstance(items, list):
        return []
    servers: list[ExternalMcpRuntimeConfig] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        server_id = str(item.get("server_id") or item.get("id") or "").strip()
        if not server_id:
            continue
        try:
            servers.append(
                ExternalMcpRuntimeConfig(
                    server_id=server_id,
                    label=item.get("label"),
                    base_url=item.get("base_url"),
                    api_key=item.get("api_key"),
                    session_id=item.get("session_id"),
                    oauth_token_url=item.get("oauth_token_url"),
                    oauth_client_id=item.get("oauth_client_id"),
                    oauth_client_secret=item.get("oauth_client_secret"),
                    oauth_scope=item.get("oauth_scope"),
                    timeout_seconds=float(item.get("timeout_seconds", 10.0)),
                    source="env",
                )
            )
        except (TypeError, ValueError):
            continue
    return servers


def _split_prefixes(raw_prefixes: str) -> list[str]:
    return [item.strip() for item in raw_prefixes.split(",") if item.strip()]


def _normalize_planner_provider(provider: str | None) -> str:
    normalized = (provider or "heuristic").strip().lower()
    if normalized in {"enterprise_ai", "enterprise-ai"}:
        return "oci_responses"
    return normalized or "heuristic"


runtime_config_store = AgentRuntimeConfigStore()
