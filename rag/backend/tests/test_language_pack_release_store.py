"""言語資源の公開契約と、失敗時に公開版を書き換えないことを確かめる。"""

from datetime import UTC, datetime

import pytest
from rag_parser_core.language import builtin_pack

from app.clients.language_pack_release_store import (
    LanguagePackReleaseConflict,
    LanguagePackReleaseStore,
    _release,
    _validated,
    language_pack_release_statements,
)
from app.clients.oracle import OracleClient
from app.schemas.language_pack_release import LanguagePackRelease, LanguagePackReleaseState
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


def release_row() -> dict[str, object]:
    pack = builtin_pack()
    return {
        "release_id": "release-a",
        "version": 1,
        "effective_json": pack.to_dict(),
        "content_hash": pack.hash,
        "ingest_hash": pack.ingest_hash,
        "published_at": datetime.now(UTC),
    }


def test_snapshot_requires_every_field_and_checks_hash() -> None:
    row = release_row()
    assert _release(row).effective == builtin_pack().to_dict()
    with pytest.raises(ValueError):
        _validated({"locale": "ja"})
    with pytest.raises(ValueError, match="照合"):
        _release({**row, "content_hash": "0" * 64})


def test_hash_columns_preserve_digest_without_fixed_width_padding() -> None:
    sql = language_pack_release_statements()
    assert "content_hash VARCHAR2(64) NOT NULL" in sql
    assert "ingest_hash VARCHAR2(64) NOT NULL" in sql


@pytest.mark.parametrize("revision,current", [(2, None), (1, "release-b"), (0, "release-a")])
def test_draft_and_head_compare_and_swap(revision: int, current: str | None) -> None:
    state = LanguagePackReleaseState(
        draft_revision=1,
        draft_effective=builtin_pack().to_dict(),
        current=LanguagePackRelease.model_validate(
            {
                "id": "release-a",
                "version": 1,
                "effective": builtin_pack().to_dict(),
                "hash": builtin_pack().hash,
                "ingest_hash": builtin_pack().ingest_hash,
                "published_at": datetime.now(UTC),
            }
        ),
    )
    with pytest.raises(LanguagePackReleaseConflict):
        LanguagePackReleaseStore._check(state, revision, current)


async def test_stale_save_rolls_back_without_insert_or_update() -> None:
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "draft_revision": 3,
                    "draft_effective_json": builtin_pack().to_dict(),
                    "current_release_id": "release-a",
                }
            ],
            [release_row()],
        ]
    )
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    with pytest.raises(LanguagePackReleaseConflict):
        await LanguagePackReleaseStore(client).save_draft(
            builtin_pack().to_dict(), expected_revision=2
        )
    statements = [call.statement.upper() for call in pool.connection.calls]
    assert "FOR UPDATE" in statements[0]
    assert all("INSERT" not in sql and "UPDATE RAG_" not in sql for sql in statements)


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (25, -1)])
async def test_invalid_paging_does_not_connect(limit: int, offset: int) -> None:
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    with pytest.raises(ValueError):
        await LanguagePackReleaseStore(client).list(limit=limit, offset=offset)
    assert pool.acquire_calls == 0
