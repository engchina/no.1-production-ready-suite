"""公開版が無ければ組み込みの言語資源を公開し、旧ファイルは読まない。"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from rag_parser_core.language import builtin_pack

from app.clients.oracle import OracleClient
from app.rag import published_language
from app.rag.language_pack_store import LANGUAGE_PACK_STORE


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


async def test_first_use_publishes_builtin_without_reading_old_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SimpleNamespace(
        current=AsyncMock(side_effect=LookupError("言語資源の公開版がまだありません。")),
        ensure_initialized=AsyncMock(),
    )
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    read_old_file = Mock(side_effect=AssertionError("旧ファイルは読み込まない"))
    monkeypatch.setattr(LANGUAGE_PACK_STORE, "overrides", read_old_file)
    assert cast(object, await published_language.initialized_release_store(OracleClient())) is store
    read_old_file.assert_not_called()
    store.ensure_initialized.assert_awaited_once_with(builtin_pack().to_dict())
