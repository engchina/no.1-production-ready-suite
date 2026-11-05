"""既存の公開版があるときは、旧ファイルから実行設定を変えない。"""

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from rag_parser_core.language import builtin_pack

from app.clients.oracle import OracleClient
from app.rag import published_language
from app.rag.language_pack_contract import LanguagePackNotInitializedError
from app.rag.language_pack_store import LANGUAGE_PACK_STORE


@pytest.fixture(autouse=True)
def isolated_legacy_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        published_language,
        "resolve_language_pack_file",
        lambda _settings: tmp_path / "synthetic-language-pack.json",
    )


async def test_existing_publication_does_not_read_changed_or_unreadable_legacy_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SimpleNamespace(
        current=AsyncMock(return_value=object()), ensure_initialized=AsyncMock()
    )
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    legacy = Mock(side_effect=AssertionError("公開後に旧ファイルは読み込まない"))
    monkeypatch.setattr(LANGUAGE_PACK_STORE, "overrides", legacy)
    assert cast(object, await published_language.initialized_release_store(OracleClient())) is store
    legacy.assert_not_called()
    store.ensure_initialized.assert_not_awaited()


@pytest.mark.parametrize("error", [ValueError("broken"), OSError("unreadable")])
async def test_first_bootstrap_uses_builtin_without_reading_legacy_file(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    store = SimpleNamespace(
        current=AsyncMock(side_effect=LanguagePackNotInitializedError()),
        ensure_initialized=AsyncMock(),
    )
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    legacy = Mock(side_effect=error)
    monkeypatch.setattr(LANGUAGE_PACK_STORE, "overrides", legacy)
    assert cast(object, await published_language.initialized_release_store(OracleClient())) is store
    store.ensure_initialized.assert_awaited_once_with(builtin_pack().to_dict())
    legacy.assert_not_called()


async def test_fresh_bootstrap_never_uses_legacy_override_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SimpleNamespace(
        current=AsyncMock(side_effect=LanguagePackNotInitializedError()),
        ensure_initialized=AsyncMock(),
    )
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    legacy = Mock(return_value={"answer_labels": {"cause_gap": "合成の旧ファイル値"}})
    monkeypatch.setattr(LANGUAGE_PACK_STORE, "overrides", legacy)
    await published_language.initialized_release_store(OracleClient())
    legacy.assert_not_called()
    store.ensure_initialized.assert_awaited_once_with(builtin_pack().to_dict())


async def test_invalid_existing_publication_never_bootstraps_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SimpleNamespace(
        current=AsyncMock(side_effect=ValueError("保存済み契約が不正")),
        ensure_initialized=AsyncMock(),
    )
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    with pytest.raises(ValueError):
        await published_language.initialized_release_store(OracleClient())
    store.ensure_initialized.assert_not_awaited()
