"""プラグインの manifest の MCP サーバーの秘密を暗号化して保存する（#1101）。

MCP 接続（`save_mcp_connection`）と同じく、プラグイン（`save_plugin`）とマーケットプレイスの一覧の
native manifest（`save_marketplace`）の `mcp_servers[]` の `api_key`・`oauth_client_secret`・
`session_id` を `app.secret_box` で暗号化して保存し、復元で復号する。

- 保存先（JSON ファイル / Oracle の `AGENT_CONTROL_PLANE_ITEMS`）の生の値に平文が無いこと
- 復元（再起動）の後に同じ値に戻り、実際の接続（`McpConnectionClient`）がその値を使うこと
- 平文で保存済みの行（#1101 より前）も読め、起動時の復元で暗号化して保存し直すこと
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest import MonkeyPatch

from app.features.agent import control_plane_store
from app.features.agent.config import runtime_config_store
from app.features.agent.control_plane_store import (
    FileItemStore,
    OracleItemStore,
    restore_control_plane,
    set_control_plane_store,
)
from app.features.agent.plugins import (
    MarketplaceListing,
    MarketplaceSource,
    PluginManifest,
    marketplace_registry,
    plugin_registry,
)
from app.features.agent.tools import McpConnectionClient, ToolInvocationContext
from app.main import app
from app.settings import get_settings

SIGNING_KEY = "plugin-secret-test-signing-key-0123456789"  # nosec B105 - テスト用
API_KEY = "plugin-api-key-plain-1101"  # nosec B105 - テスト用
CLIENT_SECRET = "plugin-oauth-client-secret-1101"  # nosec B105 - テスト用
SESSION_ID = "plugin-session-id-1101"
PLAINTEXTS = (API_KEY, CLIENT_SECRET, SESSION_ID)

PLUGIN_ID = "cp1101_plugin"
MARKET_ID = "cp1101_market"
API_SERVER = "cp1101_api"
OAUTH_SERVER = "cp1101_oauth"


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
    import anyio

    async def call() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, json=payload)

    return anyio.run(call)


def _manifest(plugin_id: str = PLUGIN_ID) -> dict[str, Any]:
    return {
        "id": plugin_id,
        "name": "秘密を持つプラグイン",
        "mcp_servers": [
            {
                "server_id": API_SERVER,
                "base_url": "https://erp.example.test/mcp",
                "auth_mode": "api_key",
                "api_key": API_KEY,
                "session_id": SESSION_ID,
            },
            {
                "server_id": OAUTH_SERVER,
                "base_url": "https://oauth.example.test/mcp",
                "auth_mode": "oauth_client_credentials",
                "oauth_token_url": "https://auth.example.test/oauth/token",
                "oauth_client_id": "plugin-client",
                "oauth_client_secret": CLIENT_SECRET,
            },
        ],
    }


def _assert_no_plaintext(raw: str) -> None:
    for plaintext in PLAINTEXTS:
        assert plaintext not in raw


def _assert_sealed_servers(servers: list[dict[str, Any]]) -> None:
    by_id = {server["server_id"]: server for server in servers}
    assert by_id[API_SERVER]["api_key"].startswith("enc:v1:")
    assert by_id[API_SERVER]["session_id"].startswith("enc:v1:")
    assert by_id[OAUTH_SERVER]["oauth_client_secret"].startswith("enc:v1:")
    # 秘密でない値はそのまま（画面・検索で使う）。
    assert by_id[OAUTH_SERVER]["oauth_client_id"] == "plugin-client"
    assert by_id[API_SERVER]["base_url"] == "https://erp.example.test/mcp"


def _assert_connection_uses_restored_secrets() -> None:
    api = runtime_config_store.get_mcp(API_SERVER)
    assert (api.api_key, api.session_id) == (API_KEY, SESSION_ID)
    assert api.source == f"plugin:{PLUGIN_ID}"
    # 実際の接続は復号した値を使う（暗号文を送らない）。
    session = McpConnectionClient(api, context=ToolInvocationContext())._session()
    assert session._headers["Authorization"] == f"Bearer {API_KEY}"
    assert session._session_id == SESSION_ID
    oauth = runtime_config_store.get_mcp(OAUTH_SERVER)
    assert oauth.oauth_client_secret == CLIENT_SECRET
    assert oauth.effective_auth_mode() == "oauth_client_credentials"


def _forget_in_memory() -> None:
    """再起動の代わりにメモリから消す。"""
    with contextlib.suppress(KeyError, ValueError):
        plugin_registry.uninstall(PLUGIN_ID)
    with contextlib.suppress(KeyError):
        marketplace_registry.remove(MARKET_ID)


@pytest.fixture
def use_store(monkeypatch: MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "app_service_token_secret", SIGNING_KEY)
    monkeypatch.setattr(
        runtime_config_store,
        "_mcp_servers",
        {config.server_id: config for config in runtime_config_store.list_mcp_servers()},
    )
    try:
        yield
    finally:
        set_control_plane_store(None)
        _forget_in_memory()


# ---------------------------------------------------------------------------
# Oracle の保存先（`AGENT_CONTROL_PLANE_ITEMS` の fake）
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: dict[tuple[str, str], str]) -> None:
        self._rows = rows
        self._result: list[tuple[str, str, str]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def setinputsizes(self, **sizes: Any) -> None:
        return None

    def execute(self, statement: str, **params: Any) -> None:
        sql = " ".join(statement.split()).upper()
        if sql.startswith("SELECT ITEM_KIND"):
            self._result = [(kind, item_id, doc) for (kind, item_id), doc in self._rows.items()]
        elif sql.startswith("MERGE INTO AGENT_CONTROL_PLANE_ITEMS"):
            self._rows[(params["item_kind"], params["item_id"])] = params["item_json"]
        elif sql.startswith("DELETE FROM AGENT_CONTROL_PLANE_ITEMS"):
            self._rows.pop((params["item_kind"], params["item_id"]), None)
        else:  # pragma: no cover - 想定外の SQL
            raise AssertionError(sql)

    def fetchall(self) -> list[tuple[str, str, str]]:
        return self._result


class _FakeConnection:
    def __init__(self, rows: dict[tuple[str, str], str]) -> None:
        self._rows = rows

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows)

    def commit(self) -> None:
        return None


def _oracle_store() -> tuple[OracleItemStore, dict[tuple[str, str], str]]:
    rows: dict[tuple[str, str], str] = {}
    return OracleItemStore(connect_factory=lambda: _FakeConnection(rows)), rows


# ---------------------------------------------------------------------------
# プラグイン
# ---------------------------------------------------------------------------


def test_plugin_secrets_are_sealed_in_file_store_and_restored(
    use_store: None, tmp_path: Path
) -> None:
    path = tmp_path / "agent-runtime.control-plane.json"
    set_control_plane_store(FileItemStore(path))

    installed = _request("POST", "/api/plugins", {"manifest": _manifest()})
    assert installed.status_code == 200, installed.text

    raw = path.read_text(encoding="utf-8")
    _assert_no_plaintext(raw)
    _assert_sealed_servers(json.loads(raw)["plugin"][PLUGIN_ID]["manifest"]["mcp_servers"])

    _forget_in_memory()
    assert restore_control_plane()["plugin"] == 1
    _assert_connection_uses_restored_secrets()
    # メモリ上の manifest も復号した値（無効化・再有効化で同じ接続を作り直すため）。
    record = plugin_registry.get(PLUGIN_ID)
    assert record is not None
    assert record.manifest.mcp_servers[0].api_key == API_KEY
    # 復元は保存し直さない（暗号化済みの行は書き換えない）。
    assert path.read_text(encoding="utf-8") == raw


def test_plugin_secrets_are_sealed_in_oracle_store_and_restored(use_store: None) -> None:
    store, rows = _oracle_store()
    set_control_plane_store(store)

    installed = _request("POST", "/api/plugins", {"manifest": _manifest()})
    assert installed.status_code == 200, installed.text
    # 有効・無効の切り替えの保存も暗号化する。
    assert _request("PATCH", f"/api/plugins/{PLUGIN_ID}", {"enabled": False}).status_code == 200
    assert _request("PATCH", f"/api/plugins/{PLUGIN_ID}", {"enabled": True}).status_code == 200

    item_json = rows[("plugin", PLUGIN_ID)]
    _assert_no_plaintext(item_json)
    _assert_sealed_servers(json.loads(item_json)["manifest"]["mcp_servers"])

    _forget_in_memory()
    assert restore_control_plane()["plugin"] == 1
    _assert_connection_uses_restored_secrets()


def test_plaintext_plugin_row_is_read_and_resealed_on_restore(use_store: None) -> None:
    """#1101 より前に平文で保存した行も読め、起動時の復元で暗号化して保存し直す。"""
    store, rows = _oracle_store()
    set_control_plane_store(store)
    legacy: dict[str, Any] = {
        "manifest": PluginManifest.model_validate(_manifest()).model_dump(mode="json"),
        "enabled": True,
        "marketplace_id": None,
    }
    rows[("plugin", PLUGIN_ID)] = json.dumps(legacy, ensure_ascii=False)
    assert API_KEY in rows[("plugin", PLUGIN_ID)]

    assert restore_control_plane()["plugin"] == 1

    _assert_connection_uses_restored_secrets()
    resealed = rows[("plugin", PLUGIN_ID)]
    _assert_no_plaintext(resealed)
    document = json.loads(resealed)
    _assert_sealed_servers(document["manifest"]["mcp_servers"])
    # 秘密のほかは保存し直しても変えない。
    for server in document["manifest"]["mcp_servers"]:
        for field in ("api_key", "oauth_client_secret", "session_id"):
            server.pop(field, None)
    for server in legacy["manifest"]["mcp_servers"]:
        for field in ("api_key", "oauth_client_secret", "session_id"):
            server.pop(field, None)
    assert document == legacy

    # 2 回目の起動では書き換えない（暗号化済み）。
    _forget_in_memory()
    assert restore_control_plane()["plugin"] == 1
    assert rows[("plugin", PLUGIN_ID)] == resealed


def test_plaintext_row_is_kept_readable_without_signing_key(
    use_store: None, monkeypatch: MonkeyPatch
) -> None:
    """署名鍵が無い環境では、平文の行は読めるが保存し直さない（起動を止めない）。"""
    store, rows = _oracle_store()
    set_control_plane_store(store)
    legacy = json.dumps(
        {"manifest": _manifest(), "enabled": True, "marketplace_id": None}, ensure_ascii=False
    )
    rows[("plugin", PLUGIN_ID)] = legacy
    monkeypatch.setattr(get_settings(), "app_service_token_secret", "")

    assert restore_control_plane()["plugin"] == 1

    assert runtime_config_store.get_mcp(API_SERVER).api_key == API_KEY
    assert rows[("plugin", PLUGIN_ID)] == legacy


def test_plugin_with_secrets_is_not_saved_without_signing_key(
    use_store: None, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "agent-runtime.control-plane.json"
    set_control_plane_store(FileItemStore(path))
    monkeypatch.setattr(get_settings(), "app_service_token_secret", "")

    response = _request("POST", "/api/plugins", {"manifest": _manifest()})

    # MCP 接続と同じく、暗号化できなければ保存せず 503（平文を残さない）。
    assert response.status_code == 503
    assert "PLATFORM_SERVICE_TOKEN_SECRET" in response.json()["error_messages"][0]
    assert not path.exists() or API_KEY not in path.read_text(encoding="utf-8")
    assert plugin_registry.get(PLUGIN_ID) is None


def test_memory_store_keeps_plugin_secrets_without_signing_key(
    use_store: None, monkeypatch: MonkeyPatch
) -> None:
    """保存しない構成（`memory`）では従来どおり署名鍵なしで導入できる。"""
    set_control_plane_store(control_plane_store.MemoryItemStore())
    monkeypatch.setattr(get_settings(), "app_service_token_secret", "")

    response = _request("POST", "/api/plugins", {"manifest": _manifest()})

    assert response.status_code == 200, response.text
    assert runtime_config_store.get_mcp(API_SERVER).api_key == API_KEY


def test_changed_signing_key_drops_plugin_secret(use_store: None, monkeypatch: MonkeyPatch) -> None:
    """署名鍵を変えると復号できない（MCP 接続と同じく、秘密なしで復元して入れ直しを求める）。"""
    store, _rows = _oracle_store()
    set_control_plane_store(store)
    assert _request("POST", "/api/plugins", {"manifest": _manifest()}).status_code == 200
    _forget_in_memory()
    monkeypatch.setattr(get_settings(), "app_service_token_secret", SIGNING_KEY[::-1])

    assert restore_control_plane()["plugin"] == 1

    api = runtime_config_store.get_mcp(API_SERVER)
    assert api.api_key is None
    assert not (api.api_key or "").startswith("enc:v1:")


# ---------------------------------------------------------------------------
# マーケットプレイスの一覧の native manifest
# ---------------------------------------------------------------------------


def test_marketplace_listing_manifest_secrets_are_sealed_and_restored(
    use_store: None,
) -> None:
    store, rows = _oracle_store()
    set_control_plane_store(store)
    listing = {"name": "秘密を持つ一覧", "plugins": [_manifest()]}

    added = _request(
        "POST",
        "/api/plugins/marketplaces",
        {"id": MARKET_ID, "name": "秘密を持つ一覧", "listing": listing},
    )
    assert added.status_code == 200, added.text

    item_json = rows[("marketplace", MARKET_ID)]
    _assert_no_plaintext(item_json)
    _assert_sealed_servers(json.loads(item_json)["listing"]["plugins"][0]["mcp_servers"])

    _forget_in_memory()
    assert restore_control_plane()["marketplace"] == 1
    restored = marketplace_registry.get_listing(MARKET_ID).plugins[0]
    assert isinstance(restored, PluginManifest)
    assert restored.mcp_servers[0].api_key == API_KEY
    assert restored.mcp_servers[1].oauth_client_secret == CLIENT_SECRET

    # 一覧から導入すると、復号した値で接続する。
    installed = _request(
        "POST", "/api/plugins", {"marketplace_id": MARKET_ID, "plugin_id": PLUGIN_ID}
    )
    assert installed.status_code == 200, installed.text
    _assert_connection_uses_restored_secrets()
    _assert_no_plaintext(rows[("plugin", PLUGIN_ID)])


def test_plaintext_marketplace_row_is_resealed_on_restore(use_store: None) -> None:
    store, rows = _oracle_store()
    set_control_plane_store(store)
    source = MarketplaceSource(id=MARKET_ID, name="旧形式")
    listing = MarketplaceListing.model_validate({"name": "旧形式", "plugins": [_manifest()]})
    rows[("marketplace", MARKET_ID)] = json.dumps(
        {"source": source.model_dump(mode="json"), "listing": listing.model_dump(mode="json")},
        ensure_ascii=False,
    )

    assert restore_control_plane()["marketplace"] == 1

    restored = marketplace_registry.get_listing(MARKET_ID).plugins[0]
    assert isinstance(restored, PluginManifest)
    assert restored.mcp_servers[0].api_key == API_KEY
    _assert_no_plaintext(rows[("marketplace", MARKET_ID)])
