"""取得途中の モデルを再利用せず、検証済みファイルだけを公開する。"""

from __future__ import annotations

import hashlib
import io
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

import pytest

from rag_engine.resources import docling_models as models


def artifact(content: bytes = b"synthetic-valid-model") -> models.ModelArtifact:
    return models.ModelArtifact("model.onnx", "https://example.invalid/model.onnx", hashlib.sha256(content).hexdigest())


def test_valid_cache_is_offline_and_unchanged(tmp_path: Path) -> None:
    spec = artifact()
    path = tmp_path / spec.name
    path.write_bytes(b"synthetic-valid-model")
    before = path.stat().st_mtime_ns
    with patch.object(models, "_registered_models", return_value={spec.name: [spec]}), patch.object(models, "urlopen") as get:
        assert models.verify_rapidocr_cache(tmp_path) == []
    get.assert_not_called()
    assert path.stat().st_mtime_ns == before


def test_corrupt_cache_is_repaired_atomically(tmp_path: Path) -> None:
    spec = artifact()
    path = tmp_path / spec.name
    path.write_bytes(b"incomplete")

    def download(*args, **kwargs):
        assert path.read_bytes() == b"incomplete"
        return io.BytesIO(b"synthetic-valid-model")

    with (
        patch.object(models, "_registered_models", return_value={spec.name: [spec]}),
        patch.object(models, "urlopen", side_effect=download),
    ):
        assert models.verify_rapidocr_cache(tmp_path) == [spec.name]
    assert path.read_bytes() == b"synthetic-valid-model"
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize("failure", [OSError("interrupted"), IncompleteRead(b"partial", 100), b"<html>not a model</html>"])
def test_failed_download_keeps_original_and_cleans_temporary_files(tmp_path: Path, failure) -> None:
    spec = artifact()
    path = tmp_path / spec.name
    path.write_bytes(b"incomplete")

    def download(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return io.BytesIO(failure)

    with (
        patch.object(models, "_registered_models", return_value={spec.name: [spec]}),
        patch.object(models, "urlopen", side_effect=download) as get,
        patch.object(models.time, "sleep"),
        pytest.raises(RuntimeError, match="モデル"),
    ):
        models.verify_rapidocr_cache(tmp_path)
    assert get.call_count == 3
    assert path.read_bytes() == b"incomplete"
    assert list(tmp_path.glob("*.part")) == []


def test_check_only_never_downloads_or_overwrites(tmp_path: Path) -> None:
    spec = artifact()
    (tmp_path / spec.name).write_bytes(b"incomplete")
    with (
        patch.object(models, "_registered_models", return_value={spec.name: [spec]}),
        patch.object(models, "urlopen") as get,
        pytest.raises(RuntimeError, match="モデル"),
    ):
        models.verify_rapidocr_cache(tmp_path, repair=False)
    get.assert_not_called()


def test_unknown_custom_model_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "custom.onnx"
    path.write_bytes(b"custom")
    with patch.object(models, "_registered_models", return_value={}), patch.object(models, "urlopen") as get:
        assert models.verify_rapidocr_cache(tmp_path) == []
    assert path.read_bytes() == b"custom"
    get.assert_not_called()


def test_absent_cache_does_not_import_optional_ocr_dependencies(tmp_path: Path) -> None:
    with patch.object(models, "_registered_models") as registry:
        assert models.verify_rapidocr_cache(tmp_path / "missing") == []
    registry.assert_not_called()


def test_ambiguous_registry_does_not_choose_first_url(tmp_path: Path) -> None:
    spec = artifact()
    (tmp_path / spec.name).write_bytes(b"incomplete")
    other = models.ModelArtifact(spec.name, "https://example.invalid/other.onnx", "0" * 64)
    with (
        patch.object(models, "_registered_models", return_value={spec.name: [spec, other]}),
        patch.object(models, "urlopen") as get,
        pytest.raises(RuntimeError, match="一意"),
    ):
        models.verify_rapidocr_cache(tmp_path)
    get.assert_not_called()


@pytest.mark.parametrize("suffix", [".onnx", ".pth", ".pdmodel"])
def test_other_registered_model_formats_are_also_repaired(tmp_path: Path, suffix: str) -> None:
    spec = models.ModelArtifact(f"model{suffix}", f"https://example.invalid/model{suffix}", artifact().digest)
    (tmp_path / spec.name).write_bytes(b"incomplete")
    with (
        patch.object(models, "_registered_models", return_value={spec.name: [spec]}),
        patch.object(models, "urlopen", return_value=io.BytesIO(b"synthetic-valid-model")),
    ):
        assert models.verify_rapidocr_cache(tmp_path) == [spec.name]


def hf_cache(root: Path, filename: str, content: bytes, *, git: bool = False) -> Path:
    repo = root / "synthetic--layout-model"
    file = repo / filename
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(content)
    digest = (
        hashlib.sha1(f"blob {len(content)}\0".encode() + content, usedforsecurity=False).hexdigest()
        if git
        else hashlib.sha256(content).hexdigest()
    )
    metadata = repo / ".cache/huggingface/download" / f"{filename}.metadata"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(f"{'a' * 40}\n{digest}\n123.0\n")
    return file


def test_hf_weights_and_git_blob_config_are_verified_offline(tmp_path: Path) -> None:
    hf_cache(tmp_path, "model.safetensors", b"weights")
    hf_cache(tmp_path, "config.json", b'{"model_type":"synthetic"}', git=True)
    with patch.object(models, "_hf_url", return_value="https://example.invalid/model"), patch.object(models, "urlopen") as get:
        assert models.verify_docling_cache(tmp_path) == []
    get.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
def test_hf_corrupt_or_missing_file_is_retrieved_at_recorded_revision(tmp_path: Path, missing: bool) -> None:
    bad = hf_cache(tmp_path, "accurate/weights.safetensors", b"valid")
    good = hf_cache(tmp_path, "config.json", b"{}", git=True)
    before = good.stat().st_mtime_ns
    if missing:
        bad.unlink()
    else:
        bad.write_bytes(b"incomplete")
    with (
        patch.object(models, "_hf_url", return_value="https://example.invalid/pinned") as url,
        patch.object(models, "urlopen", return_value=io.BytesIO(b"valid")),
    ):
        assert models.verify_docling_cache(tmp_path) == ["accurate/weights.safetensors"]
    url.assert_any_call("synthetic/layout-model", "accurate/weights.safetensors", "a" * 40)
    assert bad.read_bytes() == b"valid"
    assert good.stat().st_mtime_ns == before


def test_hf_file_without_digest_is_not_claimed_as_verified(tmp_path: Path) -> None:
    repo = tmp_path / "synthetic--model"
    repo.mkdir()
    (repo / "weights.bin").write_bytes(b"unknown")
    with pytest.raises(RuntimeError, match="公式 hash"):
        models.verify_docling_cache(tmp_path)
