"""合成の独立表で公開 CAS・復元・同時初期化を実 Oracle で検証する。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from rag_parser_core.language import builtin_pack

from app.clients import language_pack_release_store as module
from app.clients.oracle import (
    OracleClient,
    OracleConnectionProtocol,
    _execute,
    _fetch_one,
    _json_bind,
    _json_input_sizes,
    _json_loads,
)


@pytest.mark.usefixtures("oracle_db")
async def test_releases_are_atomic_immutable_and_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    suffix = uuid4().hex[:10]
    release_table = f"rag_test_lang_releases_{suffix}"
    state_table = f"rag_test_lang_state_{suffix}"
    monkeypatch.setattr(module, "RELEASE_TABLE", release_table)
    monkeypatch.setattr(module, "STATE_TABLE", state_table)
    sql = module.language_pack_release_statements().replace("rag_lang_", f"rag_test_{suffix}_")
    client = OracleClient()

    def create(connection: OracleConnectionProtocol) -> None:
        for statement in sql.split(";"):
            if statement.strip():
                _execute(connection, statement, {})

    await client._run_transaction(create)
    store = module.LanguagePackReleaseStore(client)
    initial = builtin_pack().to_dict()
    try:
        initialized = await asyncio.gather(
            store.ensure_initialized(initial),
            store.ensure_initialized(initial),
            return_exceptions=True,
        )
        # 片方が失敗しても他方の DB スレッドが終わるまで待ち、cleanup と競合させない。
        for outcome in initialized:
            if isinstance(outcome, BaseException):
                raise outcome
        first, simultaneous = initialized
        assert not isinstance(first, BaseException) and not isinstance(simultaneous, BaseException)
        assert first.current == simultaneous.current
        assert (await store.list()).total == 1
        changed = deepcopy(initial)
        changed["search_terms"]["stopwords"].append("合成語")
        draft = await store.save_draft(changed, expected_revision=first.draft_revision)
        assert draft.current == first.current
        assert draft.draft_revision == first.draft_revision + 1
        assert (await store.current()).effective == initial
        results = await asyncio.gather(
            store.publish(
                expected_revision=draft.draft_revision, expected_current_id=first.current.id
            ),
            store.publish(
                expected_revision=draft.draft_revision, expected_current_id=first.current.id
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, module.LanguagePackReleaseConflict) for item in results) == 1
        current = await store.state()
        assert current.current.version == 2
        assert current.current.effective == changed
        assert current.current.ingest_hash == first.current.ingest_hash
        assert current.current.hash != first.current.hash
        assert await store.get(first.current.id) == first.current
        with pytest.raises(module.LanguagePackReleaseConflict):
            await store.restore(
                first.current.id,
                expected_revision=first.draft_revision,
                expected_current_id=first.current.id,
            )
        restored = await store.restore(
            first.current.id,
            expected_revision=current.draft_revision,
            expected_current_id=current.current.id,
            published_by="synthetic-actor",
            change_note="復元テスト",
        )
        assert restored.current.version == 3
        assert restored.current.previous_id == current.current.id
        assert restored.current.restored_from == first.current.id
        assert restored.current.effective == initial
        assert restored.current.published_by == "synthetic-actor"
        assert restored.current.change_note == "復元テスト"
        assert restored.current.id != first.current.id
        assert restored.draft_effective == initial
        assert (await store.ensure_initialized(changed)).current == restored.current
        assert await store.get("missing-release") is None
        with pytest.raises(LookupError):
            await store.restore(
                "missing-release",
                expected_revision=restored.draft_revision,
                expected_current_id=restored.current.id,
            )
        assert (await store.state()) == restored
        for _ in range(6):
            restored = await store.publish(
                expected_revision=restored.draft_revision, expected_current_id=restored.current.id
            )
        versions = await store.list(limit=3, offset=3)
        assert versions.total == 9
        assert [item.version for item in versions.releases] == [6, 5, 4]
        assert await store.get(first.current.id) == first.current
    finally:

        def cleanup(connection: OracleConnectionProtocol) -> None:
            _execute(connection, f"DROP TABLE {state_table} PURGE", {})
            _execute(connection, f"DROP TABLE {release_table} PURGE", {})

        await client._run_transaction(cleanup)


@pytest.mark.usefixtures("oracle_db")
async def test_retired_fields_migration_restores_saved_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全項目のスナップショットから廃止した項目を除き、hash を直して読める状態に戻す(#4666)。"""
    from app.rag import language_pack_retired_fields as retired
    from app.rag.language_pack_contract import LanguagePackSnapshotError

    suffix = uuid4().hex[:10]
    release_table = f"rag_test_lang_releases_{suffix}"
    state_table = f"rag_test_lang_state_{suffix}"
    profile_table = f"rag_test_lang_profiles_{suffix}"
    monkeypatch.setattr(module, "RELEASE_TABLE", release_table)
    monkeypatch.setattr(module, "STATE_TABLE", state_table)
    monkeypatch.setattr(retired, "PROFILE_TABLE", profile_table)
    sql = module.language_pack_release_statements().replace("rag_lang_", f"rag_test_{suffix}_")
    client = OracleClient()

    def create(connection: OracleConnectionProtocol) -> None:
        for statement in sql.split(";"):
            if statement.strip():
                _execute(connection, statement, {})
        _execute(
            connection,
            f"CREATE TABLE {profile_table} (search_answer_profile_id VARCHAR2(64) PRIMARY KEY, "
            "profile_config JSON)",
            {},
        )

    await client._run_transaction(create)
    store = module.LanguagePackReleaseStore(client)
    initial = builtin_pack().to_dict()
    try:
        before = await store.ensure_initialized(initial)
        old = deepcopy(initial)
        old["question_cues"].update(
            {key: ["合成語"] for key in retired.RETIRED_QUESTION_CUE_FIELDS}
        )

        def seed(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                f"UPDATE {release_table} SET effective_json=:effective, content_hash='stale'",
                {"effective": _json_bind(old)},
                input_sizes=_json_input_sizes("effective"),
            )
            _execute(
                connection,
                f"UPDATE {state_table} SET draft_effective_json=:effective",
                {"effective": _json_bind(old)},
                input_sizes=_json_input_sizes("effective"),
            )
            _execute(
                connection,
                f"INSERT INTO {profile_table} VALUES ('p', :config)",
                {
                    "config": _json_bind(
                        {"language_pack": {"question_cues": {"means_cues": ["合成語"]}}}
                    )
                },
                input_sizes=_json_input_sizes("config"),
            )

        await client._run_transaction(seed)
        with pytest.raises(LanguagePackSnapshotError):
            await store.state()

        for _ in range(2):
            await client._run_transaction(retired.remove_retired_language_pack_fields)
            state = await store.state()
            assert state.current.effective == initial
            assert state.current.hash == before.current.hash
            assert state.current.ingest_hash == before.current.ingest_hash
            assert state.draft_effective == initial
            assert state.draft_revision == before.draft_revision

        def profile(connection: OracleConnectionProtocol) -> dict[str, object]:
            row = _fetch_one(connection, f"SELECT profile_config FROM {profile_table}", {})
            assert row is not None
            return _json_loads(row["profile_config"])

        assert await client._run_transaction(profile) == {"language_pack": {}}
    finally:

        def drop(connection: OracleConnectionProtocol) -> None:
            for table in (state_table, release_table, profile_table):
                _execute(connection, f"DROP TABLE {table} CASCADE CONSTRAINTS PURGE", {})

        await client._run_transaction(drop)


@pytest.mark.usefixtures("oracle_db")
async def test_repair_issues_complete_release_from_nonconforming_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不適合の現在版から、旧版を変えずに完全な新しい版へ切り替える(#4697)。"""
    from app.rag import language_pack_repair as repair
    from app.rag.language_pack_contract import LanguagePackSnapshotError

    suffix = uuid4().hex[:10]
    release_table = f"rag_test_lang_releases_{suffix}"
    state_table = f"rag_test_lang_state_{suffix}"
    jobs_table = f"rag_test_lang_jobs_{suffix}"
    monkeypatch.setattr(module, "RELEASE_TABLE", release_table)
    monkeypatch.setattr(module, "STATE_TABLE", state_table)
    monkeypatch.setattr(repair, "INGESTION_JOBS_TABLE", jobs_table)
    sql = module.language_pack_release_statements().replace("rag_lang_", f"rag_test_{suffix}_")
    client = OracleClient()

    def create(connection: OracleConnectionProtocol) -> None:
        for statement in sql.split(";"):
            if statement.strip():
                _execute(connection, statement, {})
        _execute(
            connection,
            f"CREATE TABLE {jobs_table} (job_id VARCHAR2(64) PRIMARY KEY, status VARCHAR2(32))",
            {},
        )

    await client._run_transaction(create)
    store = module.LanguagePackReleaseStore(client)
    initial = builtin_pack().to_dict()
    try:
        before = await store.ensure_initialized(initial)
        # 取込に使わない 2 項目を欠いた形にする(ingest_hash は変わらない)。
        old = deepcopy(initial)
        del old["answer_labels"]["applications_section"]
        del old["grounding_cues"]["citation_unit_words"]

        def seed(connection: OracleConnectionProtocol) -> None:
            _execute(
                connection,
                f"UPDATE {release_table} SET effective_json=:effective, content_hash='stale'",
                {"effective": _json_bind(old)},
                input_sizes=_json_input_sizes("effective"),
            )
            _execute(
                connection,
                f"UPDATE {state_table} SET draft_effective_json=:effective",
                {"effective": _json_bind(old)},
                input_sizes=_json_input_sizes("effective"),
            )

        await client._run_transaction(seed)

        def stored(connection: OracleConnectionProtocol) -> tuple[object, object, object]:
            row = _fetch_one(
                connection,
                f"SELECT effective_json, content_hash, ingest_hash FROM {release_table} "
                "WHERE release_id=:id",
                {"id": before.current.id},
            )
            assert row is not None
            return _json_loads(row["effective_json"]), row["content_hash"], row["ingest_hash"]

        untouched = await client._run_transaction(stored)
        with pytest.raises(LanguagePackSnapshotError):
            await store.state()
        with pytest.raises(LanguagePackSnapshotError):
            await store.save_draft(initial, expected_revision=before.draft_revision)

        plan = await repair.inspect_language_pack_repair(client)
        assert plan.blockers == []
        assert plan.current_release_id == before.current.id
        assert plan.missing_fields == [
            "answer_labels.applications_section",
            "grounding_cues.citation_unit_words",
        ]
        assert plan.ingest_hash_unchanged
        assert await client._run_transaction(stored) == untouched

        async def apply_repair() -> repair.LanguagePackRepairResult:
            return await repair.apply_language_pack_repair(
                client,
                expected_current_id=before.current.id,
                expected_draft_revision=plan.draft_revision,
            )

        async def set_job(status: str) -> None:
            def operation(connection: OracleConnectionProtocol) -> None:
                _execute(connection, f"DELETE FROM {jobs_table}", {})
                _execute(
                    connection,
                    f"INSERT INTO {jobs_table} VALUES ('job-a', :status)",
                    {"status": status},
                )

            await client._run_transaction(operation)

        await set_job("RUNNING")
        with pytest.raises(repair.LanguagePackRepairBlocked) as blocked:
            await apply_repair()
        assert blocked.value.plan.blockers == [repair.ACTIVE_INGESTION_JOBS]
        await set_job("SUCCEEDED")

        with pytest.raises(repair.LanguagePackRepairBlocked) as blocked:
            await repair.apply_language_pack_repair(
                client,
                expected_current_id="release-other",
                expected_draft_revision=plan.draft_revision,
            )
        assert blocked.value.plan.blockers == [repair.EXPECTED_CURRENT_MISMATCH]

        # 読み戻しの失敗で、発行した版とポインタの変更がすべて取り消される。
        def fail_readback(*_args: object, **_kwargs: object) -> None:
            raise LanguagePackSnapshotError("合成の読み戻しの失敗")

        with monkeypatch.context() as patch:
            patch.setattr(module, "_state", fail_readback)
            with pytest.raises(LanguagePackSnapshotError):
                await apply_repair()
        assert (await store.list()).total == 1
        assert await client._run_transaction(stored) == untouched

        # 同じ期待値で同時に適用しても 1 回だけ発行される(2 回目は状態行の lock の後に止まる)。
        outcomes = await asyncio.gather(apply_repair(), apply_repair(), return_exceptions=True)
        assert sum(isinstance(item, repair.LanguagePackRepairBlocked) for item in outcomes) == 1
        applied = next(
            item for item in outcomes if isinstance(item, repair.LanguagePackRepairResult)
        )
        assert applied.version == before.current.version + 1
        state = await store.state()
        assert state.current.id == applied.release_id
        assert state.current.previous_id == before.current.id
        assert state.current.effective == initial
        assert state.current.ingest_hash == before.current.ingest_hash
        assert state.draft_effective == initial
        assert state.draft_revision == plan.draft_revision + 1
        assert (await store.current()).id == applied.release_id
        assert (await store.list()).total == 2
        assert await client._run_transaction(stored) == untouched
        # 発行の後は通常の保存が使え、この経路はやることがないので止まる。
        changed = deepcopy(initial)
        changed["search_terms"]["stopwords"].append("合成語")
        saved = await store.save_draft(changed, expected_revision=state.draft_revision)
        assert saved.draft_revision == state.draft_revision + 1
        again = await repair.inspect_language_pack_repair(client)
        assert again.blockers == [repair.CURRENT_ALREADY_CONFORMS]
    finally:

        def drop(connection: OracleConnectionProtocol) -> None:
            for table in (state_table, release_table, jobs_table):
                _execute(connection, f"DROP TABLE {table} CASCADE CONSTRAINTS PURGE", {})

        await client._run_transaction(drop)
