"""Ontology query-session router runtime と persistence 接続のテスト。"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import pytest

import app.features.nl2sql.ontology_router as ontology_router_module
from app.features.nl2sql.models import (
    AllowedObjects,
    ExplainPlanData,
    Nl2SqlProfile,
    PreviewData,
    PreviewRequest,
    QueryResults,
    SafetyReport,
    SchemaCatalog,
    SchemaCatalogHead,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.ontology_models import (
    ColumnQueryPolicy,
    GraphPatch,
    GraphPatchOperation,
    OntologyNode,
    OntologyNodeKind,
    OntologyProvenance,
    OntologyReviewStatus,
    OntologySourceKind,
    PhysicalMapping,
    SqlConfirmationRequest,
)
from app.features.nl2sql.ontology_reasoning import OntologyPublishService
from app.features.nl2sql.ontology_router import (
    GenerateSqlRequest,
    ImprovementProposalRequest,
    OntologyApiRuntime,
    OntologyContextSearchRequest,
    OntologyDraftRequest,
    OntologyProfileRecommendationRequest,
    OntologyPublishRequest,
    ProfileOntologyViewPatch,
    ProfileRecommendationConfirmationRequest,
    QuerySessionApiCreate,
    router,
)
from app.features.nl2sql.ontology_service import (
    OntologyGateBlockedError,
    OntologyNotFoundError,
    OntologyVersionConflictError,
)
from app.features.nl2sql.ontology_store import (
    InMemoryOntologyStore,
    OntologyCollection,
    stable_physical_id,
)
from app.main import app
from app.settings import get_settings


class _FakeLegacyNl2SqlService:
    def __init__(self) -> None:
        self.profile = Nl2SqlProfile(
            id="sales",
            name="販売分析",
            allowed_tables=["APP.ORDERS"],
            default_row_limit=100,
        )
        self.catalog = SchemaCatalog(
            refreshed_at="2026-07-11T00:00:00Z",
            tables=[
                SchemaTable(
                    table_name="ORDERS",
                    logical_name="受注",
                    owner="APP",
                    comment="受注明細",
                    columns=[
                        SchemaColumn(
                            column_name="ID",
                            logical_name="受注 ID",
                            data_type="NUMBER",
                            nullable=False,
                        ),
                        SchemaColumn(
                            column_name="CUSTOMER_ID",
                            logical_name="顧客 ID",
                            data_type="NUMBER",
                            nullable=False,
                        ),
                        SchemaColumn(
                            column_name="AMOUNT",
                            logical_name="受注金額",
                            data_type="NUMBER",
                        ),
                    ],
                )
            ],
        )
        self.preview_requests: list[PreviewRequest] = []
        self.executed_sql: list[str] = []
        self.recorded_history: list[dict[str, Any]] = []

    def get_catalog(self) -> SchemaCatalog:
        return self.catalog

    def get_profile(self, profile_id: str) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise ValueError("profile not found")
        return self.profile

    def resolve_allowed_objects(
        self,
        profile_id: str,
        requested: AllowedObjects,
    ) -> AllowedObjects:
        self.get_profile(profile_id)
        if not requested.table_names:
            return AllowedObjects(table_names=["APP.ORDERS"], enforce_table_scope=True)
        accepted = [
            "APP.ORDERS"
            for name in requested.table_names
            if name.replace('"', "").upper() in {"ORDERS", "APP.ORDERS"}
        ]
        return AllowedObjects(
            table_names=accepted,
            columns={
                key: value
                for key, value in requested.columns.items()
                if key.replace('"', "").upper() in {"ORDERS", "APP.ORDERS"}
            },
            enforce_table_scope=True,
        )

    def preview(self, request: PreviewRequest) -> PreviewData:
        self.preview_requests.append(request)
        limit = request.row_limit or 100
        sql = f"SELECT COUNT(*) AS ORDER_COUNT FROM APP.ORDERS FETCH FIRST {limit} ROWS ONLY"
        return PreviewData(
            sql=sql,
            executable_sql=sql,
            is_safe=True,
            row_limit=limit,
            note="deterministic",
        )

    def execute_sql(
        self,
        sql: str,
        allowed: AllowedObjects,
        row_limit: int | None,
    ) -> tuple[SafetyReport, str, QueryResults]:
        assert allowed.table_names == ["APP.ORDERS"]
        assert row_limit == 100
        self.executed_sql.append(sql)
        return (
            SafetyReport(
                is_safe=True,
                is_select_only=True,
                row_limit_applied=row_limit,
                referenced_tables=["APP.ORDERS"],
            ),
            sql,
            QueryResults(columns=["ORDER_COUNT"], rows=[{"ORDER_COUNT": 3}], total=1),
        )

    def explain_sql(self, _sql: str) -> ExplainPlanData:
        return ExplainPlanData(
            available=True,
            total_cost=7,
            estimated_cardinality=3,
        )

    def record_ontology_history(self, **payload: Any) -> None:
        self.recorded_history.append(payload)


class _IncrementalCatalogLegacyService(_FakeLegacyNl2SqlService):
    uses_incremental_store = True

    def __init__(self) -> None:
        super().__init__()
        self.catalog_reads = 0
        self.head = SchemaCatalogHead(
            catalog_version=1,
            schema_fingerprint="catalog-head-fingerprint",
        )

    def get_catalog(self) -> SchemaCatalog:
        self.catalog_reads += 1
        return super().get_catalog()

    def get_catalog_head(self) -> SchemaCatalogHead:
        return self.head.model_copy(deep=True)


class _FakeOntologyEmbeddingClient:
    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0 if index == (len(text) % 8) else 0.0 for index in range(1536)] for text in texts
        ]


class _BatchRecordingStore(InMemoryOntologyStore):
    def __init__(self) -> None:
        super().__init__()
        self.individual_saves: list[OntologyCollection] = []
        self.atomic_saves: list[tuple[OntologyCollection, int]] = []

    def save_document(
        self,
        collection: OntologyCollection,
        document: Mapping[str, Any] | Any,
        *,
        expected_etag: str | None = None,
    ) -> dict[str, Any]:
        self.individual_saves.append(collection)
        return super().save_document(
            collection,
            document,
            expected_etag=expected_etag,
        )

    def save_documents_atomic(
        self,
        collection: OntologyCollection,
        documents: Sequence[tuple[Mapping[str, Any], str | None]],
    ) -> list[dict[str, Any]]:
        self.atomic_saves.append((collection, len(documents)))
        return super().save_documents_atomic(collection, documents)


@pytest.fixture
def runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService]:
    # 既存の query-session 単体テストは Profile 確認以外を検証する。確認ゲートは専用テストで
    # 既定 true のまま検証する。
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_profile_confirmation_required", False)
    store = InMemoryOntologyStore()
    legacy = _FakeLegacyNl2SqlService()
    return OntologyApiRuntime(legacy_service=legacy, store=store), store, legacy


def _confirmation(data: Any) -> SqlConfirmationRequest:
    artifact = data.session.sql_artifacts[-1]
    return SqlConfirmationRequest(
        artifact_id=artifact.id,
        ontology_revision_id=artifact.ontology_revision_id,
        intent_version=artifact.intent_version,
        sql_hash=artifact.sql_hash,
        validation_hash=artifact.validation_report.validation_hash,
        generation_context_hash=artifact.generation_context_hash,
    )


def _generate_request(data: Any) -> GenerateSqlRequest:
    return GenerateSqlRequest(
        base_version=data.session.current_intent_version,
        intent_version=data.session.current_intent_version,
        ontology_revision_id=data.session.ontology_revision_id,
        confirm_intent=True,
    )


def test_router_declares_complete_query_session_and_profile_view_api() -> None:
    declared_paths = {str(getattr(route, "path", "")) for route in router.routes}
    assert "/nl2sql/query-sessions" in declared_paths
    assert "/nl2sql/query-sessions/{session_id}" in declared_paths
    assert "/nl2sql/query-sessions/{session_id}/intent" in declared_paths
    assert "/nl2sql/query-sessions/{session_id}/generate-sql" in declared_paths
    assert "/nl2sql/query-sessions/{session_id}/confirm-sql" in declared_paths
    assert "/nl2sql/query-sessions/{session_id}/execute" in declared_paths
    assert "/nl2sql/query-sessions/{session_id}/improvement-proposal" in declared_paths
    assert "/nl2sql/profiles/{profile_id}/ontology-view" in declared_paths
    assert "/nl2sql/profiles/{profile_id}/ontology-view/materialize" in declared_paths
    assert "/nl2sql/ontology/revisions" in declared_paths
    assert "/nl2sql/ontology/revisions/current" in declared_paths
    assert "/nl2sql/ontology/revisions/{revision_id}/drafts" in declared_paths
    assert "/nl2sql/ontology/revisions/{revision_id}/publish" in declared_paths


def test_initial_profile_view_persists_graph_in_collection_batches() -> None:
    store = _BatchRecordingStore()
    api = OntologyApiRuntime(
        legacy_service=_FakeLegacyNl2SqlService(),
        store=store,
    )

    _view, ontology = api.profile_view("sales")

    assert set(store.individual_saves) == {"revisions"}
    assert store.atomic_saves == [
        ("nodes", len(ontology.nodes)),
        ("edges", len(ontology.edges)),
    ]
    assert len(store.list_documents("nodes", {"revision_id": ontology.revision.id})) == len(
        ontology.nodes
    )
    assert len(store.list_documents("edges", {"revision_id": ontology.revision.id})) == len(
        ontology.edges
    )


def test_incremental_catalog_signature_avoids_reloading_unchanged_catalog() -> None:
    service = _IncrementalCatalogLegacyService()
    api = OntologyApiRuntime(
        legacy_service=service,
        store=InMemoryOntologyStore(),
    )

    api.profile_view("sales")
    api.profile_view("sales")

    assert service.catalog_reads == 1
    assert api._synced_catalog_signature == (1, "catalog-head-fingerprint")  # noqa: SLF001

    service.head = service.head.model_copy(
        update={
            "catalog_version": 2,
            "schema_fingerprint": "next-catalog-head-fingerprint",
        }
    )
    api.profile_view("sales")
    api.profile_view("sales")

    assert service.catalog_reads == 2
    assert api._synced_catalog_signature == (  # noqa: SLF001
        2,
        "next-catalog-head-fingerprint",
    )


def test_cold_runtime_reuses_complete_deterministic_draft_revision() -> None:
    service = _IncrementalCatalogLegacyService()
    store = _BatchRecordingStore()
    first_runtime = OntologyApiRuntime(legacy_service=service, store=store)
    _first_view, first_ontology = first_runtime.profile_view("sales")
    store.individual_saves.clear()
    store.atomic_saves.clear()

    second_runtime = OntologyApiRuntime(legacy_service=service, store=store)
    _second_view, second_ontology = second_runtime.profile_view("sales")

    assert second_ontology.revision.id == first_ontology.revision.id
    assert store.individual_saves == []
    assert store.atomic_saves == []


def test_system_schema_reset_clears_incremental_catalog_signature() -> None:
    service = _IncrementalCatalogLegacyService()
    api = OntologyApiRuntime(
        legacy_service=service,
        store=InMemoryOntologyStore(),
    )
    api.profile_view("sales")

    api.reset_after_system_schema_change()

    assert api._synced_catalog_signature is None  # noqa: SLF001
    api.profile_view("sales")
    assert service.catalog_reads == 2


def test_ontology_revision_cache_keeps_active_and_latest_only(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, _legacy = runtime
    active = api.current_ontology()
    api._ontology_cache_max_revisions = 2  # noqa: SLF001 - bounded cache contract
    second = active.model_copy(
        update={"revision": active.revision.model_copy(update={"id": "revision-second"})},
        deep=True,
    )
    latest = active.model_copy(
        update={"revision": active.revision.model_copy(update={"id": "revision-latest"})},
        deep=True,
    )

    api._cache_ontology(second)  # noqa: SLF001 - bounded cache contract
    api._cache_ontology(latest)  # noqa: SLF001 - bounded cache contract

    assert set(api._ontologies) == {active.revision.id, latest.revision.id}  # noqa: SLF001


def test_column_business_names_reads_only_published_object_columns(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """詳細 lookup は全 graph ではなく published revision の対象列だけを読む。"""
    api, store, legacy = runtime
    draft = api.current_ontology()
    published = api.publish_ontology_revision(
        draft.revision.id,
        OntologyPublishRequest(etag=draft.revision.etag),
    )

    def fail_full_catalog() -> SchemaCatalog:
        raise AssertionError("object-level lookup must not load the schema catalog")

    def fail_full_ontology() -> Any:
        raise AssertionError("object-level lookup must not load the full ontology")

    monkeypatch.setattr(legacy, "get_catalog", fail_full_catalog)
    monkeypatch.setattr(api, "current_ontology", fail_full_ontology)
    original_list = store.list_documents
    calls: list[tuple[str, dict[str, Any], bool]] = []

    def recording_list(
        collection: Any,
        filters: Any = None,
        *,
        include_embedding: bool = True,
    ) -> list[dict[str, Any]]:
        calls.append((str(collection), dict(filters or {}), include_embedding))
        return original_list(
            collection,
            filters,
            include_embedding=include_embedding,
        )

    monkeypatch.setattr(store, "list_documents", recording_list)

    names = api.column_business_names(
        owner="APP",
        object_name="ORDERS",
        object_type="table",
    )

    assert names == {
        "AMOUNT": "受注金額",
        "CUSTOMER_ID": "顧客 ID",
        "ID": "受注 ID",
    }
    assert calls == [
        ("revisions", {"status": "published"}, True),
        (
            "nodes",
            {
                "revision_id": published.revision.id,
                "node_type": "column",
                "physical_id": stable_physical_id("table", "APP", "ORDERS"),
            },
            False,
        ),
    ]


def test_column_business_name_cache_is_revision_aware_and_resettable(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """同一 revision/object はキャッシュし、schema reset で確実に破棄する。"""
    api, store, _legacy = runtime
    draft = api.current_ontology()
    published = api.publish_ontology_revision(
        draft.revision.id,
        OntologyPublishRequest(etag=draft.revision.etag),
    )
    physical_id = stable_physical_id("table", "APP", "ORDERS")

    first = api.column_business_names(
        owner="APP",
        object_name="ORDERS",
        object_type="table",
    )
    node_documents = store.list_documents(
        "nodes",
        {
            "revision_id": published.revision.id,
            "node_type": "column",
            "physical_id": physical_id,
        },
    )
    id_document = next(
        item for item in node_documents if item["payload"]["metadata"]["column_name"] == "ID"
    )
    id_document["payload"]["business_name_ja"] = "受注番号"
    store.save_document("nodes", id_document, expected_etag=str(id_document["etag"]))

    cached = api.column_business_names(
        owner="APP",
        object_name="ORDERS",
        object_type="table",
    )
    assert cached == first

    api.reset_after_system_schema_change()
    refreshed = api.column_business_names(
        owner="APP",
        object_name="ORDERS",
        object_type="table",
    )
    assert refreshed["ID"] == "受注番号"
    assert len(api._business_name_cache) == 1  # noqa: SLF001 - LRU cache contract
    assert api._business_name_cache_max_objects == 512  # noqa: SLF001


def test_column_business_names_does_not_wait_for_full_ontology_lock(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """全量 ontology の同期中でも object-level lookup は独立して完了する。"""
    api, store, legacy = runtime
    draft = api.current_ontology()
    api.publish_ontology_revision(
        draft.revision.id,
        OntologyPublishRequest(etag=draft.revision.etag),
    )
    lookup = OntologyApiRuntime(legacy_service=legacy, store=store)
    completed = threading.Event()
    result: dict[str, str] = {}

    def load_names() -> None:
        result.update(
            lookup.column_business_names(
                owner="APP",
                object_name="ORDERS",
                object_type="table",
            )
        )
        completed.set()

    lookup._lock.acquire()  # noqa: SLF001 - full-ontology lock independence contract
    worker = threading.Thread(target=load_names)
    try:
        worker.start()
        assert completed.wait(timeout=1)
    finally:
        lookup._lock.release()  # noqa: SLF001
        worker.join(timeout=1)

    assert result["ID"] == "受注 ID"


def test_profile_scenario_version_changes_for_keyword_only_update(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, _legacy = runtime
    current, _ontology = api.profile_view("sales")

    updated, _ontology = api.patch_profile_view(
        "sales",
        ProfileOntologyViewPatch(
            base_etag=current.etag,
            activation_keywords=["受注", " 受注 ", "売上"],
        ),
    )

    assert updated.activation_keywords == ["受注", "売上"]
    assert updated.scenario_version == current.scenario_version + 1


def test_profile_view_is_materialized_only_explicitly_and_becomes_stale_after_profile_edit(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, legacy = runtime
    preview, _ontology = api.profile_view("sales")

    assert api.profile_view_persistence_state(preview) == (False, False)
    assert store.list_documents("profile_views", {"profile_id": "sales"}) == []

    materialized = api.materialize_profile_view("sales")
    assert api.profile_view_persistence_state(materialized) == (True, False)
    stored_before = store.get_document(
        "profile_views",
        {"profile_id": "sales", "revision_id": materialized.ontology_revision_id},
    )
    assert stored_before is not None

    legacy.profile = legacy.profile.model_copy(
        update={"etag": "profile-etag-2", "allowed_views": ["APP.ORDER_VIEW"]},
        deep=True,
    )
    current_preview, _ontology = api.profile_view("sales")
    assert api.profile_view_persistence_state(current_preview) == (True, True)
    stored_after_edit = store.get_document(
        "profile_views",
        {"profile_id": "sales", "revision_id": materialized.ontology_revision_id},
    )
    assert stored_after_edit == stored_before

    rebuilt = api.materialize_profile_view("sales")
    assert rebuilt.source_profile_etag == "profile-etag-2"
    assert api.profile_view_persistence_state(rebuilt) == (True, False)


def test_profile_state_delete_removes_live_views_but_preserves_audit_documents(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, _legacy = runtime
    api.materialize_profile_view("sales")
    store.save_document(
        "query_sessions",
        {
            "session_id": "audit-session-1",
            "profile_id": "sales",
            "payload": {"snapshot": True},
        },
    )

    assert api.delete_profile_state("sales") == 1
    assert store.list_documents("profile_views", {"profile_id": "sales"}) == []
    assert store.get_document("query_sessions", {"session_id": "audit-session-1"}) is not None


def test_published_revision_pins_queries_until_reviewed_draft_is_published(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, legacy = runtime
    base = api.current_ontology()
    published_base = api.publish_ontology_revision(
        base.revision.id,
        OntologyPublishRequest(etag=base.revision.etag),
    )
    table_node = next(node for node in base.nodes if node.kind == OntologyNodeKind.TABLE)
    business_node = OntologyNode(
        id="business_orders",
        revision_id=base.revision.id,
        kind=OntologyNodeKind.BUSINESS_ENTITY,
        technical_name="orders",
        business_name_ja="受注",
        physical_mappings=[PhysicalMapping(object_ref=table_node.physical_mappings[0].object_ref)],
        provenance=OntologyProvenance(source_kind=OntologySourceKind.MANUAL),
        review_status=OntologyReviewStatus.APPROVED,
    )
    draft = api.create_ontology_draft(
        published_base.revision.id,
        OntologyDraftRequest(
            base_etag=published_base.revision.etag,
            note="受注 entity を追加",
            node_upserts=[business_node],
        ),
    )

    before_publish = api.create_session(
        QuerySessionApiCreate(question="受注件数を表示", profile_id="sales")
    )
    assert before_publish.session.ontology_revision_id == published_base.revision.id

    published_draft = api.publish_ontology_revision(
        draft.revision.id,
        OntologyPublishRequest(etag=draft.revision.etag),
    )
    after_publish = api.create_session(
        QuerySessionApiCreate(question="受注件数を表示", profile_id="sales")
    )
    assert after_publish.session.ontology_revision_id == published_draft.revision.id
    assert any(node.id == business_node.id for node in after_publish.ontology_graph.nodes)

    # schema drift は新 draft を作るが、公開済み query scope を自動で置換しない。
    legacy.catalog = legacy.catalog.model_copy(
        update={
            "tables": [
                legacy.catalog.tables[0].model_copy(
                    update={
                        "columns": [
                            *legacy.catalog.tables[0].columns,
                            SchemaColumn(
                                column_name="CREATED_AT",
                                logical_name="作成日時",
                                data_type="TIMESTAMP",
                            ),
                        ]
                    }
                )
            ]
        },
        deep=True,
    )
    drift = api.current_ontology()
    assert drift.revision.status.value == "draft"
    assert drift.revision.parent_revision_id == published_draft.revision.id
    active_view, active_ontology = api.profile_view("sales")
    assert active_view.ontology_revision_id == published_draft.revision.id
    assert active_ontology.revision.id == published_draft.revision.id
    pinned = api.create_session(
        QuerySessionApiCreate(question="受注件数を表示", profile_id="sales")
    )
    assert pinned.session.ontology_revision_id == published_draft.revision.id


def test_publish_blocks_unreviewed_business_definition(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, _legacy = runtime
    base = api.current_ontology()
    table_node = next(node for node in base.nodes if node.kind == OntologyNodeKind.TABLE)
    proposal = OntologyNode(
        id="business_orders_proposal",
        revision_id=base.revision.id,
        kind=OntologyNodeKind.BUSINESS_ENTITY,
        technical_name="orders_proposal",
        business_name_ja="受注候補",
        physical_mappings=[PhysicalMapping(object_ref=table_node.physical_mappings[0].object_ref)],
        provenance=OntologyProvenance(source_kind=OntologySourceKind.MANUAL),
        review_status=OntologyReviewStatus.PROPOSED,
    )
    draft = api.create_ontology_draft(
        base.revision.id,
        OntologyDraftRequest(
            base_etag=base.revision.etag,
            node_upserts=[proposal],
        ),
    )

    with pytest.raises(OntologyGateBlockedError) as exc_info:
        api.publish_ontology_revision(
            draft.revision.id,
            OntologyPublishRequest(etag=draft.revision.etag),
        )

    assert exc_info.value.code == "ONTOLOGY_REVIEW_REQUIRED"


def test_runtime_executes_two_confirmation_flow_and_persists_every_artifact(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, legacy = runtime
    created = api.create_session(
        QuerySessionApiCreate(
            question="受注件数を表示",
            profile_id="sales",
            allowed_objects=AllowedObjects(table_names=["APP.ORDERS"]),
        )
    )
    assert created.session.status.value == "awaiting_intent_confirmation"
    assert created.session.intents[-1].ambiguities == []

    generated = api.generate_sql(
        created.session.id,
        _generate_request(created),
    )
    assert generated.preview is not None
    assert generated.session.status.value == "awaiting_sql_confirmation"
    assert generated.session.sql_artifacts[-1].validation_report.is_valid is True
    confirmation = _confirmation(generated)

    confirmed = api.confirm_sql(created.session.id, confirmation)
    assert confirmed.session.sql_confirmation is not None
    executed = api.execute(created.session.id, confirmation)
    assert executed.session.status.value == "done"
    assert executed.result.rows == [{"ORDER_COUNT": 3}]
    assert legacy.executed_sql == [generated.session.sql_artifacts[-1].sql]
    assert legacy.recorded_history[0]["session_id"] == created.session.id
    assert legacy.recorded_history[0]["ontology_trace_summary"]["validation_hash"]

    assert len(store.list_revisions()) == 1
    assert len(store.list_nodes(generated.session.ontology_revision_id)) >= 4
    persisted_session = store.get_query_session(created.session.id)
    assert persisted_session is not None
    assert persisted_session["profile_view_snapshot"]["profile_id"] == "sales"
    assert store.get_query_session(created.session.id) is not None
    assert store.get_artifact(generated.session.sql_artifacts[-1].id) is not None


def test_runtime_rehydrates_complete_query_trace_and_profile_draft_after_restart(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, legacy = runtime
    current_view, source_ontology = api.profile_view("sales")
    table_id = current_view.physical_objects[0].node_id
    updated_view, _ = api.patch_profile_view(
        "sales",
        ProfileOntologyViewPatch(
            base_etag=current_view.etag,
            table_usages_ja={table_id: "再起動後も保持する受注用途"},
            node_overrides=[{"node_id": table_id, "business_name_ja": "受注業務"}],
        ),
    )
    created = api.create_session(
        QuerySessionApiCreate(question="受注件数を表示", profile_id="sales")
    )
    generated = api.generate_sql(
        created.session.id,
        _generate_request(created),
    )
    assert generated.preview is not None
    confirmation = _confirmation(generated)
    api.confirm_sql(created.session.id, confirmation)
    api.execute(created.session.id, confirmation)
    proposal, proposal_data = api.create_proposal(
        created.session.id,
        ImprovementProposalRequest(
            title_ja="受注別名の改善",
            summary="受注語彙を保持",
        ),
    )

    restarted = OntologyApiRuntime(legacy_service=legacy, store=store)
    restored = restarted.get_session(created.session.id)
    restored_view, restored_ontology = restarted.profile_view("sales")

    assert restored.session == proposal_data.session
    assert restored.preview is not None
    assert restored.preview.executable_sql == generated.preview.executable_sql
    assert restored.result is not None
    assert restored.result.rows == [{"ORDER_COUNT": 3}]
    assert restored.performance_check is not None
    assert restored.performance_check.total_cost == 7
    assert restored.ontology_trace_summary["retrieved_node_ids"]
    assert restored.profile_ontology_view.id == created.profile_ontology_view.id
    assert restored_view.etag == updated_view.etag
    assert restored_view.table_usages_ja[table_id] == "再起動後も保持する受注用途"
    assert restored_view.draft_node_overrides[0]["business_name_ja"] == "受注業務"
    assert restored_ontology.revision.id == created.session.ontology_revision_id
    assert {node.id for node in restored_ontology.nodes} == {
        node.id for node in source_ontology.nodes
    }
    assert {edge.id for edge in restored_ontology.edges} == {
        edge.id for edge in source_ontology.edges
    }
    assert restarted.get_proposal(proposal.id) == proposal


def test_rehydrated_revision_is_parent_for_drift_and_preserves_orphan_mapping(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, legacy = runtime
    previous = api.current_ontology()
    previous_view, _ = api.profile_view("sales")
    table_node = next(node for node in previous.nodes if node.kind == OntologyNodeKind.TABLE)
    amount_node = next(
        node
        for node in previous.nodes
        if node.kind == OntologyNodeKind.COLUMN and node.metadata.get("column_name") == "AMOUNT"
    )
    api.patch_profile_view(
        "sales",
        ProfileOntologyViewPatch(
            base_etag=previous_view.etag,
            table_usages_ja={table_node.id: "drift 後も保持する用途"},
            node_overrides=[{"node_id": table_node.id, "business_name_ja": "受注業務"}],
        ),
    )
    business_node = OntologyNode(
        id="business_order_amount",
        revision_id=previous.revision.id,
        kind=OntologyNodeKind.PROPERTY,
        technical_name="order_amount",
        business_name_ja="受注金額",
        physical_mappings=[
            PhysicalMapping(
                object_ref=table_node.physical_mappings[0].object_ref,
                column_refs=[amount_node.physical_mappings[0].column_refs[0]],
            )
        ],
        provenance=OntologyProvenance(source_kind=OntologySourceKind.MANUAL),
        review_status=OntologyReviewStatus.APPROVED,
    )
    store.save_node(
        {
            "revision_id": previous.revision.id,
            "node_id": business_node.id,
            "node_type": business_node.kind.value,
            "review_status": business_node.review_status.value,
            "physical_id": table_node.id,
            "embedding": None,
            "payload": business_node,
        }
    )
    source_table = legacy.catalog.tables[0]
    legacy.catalog = legacy.catalog.model_copy(
        update={
            "tables": [
                source_table.model_copy(
                    update={
                        "columns": [
                            column
                            for column in source_table.columns
                            if column.column_name != "AMOUNT"
                        ]
                    }
                )
            ]
        },
        deep=True,
    )

    restarted = OntologyApiRuntime(legacy_service=legacy, store=store)
    drifted = restarted.current_ontology()
    migrated_view, _ = restarted.profile_view("sales")
    preserved = next(node for node in drifted.nodes if node.id == business_node.id)

    assert drifted.revision.version == previous.revision.version + 1
    assert drifted.revision.parent_revision_id == previous.revision.id
    assert preserved.review_status == OntologyReviewStatus.ORPHANED
    assert preserved.metadata["orphaned_mapping_node_ids"] == [amount_node.id]
    assert migrated_view.ontology_revision_id == previous.revision.id
    assert migrated_view.ontology_revision_id != drifted.revision.id
    assert migrated_view.table_usages_ja[table_node.id] == "drift 後も保持する用途"
    assert migrated_view.draft_node_overrides[0]["business_name_ja"] == "受注業務"
    assert migrated_view.draft_schema_fingerprint == ""


def test_sync_reuses_registered_revision_instead_of_conflict(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """self._ontology が旧版へ巻き戻っても、既登録の drift revision を再登録せず採用する。

    revision id は (fingerprint, version) の決定論ハッシュのため、現在 revision が旧版 V を
    指したまま再 sync すると evolve は既登録の V+1 と同じ id を生成する。修正前は
    register_revision が REVISION_ALREADY_EXISTS(409)を投げ、AI 構築 job が
    「オントロジー構築に失敗しました」で落ちていた。
    """

    api, _store, legacy = runtime
    previous = api.current_ontology()

    # schema drift を発生させ、版 V+1 の revision を登録する。
    source_table = legacy.catalog.tables[0]
    legacy.catalog = legacy.catalog.model_copy(
        update={
            "tables": [
                source_table.model_copy(
                    update={
                        "columns": [
                            column
                            for column in source_table.columns
                            if column.column_name != "AMOUNT"
                        ]
                    }
                )
            ]
        },
        deep=True,
    )
    drifted = api.current_ontology()
    assert drifted.revision.version == previous.revision.version + 1

    # 現在 revision を旧版 V へ巻き戻す(復元直後などに起きうる状態 divergence を再現)。
    api._ontology = api._ontologies[previous.revision.id]

    # 再 sync しても 409 を投げず、既登録の V+1 revision を採用する(冪等)。
    resynced = api.current_ontology()
    assert resynced.revision.id == drifted.revision.id
    assert resynced.revision.version == drifted.revision.version
    # 自己修復: 現在 revision が V+1 に戻っている。
    assert api._ontology.revision.id == drifted.revision.id


def test_request_scope_is_intersection_and_unknown_profile_never_falls_back(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, _legacy = runtime
    with pytest.raises(OntologyGateBlockedError) as scope_error:
        api.create_session(
            QuerySessionApiCreate(
                question="受注件数を表示",
                profile_id="sales",
                allowed_objects=AllowedObjects(table_names=["OTHER.SECRETS"]),
            )
        )
    assert scope_error.value.code == "REQUEST_SCOPE_EMPTY"

    with pytest.raises(OntologyNotFoundError) as profile_error:
        api.create_session(
            QuerySessionApiCreate(
                question="受注件数を表示",
                profile_id="unknown",
            )
        )
    assert profile_error.value.code == "NL2SQL_PROFILE_NOT_FOUND"


def test_ontology_embedding_uses_oci_boundary_and_is_persisted_with_nodes(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, legacy = runtime
    legacy._embedding_client = _FakeOntologyEmbeddingClient()  # type: ignore[attr-defined]

    created = api.create_session(
        QuerySessionApiCreate(question="受注件数を表示", profile_id="sales")
    )

    nodes = store.list_nodes(created.session.ontology_revision_id)
    embedded = [item for item in nodes if item.get("embedding")]
    assert embedded
    assert all(len(item["embedding"]) == 1536 for item in embedded)


def test_profile_view_patch_only_updates_business_metadata_and_checks_etag(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, _legacy = runtime
    current, ontology = api.profile_view("sales")
    table_id = current.physical_objects[0].node_id
    column_id = next(
        node.id
        for node in ontology.nodes
        if node.kind.value == "column" and node.id in current.node_ids
    )
    request = ProfileOntologyViewPatch(
        base_etag=current.etag,
        table_usages_ja={table_id: "販売部門の受注件数分析に使用"},
        column_policies={column_id: ColumnQueryPolicy(aggregatable=True)},
        allowed_path_ids=[],
    )

    updated, _ = api.patch_profile_view("sales", request)

    assert updated.etag != current.etag
    assert updated.table_usages_ja[table_id] == "販売部門の受注件数分析に使用"
    assert updated.column_policies[column_id].aggregatable is True
    stored = store.get_profile_view("sales")
    assert stored is not None
    assert stored["payload"]["etag"] == updated.etag
    with pytest.raises(OntologyVersionConflictError) as exc_info:
        api.patch_profile_view("sales", request)
    assert exc_info.value.code == "PROFILE_VIEW_ETAG_MISMATCH"


def test_improvement_proposal_is_bound_to_session_and_persisted(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, _legacy = runtime
    created = api.create_session(
        QuerySessionApiCreate(question="受注件数を表示", profile_id="sales")
    )
    proposal, session_data = api.create_proposal(
        created.session.id,
        ImprovementProposalRequest(
            title_ja="受注の別名追加",
            description_ja="質問で使われる語彙を提案する",
            patch=GraphPatch(
                base_version=1,
                operations=[
                    GraphPatchOperation(
                        op="add",
                        path="/entities/0",
                        value=created.session.intents[-1].entities[0].model_dump(),
                    )
                ],
            ),
        ),
    )

    assert proposal.session_id == created.session.id
    assert proposal.id in session_data.session.proposal_ids
    assert store.get_proposal(proposal.id) is not None


@pytest.mark.asyncio
async def test_http_contract_accepts_frontend_confirmation_and_draft_payloads(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _store, _legacy = runtime
    monkeypatch.setattr(ontology_router_module, "ontology_runtime", api)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created_response = await client.post(
            "/api/nl2sql/query-sessions",
            headers={"Idempotency-Key": "test-create-session"},
            json={
                "question": "受注件数を表示",
                "profile_id": "sales",
                "allowed_objects": {"table_names": ["APP.ORDERS"], "columns": {}},
            },
        )
        assert created_response.status_code == 200
        created = created_response.json()["data"]["session"]

        generated_response = await client.post(
            f"/api/nl2sql/query-sessions/{created['id']}/generate-sql",
            headers={"Idempotency-Key": "test-generate-sql"},
            json={
                "base_version": 1,
                "intent_version": 1,
                "ontology_revision_id": created["ontology_revision_id"],
                "confirm_intent": True,
            },
        )
        assert generated_response.status_code == 200
        generated = generated_response.json()["data"]["session"]
        artifact = generated["sql_artifacts"][-1]
        binding = {
            "session_id": created["id"],
            "artifact_id": artifact["id"],
            "ontology_revision_id": artifact["ontology_revision_id"],
            "intent_version": artifact["intent_version"],
            "sql_hash": artifact["sql_hash"],
            "validation_hash": artifact["validation_report"]["validation_hash"],
            "generation_context_hash": artifact["generation_context_hash"],
            "confirm_sql": True,
        }

        unbound_response = await client.post(
            f"/api/nl2sql/query-sessions/{created['id']}/confirm-sql",
            json={key: value for key, value in binding.items() if key != "session_id"},
        )
        assert unbound_response.status_code == 422

        confirmed_response = await client.post(
            f"/api/nl2sql/query-sessions/{created['id']}/confirm-sql",
            headers={"Idempotency-Key": "test-confirm-sql"},
            json=binding,
        )
        assert confirmed_response.status_code == 200
        executed_response = await client.post(
            f"/api/nl2sql/query-sessions/{created['id']}/execute",
            headers={"Idempotency-Key": "test-execute-sql"},
            json={**binding, "confirm_sql": True},
        )
        assert executed_response.status_code == 200
        assert executed_response.json()["data"]["result"]["rows"] == [{"ORDER_COUNT": 3}]

        view_response = await client.get("/api/nl2sql/profiles/sales/ontology-view")
        view = view_response.json()["data"]["profile_ontology_view"]
        draft_response = await client.patch(
            "/api/nl2sql/profiles/sales/ontology-view",
            json={
                "base_etag": view["etag"],
                "node_overrides": [
                    {
                        "node_id": "business:APP:TABLE:ORDERS",
                        "business_name_ja": "受注",
                        "table_usage": "受注件数の分析",
                    }
                ],
                "edge_overrides": [],
                "schema_fingerprint": "client-draft",
                "physical_scope": {"table_names": ["APP.ORDERS"], "view_names": []},
            },
        )
        assert draft_response.status_code == 200
        assert (
            draft_response.json()["data"]["profile_ontology_view"]["draft_node_overrides"][0][
                "business_name_ja"
            ]
            == "受注"
        )


@pytest.mark.asyncio
async def test_ontology_proposals_does_not_block_unrelated_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "app_auth_enabled", True)

    started = threading.Event()
    release = threading.Event()

    class _BlockingOntologyRuntime:
        def list_profile_proposals(self, profile_id: str) -> list[Any]:
            assert profile_id == "sales"
            started.set()
            release.wait(timeout=1.0)
            return []

    monkeypatch.setattr(
        ontology_router_module,
        "ontology_runtime",
        _BlockingOntologyRuntime(),
    )
    timer = threading.Timer(0.5, release.set)
    timer.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        proposal_task = asyncio.create_task(
            client.get("/api/nl2sql/profiles/sales/ontology-proposals")
        )
        try:
            started_at = time.perf_counter()
            assert await asyncio.to_thread(started.wait, 1.0)
            assert time.perf_counter() - started_at < 0.2

            permissions_response = await asyncio.wait_for(
                client.get("/api/security/permissions"),
                timeout=0.2,
            )
            assert permissions_response.status_code == 200
        finally:
            release.set()
            timer.cancel()
        proposal_response = await asyncio.wait_for(proposal_task, timeout=1.0)
        assert proposal_response.status_code == 200
        assert proposal_response.json()["data"]["proposals"] == []


def test_pure_physical_published_follows_schema_drift_automatically(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """空カタログで固定された純物理 published が、カタログ更新後に自動で追従する。"""

    api, _store, legacy = runtime
    populated_catalog = legacy.catalog
    legacy.catalog = SchemaCatalog(refreshed_at="2026-07-12T00:00:00Z", tables=[])

    empty_view, empty_ontology = api.profile_view("sales")
    assert empty_view.node_ids == []
    assert empty_ontology.revision.status.value == "published"

    # スキーマ refresh 相当: カタログにテーブルが入る
    legacy.catalog = populated_catalog
    view, ontology = api.profile_view("sales")

    assert ontology.revision.status.value == "published"
    assert ontology.revision.id != empty_ontology.revision.id
    assert any(node.kind == OntologyNodeKind.TABLE for node in ontology.nodes)
    assert view.node_ids  # APP.ORDERS が解決される
    assert any(item.object_name == "ORDERS" for item in view.physical_objects)


def test_business_definitions_keep_pinning_after_auto_follow_fix(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    """業務定義を含む published は schema drift しても自動置換されない(pin 仕様の回帰)。"""

    api, _store, legacy = runtime
    base = api.current_ontology()
    published_base = api.publish_ontology_revision(
        base.revision.id,
        OntologyPublishRequest(etag=base.revision.etag),
    )
    table_node = next(node for node in base.nodes if node.kind == OntologyNodeKind.TABLE)
    business_node = OntologyNode(
        id="business_orders_pin",
        revision_id=base.revision.id,
        kind=OntologyNodeKind.BUSINESS_ENTITY,
        technical_name="orders",
        business_name_ja="受注",
        physical_mappings=[PhysicalMapping(object_ref=table_node.physical_mappings[0].object_ref)],
        provenance=OntologyProvenance(source_kind=OntologySourceKind.MANUAL),
        review_status=OntologyReviewStatus.APPROVED,
    )
    draft = api.create_ontology_draft(
        published_base.revision.id,
        OntologyDraftRequest(base_etag=published_base.revision.etag, node_upserts=[business_node]),
    )
    published_with_business = api.publish_ontology_revision(
        draft.revision.id,
        OntologyPublishRequest(etag=draft.revision.etag),
    )

    legacy.catalog = legacy.catalog.model_copy(
        update={
            "tables": [
                legacy.catalog.tables[0].model_copy(
                    update={
                        "columns": [
                            *legacy.catalog.tables[0].columns,
                            SchemaColumn(
                                column_name="UPDATED_AT",
                                logical_name="更新日時",
                                data_type="TIMESTAMP",
                            ),
                        ]
                    }
                )
            ]
        },
        deep=True,
    )
    _view, active = api.profile_view("sales")
    assert active.revision.id == published_with_business.revision.id


def test_profile_view_warnings_report_unresolved_objects(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, legacy = runtime
    legacy.profile = legacy.profile.model_copy(
        update={"allowed_tables": ["APP.ORDERS", "APP.MISSING_TABLE"]}
    )

    view, _ontology = api.profile_view("sales")
    warnings = api.profile_view_warnings("sales", view)

    assert len(warnings) == 1
    assert "APP.MISSING_TABLE" in warnings[0]
    assert "スキーマ情報" in warnings[0]


def test_profile_recommendation_requires_explicit_confirmation_and_binds_token(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _store, _legacy = runtime
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_profile_confirmation_required", True)
    recommendation = api.recommend_profiles(
        OntologyProfileRecommendationRequest(question="販売分析で受注件数を表示")
    )
    assert recommendation.candidates[0].profile_id == "sales"

    with pytest.raises(OntologyGateBlockedError) as missing_confirmation:
        api.create_session(
            QuerySessionApiCreate(question="販売分析で受注件数を表示", profile_id="sales")
        )
    assert missing_confirmation.value.code == "PROFILE_CONFIRMATION_REQUIRED"

    with pytest.raises(OntologyVersionConflictError):
        api.confirm_profile_recommendation(
            recommendation.id,
            ProfileRecommendationConfirmationRequest(
                selected_profile_id="sales",
                selected_revision_id="stale-revision",
            ),
        )
    confirmed, token = api.confirm_profile_recommendation(
        recommendation.id,
        ProfileRecommendationConfirmationRequest(
            selected_profile_id="sales",
            selected_revision_id=recommendation.ontology_revision_id,
        ),
    )
    assert confirmed.selected_profile_id == "sales"
    assert token

    created = api.create_session(
        QuerySessionApiCreate(
            question="販売分析で受注件数を表示",
            profile_id="sales",
            profile_confirmation_token=token,
        )
    )
    assert created.session.profile_id == "sales"
    persisted = api.store.get_document("query_sessions", {"session_id": created.session.id})
    assert persisted is not None
    assert persisted["runtime_context"]["profile_selection_source"] == "confirmed"

    with pytest.raises(OntologyGateBlockedError):
        api.create_session(
            QuerySessionApiCreate(
                question="同じ token を別の質問に使用",
                profile_id="sales",
                profile_confirmation_token=token,
            )
        )


def test_zero_match_recommendation_can_be_confirmed_manually(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _store, _legacy = runtime
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_profile_confirmation_required", True)
    recommendation = api.recommend_profiles(
        OntologyProfileRecommendationRequest(question="完全に無関係な質問")
    )
    assert recommendation.candidates == []
    confirmed, token = api.confirm_profile_recommendation(
        recommendation.id,
        ProfileRecommendationConfirmationRequest(
            selected_profile_id="sales",
            selected_revision_id=recommendation.ontology_revision_id,
        ),
    )
    assert confirmed.selected_profile_id == "sales"
    assert token


def test_profile_recommendation_uses_existing_oci_embedding_boundary(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, legacy = runtime

    class _MatchingEmbeddingClient:
        def is_configured(self) -> bool:
            return True

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _text in texts]

    legacy._embedding_client = _MatchingEmbeddingClient()  # type: ignore[attr-defined]
    recommendation = api.recommend_profiles(
        OntologyProfileRecommendationRequest(question="語彙一致のない意味検索")
    )
    assert recommendation.candidates[0].profile_id == "sales"
    assert any("意味類似度" in reason for reason in recommendation.candidates[0].reasons_ja)


def test_context_search_is_revision_pinned_profile_scoped_and_deterministic(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, _store, _legacy = runtime
    view, ontology = api.profile_view("sales")
    request = OntologyContextSearchRequest(
        question="受注金額",
        ontology_revision_id=ontology.revision.id,
        top_k=8,
        max_hops=2,
    )
    first = api.search_ontology_context("sales", request)
    second = api.search_ontology_context("sales", request)

    assert first.context_hash == second.context_hash
    assert {node.id for node in first.nodes} <= set(view.node_ids)
    assert {edge.id for edge in first.edges} <= set(view.edge_ids)
    assert all(edge.review_status == OntologyReviewStatus.APPROVED for edge in first.edges)
    assert first.ontology_revision_id == ontology.revision.id
    assert ontology.revision.id in first.llm_markdown

    with pytest.raises(OntologyVersionConflictError):
        api.search_ontology_context(
            "sales",
            request.model_copy(update={"ontology_revision_id": "stale-revision"}),
        )


def test_inferred_context_expansion_never_leaves_profile_view(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
) -> None:
    api, store, _legacy = runtime
    view, ontology = api.profile_view("sales")
    source_id, target_id = view.node_ids[:2]
    outside_id = "outside-profile-node"
    store.save_artifact(
        {
            "artifact_id": "inferred-context-test",
            "session_id": ontology.revision.id,
            "artifact_type": "ontology_inferred_turtle",
            "content_hash": "hash",
            "created_at": "2026-07-19T00:00:00Z",
            "content": (
                "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                f"<urn:nl2sql:ontology:node:{source_id}> rdfs:subClassOf "
                f"<urn:nl2sql:ontology:node:{target_id}> .\n"
                f"<urn:nl2sql:ontology:node:{target_id}> rdfs:subClassOf "
                f"<urn:nl2sql:ontology:node:{outside_id}> .\n"
            ),
        }
    )
    expanded = api._inferred_context_node_ids(
        ontology.revision.id,
        {source_id},
        allowed_node_ids=set(view.node_ids),
        max_hops=3,
    )
    assert target_id in expanded
    assert outside_id not in expanded

    store.save_artifact(
        {
            "artifact_id": "inferred-context-encoded-id",
            "session_id": ontology.revision.id,
            "artifact_type": "ontology_inferred_turtle",
            "content_hash": "hash-encoded",
            "created_at": "2026-07-19T00:00:01Z",
            "content": (
                "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                "<urn:nl2sql:ontology:node:node%3Asource> rdfs:subClassOf "
                "<urn:nl2sql:ontology:node:node%3Atarget> .\n"
            ),
        }
    )
    assert api._inferred_context_node_ids(
        ontology.revision.id,
        {"node:source"},
        allowed_node_ids={"node:source", "node:target"},
        max_hops=1,
    ) == {"node:target"}


def test_async_semantic_publish_succeeds_and_is_idempotent(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, store, _legacy = runtime
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")
    revision = api.current_ontology().revision
    publisher = OntologyPublishService(api)

    queued = publisher.start(revision.id, etag=revision.etag, idempotency_key="publish-1")
    duplicate = publisher.start(revision.id, etag=revision.etag, idempotency_key="publish-1")
    assert duplicate.id == queued.id
    finished = publisher.run_persisted(queued.id)

    assert finished.status.value == "succeeded"
    assert finished.requested_etag == revision.etag
    assert finished.shacl_conforms is True
    published = api.ontology_revision(revision.id)
    assert published.revision.status.value == "published"
    assert published.revision.reasoning_status.value == "ready"
    assert published.revision.rdf_graph_name.startswith("ONT_")
    artifacts = store.list_documents("artifacts", {"session_id": revision.id})
    assert {item["artifact_type"] for item in artifacts} >= {
        "ontology_owl_turtle",
        "ontology_shacl_turtle",
        "ontology_llm_markdown",
        "ontology_shacl_report",
    }


def test_publish_validates_review_gate_before_materialization(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _store, _legacy = runtime
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")
    base = api.current_ontology()
    proposed = OntologyNode(
        id="unreviewed-term",
        revision_id=base.revision.id,
        kind=OntologyNodeKind.BUSINESS_TERM,
        business_name_ja="未確認用語",
        provenance=OntologyProvenance(source_kind=OntologySourceKind.MANUAL),
        review_status=OntologyReviewStatus.PROPOSED,
    )
    draft = api.create_ontology_draft(
        base.revision.id,
        OntologyDraftRequest(
            base_etag=base.revision.etag,
            node_upserts=[proposed],
        ),
    )
    materialized = False

    class _Materializer:
        def materialize(self, **_values: Any) -> str:
            nonlocal materialized
            materialized = True
            return ""

    publisher = OntologyPublishService(api, materializer=_Materializer())
    queued = publisher.start(
        draft.revision.id,
        etag=draft.revision.etag,
        idempotency_key="publish-review-gate",
    )
    finished = publisher.run_persisted(queued.id)

    assert finished.status.value == "failed"
    assert materialized is False
    assert api.ontology_revision(draft.revision.id).revision.reasoning_status.value == "failed"


def test_publish_can_skip_shacl_only_with_explicit_rollout_flag(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _store, _legacy = runtime
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_shacl_enabled", False)
    revision = api.current_ontology().revision
    publisher = OntologyPublishService(api)

    queued = publisher.start(
        revision.id,
        etag=revision.etag,
        idempotency_key="publish-shacl-rollout",
    )
    finished = publisher.run_persisted(queued.id)

    assert finished.status.value == "succeeded"
    assert finished.shacl_conforms is None
    assert finished.warnings_ja == ["段階導入設定により SHACL Core 検証をスキップしました。"]


def test_publish_materialization_failure_keeps_previous_revision_active(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _store, _legacy = runtime
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")
    base = api.current_ontology()
    active = api.publish_ontology_revision(
        base.revision.id,
        OntologyPublishRequest(etag=base.revision.etag),
    )
    draft = api.create_ontology_draft(
        active.revision.id,
        OntologyDraftRequest(base_etag=active.revision.etag, note="失敗確認"),
    )

    class _FailingMaterializer:
        def materialize(self, **_values: Any) -> str:
            raise RuntimeError("Oracle OWL2RL unavailable")

    publisher = OntologyPublishService(api, materializer=_FailingMaterializer())
    queued = publisher.start(
        draft.revision.id,
        etag=draft.revision.etag,
        idempotency_key="publish-failure",
    )
    finished = publisher.run_persisted(queued.id)

    assert finished.status.value == "failed"
    assert finished.error_code == "ONTOLOGY_PUBLISH_FAILED"
    _view, still_active = api.profile_view("sales")
    assert still_active.revision.id == active.revision.id
    failed_revision = api.ontology_revision(draft.revision.id).revision
    assert failed_revision.status.value == "draft"
    assert failed_revision.reasoning_status.value == "failed"


def test_atomic_revision_switch_failure_restores_in_memory_active_revision(
    runtime: tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, store, _legacy = runtime
    base = api.current_ontology()
    published = api.publish_ontology_revision(
        base.revision.id,
        OntologyPublishRequest(etag=base.revision.etag),
    )
    draft = api.create_ontology_draft(
        published.revision.id,
        OntologyDraftRequest(base_etag=published.revision.etag, note="atomic failure"),
    )

    def fail_atomic(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("Oracle transaction failed")

    monkeypatch.setattr(store, "save_documents_atomic", fail_atomic)
    with pytest.raises(RuntimeError, match="Oracle transaction failed"):
        api.publish_ontology_revision(
            draft.revision.id,
            OntologyPublishRequest(etag=draft.revision.etag),
        )

    _view, active = api.profile_view("sales")
    assert active.revision.id == published.revision.id
    assert api.ontology_revision(draft.revision.id).revision.status.value == "draft"
