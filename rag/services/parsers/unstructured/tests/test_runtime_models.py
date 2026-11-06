"""実際の Unstructured で、モデルを配備で固定して解析の要求の時には取得しないことを確かめる。

版を上げてモデルの取得の仕組み・repo・ファイルが変わったら失敗する
（rag_parser_core.unstructured_models を直す）。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import spacy
import unstructured.nlp.tokenize as tokenize
from fastapi.testclient import TestClient
from rag_parser_core import unstructured_models
from unstructured_inference.models import base, tables, yolox

SERVICE_DIR = Path(__file__).resolve().parents[1]


def test_spacy_model_is_installed_from_lock_at_unstructured_pinned_version_and_hash() -> None:
    lock = tomllib.loads((SERVICE_DIR / "uv.lock").read_text(encoding="utf-8"))
    (package,) = [p for p in lock["package"] if p["name"] == "en-core-web-sm"]
    assert package["version"] == tokenize._SPACY_MODEL_VERSION
    (wheel,) = package["wheels"]
    assert wheel["url"] == tokenize._SPACY_MODEL_URL
    assert wheel["hash"] == f"sha256:{tokenize._SPACY_MODEL_SHA256}"
    # 配備の uv sync --locked が入れた venv のファイルが wheel の RECORD と一致する。
    unstructured_models.verify_spacy_model()


def test_pinned_hub_models_are_the_ones_unstructured_inference_reads() -> None:
    by_repo = {model.repo: model for model in unstructured_models.HUB_MODELS}
    assert base.DEFAULT_MODEL == "yolox"
    layout = yolox.MODEL_TYPES["yolox"]._raw_dict["model_path"]
    repo, filename = layout.info[0]
    assert filename in {file.filename for file in by_repo[repo].files}
    assert tables.DEFAULT_MODEL in by_repo
    for model in unstructured_models.HUB_MODELS:
        assert re.fullmatch(r"[0-9a-f]{40}", model.revision)


def test_service_never_downloads_spacy_model_at_request_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main  # noqa: F401 - import で実行時の取得を禁じる

    def missing(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic: model is not installed")

    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("解析の要求の時に spaCy のモデルを取得しようとした")

    monkeypatch.setattr(spacy, "load", missing)
    monkeypatch.setattr(tokenize, "_download_with_timeout", no_network)
    tokenize._get_nlp.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="解析の要求の時にはモデルを取得しません"):
            tokenize._get_nlp()
    finally:
        tokenize._get_nlp.cache_clear()


def test_health_is_degraded_until_models_are_deployed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.main import app

    monkeypatch.setattr(unstructured_models, "_hub_cache_dir", lambda: tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "degraded"
    monkeypatch.setattr(unstructured_models, "_check_snapshot", lambda *_args: None)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
