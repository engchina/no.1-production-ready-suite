"""Ontology query-session router runtime と persistence 接続のテスト。"""

from __future__ import annotations

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
from app.features.nl2sql.ontology_router import (
    GenerateSqlRequest,
    ImprovementProposalRequest,
    OntologyApiRuntime,
    OntologyDraftRequest,
    OntologyPublishRequest,
    ProfileOntologyViewPatch,
    QuerySessionApiCreate,
    router,
)
from app.features.nl2sql.ontology_service import (
    OntologyGateBlockedError,
    OntologyNotFoundError,
    OntologyVersionConflictError,
)
from app.features.nl2sql.ontology_store import InMemoryOntologyStore
from app.main import app


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


class _FakeOntologyEmbeddingClient:
    def is_configured(self) -> bool:
        return True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0 if index == (len(text) % 8) else 0.0 for index in range(1536)] for text in texts
        ]


@pytest.fixture
def runtime() -> tuple[OntologyApiRuntime, InMemoryOntologyStore, _FakeLegacyNl2SqlService]:
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
    assert "/nl2sql/ontology/revisions" in declared_paths
    assert "/nl2sql/ontology/revisions/current" in declared_paths
    assert "/nl2sql/ontology/revisions/{revision_id}/drafts" in declared_paths
    assert "/nl2sql/ontology/revisions/{revision_id}/publish" in declared_paths


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
    assert store.get_profile_view("sales") is not None
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
