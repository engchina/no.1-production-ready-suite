"""プラグインの API の応答の形（#1081）。

プラグインの manifest の `mcp_servers` は内部の `McpConnectionConfig` で、API キー・OAuth Client
Secret・セッション ID と、資格情報を含み得る URL を持つ。応答では
- 秘密の値は返さず、設定済みかの真偽（`*_configured`）だけを返す
- URL（MCP の URL・OAuth のトークン URL）は userinfo と secret らしい query の値を `***` に伏せる

router はこれらを `response_model` に渡し、内部の `PluginRecord` などを応答の形へ変換する。
内部の model・保存・実際の接続（保存した値の利用）は変えない。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from app.features.agent import plugins as plugins_module
from app.features.agent.config import McpAuthMode, McpConnectionConfig
from app.features.agent.plugins import (
    MarketplaceEntry,
    MarketplaceListing,
    PluginImportPreview,
    PluginManifest,
    PluginRecord,
)

# --- URL の伏せ字 ----------------------------------------------------------
# #1078（`app/features/agent/mcp_url.py`、未 merge）の `mask_url_credentials` と同じ名前・同じ挙動。
# #1078 の merge 後は、ここを消して `from app.features.agent.mcp_url import mask_url_credentials`
# にする。

URL_CREDENTIAL_MASK = "***"

# query の名前を語に分けたとき、どれかの語がこれなら secret とみなす
# （`api_key`・`apiKey`・`X-Api-Key`・`access_token`・`X-Amz-Signature`・`client_secret` など）。
_SECRET_QUERY_WORDS = frozenset(
    {
        "key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "signature",
        "sig",
        "jwt",
        "bearer",
    }
)
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")
_WORD_SEPARATOR = re.compile(r"[^a-z0-9]+")


def _is_secret_query_name(name: str) -> bool:
    words = _WORD_SEPARATOR.split(_CAMEL_BOUNDARY.sub(r"\1_\2", name).lower())
    return any(word in _SECRET_QUERY_WORDS for word in words if word)


def _query_name(part: str) -> str:
    return unquote_plus(part.split("=", 1)[0])


def mask_url_credentials(url: str | None) -> str | None:
    """userinfo と secret らしい query の値を `***` にした URL（それ以外はそのまま）。"""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        # 解析できない URL は中身を出さない。
        return URL_CREDENTIAL_MASK
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{URL_CREDENTIAL_MASK}@{netloc.rsplit('@', 1)[1]}"
    query = "&".join(
        f"{part.split('=', 1)[0]}={URL_CREDENTIAL_MASK}"
        if part and _is_secret_query_name(_query_name(part))
        else part
        for part in parts.query.split("&")
    )
    if netloc == parts.netloc and query == parts.query:
        return url
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


# --- 応答の形 --------------------------------------------------------------


class PluginMcpServerView(BaseModel):
    """プラグインの MCP サーバー（資格情報の値は返さず、設定済みかだけを返す）。"""

    server_id: str
    label: str | None = None
    base_url: str | None = None
    auth_mode: McpAuthMode = "none"
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_scope: str | None = None
    service_audience: str | None = None
    timeout_seconds: float = 10.0
    source: str = "runtime"
    api_key_configured: bool = False
    oauth_client_secret_configured: bool = False
    session_configured: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_connection_config(cls, value: Any) -> Any:
        # 内部の構成（model か、FastAPI が応答の検証の前に dump した dict）を公開の形にする。
        if isinstance(value, dict) and "api_key_configured" not in value:
            value = McpConnectionConfig.model_validate(value)
        if not isinstance(value, McpConnectionConfig):
            return value
        return {
            "server_id": value.server_id,
            "label": value.label,
            "base_url": value.base_url,
            "auth_mode": value.effective_auth_mode(),
            "oauth_token_url": value.oauth_token_url,
            "oauth_client_id": value.oauth_client_id,
            "oauth_scope": value.oauth_scope,
            "service_audience": value.service_audience,
            "timeout_seconds": value.timeout_seconds,
            "source": value.source,
            "api_key_configured": bool(value.api_key),
            "oauth_client_secret_configured": bool(value.oauth_client_secret),
            "session_configured": bool(value.session_id),
        }

    @field_validator("base_url", "oauth_token_url")
    @classmethod
    def _mask_url(cls, value: str | None) -> str | None:
        return mask_url_credentials(value)


# 応答の形は内部の model の項目を受け継ぎ、`mcp_servers` を含む項目だけを公開の形に替える
# （内部の model に足した項目も応答に出るようにするため。MCP サーバーは公開の形の項目だけを出す）。
class PluginManifestView(PluginManifest):
    mcp_servers: list[PluginMcpServerView] = Field(  # type: ignore[assignment]
        default_factory=list
    )


class PluginRecordView(PluginRecord):
    manifest: PluginManifestView


class PluginImportPreviewView(PluginImportPreview):
    manifest: PluginManifestView


class MarketplaceListingView(MarketplaceListing):
    plugins: list[PluginManifestView | MarketplaceEntry] = Field(  # type: ignore[assignment]
        default_factory=list
    )


# 受け継いだ項目の注釈（plugins の `from __future__ import annotations` の文字列。
# `PluginResource` など）を plugins の名前で解決する。
for _view in (
    PluginManifestView,
    PluginRecordView,
    PluginImportPreviewView,
    MarketplaceListingView,
):
    _view.model_rebuild(_types_namespace={**vars(plugins_module), **globals()})
