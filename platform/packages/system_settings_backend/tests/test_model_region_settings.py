"""モデル別のGenerative AIリージョンを保存・読戻しする。"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_model import PAYLOAD, FakeSettings, make_client, make_store

from pr_system_settings.model import GenerativeAiModelSettings


class RegionSettings(FakeSettings):
    oci_genai_embedding_region: str = "ap-osaka-1"
    oci_genai_rerank_region: str = "ap-osaka-1"


def test_missing_regions_default_to_osaka() -> None:
    actual = GenerativeAiModelSettings().model_dump()
    assert actual["embedding_region"] == "ap-osaka-1"
    assert actual["rerank_region"] == "ap-osaka-1"


@pytest.mark.parametrize("field", ["embedding_region", "rerank_region"])
@pytest.mark.parametrize("region", ["", "eu-frankfurt-1", "synthetic-invalid"])
def test_invalid_region_is_rejected(field: str, region: str) -> None:
    with pytest.raises(ValidationError):
        GenerativeAiModelSettings.model_validate({field: region})


def test_independent_regions_round_trip_in_settings_file_and_api(tmp_path: Path) -> None:
    settings = RegionSettings()
    store = make_store(tmp_path)
    store.load(settings)
    payload: dict[str, Any] = deepcopy(PAYLOAD)
    payload["generative_ai"].update(embedding_region="us-chicago-1", rerank_region="ap-osaka-1")
    client = make_client(settings, store)
    response = client.patch("/api/settings/model", json=payload)
    assert response.status_code == 200, response.text
    generative = response.json()["data"]["settings"]["generative_ai"]
    assert generative["embedding_region"] == "us-chicago-1"
    assert generative["rerank_region"] == "ap-osaka-1"
    fresh = RegionSettings()
    make_store(tmp_path).load(fresh)
    assert fresh.oci_genai_embedding_region == "us-chicago-1"
    assert fresh.oci_genai_rerank_region == "ap-osaka-1"
    readback = client.get("/api/settings/model").json()["data"]["settings"]["generative_ai"]
    assert readback["embedding_region"] == "us-chicago-1"
    assert readback["rerank_region"] == "ap-osaka-1"


def test_invalid_region_patch_preserves_existing_settings(tmp_path: Path) -> None:
    settings = RegionSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    payload = deepcopy(PAYLOAD)
    payload["generative_ai"]["embedding_region"] = "synthetic-invalid"
    response = client.patch("/api/settings/model", json=payload)
    assert response.status_code == 422
    assert settings.oci_genai_embedding_region == "ap-osaka-1"
    assert settings.oci_genai_rerank_region == "ap-osaka-1"
    assert not (tmp_path / "model-settings.json").exists()


@pytest.mark.parametrize("field", ["embedding_region", "rerank_region"])
def test_tokyo_cannot_be_newly_saved(tmp_path: Path, field: str) -> None:
    settings = RegionSettings()
    store = make_store(tmp_path)
    store.load(settings)
    payload = deepcopy(PAYLOAD)
    payload["generative_ai"][field] = "ap-tokyo-1"
    response = make_client(settings, store).patch("/api/settings/model", json=payload)
    assert response.status_code == 422
    assert "選び直してください" in response.text
    assert settings.oci_genai_embedding_region == "ap-osaka-1"
    assert settings.oci_genai_rerank_region == "ap-osaka-1"
    assert not (tmp_path / "model-settings.json").exists()


def test_saved_tokyo_remains_readable_without_migration(tmp_path: Path) -> None:
    import json

    settings = RegionSettings()
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    assert client.patch("/api/settings/model", json=deepcopy(PAYLOAD)).status_code == 200
    path = tmp_path / "model-settings.json"
    saved = json.loads(path.read_text())
    saved["generative_ai"].update(embedding_region="ap-tokyo-1", rerank_region="ap-tokyo-1")
    path.write_text(json.dumps(saved))
    original = path.read_bytes()
    fresh = RegionSettings()
    fresh_store = make_store(tmp_path)
    fresh_store.load(fresh)
    response = make_client(fresh, fresh_store).get("/api/settings/model")
    assert response.status_code == 200
    regions = response.json()["data"]["settings"]["generative_ai"]
    assert regions["embedding_region"] == "ap-tokyo-1"
    assert regions["rerank_region"] == "ap-tokyo-1"
    assert fresh.oci_genai_embedding_region == "ap-tokyo-1"
    assert fresh.oci_genai_rerank_region == "ap-tokyo-1"
    assert path.read_bytes() == original


def test_unchanged_tokyo_does_not_block_other_model_settings(tmp_path: Path) -> None:
    settings = RegionSettings(oci_genai_embedding_region="ap-tokyo-1")
    store = make_store(tmp_path)
    store.load(settings)
    client = make_client(settings, store)
    payload = client.get("/api/settings/model").json()["data"]["settings"]
    payload["enterprise_ai"]["timeout_seconds"] = 121
    response = client.patch("/api/settings/model", json=payload)
    assert response.status_code == 200, response.text
    assert settings.oci_genai_embedding_region == "ap-tokyo-1"
    assert response.json()["data"]["settings"]["enterprise_ai"]["timeout_seconds"] == 121
