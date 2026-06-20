"""health / Agent ツールの疎通テスト（Oracle 不要）。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


def test_ready() -> None:
    resp = client.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


def test_list_tools_includes_echo() -> None:
    resp = client.get("/api/agent/tools")
    assert resp.status_code == 200
    assert "echo" in resp.json()["data"]["tools"]


def test_invoke_tool_echo() -> None:
    resp = client.post("/api/agent/tools/invoke", json={"name": "echo", "arguments": {"a": 1}})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["success"] is True
    assert data["output"]["echo"] == {"a": 1}


def test_invoke_unknown_tool() -> None:
    resp = client.post("/api/agent/tools/invoke", json={"name": "nope", "arguments": {}})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["success"] is False
    assert data["error"] == "unknown tool"
