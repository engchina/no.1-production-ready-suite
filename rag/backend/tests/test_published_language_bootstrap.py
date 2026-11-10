"""言語資源の公開版の初期化（未初期化の DB だけを組み込みの完全な言語資源で初期化する）。"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from rag_parser_core.language import builtin_pack

from app.clients.oracle import OracleClient
from app.rag import published_language
from app.rag.language_pack_contract import LanguagePackNotInitializedError


async def test_existing_publication_is_not_bootstrapped_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SimpleNamespace(
        current=AsyncMock(return_value=object()), ensure_initialized=AsyncMock()
    )
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    assert cast(object, await published_language.initialized_release_store(OracleClient())) is store
    store.ensure_initialized.assert_not_awaited()


async def test_first_bootstrap_uses_builtin_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SimpleNamespace(
        current=AsyncMock(side_effect=LanguagePackNotInitializedError()),
        ensure_initialized=AsyncMock(),
    )
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    assert cast(object, await published_language.initialized_release_store(OracleClient())) is store
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
