"""言語資源の公開契約と、失敗時に公開版を書き換えないことを確かめる。"""

import hashlib
import json
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


_COMPAT_KEYS = (
    "crag_refusal_summary",
    "crag_missing_aspect_template",
    "crag_unknown_aspect_template",
    "crag_quote_only_note",
    "guide_acquisition_incomplete_template",
)


def _stored_hash(effective: dict[str, object]) -> str:
    canonical = json.dumps(
        [
            [section, key, value]
            for section, fields in sorted(effective.items())
            if isinstance(fields, dict)
            for key, value in sorted(fields.items())
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(f"{effective['locale']}\n{canonical}".encode()).hexdigest()[:16]


def legacy_release_row(mask: int = (1 << len(_COMPAT_KEYS)) - 1) -> dict[str, object]:
    """旧形式の項目集合から独立に元の保存 hash を計算する。"""
    row = release_row()
    effective = builtin_pack().to_dict()
    effective["answer_labels"]["gaps_section"] = "合成の保存済み文言"
    effective["answer_labels"]["crag_quote_only_note"] = "合成の保存済み注記"
    for index, key in enumerate(_COMPAT_KEYS):
        if mask & (1 << index):
            del effective["answer_labels"][key]
    row["effective_json"] = effective
    row["content_hash"] = _stored_hash(effective)
    return row


def test_incomplete_release_is_rejected_without_defaulting() -> None:
    row = release_row()
    effective = builtin_pack().to_dict()
    del effective["grounding_cues"]["citation_unit_words"]
    row["effective_json"] = effective
    row["content_hash"] = _stored_hash(effective)
    untouched = deepcopy(row)
    with pytest.raises(ValueError):
        _release(row)
    assert row == untouched


@pytest.mark.parametrize("mask", range(1 << len(_COMPAT_KEYS)))
def test_release_requires_current_fields_without_changing_custom_values(mask: int) -> None:
    from app.rag.language_pack_releases import pack_from_release

    row = legacy_release_row(mask)
    untouched = deepcopy(row)
    if mask:
        with pytest.raises(ValueError):
            _release(row)
    else:
        release = _release(row)
        assert (
            pack_from_release(release).text("answer_labels", "gaps_section") == "合成の保存済み文言"
        )
    assert row == untouched


@pytest.mark.parametrize(
    "mutation", ["hash", "mandatory", "extra", "normalized", "ingest", "coerced"]
)
def test_legacy_release_does_not_weaken_integrity(mutation: str) -> None:
    from app.rag.language_pack_releases import pack_from_release

    row = release_row()
    original = _release(deepcopy(row))
    effective = row["effective_json"]
    assert isinstance(effective, dict)
    if mutation == "hash":
        row["content_hash"] = "incorrect"
    elif mutation == "ingest":
        row["ingest_hash"] = "incorrect"
    elif mutation == "mandatory":
        del effective["answer_labels"]["gaps_section"]
    elif mutation == "extra":
        effective["answer_labels"]["unknown_key"] = "合成の余剰項目"
    elif mutation == "coerced":
        effective["answer_labels"]["crag_refusal_summary"] = 3
    else:
        effective["answer_labels"]["gaps_section"] += " "
    with pytest.raises(ValueError):
        _release(row)
    broken = original.model_copy(
        update={
            "effective": effective,
            "hash": row["content_hash"],
            "ingest_hash": row["ingest_hash"],
        }
    )
    with pytest.raises(ValueError):
        pack_from_release(broken)


@pytest.mark.parametrize("missing_extra", [False, True])
def test_incomplete_draft_read_is_rejected_without_writes(missing_extra: bool) -> None:
    from app.clients.language_pack_release_store import _state

    row = release_row()
    draft = builtin_pack().to_dict()
    del draft["answer_labels"]["crag_refusal_summary"]
    if missing_extra:
        del draft["grounding_cues"]["citation_unit_words"]
    original_draft = deepcopy(draft)
    pool = FakeOraclePool(
        execute_results=[
            [
                {
                    "draft_revision": 1,
                    "draft_effective_json": draft,
                    "current_release_id": "release-a",
                }
            ],
            [row],
        ]
    )
    with pytest.raises(ValueError):
        _state(pool.connection)
    assert draft == original_draft
    assert all(call.statement.lstrip().startswith("SELECT") for call in pool.connection.calls)


async def test_explicit_publish_preserves_complete_custom_draft_without_rewriting_old(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.clients import language_pack_release_store as store_module
    from app.clients import oracle as oracle_module
    from app.rag.language_pack_releases import pack_from_release

    row = release_row()
    unchanged = deepcopy(row)
    old = _release(row)
    draft = deepcopy(old.effective)
    draft["answer_labels"]["gaps_section"] = "合成の保存済み文言"
    state = LanguagePackReleaseState(draft_revision=1, draft_effective=draft, current=old)
    inserted: list[LanguagePackRelease] = []
    monkeypatch.setattr(store_module, "_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(
        store_module, "_insert", lambda _connection, release: inserted.append(release)
    )
    monkeypatch.setattr(oracle_module, "_execute", lambda *_args, **_kwargs: None)
    pool = FakeOraclePool()
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    await LanguagePackReleaseStore(oracle).publish(expected_revision=1, expected_current_id=old.id)
    assert len(inserted) == 1
    new = inserted[0]
    assert new.version == old.version + 1
    assert new.previous_id == old.id
    assert new.hash != old.hash
    assert new.ingest_hash == old.ingest_hash
    assert _validated(new.effective).hash == new.hash
    assert pack_from_release(new).hash == new.hash
    assert new.effective["answer_labels"]["gaps_section"] == "合成の保存済み文言"
    assert row == unchanged
    assert old.effective == row["effective_json"]
    assert old.hash == row["content_hash"]


def retired_inquiry_release_row() -> dict[str, object]:
    from rag_parser_core.language import FIELD_SPECS

    row = release_row()
    data = deepcopy(row["effective_json"])
    assert isinstance(data, dict)
    data["inquiry_cues"] = {
        "document_kind_words": ["合成=synthetic"],
        "file_data_kind": "synthetic",
        "decision_words": ["合成=procedure"],
        **{
            key: ["合成"]
            for key in [
                "operation_trigger_terms",
                "condition_trigger_terms",
                "condition_terms",
                "file_data_terms",
                "visual_terms",
                "external_context_terms",
                "screen_suffixes",
                "screen_name_bracket_chars",
                "route_arrow_chars",
                "interrogative_words",
                "term_joiner_chars",
                "page_ordinal_suffixes",
                "column_words",
            ]
        },
    }

    def digest(ingest: bool) -> str:
        fields = {(spec.section, spec.key) for spec in FIELD_SPECS if not ingest or spec.ingest}
        retired = (
            [
                "document_kind_words",
                "file_data_kind",
                "operation_trigger_terms",
                "condition_trigger_terms",
                "condition_terms",
                "file_data_terms",
            ]
            if ingest
            else list(data["inquiry_cues"])
        )
        fields.update(("inquiry_cues", key) for key in retired)
        payload = [[section, key, data[section][key]] for section, key in sorted(fields)]
        return hashlib.sha256(
            (data["locale"] + "\n" + json.dumps(payload, ensure_ascii=False)).encode()
        ).hexdigest()[:16]

    row["effective_json"] = data
    row["content_hash"] = digest(False)
    row["ingest_hash"] = digest(True)
    return row


def test_retired_inquiry_release_is_rejected_without_rewriting_history() -> None:
    row = retired_inquiry_release_row()
    untouched = deepcopy(row)
    with pytest.raises(ValueError):
        _release(row)
    assert row == untouched


@pytest.mark.parametrize("mutation", ["unknown", "value", "hash", "ingest"])
def test_retired_inquiry_release_keeps_integrity_checks(mutation: str) -> None:
    row = retired_inquiry_release_row()
    effective = row["effective_json"]
    assert isinstance(effective, dict)
    if mutation == "unknown":
        effective["inquiry_cues"]["unknown"] = ["合成"]
    elif mutation == "value":
        effective["inquiry_cues"]["condition_terms"] = ["変更"]
    elif mutation == "hash":
        row["content_hash"] = "invalid"
    else:
        row["ingest_hash"] = "invalid"
    with pytest.raises(ValueError):
        _release(row)


async def test_history_metadata_lists_unsupported_snapshot_without_executing_it() -> None:
    """現在版とは別の旧保存物を一覧で解釈せず、個別取得は拒否する。"""
    old = release_row()
    effective = deepcopy(old["effective_json"])
    assert isinstance(effective, dict)
    del effective["answer_labels"]["crag_refusal_summary"]
    old["effective_json"] = effective
    untouched = deepcopy(old)
    pool = FakeOraclePool(execute_results=[[{"total": 1}], [old]])
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    page = await LanguagePackReleaseStore(oracle).list()
    assert page.total == 1
    assert page.releases[0].id == old["release_id"]
    assert "effective" not in page.releases[0].model_dump()
    assert "effective" not in pool.connection.calls[1].statement.lower()
    assert old == untouched
    pool = FakeOraclePool(execute_results=[[old]])
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    from app.rag.language_pack_contract import LanguagePackSnapshotError

    with pytest.raises(LanguagePackSnapshotError):
        await LanguagePackReleaseStore(oracle).get(str(old["release_id"]))
    assert all(call.statement.startswith("SELECT") for call in pool.connection.calls)


def _nonconforming_state() -> tuple[dict[str, object], dict[str, object]]:
    """契約の切替より前に公開された、現行の項目が欠けた現在版と同じ内容の下書き(#4697)。"""
    row = release_row()
    effective = deepcopy(row["effective_json"])
    assert isinstance(effective, dict)
    del effective["answer_labels"]["applications_section"]
    del effective["grounding_cues"]["citation_unit_words"]
    row["effective_json"] = effective
    state = {
        "draft_revision": 1,
        "draft_effective_json": deepcopy(effective),
        "current_release_id": row["release_id"],
    }
    return state, row


@pytest.mark.parametrize("operation", ["save_draft", "publish", "restore"])
async def test_nonconforming_current_keeps_save_and_publish_blocked(operation: str) -> None:
    """通常の保存・公開・復元は不適合の現在版を拒否し続ける(復旧は別の明示の経路。#4697)。"""
    from app.rag.language_pack_contract import LanguagePackSnapshotError

    state, row = _nonconforming_state()
    pool = FakeOraclePool(execute_results=[[state], [row]])
    oracle = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    store = LanguagePackReleaseStore(oracle)
    with pytest.raises(LanguagePackSnapshotError):
        if operation == "save_draft":
            await store.save_draft(builtin_pack().to_dict(), expected_revision=1)
        elif operation == "publish":
            await store.publish(expected_revision=1, expected_current_id="release-a")
        else:
            await store.restore("release-a", expected_revision=1, expected_current_id="release-a")
    statements = [call.statement.upper() for call in pool.connection.calls]
    assert all("INSERT" not in sql and "UPDATE RAG_" not in sql for sql in statements)
    assert pool.connection.commits == 0
