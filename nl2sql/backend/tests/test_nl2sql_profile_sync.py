from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.features.nl2sql.models import (
    AssetRefreshData,
    Nl2SqlEngine,
    Nl2SqlProfile,
    ProfileSelectAiConfig,
    ProfileSyncJobRequest,
    ProfileSyncJobStatus,
    SelectAiDbProfileMutationData,
)
from app.features.nl2sql.ontology_store import InMemoryOntologyStore
from app.features.nl2sql.oracle_adapter import (
    OracleNl2SqlAdapter,
    SelectAiCredentialMissingError,
)
from app.features.nl2sql.profile_sync import ProfileSyncService
from app.features.nl2sql.service import Nl2SqlService, SelectAiDbProfileListRefreshSync
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.settings import Settings


class _FakeProfileService:
    def __init__(self) -> None:
        self.profile = Nl2SqlProfile(id="profile-1", name="請求分析", etag="etag-1")
        self.oracle_calls = 0
        self.oracle_original_names: list[str] = []
        self.agent_calls = 0
        self.fail_oracle = False

    def get_profile(self, profile_id: str) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise ValueError("指定された profile が見つかりません。")
        return self.profile.model_copy(deep=True)

    def upsert_profile_select_ai_profile(
        self,
        profile_id: str,
        request: object,
    ) -> SelectAiDbProfileMutationData:
        assert profile_id == self.profile.id
        self.oracle_calls += 1
        self.oracle_original_names.append(str(getattr(request, "original_name", "")))
        if self.fail_oracle:
            raise TimeoutError("Oracle round-trip timeout")
        return SelectAiDbProfileMutationData(
            executed=True,
            status="updated",
            profile_name="INVOICE_PROFILE",
        )

    def clear_profile_select_ai_previous_name(
        self,
        profile_id: str,
        *,
        expected_etag: str | None = None,
    ) -> Nl2SqlProfile:
        assert profile_id == self.profile.id
        assert expected_etag in {None, self.profile.etag}
        self.profile = self.profile.model_copy(
            update={
                "etag": f"{self.profile.etag}-cleared",
                "select_ai_config": self.profile.select_ai_config.model_copy(
                    update={"previous_profile_name": ""}
                ),
            }
        )
        return self.profile.model_copy(deep=True)

    def refresh_select_ai_agent_assets(
        self,
        profile_id: str,
        *,
        profile_already_synced: bool = False,
    ) -> AssetRefreshData:
        assert profile_id == self.profile.id
        assert profile_already_synced is True
        self.agent_calls += 1
        return AssetRefreshData(
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            refreshed=True,
            status="ready",
        )


class _ProfileSyncOracleAdapter:
    def __init__(self) -> None:
        self.active_profiles: set[str] = set()
        self.original_names: list[str] = []
        self.object_lists: dict[str, list[dict[str, str]]] = {}

    def upsert_select_ai_profile_low_level(
        self,
        *,
        profile_name: str,
        attributes: dict[str, object],
        description: str,
        original_name: str,
    ) -> dict[str, object]:
        del description
        self.original_names.append(original_name)
        if original_name:
            self.active_profiles.discard(original_name)
        self.active_profiles.discard(profile_name)
        self.active_profiles.add(profile_name)
        raw_object_list = attributes.get("object_list", [])
        object_list = raw_object_list if isinstance(raw_object_list, list) else []
        self.object_lists[profile_name] = [
            dict(item) for item in object_list if isinstance(item, dict)
        ]
        return {"runtime": "oracle"}

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, object]:
        return {
            "name": profile_name,
            "object_list": self.object_lists.get(profile_name, []),
        }


class _OracleRuntimeProfileService(Nl2SqlService):
    def __init__(self, adapter: _ProfileSyncOracleAdapter) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter  # type: ignore[assignment]  # noqa: SLF001

    def _use_oracle_runtime(self) -> bool:
        return True

    def _submit_select_ai_db_profile_list_refresh_after_mutation(
        self,
        **_kwargs: object,
    ) -> SelectAiDbProfileListRefreshSync:
        return SelectAiDbProfileListRefreshSync()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        nl2sql_ontology_worker_mode="external",
        nl2sql_profile_sync_job_timeout_seconds=300.0,
    )


def test_profile_sync_is_idempotent_and_agent_reuses_oracle_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    request = ProfileSyncJobRequest(
        confirmation="ADMIN_EXECUTE",
        rebuild_agent_assets=True,
    )

    first = sync.start("profile-1", request, idempotency_key="same-request")
    duplicate = sync.start("profile-1", request, idempotency_key="same-request")
    assert duplicate.job_id == first.job_id

    completed = sync.run_persisted(first.job_id)
    assert completed.status == ProfileSyncJobStatus.SUCCEEDED
    assert service.oracle_calls == 1
    assert service.agent_calls == 1


def test_concurrent_profile_sync_submissions_share_one_persisted_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    request = ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE")

    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs = list(
            executor.map(
                lambda _index: sync.start(
                    "profile-1",
                    request,
                    idempotency_key="concurrent-request",
                ),
                range(16),
            )
        )

    assert len({job.job_id for job in jobs}) == 1
    assert len(store.list_documents("jobs", {"profile_id": "profile-1"})) == 1


def test_profile_sync_failure_is_persisted_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    service.fail_oracle = True
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    request = ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE")

    started = sync.start("profile-1", request, idempotency_key="will-fail")
    failed = sync.run_persisted(started.job_id)
    assert failed.status == ProfileSyncJobStatus.FAILED
    assert "Oracle round-trip timeout" in failed.error_message_ja

    service.fail_oracle = False
    retried = sync.retry(failed.job_id)
    completed = sync.run_persisted(retried.job_id)
    assert completed.status == ProfileSyncJobStatus.SUCCEEDED
    assert completed.retry_of_job_id == failed.job_id
    assert service.oracle_calls == 2


def test_profile_sync_passes_original_name_after_profile_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    service.profile = service.profile.model_copy(
        update={
            "name": "INVOICE_PROFILE_V2",
            "select_ai_config": service.profile.select_ai_config.model_copy(
                update={
                    "profile_name": "INVOICE_PROFILE_V2",
                    "previous_profile_name": "INVOICE_PROFILE",
                }
            ),
        }
    )
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]

    started = sync.start(
        "profile-1",
        ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE"),
        idempotency_key="renamed-profile",
    )
    completed = sync.run_persisted(started.job_id)

    assert started.original_name == "INVOICE_PROFILE"
    assert completed.status == ProfileSyncJobStatus.SUCCEEDED
    assert service.oracle_original_names == ["INVOICE_PROFILE"]


def test_profile_sync_clears_previous_name_after_each_successful_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    adapter = _ProfileSyncOracleAdapter()
    adapter.active_profiles.add("SALES_PROFILE")
    service = _OracleRuntimeProfileService(adapter)
    service.create_profile(
        Nl2SqlProfile(
            id="profile-1",
            name="SALES_PROFILE",
            allowed_tables=["APP.INVOICES"],
            select_ai_config=ProfileSelectAiConfig(profile_name="SALES_PROFILE"),
        )
    )
    store = InMemoryOntologyStore()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)

    service.update_profile(
        "profile-1",
        lambda profile: profile.model_copy(
            update={
                "name": "SALES_V2",
                "select_ai_config": profile.select_ai_config.model_copy(
                    update={"profile_name": "SALES_V2"}
                ),
            }
        ),
    )
    first = sync.start(
        "profile-1",
        ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE"),
        idempotency_key="sales-v2",
    )
    first_completed = sync.run_persisted(first.job_id)

    assert first_completed.status == ProfileSyncJobStatus.SUCCEEDED
    assert service.get_profile("profile-1").select_ai_config.previous_profile_name == ""

    service.update_profile(
        "profile-1",
        lambda profile: profile.model_copy(
            update={
                "name": "SALES_V3",
                "select_ai_config": profile.select_ai_config.model_copy(
                    update={"profile_name": "SALES_V3"}
                ),
            }
        ),
    )
    assert service.get_profile("profile-1").select_ai_config.previous_profile_name == "SALES_V2"
    second = sync.start(
        "profile-1",
        ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE"),
        idempotency_key="sales-v3",
    )
    second_completed = sync.run_persisted(second.job_id)

    assert second_completed.status == ProfileSyncJobStatus.SUCCEEDED
    assert adapter.original_names == ["SALES_PROFILE", "SALES_V2"]
    assert adapter.active_profiles == {"SALES_V3"}


def test_profile_sync_credential_missing_uses_recoverable_code_without_oracle_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingCredentialService(_FakeProfileService):
        def upsert_profile_select_ai_profile(
            self,
            profile_id: str,
            _request: object,
        ) -> SelectAiDbProfileMutationData:
            assert profile_id == self.profile.id
            self.oracle_calls += 1
            error = SelectAiCredentialMissingError("OCI_CRED", "ADMIN")
            error.__cause__ = RuntimeError("ORA-06512: at line 2291 private stack")
            raise error

    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = MissingCredentialService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    started = sync.start(
        "profile-1",
        ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE"),
        idempotency_key="missing-credential",
    )

    failed = sync.run_persisted(started.job_id)

    assert failed.status == ProfileSyncJobStatus.FAILED
    assert failed.error_code == "SELECT_AI_CREDENTIAL_MISSING"
    assert "OCI_CRED" in failed.error_message_ja
    assert "データベース設定" in failed.error_message_ja
    assert "ORA-" not in failed.error_message_ja
    retried = sync.retry(failed.job_id)
    assert retried.retry_of_job_id == failed.job_id


def test_profile_sync_can_be_cancelled_before_worker_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.features.nl2sql.profile_sync.get_settings", _settings)
    store = InMemoryOntologyStore()
    service = _FakeProfileService()
    sync = ProfileSyncService(service=service, store_provider=lambda: store)  # type: ignore[arg-type]
    started = sync.start(
        "profile-1",
        ProfileSyncJobRequest(confirmation="ADMIN_EXECUTE"),
        idempotency_key="cancel-me",
    )

    assert sync.cancel_for_profile("profile-1") == 1
    cancelled = sync.run_persisted(started.job_id)
    assert cancelled.status == ProfileSyncJobStatus.CANCELLED
    assert service.oracle_calls == 0


def test_oracle_connection_applies_round_trip_timeout_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Connection:
        call_timeout = 0
        closed = False
        rollbacks = 0

        def close(self) -> None:
            self.closed = True

        def rollback(self) -> None:
            self.rollbacks += 1

    connection = Connection()

    class Driver:
        @staticmethod
        def connect(**_kwargs: object) -> Connection:
            return connection

    adapter = OracleNl2SqlAdapter(
        Settings(
            oracle_user="APP",
            oracle_password="password",
            oracle_dsn="localhost/FREEPDB1",
            # CI には Wallet が無いため wallet 不要モードで接続 kwargs を組み立てる。
            oracle_connection_security="walletless_tls",
            nl2sql_oracle_call_timeout_seconds=12.5,
        )
    )
    monkeypatch.setattr(adapter, "_load_oracledb", lambda: Driver())
    monkeypatch.setattr(adapter, "_init_client", lambda _driver: None)

    with pytest.raises(TimeoutError, match="round-trip"), adapter.connection() as opened:
        assert opened is connection
        assert connection.call_timeout == 12_500
        raise TimeoutError("round-trip")

    assert connection.closed is True
    assert connection.rollbacks == 1


def test_drop_profile_compatibility_signatures_stop_after_first_success() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, _sql: str, _params: object) -> None:
            self.calls += 1

    cursor = Cursor()
    adapter = OracleNl2SqlAdapter(Settings())

    adapter._drop_cloud_ai_profile_best_effort(cursor, "INVOICE_PROFILE")  # noqa: SLF001

    assert cursor.calls == 1


def test_select_ai_credential_preflight_happens_before_profile_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []

    class Cursor:
        result: tuple[object, ...] = ()

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, _params: object | None = None) -> None:
            statements.append(sql)
            if "CURRENT_SCHEMA" in sql:
                self.result = ("ADMIN",)
            elif "USER_CREDENTIALS" in sql:
                self.result = (0,)
            else:
                raise AssertionError(
                    "Credential preflight 後に Profile mutation が実行されました。"
                )

        def fetchone(self) -> tuple[object, ...]:
            return self.result

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

    adapter = OracleNl2SqlAdapter(Settings())

    @contextmanager  # type: ignore[arg-type]
    def fake_connection() -> object:
        yield Connection()

    monkeypatch.setattr(adapter, "connection", fake_connection)

    with pytest.raises(SelectAiCredentialMissingError) as exc_info:
        adapter.upsert_select_ai_profile_low_level(
            profile_name="INVOICE_PROFILE",
            original_name="OLD_PROFILE",
            attributes={"provider": "oci", "credential_name": "OCI_CRED"},
        )

    assert exc_info.value.code == "SELECT_AI_CREDENTIAL_MISSING"
    assert any("USER_CREDENTIALS" in sql for sql in statements)
    assert all("DROP_PROFILE" not in sql for sql in statements)
    assert all("CREATE_PROFILE" not in sql for sql in statements)
