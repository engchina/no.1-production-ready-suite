"""マーケットプレイスの画面が使う API の検証とエラーの文（#928）。"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.features.agent.plugins import marketplace_registry, plugin_registry
from app.main import app

client = TestClient(app)

MARKETPLACE_ID = "market_928"
PLUGIN_ID = "plugin_928"


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    with contextlib.suppress(KeyError, ValueError):
        plugin_registry.uninstall(PLUGIN_ID)
    with contextlib.suppress(KeyError):
        marketplace_registry.remove(MARKETPLACE_ID)


def _messages(response: Any) -> list[str]:
    return list(response.json()["error_messages"])


def _listing() -> dict[str, Any]:
    return {
        "name": "First",
        "plugins": [{"id": PLUGIN_ID, "name": "経理のプラグイン", "version": "1", "skills": []}],
    }


def test_duplicate_marketplace_id_is_rejected_without_overwriting() -> None:
    first = client.post(
        "/api/plugins/marketplaces",
        json={"id": MARKETPLACE_ID, "name": "First", "listing": _listing()},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/plugins/marketplaces",
        json={"id": f" {MARKETPLACE_ID} ", "name": "Second", "url": "https://example.invalid/x"},
    )
    assert second.status_code == 409
    assert _messages(second) == ["同じ ID のマーケットプレイスがあります。"]

    sources = [
        source
        for source in client.get("/api/plugins/marketplaces").json()["data"]["marketplaces"]
        if source["id"] == MARKETPLACE_ID
    ]
    assert len(sources) == 1
    assert sources[0]["name"] == "First"
    assert sources[0]["url"] is None
    assert sources[0]["plugin_count"] == 1


@pytest.mark.parametrize("marketplace_id", ["a/b", "a b", ".hidden", "社内", "x" * 101])
def test_marketplace_id_must_be_url_safe(marketplace_id: str) -> None:
    created = client.post("/api/plugins/marketplaces", json={"id": marketplace_id})
    assert created.status_code == 422
    assert _messages(created) == [
        "マーケットプレイスの ID は英数字で始め、英数字・_・-・. の 100 文字以内にしてください。"
    ]


def test_marketplace_api_errors_are_japanese() -> None:
    blank = client.post("/api/plugins/marketplaces", json={"id": "  "})
    assert blank.status_code == 400
    assert _messages(blank) == ["マーケットプレイスの ID を入力してください。"]

    for response in (
        client.post("/api/plugins/marketplaces/missing_928/refresh"),
        client.get("/api/plugins/marketplaces/missing_928/plugins"),
        client.request("DELETE", "/api/plugins/marketplaces/missing_928"),
    ):
        assert response.status_code == 404
        assert _messages(response) == ["マーケットプレイスが見つかりません。"]

    assert (
        client.post(
            "/api/plugins/marketplaces", json={"id": MARKETPLACE_ID, "listing": _listing()}
        ).status_code
        == 200
    )
    install = {"marketplace_id": MARKETPLACE_ID, "plugin_id": PLUGIN_ID}
    assert client.post("/api/plugins", json=install).status_code == 200
    again = client.post("/api/plugins", json=install)
    assert again.status_code == 409
    assert _messages(again) == ["このプラグインは導入済みです。"]

    missing = client.post(
        "/api/plugins", json={"marketplace_id": MARKETPLACE_ID, "plugin_id": "missing_928"}
    )
    assert missing.status_code == 404
    assert _messages(missing) == ["マーケットプレイスに対象のプラグインがありません。"]

    neither = client.post("/api/plugins", json={})
    assert neither.status_code == 400
    assert _messages(neither) == ["導入するプラグインを指定してください。"]
