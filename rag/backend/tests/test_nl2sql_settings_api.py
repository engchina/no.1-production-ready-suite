"""NL2SQL 設定 API(router / guardrail / cache)のテスト。"""

from pathlib import Path

from pytest import MonkeyPatch

from app.api.routes import settings as settings_routes
from app.config import get_settings
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def _env_file(monkeypatch: MonkeyPatch, tmp_path: Path) -> Path:
    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", env_file)
    return env_file


# --- Router ---
def test_router_settings_reports_runtime_profile(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_router_profile", "complexity_aware")

    resp = client.get("/api/settings/nl2sql/router")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["profile"] == "complexity_aware"
    assert body["default_generation_backend"] == "select_ai_agent"
    names = [item["name"] for item in body["profiles"]]
    assert names[0] == "off"
    assert [item["name"] for item in body["profiles"] if item["selected"]] == ["complexity_aware"]


def test_update_router_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_router_profile", "off")
    monkeypatch.setattr(settings, "nl2sql_router_complexity_threshold", 2)
    env_file = _env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/nl2sql/router",
        json={"profile": "classifier", "complexity_threshold": 3},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["profile"] == "classifier"
    assert settings.nl2sql_router_profile == "classifier"
    assert settings.nl2sql_router_complexity_threshold == 3
    text = env_file.read_text(encoding="utf-8")
    assert "NL2SQL_ROUTER_PROFILE=classifier" in text
    assert "NL2SQL_ROUTER_COMPLEXITY_THRESHOLD=3" in text


def test_update_router_rejects_unknown_profile() -> None:
    resp = client.patch("/api/settings/nl2sql/router", json={"profile": "magic"})
    assert resp.status_code == 422


# --- Guardrail ---
def test_nl2sql_guardrail_settings_reports_runtime_policy(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_guardrail_policy", "strict")

    resp = client.get("/api/settings/nl2sql/guardrail")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["policy"] == "strict"
    assert body["semantic_verify"] is True
    assert body["require_object_allowlist"] is True
    assert all(item["enforce_read_only"] for item in body["policies"])
    assert [item["name"] for item in body["policies"]][0] == "read_only"


def test_update_nl2sql_guardrail_settings_persists_env(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_guardrail_policy", "read_only")
    monkeypatch.setattr(settings, "nl2sql_guardrail_max_rows", 1000)
    monkeypatch.setattr(settings, "nl2sql_guardrail_run_role", "")
    env_file = _env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/nl2sql/guardrail",
        json={"policy": "sandboxed", "max_rows": 500, "run_role": "NL2SQL_RO"},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["policy"] == "sandboxed"
    assert body["run_role"] == "NL2SQL_RO"
    assert settings.nl2sql_guardrail_policy == "sandboxed"
    assert settings.nl2sql_guardrail_max_rows == 500
    assert settings.nl2sql_guardrail_run_role == "NL2SQL_RO"
    text = env_file.read_text(encoding="utf-8")
    assert "NL2SQL_GUARDRAIL_POLICY=sandboxed" in text
    assert "NL2SQL_GUARDRAIL_MAX_ROWS=500" in text


def test_update_nl2sql_guardrail_rejects_unknown_policy() -> None:
    resp = client.patch("/api/settings/nl2sql/guardrail", json={"policy": "paranoid"})
    assert resp.status_code == 422


# --- Cache ---
def test_cache_settings_reports_runtime_policy(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_cache_policy", "nl_sql")

    resp = client.get("/api/settings/nl2sql/cache")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["policy"] == "nl_sql"
    assert body["cache_nl_to_sql"] is True
    assert body["cache_sql_to_result"] is False
    assert [item["name"] for item in body["policies"]][0] == "off"


def test_update_cache_settings_persists_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_cache_policy", "off")
    monkeypatch.setattr(settings, "nl2sql_cache_similarity_threshold", 0.95)
    monkeypatch.setattr(settings, "nl2sql_cache_ttl_seconds", 300)
    env_file = _env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/nl2sql/cache",
        json={"policy": "sql_result", "ttl_seconds": 120},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["policy"] == "sql_result"
    assert settings.nl2sql_cache_policy == "sql_result"
    assert settings.nl2sql_cache_ttl_seconds == 120
    assert "NL2SQL_CACHE_POLICY=sql_result" in env_file.read_text(encoding="utf-8")


def test_update_cache_rejects_unknown_policy() -> None:
    resp = client.patch("/api/settings/nl2sql/cache", json={"policy": "magic"})
    assert resp.status_code == 422
