"""parser-unstructured のモデルを配備で取得・検証し、解析の要求の時には取得させない。"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from rag_parser_core import model_integrity as integrity
from rag_parser_core import unstructured_models as models

WEIGHTS = b"synthetic-weights"
CONFIG = b'{"model_type":"synthetic"}'


def _git_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


MODEL = models.HubModel(
    repo="synthetic/layout-model",
    revision="a" * 40,
    files=(
        models.HubFile("weights.onnx", hashlib.sha256(WEIGHTS).hexdigest(), "sha256"),
        models.HubFile("config.json", _git_sha1(CONFIG), "git-sha1"),
    ),
)
CONTENT = {"weights.onnx": WEIGHTS, "config.json": CONFIG}


@pytest.fixture(autouse=True)
def _synthetic_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "HUB_MODELS", (MODEL,))
    monkeypatch.setattr(
        models, "_hub_url", lambda repo, filename, revision: f"https://example.invalid/{filename}"
    )
    monkeypatch.setattr(integrity.time, "sleep", lambda _seconds: None)


def _serve(url: str, timeout: float) -> io.BytesIO:
    return io.BytesIO(CONTENT[url.rsplit("/", 1)[1]])


def _repo(cache: Path) -> Path:
    return cache / "models--synthetic--layout-model"


def test_fetch_publishes_pinned_snapshot_then_reuses_it_offline(tmp_path: Path) -> None:
    with patch.object(integrity, "urlopen", side_effect=_serve) as get:
        repaired = models.verify_hub_models(tmp_path)
    assert sorted(repaired) == [
        "synthetic/layout-model/config.json",
        "synthetic/layout-model/weights.onnx",
    ]
    assert get.call_count == 2
    snapshot = _repo(tmp_path) / "snapshots" / MODEL.revision
    # HF のキャッシュの構成: snapshot は blob への相対の symlink、refs/main は固定の commit。
    assert (snapshot / "weights.onnx").read_bytes() == WEIGHTS
    assert not os.path.isabs(os.readlink(snapshot / "weights.onnx"))
    assert (_repo(tmp_path) / "refs" / "main").read_text() == MODEL.revision
    assert list(_repo(tmp_path).rglob("*.part")) == []

    with patch.object(integrity, "urlopen") as get:
        assert models.verify_hub_models(tmp_path) == []
        models.verify_hub_models(tmp_path, repair=False)
    get.assert_not_called()


def test_corrupt_blob_is_refetched_at_pinned_revision(tmp_path: Path) -> None:
    with patch.object(integrity, "urlopen", side_effect=_serve):
        models.verify_hub_models(tmp_path)
    blob = _repo(tmp_path) / "blobs" / MODEL.files[0].digest
    blob.write_bytes(b"truncated")
    with patch.object(integrity, "urlopen", side_effect=_serve) as get:
        assert models.verify_hub_models(tmp_path) == ["synthetic/layout-model/weights.onnx"]
    assert get.call_args.args[0] == "https://example.invalid/weights.onnx"
    assert blob.read_bytes() == WEIGHTS


def test_download_with_wrong_content_is_rejected_and_not_published(tmp_path: Path) -> None:
    with (
        patch.object(integrity, "urlopen", side_effect=lambda *_a, **_k: io.BytesIO(b"<html>")),
        pytest.raises(RuntimeError, match="モデルの修復に失敗"),
    ):
        models.verify_hub_models(tmp_path)
    assert not (_repo(tmp_path) / "refs" / "main").exists()
    assert not (_repo(tmp_path) / "blobs" / MODEL.files[0].digest).exists()


@pytest.mark.parametrize("state", ["empty", "corrupt", "other_revision"])
def test_check_only_never_downloads_and_fails_clearly(tmp_path: Path, state: str) -> None:
    if state != "empty":
        with patch.object(integrity, "urlopen", side_effect=_serve):
            models.verify_hub_models(tmp_path)
    if state == "corrupt":
        (_repo(tmp_path) / "blobs" / MODEL.files[1].digest).write_bytes(b"{}")
    if state == "other_revision":
        (_repo(tmp_path) / "refs" / "main").write_text("b" * 40)
    with patch.object(integrity, "urlopen") as get, pytest.raises(RuntimeError, match="モデル"):
        models.verify_hub_models(tmp_path, repair=False)
    get.assert_not_called()


def _fake_tokenize(monkeypatch: pytest.MonkeyPatch, version: str = "3.8.0") -> types.ModuleType:
    """unstructured.nlp.tokenize の代わり（rag_parser_core の venv に unstructured は無い）。"""
    module = types.ModuleType("unstructured.nlp.tokenize")
    module._SPACY_MODEL_VERSION = version  # type: ignore[attr-defined]

    def install() -> None:
        raise AssertionError("実行時に取得しようとした")

    module._install_spacy_model = install  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "unstructured.nlp.tokenize", module)
    return module


def test_runtime_spacy_download_is_replaced_by_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tokenize = _fake_tokenize(monkeypatch)
    models.forbid_runtime_downloads()
    forbidden = tokenize._install_spacy_model
    with pytest.raises(
        models.ModelsNotDeployedError, match="解析の要求の時にはモデルを取得しません"
    ):
        forbidden()
    # 2 回目は置き直さない。
    models.forbid_runtime_downloads()
    assert tokenize._install_spacy_model is forbidden


@pytest.mark.parametrize(
    ("installed", "fetched", "expected"),
    [("3.8.0", True, True), ("3.7.1", True, False), (None, True, False), ("3.8.0", False, False)],
    ids=["ready", "spacy-version", "spacy-missing", "hub-missing"],
)
def test_health_requires_deployed_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    installed: str | None,
    fetched: bool,
    expected: bool,
) -> None:
    _fake_tokenize(monkeypatch)

    def version(name: str) -> str:
        if installed is None:
            raise models.importlib.metadata.PackageNotFoundError(name)
        return installed

    monkeypatch.setattr(models.importlib.metadata, "version", version)
    monkeypatch.setattr(models, "_hub_cache_dir", lambda: tmp_path)
    if fetched:
        with patch.object(integrity, "urlopen", side_effect=_serve):
            models.verify_hub_models(tmp_path)
    assert models.models_ready() is expected
