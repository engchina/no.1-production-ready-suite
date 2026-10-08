"""Agent Runtime 用の実効設定。

`.env` / pydantic-settings を初期値にし、UI からの PATCH はプロセス内 override として保持する。
本番では Secret / DB 永続化へ置き換える。
"""

from __future__ import annotations

import json
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field

from app.settings import get_settings

McpAuthMode = Literal["none", "api_key", "oauth_client_credentials", "service_token"]

# RAG / NL2SQL の MCP 接続（#757）。各製品の `POST /api/mcp` を、Run の利用者を `sub` にした
# サービストークン（aud は製品名）で呼ぶ（#233）。URL は terraform・init_script が書く
# `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL` を初期値にする。
# 接続 ID はツール名の接頭辞（`rag__` / `nl2sql__`）と評価・記録が使うため変えない。
PRODUCT_MCP_CONNECTION_IDS: tuple[str, ...] = ("rag", "nl2sql")
# 標準の接続の表示名（#1325）。製品の略称だけでは用途が伝わらないため、用途の名前に製品名を添える。
# 名前は配備が決め、画面・API では変えない（保存した名前も復元しない）。
PRODUCT_MCP_CONNECTION_LABELS: dict[str, str] = {
    "rag": "ナレッジ検索（RAG）",
    "nl2sql": "データ問い合わせ（NL2SQL）",
}
# 標準の接続の呼び先の製品名（失敗の案内の「〜のサービスが起動しているか」に使う）。
PRODUCT_MCP_SERVICE_NAMES: dict[str, str] = {"rag": "RAG", "nl2sql": "NL2SQL"}


class McpConnectionConfig(BaseModel):
    """MCP 接続（#757。旧「外部 MCP」のサーバー・外部 RAG・外部 NL2SQL をまとめたもの）。"""

    server_id: str
    label: str | None = None
    base_url: str | None = None
    # 認証方式。未指定なら設定された資格情報から推定する（OAuth > API キー > なし）。
    auth_mode: McpAuthMode | None = None
    api_key: str | None = None
    session_id: str | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scope: str | None = None
    # サービストークンの aud（呼び先の製品名）。未指定なら server_id。
    service_audience: str | None = None
    timeout_seconds: float = 10.0
    # 由来層: builtin(RAG / NL2SQL) / env(JSON 宣言) / plugin:<id> / runtime(UI/API 追加)
    source: str = "runtime"
    # 配備（環境変数）が URL を決めた標準の接続か（#1325）。True なら URL を画面・API で変えられず、
    # 保存した URL も復元しない。起動のたびに設定から決める（保存した値は使わない）。
    base_url_locked: bool = False

    def effective_auth_mode(self) -> McpAuthMode:
        if self.auth_mode is not None:
            return self.auth_mode
        if self.oauth_token_url and self.oauth_client_id and self.oauth_client_secret:
            return "oauth_client_credentials"
        if self.api_key:
            return "api_key"
        return "none"

    def audience(self) -> str:
        return (self.service_audience or self.server_id).strip()


class ToolPolicyRuntimeConfig(BaseModel):
    default_mode: str = "approval"
    allow: set[str] = Field(default_factory=set)
    ask: set[str] = Field(default_factory=set)
    deny: set[str] = Field(default_factory=set)


def _product_connections() -> list[McpConnectionConfig]:
    """標準の接続（RAG / NL2SQL）。配備が URL を与えたら、その URL に固定する（#1325）。

    URL の環境変数が無い構成（ローカルの開発など）は、画面で URL を設定できる。
    """
    settings = get_settings()
    targets = (
        ("rag", settings.agent_external_rag_mcp_url, settings.agent_external_rag_timeout_seconds),
        (
            "nl2sql",
            settings.agent_external_nl2sql_mcp_url,
            settings.agent_external_nl2sql_timeout_seconds,
        ),
    )
    connections: list[McpConnectionConfig] = []
    for server_id, raw_url, timeout_seconds in targets:
        url = (raw_url or "").strip() or None
        connections.append(
            McpConnectionConfig(
                server_id=server_id,
                label=PRODUCT_MCP_CONNECTION_LABELS[server_id],
                base_url=url,
                auth_mode="service_token",
                service_audience=server_id,
                timeout_seconds=timeout_seconds,
                source="builtin",
                base_url_locked=url is not None,
            )
        )
    return connections


class AgentRuntimeConfigStore:
    def __init__(self) -> None:
        settings = get_settings()
        self._lock = Lock()
        # RAG / NL2SQL は常駐し（削除できない）、AGENT_EXTERNAL_MCP_SERVERS_JSON の宣言を重ねる。
        self._mcp_servers: dict[str, McpConnectionConfig] = {
            config.server_id: config for config in _product_connections()
        }
        for declared in _mcp_servers_from_json(settings.agent_external_mcp_servers_json):
            if declared.server_id in PRODUCT_MCP_CONNECTION_IDS:
                continue
            self._mcp_servers[declared.server_id] = declared
        self._tool_policy = ToolPolicyRuntimeConfig(
            default_mode=settings.agent_permission_default_mode
        )

    def get_mcp(self, server_id: str) -> McpConnectionConfig:
        with self._lock:
            config = self._mcp_servers.get(server_id)
            if config is None:
                raise KeyError(server_id)
            return config.model_copy(deep=True)

    def list_mcp_servers(self) -> list[McpConnectionConfig]:
        with self._lock:
            return [
                config.model_copy(deep=True)
                for config in sorted(self._mcp_servers.values(), key=lambda item: item.server_id)
            ]

    def upsert_mcp_server(
        self,
        server_id: str,
        *,
        label: str | None = None,
        base_url: str | None = None,
        auth_mode: McpAuthMode | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        session_id: str | None = None,
        oauth_token_url: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_scope: str | None = None,
        service_audience: str | None = None,
    ) -> McpConnectionConfig:
        """接続を追加・更新する。None は「変えない」、空文字は「消す」。"""
        sid = server_id.strip()
        if not sid:
            raise ValueError("server_id is required")
        with self._lock:
            config = self._mcp_servers.get(sid)
            if config is None:
                config = McpConnectionConfig(server_id=sid, source="runtime")
                self._mcp_servers[sid] = config
            if label is not None:
                config.label = label or None
            if base_url is not None:
                config.base_url = base_url or None
            if auth_mode is not None:
                config.auth_mode = auth_mode
            if api_key is not None:
                config.api_key = api_key or None
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
            if service_audience is not None:
                config.service_audience = service_audience or None
            return config.model_copy(deep=True)

    def restore_mcp_server(self, stored: McpConnectionConfig) -> None:
        """保存した接続を重ねる（#764）。

        RAG / NL2SQL・宣言の接続は、画面で変えた項目だけを上書きする。標準の接続（RAG / NL2SQL）の
        名前・認証と、配備が決めた URL は保存した値で上書きしない（#1325）。
        """
        with self._lock:
            current = self._mcp_servers.get(stored.server_id)
            if current is None:
                self._mcp_servers[stored.server_id] = stored.model_copy(
                    deep=True, update={"source": "runtime", "base_url_locked": False}
                )
                return
            fields = {
                "label": stored.label,
                "base_url": stored.base_url,
                "timeout_seconds": stored.timeout_seconds,
                "session_id": stored.session_id,
                "api_key": stored.api_key,
                "oauth_token_url": stored.oauth_token_url,
                "oauth_client_id": stored.oauth_client_id,
                "oauth_client_secret": stored.oauth_client_secret,
                "oauth_scope": stored.oauth_scope,
            }
            if current.source == "builtin":
                del fields["label"]
                if current.base_url_locked:
                    del fields["base_url"]
            else:
                fields["auth_mode"] = stored.auth_mode
                fields["service_audience"] = stored.service_audience
            self._mcp_servers[stored.server_id] = current.model_copy(update=fields)

    def remove_mcp_server(self, server_id: str) -> None:
        """画面・API で追加した接続だけを削除する（RAG / NL2SQL・宣言・プラグインの接続は不可）。"""
        with self._lock:
            config = self._mcp_servers.get(server_id)
            if config is None:
                raise KeyError(server_id)
            if config.source != "runtime":
                raise ValueError(f"{config.source} connection cannot be removed")
            del self._mcp_servers[server_id]

    def set_plugin_mcp_servers(self, source: str, configs: list[McpConnectionConfig]) -> None:
        """指定 source(例: plugin:<id>)の MCP 接続を一括置換する。RAG / NL2SQL は保護。"""
        with self._lock:
            self._remove_mcp_source_locked(source)
            for config in configs:
                sid = config.server_id.strip()
                if not sid or sid in PRODUCT_MCP_CONNECTION_IDS:
                    continue
                self._mcp_servers[sid] = config.model_copy(
                    deep=True, update={"source": source, "base_url_locked": False}
                )

    def remove_mcp_servers_by_source(self, source: str) -> None:
        with self._lock:
            self._remove_mcp_source_locked(source)

    def _remove_mcp_source_locked(self, source: str) -> None:
        for sid in [
            server_id
            for server_id, config in self._mcp_servers.items()
            if config.source == source and server_id not in PRODUCT_MCP_CONNECTION_IDS
        ]:
            del self._mcp_servers[sid]

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


def _mcp_servers_from_json(raw: str | None) -> list[McpConnectionConfig]:
    """`AGENT_EXTERNAL_MCP_SERVERS_JSON` を MCP 接続の宣言として読む。

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
    servers: list[McpConnectionConfig] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        server_id = str(item.get("server_id") or item.get("id") or "").strip()
        if not server_id:
            continue
        try:
            servers.append(
                McpConnectionConfig(
                    server_id=server_id,
                    label=item.get("label"),
                    base_url=item.get("base_url"),
                    auth_mode=item.get("auth_mode"),
                    api_key=item.get("api_key"),
                    session_id=item.get("session_id"),
                    oauth_token_url=item.get("oauth_token_url"),
                    oauth_client_id=item.get("oauth_client_id"),
                    oauth_client_secret=item.get("oauth_client_secret"),
                    oauth_scope=item.get("oauth_scope"),
                    service_audience=item.get("service_audience"),
                    timeout_seconds=float(item.get("timeout_seconds", 10.0)),
                    source="env",
                )
            )
        except (TypeError, ValueError):
            continue
    return servers


runtime_config_store = AgentRuntimeConfigStore()
