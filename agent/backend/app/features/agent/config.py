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
    """RAG の MCP（`POST /api/mcp`）。認証は呼び出しごとのサービストークン（#233）。"""

    mcp_url: str | None = None
    timeout_seconds: float = 60.0


class ExternalNl2SqlRuntimeConfig(BaseModel):
    """NL2SQL の MCP（`POST /api/mcp`）。`default_limit` は `row_limit` の既定値。"""

    mcp_url: str | None = None
    timeout_seconds: float = 60.0
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


class ToolPolicyRuntimeConfig(BaseModel):
    default_mode: str = "approval"
    allow: set[str] = Field(default_factory=set)
    ask: set[str] = Field(default_factory=set)
    deny: set[str] = Field(default_factory=set)


class AgentRuntimeConfigStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._lock = Lock()
        self._rag = ExternalRagRuntimeConfig(
            mcp_url=settings.agent_external_rag_mcp_url,
            timeout_seconds=settings.agent_external_rag_timeout_seconds,
        )
        self._nl2sql = ExternalNl2SqlRuntimeConfig(
            mcp_url=settings.agent_external_nl2sql_mcp_url,
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
        self._tool_policy = ToolPolicyRuntimeConfig(
            default_mode=settings.agent_permission_default_mode
        )

    def get_rag(self) -> ExternalRagRuntimeConfig:
        with self._lock:
            return self._rag.model_copy(deep=True)

    def patch_rag(
        self,
        *,
        mcp_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ExternalRagRuntimeConfig:
        with self._lock:
            if mcp_url is not None:
                self._rag.mcp_url = mcp_url or None
            if timeout_seconds is not None:
                self._rag.timeout_seconds = timeout_seconds
            return self._rag.model_copy(deep=True)

    def get_nl2sql(self) -> ExternalNl2SqlRuntimeConfig:
        with self._lock:
            return self._nl2sql.model_copy(deep=True)

    def patch_nl2sql(
        self,
        *,
        mcp_url: str | None = None,
        timeout_seconds: float | None = None,
        default_limit: int | None = None,
    ) -> ExternalNl2SqlRuntimeConfig:
        with self._lock:
            if mcp_url is not None:
                self._nl2sql.mcp_url = mcp_url or None
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
            config = self._mcp_servers.get(self._mcp_default_id) or self._mcp_servers.get("default")
            if config is None:
                raise KeyError(server_id or self._mcp_default_id)
            return config.model_copy(deep=True)

    def list_mcp_servers(self) -> list[ExternalMcpRuntimeConfig]:
        with self._lock:
            return [
                config.model_copy(deep=True)
                for config in sorted(self._mcp_servers.values(), key=lambda item: item.server_id)
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

    def set_plugin_mcp_servers(self, source: str, configs: list[ExternalMcpRuntimeConfig]) -> None:
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


runtime_config_store = AgentRuntimeConfigStore()
