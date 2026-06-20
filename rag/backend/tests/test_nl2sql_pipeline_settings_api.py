"""NL2SQL パイプライン preset 設定 API のテスト。"""

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


def test_pipeline_settings_lists_all_adapters(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_knowledge_profile", "few_shot")

    resp = client.get("/api/settings/nl2sql/pipeline")

    assert resp.status_code == 200
    adapters = resp.json()["data"]["adapters"]
    keys = [adapter["key"] for adapter in adapters]
    assert keys == [
        "schema_source",
        "schema_linking",
        "knowledge",
        "clarify",
        "generation",
        "correction",
        "agentic",
        "result",
        "evaluation",
    ]
    knowledge = next(a for a in adapters if a["key"] == "knowledge")
    assert knowledge["selected"] == "few_shot"
    assert [o["name"] for o in knowledge["options"] if o["selected"]] == ["few_shot"]


def test_update_pipeline_setting_persists_and_mutates(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_schema_linking", "enforce_all")
    env_file = _env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/nl2sql/pipeline/schema_linking", json={"selection": "auto_prune"}
    )

    assert resp.status_code == 200
    adapters = resp.json()["data"]["adapters"]
    linking = next(a for a in adapters if a["key"] == "schema_linking")
    assert linking["selected"] == "auto_prune"
    assert settings.nl2sql_schema_linking == "auto_prune"
    assert "NL2SQL_SCHEMA_LINKING=auto_prune" in env_file.read_text(encoding="utf-8")


def test_update_unknown_adapter_returns_404() -> None:
    resp = client.patch("/api/settings/nl2sql/pipeline/nope", json={"selection": "x"})
    assert resp.status_code == 404


def test_update_invalid_selection_returns_422() -> None:
    resp = client.patch("/api/settings/nl2sql/pipeline/result", json={"selection": "hologram"})
    assert resp.status_code == 422
