"""回答フローの業務 profile(domain_profile.json)の読み先を固定する(#569)。

回答フローが使う `current_profile()` は、rag_engine の設定(`build_engine_settings`)ではなく、
process の環境変数 `RAG_ENGINE_DOMAIN_PROFILE_FILE` を直接読み、未指定なら作業ディレクトリの
`domain_profile.json` を読む。`rag/docs/rag-engine.md` と `backend/.env.example` の説明と対応する。
"""

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from rag_engine.profiles import load_profile
from rag_engine.resources.runtime import current_profile

from app.config import Settings
from app.rag.answer_engine import build_engine_settings


@pytest.fixture(autouse=True)
def _fresh_profile_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """profile は process 内で cache されるため、テストの前後で捨てる。"""
    monkeypatch.delenv("RAG_ENGINE_DOMAIN_PROFILE_FILE", raising=False)
    load_profile.cache_clear()
    yield
    load_profile.cache_clear()


def _write_profile(path: Path, large_category: str) -> Path:
    path.write_text(
        json.dumps({"categories": {large_category: ["中分類"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_profile_without_env_reads_domain_profile_in_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """環境変数が未指定なら、作業ディレクトリの domain_profile.json を拾う。"""
    _write_profile(tmp_path / "domain_profile.json", "作業ディレクトリの大分類")
    monkeypatch.chdir(tmp_path)

    assert current_profile().large_categories == ("作業ディレクトリの大分類",)


def test_profile_env_path_wins_over_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """環境変数で指定したファイルを読み、作業ディレクトリのファイルは読まない。"""
    _write_profile(tmp_path / "domain_profile.json", "作業ディレクトリの大分類")
    explicit = _write_profile(tmp_path / "explicit.json", "指定した大分類")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAG_ENGINE_DOMAIN_PROFILE_FILE", str(explicit))

    assert current_profile().large_categories == ("指定した大分類",)


def test_build_engine_settings_does_not_depend_on_profile_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rag_engine の設定は profile の読み先を持たない(環境変数の有無で変わらない)。"""
    settings = Settings()
    without_env = build_engine_settings(settings, output_dir=tmp_path)
    monkeypatch.setenv("RAG_ENGINE_DOMAIN_PROFILE_FILE", str(tmp_path / "explicit.json"))
    with_env = build_engine_settings(settings, output_dir=tmp_path)

    assert dataclasses.asdict(with_env) == dataclasses.asdict(without_env)
