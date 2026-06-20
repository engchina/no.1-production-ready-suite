"""health / NL2SQL preview の疎通テスト（Oracle 不要）。"""

from fastapi.testclient import TestClient

from app.features.nl2sql.router import is_select_only
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


def test_nl2sql_preview_returns_safe_select() -> None:
    resp = client.post("/api/nl2sql/preview", json={"question": "売上トップ10は?"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["is_safe"] is True
    assert data["sql"].lower().startswith("select")


def test_select_only_guard() -> None:
    assert is_select_only("SELECT * FROM t") is True
    assert is_select_only("WITH x AS (SELECT 1) SELECT * FROM x") is True
    assert is_select_only("DELETE FROM t") is False
    assert is_select_only("drop table t") is False
