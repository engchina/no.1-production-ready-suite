"""空の初期化と、壊れた公開ポインタを区別する。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from rag_parser_core.language import builtin_pack

from app.clients.language_pack_release_store import (
    LanguagePackReleaseStore,
    _state,
)
from app.clients.oracle import OracleClient
from app.rag import published_language
from app.rag.language_pack_contract import LanguagePackSnapshotError
from app.schemas.language_pack_release import LanguagePackRelease
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


def invalid_rows(kind: str) -> list[list[dict[str, object]]]:
    if kind == "orphan_release":
        return [[], [{"release_id": "synthetic-orphan"}]]
    state: dict[str, object] = {
        "draft_revision": 7,
        "draft_effective_json": builtin_pack().to_dict(),
        "current_release_id": None,
    }
    if kind == "missing_release":
        state["current_release_id"] = "synthetic-missing"
        return [[state], []]
    return [[state]]


@pytest.mark.parametrize("kind", ["orphan_release", "missing_head", "missing_release"])
def test_invalid_existing_state_is_never_uninitialized(kind: str) -> None:
    pool = FakeOraclePool(execute_results=invalid_rows(kind))
    with pytest.raises(LanguagePackSnapshotError):
        _state(pool.connection)
    assert all(call.statement.lstrip().startswith("SELECT") for call in pool.connection.calls)


@pytest.mark.parametrize("kind", ["orphan_release", "missing_head", "missing_release"])
async def test_direct_initializer_never_writes_over_invalid_existing_state(kind: str) -> None:
    pool = FakeOraclePool(execute_results=invalid_rows(kind))
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    with pytest.raises(LanguagePackSnapshotError):
        await LanguagePackReleaseStore(oracle).ensure_initialized(builtin_pack().to_dict())
    assert all(call.statement.lstrip().startswith("SELECT") for call in pool.connection.calls)


async def test_empty_database_bootstraps_from_builtin_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakeOraclePool(execute_results=[[], []])

    async def current() -> LanguagePackRelease:
        return _state(pool.connection).current

    store = SimpleNamespace(current=current, ensure_initialized=AsyncMock())
    monkeypatch.setattr(published_language, "LanguagePackReleaseStore", lambda _oracle: store)
    await published_language.initialized_release_store(OracleClient())
    store.ensure_initialized.assert_awaited_once_with(builtin_pack().to_dict())
    assert all(call.statement.lstrip().startswith("SELECT") for call in pool.connection.calls)


def test_completed_concurrent_bootstrap_is_read_as_current_state() -> None:
    from tests.test_language_pack_release_store import release_row

    row = release_row()
    pool = FakeOraclePool(
        execute_results=[
            [],
            [{"release_id": "release-a"}],
            [
                {
                    "draft_revision": 1,
                    "draft_effective_json": row["effective_json"],
                    "current_release_id": "release-a",
                }
            ],
            [row],
        ]
    )
    assert _state(pool.connection).current.id == "release-a"
    assert all(call.statement.lstrip().startswith("SELECT") for call in pool.connection.calls)
