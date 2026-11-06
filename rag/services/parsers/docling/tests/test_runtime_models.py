"""実際の Docling で、解析の要求の時にはモデルを取得・修復しないことを確かめる(#4557)。

Docling の自動管理キャッシュ(``<cache_dir>/models``)を一時ディレクトリにし、HF Hub からの取得
(``huggingface_hub.snapshot_download``)と配布元からの取り直し(``model_integrity.urlopen``)が
呼ばれたら記録して失敗させる。
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Iterator
from pathlib import Path

import huggingface_hub
import pypdfium2 as pdfium
import pytest
import rag_engine.resources.model_pool as model_pool
from docling.datamodel.settings import settings as docling_settings
from fastapi.testclient import TestClient
from rag_parser_core import model_integrity

from app.main import app

REDEPLOY = "parser-docling を配備し直してください"


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[list[str]]:
    """空のモデルのキャッシュで、要求の時の取得の呼び出しを記録する。"""
    calls: list[str] = []

    def forbidden(name: str):
        def record(*_args: object, **_kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"解析の要求の時にモデルを取得しようとした: {name}")

        return record

    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.setenv("RAG_ENGINE_OUTPUT_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(docling_settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(huggingface_hub, "snapshot_download", forbidden("snapshot_download"))
    monkeypatch.setattr(model_integrity, "urlopen", forbidden("urlopen"))
    model_pool.unload_all()
    yield calls
    model_pool.unload_all()


def _hf_file(models: Path, filename: str, content: bytes) -> Path:
    """docling-tools models download と同じ形(取得の metadata 付き)の合成のモデルのファイル。"""
    repo = models / "synthetic--layout-model"
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    metadata = repo / ".cache/huggingface/download" / f"{filename}.metadata"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(f"{'a' * 40}\n{hashlib.sha256(content).hexdigest()}\n0.0\n")
    return path


def _pdf() -> bytes:
    document = pdfium.PdfDocument.new()
    document.new_page(200, 200)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _parse(client: TestClient) -> dict:
    response = client.post(
        "/parse",
        files={"file": ("synthetic.pdf", _pdf(), "application/pdf")},
        data={"content_type": "application/pdf"},
    )
    assert response.status_code == 200
    return response.json()


def test_parse_without_deployed_models_fails_without_downloading(downloads: list[str]) -> None:
    payload = _parse(TestClient(app))

    assert downloads == []
    assert payload["extraction"] is None
    assert any(REDEPLOY in warning for warning in payload["warnings"]), payload["warnings"]


def test_parse_with_corrupt_models_fails_without_repairing(downloads: list[str]) -> None:
    models = docling_settings.cache_dir / "models"
    _hf_file(models, "config.json", b"{}")
    _hf_file(models, "model.safetensors", b"synthetic-weights").write_bytes(b"incomplete")

    payload = _parse(TestClient(app))

    assert downloads == []
    assert payload["extraction"] is None
    assert any(REDEPLOY in warning for warning in payload["warnings"]), payload["warnings"]
    assert (models / "synthetic--layout-model/model.safetensors").read_bytes() == b"incomplete"


def test_health_is_degraded_until_models_are_deployed(downloads: list[str]) -> None:
    # 起動(lifespan)の検証も取得しない。
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "degraded"

        models = docling_settings.cache_dir / "models"
        weights = _hf_file(models, "model.safetensors", b"synthetic-weights")
        assert client.get("/health").json()["status"] == "ok"

        weights.write_bytes(b"corrupt-weights!")
        assert client.get("/health").json()["status"] == "degraded"
    assert downloads == []
