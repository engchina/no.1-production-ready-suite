"""言語資源の公開契約と、失敗時に公開版を書き換えないことを確かめる。"""

from copy import deepcopy
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


@pytest.mark.parametrize(
    "section,key",
    [
        ("answer_labels", "insufficient_evidence_summary"),
        ("answer_labels", "empty_search_guidance"),
        ("answer_labels", "abstention_guidance"),
        ("answer_labels", "answer_evidence_budget_limited"),
        ("answer_labels", "crag_refusal_summary"),
        ("answer_labels", "crag_missing_aspect_template"),
        ("answer_labels", "crag_unknown_aspect_template"),
        ("answer_labels", "crag_quote_only_note"),
        ("answer_labels", "crag_definition_aspect_template"),
        ("answer_labels", "guide_acquisition_incomplete_template"),
        ("grounding_cues", "citation_unit_words"),
    ],
)
def test_missing_fields_are_rejected_by_storage_and_runtime(section: str, key: str) -> None:
    from app.rag.language_pack_releases import pack_from_release

    row = release_row()
    original = _release(row)
    effective = deepcopy(original.effective)
    del effective[section][key]
    before = deepcopy(effective)
    with pytest.raises(ValueError, match=key):
        _release({**row, "effective_json": effective})
    with pytest.raises(ValueError, match=key):
        pack_from_release(original.model_copy(update={"effective": effective}))
    assert effective == before


@pytest.mark.parametrize(
    "mutation", ["hash", "ingest", "extra", "normalized", "coerced", "retired"]
)
def test_release_requires_canonical_content_and_current_hashes(mutation: str) -> None:
    from app.rag.language_pack_releases import pack_from_release

    row = release_row()
    original = _release(row)
    effective = deepcopy(original.effective)
    if mutation == "hash":
        row["content_hash"] = "incorrect"
    elif mutation == "ingest":
        row["ingest_hash"] = "incorrect"
    elif mutation == "extra":
        effective["answer_labels"]["unknown_key"] = "合成の余剰項目"
    elif mutation == "normalized":
        effective["answer_labels"]["cause_gap"] += " "
    elif mutation == "coerced":
        effective["answer_labels"]["crag_refusal_summary"] = 3
    else:
        effective["inquiry_cues"] = {"column_words": ["合成"]}
    row["effective_json"] = effective
    with pytest.raises(ValueError):
        _release(row)
    with pytest.raises(ValueError):
        pack_from_release(
            original.model_copy(
                update={
                    "effective": effective,
                    "hash": row["content_hash"],
                    "ingest_hash": row["ingest_hash"],
                }
            )
        )


def test_incomplete_draft_fails_without_writes() -> None:
    from app.clients.language_pack_release_store import _state

    draft = builtin_pack().to_dict()
    del draft["answer_labels"]["crag_refusal_summary"]
    before = deepcopy(draft)
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "draft_revision": 1,
                    "draft_effective_json": draft,
                    "current_release_id": "release-a",
                }
            ],
            [release_row()],
        ]
    )
    with pytest.raises(ValueError, match="crag_refusal_summary"):
        _state(pool.connection)
    assert draft == before
    assert all(call.statement.lstrip().startswith("SELECT") for call in pool.connection.calls)


@pytest.mark.parametrize("restoring", [False, True])
async def test_publish_and_restore_preserve_complete_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    restoring: bool,
) -> None:
    from app.clients import language_pack_release_store as store_module
    from app.clients import oracle as oracle_module
    from app.rag.language_pack_releases import pack_from_release

    old = _release(release_row())
    effective = deepcopy(old.effective)
    effective["answer_labels"]["cause_gap"] = "合成の保存済み文言"
    pack = _validated(effective)
    source = old.model_copy(
        update={
            "id": "release-source",
            "effective": effective,
            "hash": pack.hash,
            "ingest_hash": pack.ingest_hash,
        }
    )
    state = LanguagePackReleaseState(draft_revision=1, draft_effective=effective, current=old)
    inserted: list[LanguagePackRelease] = []
    monkeypatch.setattr(store_module, "_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(store_module, "_get", lambda *_args: source)
    monkeypatch.setattr(
        store_module, "_insert", lambda _connection, release: inserted.append(release)
    )
    monkeypatch.setattr(oracle_module, "_execute", lambda *_args, **_kwargs: None)
    pool = FakeOraclePool()
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    store = LanguagePackReleaseStore(oracle)
    if restoring:
        await store.restore(source.id, expected_revision=1, expected_current_id=old.id)
    else:
        await store.publish(expected_revision=1, expected_current_id=old.id)
    assert len(inserted) == 1
    new = inserted[0]
    assert new.effective == effective
    assert new.previous_id == old.id
    assert new.version == old.version + 1
    assert new.restored_from == (source.id if restoring else None)
    assert pack_from_release(new).hash == pack.hash
    assert old.effective == builtin_pack().to_dict()


@pytest.mark.parametrize("restoring", [False, True])
async def test_publish_and_restore_reject_incomplete_snapshot_before_insert(
    monkeypatch: pytest.MonkeyPatch,
    restoring: bool,
) -> None:
    from app.clients import language_pack_release_store as store_module

    current = _release(release_row())
    effective = deepcopy(current.effective)
    del effective["grounding_cues"]["citation_unit_words"]
    state = LanguagePackReleaseState(draft_revision=1, draft_effective=effective, current=current)
    monkeypatch.setattr(store_module, "_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(
        store_module, "_get", lambda *_args: current.model_copy(update={"effective": effective})
    )
    pool = FakeOraclePool()
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    store = LanguagePackReleaseStore(oracle)
    with pytest.raises(ValueError, match="citation_unit_words"):
        if restoring:
            await store.restore(current.id, expected_revision=1, expected_current_id=current.id)
        else:
            await store.publish(expected_revision=1, expected_current_id=current.id)
    assert pool.connection.calls == []
