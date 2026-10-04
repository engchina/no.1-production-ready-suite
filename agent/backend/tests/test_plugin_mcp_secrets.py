"""プラグインの API の応答・ログに MCP サーバーの資格情報を出さない（#1081）。

プラグインの manifest の `mcp_servers` は内部の `McpConnectionConfig`（API キー・OAuth Client
Secret・セッション ID・資格情報を含み得る URL）を持つ。応答は設定済みかの真偽だけを返し、URL は
伏せる。実際の接続（保存した値の利用）は変えない。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.features.agent.config import runtime_config_store
from app.features.agent.plugins import (
    _plugins_from_json,
    marketplace_registry,
    plugin_registry,
)
from app.main import app

client = TestClient(app)

PLUGIN_ID = "plugin-1081"
MARKETPLACE_ID = "marketplace-1081"
MARKETPLACE_PLUGIN_ID = "plugin-1081-from-marketplace"
API_KEY = "sk-plugin-api-key-1081"
CLIENT_SECRET = "oauth-client-secret-1081"
SESSION_ID = "mcp-session-id-1081"
URL_PASSWORD = "url-password-1081"
URL_TOKEN = "url-token-1081"
SECRETS = (API_KEY, CLIENT_SECRET, SESSION_ID, URL_PASSWORD, URL_TOKEN)
RAW_BASE_URL = f"https://svc:{URL_PASSWORD}@mcp.example.test/jsonrpc?api_key={URL_TOKEN}&tenant=a"
MASKED_BASE_URL = "https://***@mcp.example.test/jsonrpc?api_key=***&tenant=a"
RAW_TOKEN_URL = f"https://auth.example.test/token?client_secret={URL_TOKEN}"
MASKED_TOKEN_URL = "https://auth.example.test/token?client_secret=***"


def _manifest(plugin_id: str, server_prefix: str) -> dict[str, Any]:
    return {
        "id": plugin_id,
        "name": "資格情報を持つプラグイン",
        "mcp_servers": [
            {
                "server_id": f"{server_prefix}_api_key",
                "label": "API キーの接続",
                "base_url": RAW_BASE_URL,
                "auth_mode": "api_key",
                "api_key": API_KEY,
                "session_id": SESSION_ID,
            },
            {
                "server_id": f"{server_prefix}_oauth",
                "base_url": "https://mcp.example.test/oauth",
                "oauth_token_url": RAW_TOKEN_URL,
                "oauth_client_id": "client-1081",
                "oauth_client_secret": CLIENT_SECRET,
                "oauth_scope": "mcp.read",
            },
        ],
    }


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    for plugin_id in (PLUGIN_ID, MARKETPLACE_PLUGIN_ID):
        with contextlib.suppress(KeyError, ValueError):
            plugin_registry.uninstall(plugin_id)
    with contextlib.suppress(KeyError):
        marketplace_registry.remove(MARKETPLACE_ID)


def _assert_no_secrets(response: Any) -> None:
    assert response.status_code == 200, response.text
    for secret in SECRETS:
        assert secret not in response.text
    for field in ('"api_key":', '"oauth_client_secret":', '"session_id":'):
        assert field not in response.text


def _servers(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {server["server_id"]: server for server in manifest["mcp_servers"]}


def _assert_public_servers(manifest: dict[str, Any], server_prefix: str) -> None:
    servers = _servers(manifest)
    api_key_server = servers[f"{server_prefix}_api_key"]
    assert api_key_server["base_url"] == MASKED_BASE_URL
    assert api_key_server["auth_mode"] == "api_key"
    assert api_key_server["api_key_configured"] is True
    assert api_key_server["session_configured"] is True
    assert api_key_server["oauth_client_secret_configured"] is False
    oauth_server = servers[f"{server_prefix}_oauth"]
    assert oauth_server["base_url"] == "https://mcp.example.test/oauth"
    assert oauth_server["auth_mode"] == "oauth_client_credentials"
    assert oauth_server["oauth_token_url"] == MASKED_TOKEN_URL
    assert oauth_server["oauth_client_id"] == "client-1081"
    assert oauth_server["oauth_client_secret_configured"] is True
    assert oauth_server["api_key_configured"] is False
    assert oauth_server["session_configured"] is False


def test_plugin_responses_do_not_expose_mcp_credentials() -> None:
    installed = client.post("/api/plugins", json={"manifest": _manifest(PLUGIN_ID, "p1081")})
    _assert_no_secrets(installed)
    _assert_public_servers(installed.json()["data"]["manifest"], "p1081")

    detail = client.get(f"/api/plugins/{PLUGIN_ID}")
    _assert_no_secrets(detail)
    _assert_public_servers(detail.json()["data"]["manifest"], "p1081")

    _assert_no_secrets(client.get("/api/plugins"))
    _assert_no_secrets(client.patch(f"/api/plugins/{PLUGIN_ID}", json={}))
    disabled = client.patch(f"/api/plugins/{PLUGIN_ID}", json={"enabled": False})
    _assert_no_secrets(disabled)
    _assert_public_servers(disabled.json()["data"]["manifest"], "p1081")
    enabled = client.patch(f"/api/plugins/{PLUGIN_ID}", json={"enabled": True})
    _assert_no_secrets(enabled)


def test_plugin_connection_keeps_stored_credentials() -> None:
    """応答だけを伏せ、実際の接続は保存した URL・資格情報をそのまま使う。"""
    client.post("/api/plugins", json={"manifest": _manifest(PLUGIN_ID, "p1081")})

    api_key_config = runtime_config_store.get_mcp("p1081_api_key")
    assert api_key_config.base_url == RAW_BASE_URL
    assert api_key_config.api_key == API_KEY
    assert api_key_config.session_id == SESSION_ID
    oauth_config = runtime_config_store.get_mcp("p1081_oauth")
    assert oauth_config.oauth_token_url == RAW_TOKEN_URL
    assert oauth_config.oauth_client_secret == CLIENT_SECRET
    record = plugin_registry.get(PLUGIN_ID)
    assert record is not None
    assert record.manifest.mcp_servers[0].api_key == API_KEY


def test_marketplace_responses_do_not_expose_mcp_credentials() -> None:
    manifest = _manifest(MARKETPLACE_PLUGIN_ID, "m1081")
    added = client.post(
        "/api/plugins/marketplaces",
        json={"id": MARKETPLACE_ID, "name": "1081", "listing": {"plugins": [manifest]}},
    )
    _assert_no_secrets(added)

    listing = client.get(f"/api/plugins/marketplaces/{MARKETPLACE_ID}/plugins")
    _assert_no_secrets(listing)
    plugins = listing.json()["data"]["plugins"]
    assert "catalog_entry" not in plugins[0]
    _assert_public_servers(plugins[0], "m1081")

    preview = client.post(
        f"/api/plugins/marketplaces/{MARKETPLACE_ID}/plugins/{MARKETPLACE_PLUGIN_ID}/preview"
    )
    _assert_no_secrets(preview)
    _assert_public_servers(preview.json()["data"]["manifest"], "m1081")

    installed = client.post(
        "/api/plugins",
        json={"marketplace_id": MARKETPLACE_ID, "plugin_id": MARKETPLACE_PLUGIN_ID},
    )
    _assert_no_secrets(installed)
    _assert_public_servers(installed.json()["data"]["manifest"], "m1081")
    assert runtime_config_store.get_mcp("m1081_api_key").api_key == API_KEY


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_invalid_declared_plugin_log_omits_credentials() -> None:
    """宣言の不正な plugin の警告の文に、入力（資格情報）を含めない。

    共通の JSON の出力は例外を型だけにするが、logger に付く他の handler にも文のまま渡るため、
    文そのものに入力を含めない（共通の formatter を通さない handler で確かめる）。
    """
    raw = (
        '[{"id": "declared-1081", "name": "x", "mcp_servers": '
        f'[{{"api_key": "{API_KEY}", "base_url": "{RAW_BASE_URL}"}}]}}]'
    )
    logger = logging.getLogger("app.features.agent.plugins")
    handler = _RecordingHandler()
    logger.addHandler(handler)
    try:
        assert _plugins_from_json(raw) == []
    finally:
        logger.removeHandler(handler)

    assert handler.messages == ["宣言 plugin が不正: mcp_servers.0.server_id (missing)"]
