"""MinerU のライセンスの条件の確認（#3359）のテスト。

管理者が「文書解析」で条件を確認するまで、MinerU を有効にも選択にもできず（設定・レシピの保存は
422）、取込でも MinerU を呼ばない。確認した人・日時は parser-settings.json に保存する。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from pytest import MonkeyPatch

from app.clients.external_parser import ExternalParserCallError, ExternalParserClient
from app.clients.parser_service import ParserServiceClient, ParserServiceUnavailableError
from app.config import PARSER_SETTINGS_STORE, Settings, get_settings
from app.main import app
from app.rag.mineru_license import MINERU_LICENSE_REQUIRED_MESSAGE
from app.schemas.document import SourceModality, SourceProfile
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
ACKNOWLEDGED_AT = "2026-10-26T00:00:00+00:00"


def _persisted_parser(settings: Settings) -> dict[str, Any]:
    persisted: dict[str, Any] = json.loads(
        PARSER_SETTINGS_STORE.path(settings).read_text(encoding="utf-8")
    )
    return persisted


def _profile() -> SourceProfile:
    return SourceProfile(
        original_file_name="a.pdf",
        sanitized_file_name="a.pdf",
        content_type="application/pdf",
        file_size_bytes=4,
        content_sha256="0" * 64,
        modality=SourceModality.PDF,
        parser_profile="pdf",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"adapter_backend": "mineru", "mineru_enabled": True},
        {"adapter_backend": "docling", "mineru_enabled": True},
        {"adapter_backend": "mineru"},
    ],
    ids=["select-and-enable", "enable-only", "select-only"],
)
def test_enabling_mineru_without_license_acknowledgement_is_rejected(
    payload: dict[str, object],
) -> None:
    settings = get_settings()
    before_backend = settings.rag_parser_adapter_backend
    before_vision = settings.rag_vision_enabled

    response = client.patch(
        "/api/settings/parser-adapters", json={**payload, "vision_enabled": not before_vision}
    )

    assert response.status_code == 422
    assert MINERU_LICENSE_REQUIRED_MESSAGE in response.text, response.text
    assert settings.rag_parser_adapter_backend == before_backend
    assert settings.rag_parser_mineru_enabled is False
    # 拒否した保存は、同じ要求の「解析後の処理」も書かない。
    assert settings.rag_vision_enabled is before_vision
    assert not PARSER_SETTINGS_STORE.path(settings).exists()


def test_enabling_mineru_with_license_acknowledgement_records_who_and_when(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(
        "app.api.routes.settings._principal_display_name", lambda _request: "管理者 A"
    )

    response = client.patch(
        "/api/settings/parser-adapters",
        json={
            "adapter_backend": "mineru",
            "mineru_enabled": True,
            "mineru_license_acknowledged": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["adapter_backend"] == "mineru"
    license_data = data["mineru_license"]
    assert license_data["acknowledged"] is True
    assert license_data["acknowledged_by"] == "管理者 A"
    assert datetime.fromisoformat(license_data["acknowledged_at"]).tzinfo is not None
    persisted = _persisted_parser(settings)
    assert persisted["mineru_license_acknowledged_by"] == "管理者 A"
    assert persisted["mineru_license_acknowledged_at"] == license_data["acknowledged_at"]

    # 確認済みのまま保存し直しても、最初の確認の記録を保つ。
    monkeypatch.setattr(
        "app.api.routes.settings._principal_display_name", lambda _request: "管理者 B"
    )
    again = client.patch(
        "/api/settings/parser-adapters",
        json={"adapter_backend": "mineru", "mineru_license_acknowledged": True},
    )
    assert again.status_code == 200
    assert again.json()["data"]["mineru_license"] == license_data


def test_get_reports_unacknowledged_mineru_license_by_default() -> None:
    response = client.get("/api/settings/parser-adapters")

    assert response.status_code == 200
    assert response.json()["data"]["mineru_license"] == {
        "acknowledged": False,
        "acknowledged_by": None,
        "acknowledged_at": None,
    }


def test_withdrawing_acknowledgement_while_mineru_is_used_is_rejected(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "mineru")
    monkeypatch.setattr(settings, "rag_parser_mineru_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_mineru_license_acknowledged_at", ACKNOWLEDGED_AT)

    response = client.patch(
        "/api/settings/parser-adapters",
        json={"adapter_backend": "mineru", "mineru_license_acknowledged": False},
    )

    assert response.status_code == 422
    assert settings.rag_parser_mineru_license_acknowledged_at == ACKNOWLEDGED_AT


def test_withdrawing_acknowledgement_after_switching_away_from_mineru(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "mineru")
    monkeypatch.setattr(settings, "rag_parser_mineru_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_mineru_license_acknowledged_at", ACKNOWLEDGED_AT)

    response = client.patch(
        "/api/settings/parser-adapters",
        json={
            "adapter_backend": "docling",
            "docling_enabled": True,
            "mineru_enabled": False,
            "mineru_license_acknowledged": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["mineru_license"]["acknowledged"] is False
    persisted = _persisted_parser(settings)
    assert persisted["mineru_license_acknowledged_at"] == ""
    assert persisted["mineru_license_acknowledged_by"] == ""


def test_unrelated_save_is_not_blocked_when_mineru_was_enabled_before(
    monkeypatch: MonkeyPatch,
) -> None:
    """`.env` で前から MinerU が有効でも、ほかの項目の保存は止めない（取込で止める）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_mineru_enabled", True)

    response = client.patch(
        "/api/settings/parser-adapters",
        json={"adapter_backend": "docling", "docling_enabled": True, "mineru_enabled": True},
    )

    assert response.status_code == 200


def test_recipe_selecting_mineru_without_acknowledgement_is_rejected() -> None:
    response = client.put(
        "/api/documents/doc-1/recipes/recipe-1",
        json={"parser_adapter_backend": "mineru"},
    )

    assert response.status_code == 422
    assert MINERU_LICENSE_REQUIRED_MESSAGE in response.text, response.text


def test_recipe_gate_allows_mineru_after_acknowledgement(monkeypatch: MonkeyPatch) -> None:
    from app.api.routes.documents import _raise_if_recipe_selects_mineru_without_license
    from app.schemas.document import DocumentProcessingConfig

    settings = get_settings()
    config = DocumentProcessingConfig(parser_adapter_backend="mineru")
    monkeypatch.setattr(settings, "rag_parser_mineru_license_acknowledged_at", ACKNOWLEDGED_AT)

    _raise_if_recipe_selects_mineru_without_license(config)
    # MinerU を選ばないレシピは確認に関係なく保存できる。
    monkeypatch.setattr(settings, "rag_parser_mineru_license_acknowledged_at", "")
    _raise_if_recipe_selects_mineru_without_license(
        DocumentProcessingConfig(parser_adapter_backend="docling")
    )


def test_external_parser_does_not_call_mineru_without_acknowledgement() -> None:
    settings = Settings(rag_parser_mineru_api_host="https://mineru.example.com")

    with pytest.raises(ExternalParserCallError) as exc_info:
        ExternalParserClient(settings).parse("mineru", b"%PDF", _profile(), "application/pdf")

    assert exc_info.value.reason == "license_unacknowledged"
    assert exc_info.value.warning_code == "mineru_license_unacknowledged"


def test_ingestion_stops_with_license_message_without_acknowledgement() -> None:
    client_ = ParserServiceClient(Settings(rag_parser_mineru_api_host="https://mineru.example.com"))

    with pytest.raises(ParserServiceUnavailableError) as exc_info:
        client_.runner("mineru", b"%PDF", _profile(), "application/pdf", fail_fast=True)

    assert exc_info.value.reason == "license_unacknowledged"
    assert str(exc_info.value).startswith(MINERU_LICENSE_REQUIRED_MESSAGE)
