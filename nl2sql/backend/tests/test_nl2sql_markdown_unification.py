"""13分類、自由編集の確認、原子的公開と同版 graph/context の回帰。"""

import json
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn

import pytest
from fastapi import Request
from starlette.responses import Response
from test_nl2sql_ontology_definitions import runtime
from test_nl2sql_ontology_workspace import model

from app.features.nl2sql.ontology_definitions import BusinessDefinition
from app.features.nl2sql.ontology_markdown_workspace import (
    MarkdownConfirmRequest,
    MarkdownOntologyWorkspace,
)
from app.features.nl2sql.ontology_published_context import published_context
from app.features.nl2sql.ontology_router import OntologyApiRuntime, OntologyMarkdownDraftPatch
from app.features.nl2sql.ontology_service import OntologyNotFoundError, OntologyVersionConflictError
from app.features.nl2sql.ontology_unified_model import (
    CONCEPT_ORDER,
    DEFINITIONS,
    merge_definitions,
    project_graph,
    render_concepts,
)
from app.settings import get_settings


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_auth_enabled", False)
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")


def all_concepts() -> list[BusinessDefinition]:
    base = [d.model_dump(mode="json") for d in model()]
    extra: list[dict[str, Any]] = [
        dict(
            kind="link_type",
            api_name="Related",
            source="Order",
            target="Order",
            cardinality="one_to_many",
        ),
        dict(kind="interface", api_name="Identified"),
        dict(
            kind="function", api_name="Calculate", return_type="number", dependencies=["Order.id"]
        ),
        dict(
            kind="action_type",
            api_name="Approve",
            object_type="Order",
            affected_properties=["Order.id"],
        ),
        dict(kind="shared_property", api_name="SharedId", data_type="integer"),
        dict(kind="value_type", api_name="IdType", data_type="integer"),
        dict(
            kind="enumeration",
            api_name="Codes",
            property="Order.id",
            values=[{"code": "1", "label_ja": "有効"}],
        ),
        dict(
            kind="metric",
            api_name="Total",
            dependencies=["Order.id"],
            expression_sql="COUNT(APP.ORDERS.ID)",
            aggregation="count",
            grain=["Order.id"],
            unit="件",
            null_policy_ja="NULL を除外",
            time_policy_ja="全期間",
        ),
        dict(kind="business_rule", api_name="Valid", applies_to=["Order"]),
        dict(
            kind="business_event",
            api_name="Created",
            object_type="Order",
            timestamp_property="Order.id",
        ),
        dict(kind="object_set", api_name="Orders", object_type="Order"),
    ]
    return DEFINITIONS.validate_python(
        base + [dict(name_ja=e["api_name"], description_ja="業務定義", **e) for e in extra]
    )


def test_all_thirteen_types_share_identity_in_markdown_and_graph() -> None:
    rt, _ = runtime()
    definitions, conflicts = merge_definitions("sales", all_concepts())
    assert not conflicts
    assert tuple(d.kind for d in definitions) == CONCEPT_ORDER
    markdown = render_concepts(definitions)
    graph = project_graph(rt.profile_view("sales")[1], definitions)
    for d in definitions:
        assert d.api_name in markdown
        if d.kind == "link_type":
            assert (
                next(e for e in graph.edges if e.id == d.id).metadata["definition"]["kind"]
                == d.kind
            )
        else:
            node = next(n for n in graph.nodes if n.id == d.id)
            assert node.kind.value == d.kind
            assert node.metadata["definition"]["api_name"] == d.api_name
    assert not any(n.kind.value == "business_entity" for n in graph.nodes)


def test_identity_dedup_and_conflicts_do_not_merge_same_physical_object() -> None:
    first, prop = model()
    duplicate = first.model_copy(update={"aliases": ["注文"]})
    other = first.model_copy(update={"api_name": "Another", "name_ja": "別の業務概念"})
    values, conflicts = merge_definitions("sales", [first, prop, duplicate, other])
    assert len(values) == 3 and not conflicts
    assert next(v for v in values if v.api_name == "Order").aliases == ["注文"]
    changed = prop.model_copy(update={"data_type": "string"})
    _, conflicts = merge_definitions("sales", [prop, changed])
    assert conflicts and "データ型" in conflicts[0]


def test_legacy_names_sharing_a_physical_table_remain_distinct_and_order_independent() -> None:
    from app.features.nl2sql.ontology_models import OntologyNodeKind
    from app.features.nl2sql.ontology_unified_model import legacy_definitions

    rt, _ = runtime()
    base = rt.profile_view("sales")[1]
    definitions, _ = merge_definitions("sales", model())
    graph = project_graph(base, definitions)
    obj = next(n for n in graph.nodes if n.kind.value == "object_type")
    legacy = obj.model_copy(
        update={
            "kind": OntologyNodeKind.BUSINESS_ENTITY,
            "metadata": {},
            "technical_name": "APP.ORDERS",
        }
    )
    other = legacy.model_copy(update={"id": "different-business-identity"})
    graphs = [
        graph.model_copy(update={"nodes": nodes, "edges": []})
        for nodes in ([legacy, other], [other, legacy])
    ]
    converted = [merge_definitions("sales", legacy_definitions(g))[0] for g in graphs]
    assert len(converted[0]) == 2
    assert {d.id: d.api_name for d in converted[0]} == {d.id: d.api_name for d in converted[1]}


def test_real_build_worker_renders_all_thirteen_once_before_saving() -> None:
    from test_nl2sql_ontology_build import _FakeEnterpriseAiClient, _wait_for_job

    from app.features.nl2sql.ontology_build import OntologyBuildService

    rt, legacy = runtime()
    legacy._enterprise_ai_client = _FakeEnterpriseAiClient(
        json.dumps({"definitions": [d.model_dump(mode="json") for d in all_concepts()]})
    )
    build = OntologyBuildService(rt)
    queued = build.start("sales", business_text="受注の業務定義")
    build.run_persisted(queued.id)
    job = _wait_for_job(build, queued.id)
    assert job.status == "succeeded", job.error_message_ja
    assert [c.kind for c in job.concept_coverage] == list(CONCEPT_ORDER)
    assert all(c.count == 1 for c in job.concept_coverage)
    assert job.definition_phases[-2].name == "markdown"
    assert job.definition_phases[-1].name == "save"
    assert job.definition_phases[-2].finished_at is not None
    assert job.definition_phases[-1].started_at is not None
    assert job.definition_phases[-2].finished_at <= job.definition_phases[-1].started_at
    for d in all_concepts():
        assert job.markdown_output.count(f"(`{d.api_name}`)") == 1


class Parser:
    def __init__(self, definitions: list[BusinessDefinition]) -> None:
        self.definitions = definitions
        self.calls = 0
        self.lines: list[dict[str, Any]] = [
            {
                "start_line": 1,
                "end_line": 1,
                "disposition": "definition",
                "definition_api_names": [d.api_name for d in definitions],
            }
        ]

    def is_configured(self) -> bool:
        return True

    def generate(self, **kwargs: Any) -> str:
        self.calls += 1
        return json.dumps(
            {
                "definitions": [d.model_dump(mode="json") for d in self.definitions],
                "coverage": [],
                "lines": self.lines,
            }
        )


def prepared_workspace() -> (
    tuple[OntologyApiRuntime, MarkdownOntologyWorkspace, Parser, dict[str, Any]]
):
    rt, _ = runtime()
    base = rt.profile_view("sales")[1]
    rt.create_build_markdown_draft(
        profile_id="sales",
        base_revision_id=base.revision.id,
        payloads=[],
        titles=[],
        markdown="受注を受注番号で識別する。",
        note="確認",
    )
    parser = Parser(model())
    rt.legacy_service._enterprise_ai_client = parser
    svc = MarkdownOntologyWorkspace(rt)
    state = rt.ontology_markdown_state("sales")
    preparation = svc.prepare("sales", state.draft_etag, "prepare-one", None)
    svc.run_preparation("sales", preparation["id"])
    result = svc.preparation("sales", preparation["id"])
    return rt, svc, parser, result


def test_publish_uses_confirmed_snapshot_without_second_llm_and_keeps_history() -> None:
    rt, svc, parser, preparation = prepared_workspace()
    assert preparation["status"] == "ready", preparation
    req = MarkdownConfirmRequest(
        preparation_id=preparation["id"], draft_etag=preparation["draft_etag"], confirmed=True
    )
    job = svc.publish("sales", req, "publish-one", None)
    assert parser.calls == 1
    assert svc.publish("sales", req, "publish-one", None).id == job.id
    state = rt.ontology_markdown_state("sales")
    assert state.published_markdown == preparation["markdown"]
    view, graph = rt.profile_view("sales")
    assert graph.revision.id == job.revision_id
    assert any(n.kind.value == "object_type" for n in graph.nodes)
    assert published_context(rt, "sales", job.revision_id) == preparation["markdown"]
    assert rt.profile_view("sales")[1].revision.id == job.revision_id  # repeated read
    assert rt.ontology_revision(job.revision_id).revision.id == job.revision_id
    with pytest.raises(OntologyNotFoundError):
        svc.snapshot("support", job.id)


def test_historical_sql_context_keeps_original_markdown_after_new_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import ontology_router
    from app.features.nl2sql.models import JobCreateRequest
    from app.features.nl2sql.service import Nl2SqlService
    from app.features.nl2sql.store import MemoryNl2SqlStore

    rt, svc, _, first = prepared_workspace()
    old = svc.publish(
        "sales",
        MarkdownConfirmRequest(
            preparation_id=first["id"], draft_etag=first["draft_etag"], confirmed=True
        ),
        "original-context",
        None,
    )
    edited = rt.save_ontology_markdown_draft(
        "sales",
        OntologyMarkdownDraftPatch(
            markdown="新しい受注の公開定義",
            base_etag=rt.ontology_markdown_state("sales").draft_etag,
        ),
    )
    next_check = svc.prepare("sales", edited.draft_etag, "next-context", None)
    svc.run_preparation("sales", next_check["id"])
    latest = svc.publish(
        "sales",
        MarkdownConfirmRequest(
            preparation_id=next_check["id"],
            draft_etag=edited.draft_etag,
            expected_head=old.id,
            confirmed=True,
        ),
        "next-publication",
        None,
    )
    monkeypatch.setattr(ontology_router, "ontology_runtime", rt)
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    request = JobCreateRequest(profile_id="sales", question="受注", use_ontology_context=True)
    profile = rt._strict_profile("sales")
    assert (
        service._job_published_ontology_markdown(
            request=request, profile=profile, business_release_id=old.id
        )
        == first["markdown"]
    )
    assert (
        service._job_published_ontology_markdown(
            request=request, profile=profile, business_release_id=latest.id
        )
        == edited.draft_markdown
    )
    assert (
        service._job_published_ontology_markdown(
            request=request, profile=profile, business_release_id=""
        )
        is None
    )


def test_edit_invalidates_confirmation_and_parse_omission_blocks_publish() -> None:
    rt, svc, parser, preparation = prepared_workspace()
    rt.save_ontology_markdown_draft(
        "sales",
        OntologyMarkdownDraftPatch(markdown="新しい本文", base_etag=preparation["draft_etag"]),
    )
    with pytest.raises(OntologyVersionConflictError):
        svc.publish(
            "sales",
            MarkdownConfirmRequest(
                preparation_id=preparation["id"],
                draft_etag=preparation["draft_etag"],
                confirmed=True,
            ),
            "key",
            None,
        )
    assert svc.head("sales")["snapshot_id"] == ""
    state = rt.ontology_markdown_state("sales")
    parser.lines = []
    fresh = svc.prepare("sales", state.draft_etag, "second", None)
    svc.run_preparation("sales", fresh["id"])
    assert svc.preparation("sales", fresh["id"])["status"] == "failed"


def test_atomic_failure_leaves_previous_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    rt, svc, _, preparation = prepared_workspace()
    req = MarkdownConfirmRequest(
        preparation_id=preparation["id"], draft_etag=preparation["draft_etag"], confirmed=True
    )

    def fail(*args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError("storage failure")

    monkeypatch.setattr(svc.store, "save_documents_atomic", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        svc.publish("sales", req, "failed", None)
    assert svc.head("sales")["snapshot_id"] == ""
    assert rt.ontology_markdown_state("sales").published_markdown == ""


def test_publication_rechecks_head_after_expensive_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, svc, _, prepared = prepared_workspace()
    original = svc._validate_confirmation
    calls = 0

    def validate_then_change(*args: Any) -> None:
        nonlocal calls
        original(*args)
        calls += 1
        if calls == 2:
            monkeypatch.setattr(svc, "head", lambda _: {"snapshot_id": "concurrent", "etag": "new"})

    monkeypatch.setattr(svc, "_validate_confirmation", validate_then_change)
    with pytest.raises(OntologyVersionConflictError):
        svc.publish(
            "sales",
            MarkdownConfirmRequest(
                preparation_id=prepared["id"], draft_etag=prepared["draft_etag"], confirmed=True
            ),
            "race",
            None,
        )
    assert not any(
        d["artifact_type"] == "ontology_markdown_snapshot"
        for d in svc.store.list_artifacts("profile-ontology:sales")
    )


def test_concept_references_ground_function_and_link_names_in_same_profile() -> None:
    from app.features.nl2sql.ontology_catalog import retrieve_ontology_nodes

    rt, svc, parser, original = prepared_workspace()
    parser.definitions = all_concepts()
    parser.lines[0]["definition_api_names"] = [d.api_name for d in parser.definitions]
    preparation = svc.prepare("sales", original["draft_etag"], "references", None)
    svc.run_preparation("sales", preparation["id"])
    svc.publish(
        "sales",
        MarkdownConfirmRequest(
            preparation_id=preparation["id"], draft_etag=original["draft_etag"], confirmed=True
        ),
        "references-publish",
        None,
    )
    view, graph = rt.profile_view("sales")
    for question in ["Calculate", "Related"]:
        hits = retrieve_ontology_nodes(question, graph, view)
        object_ids = {n.id for n in graph.nodes if n.kind.value == "object_type"}
        assert object_ids & {h.node_id for h in hits}


def test_data_checks_keep_sql_permission_and_failed_checks_block_publication() -> None:
    from fastapi import HTTPException
    from test_nl2sql_ontology_access import _principal

    from app.features.nl2sql.ontology_definitions import DefinitionDataValidationRequest
    from app.features.nl2sql.ontology_markdown_workspace import PREPARATION
    from app.features.nl2sql.ontology_service import OntologyGateBlockedError

    _, svc, _, prepared = prepared_workspace()
    with pytest.raises(HTTPException) as denied:
        svc.validate_data(
            "sales",
            prepared["id"],
            DefinitionDataValidationRequest(confirmed=True),
            _principal({"sales"}, profile_manager=True),
        )
    assert denied.value.status_code == 403
    prepared["data_report"] = {"errors": 1, "acceptance_cases": [{"passed": False}]}
    svc._write("sales", prepared["id"], PREPARATION, prepared)
    with pytest.raises(OntologyGateBlockedError) as blocked:
        svc.publish(
            "sales",
            MarkdownConfirmRequest(
                preparation_id=prepared["id"], draft_etag=prepared["draft_etag"], confirmed=True
            ),
            "failed-data",
            None,
        )
    assert blocked.value.code == "MARKDOWN_DATA_VALIDATION_FAILED"
    assert svc.head("sales")["snapshot_id"] == ""


@pytest.mark.asyncio
async def test_retired_capability_mutations_check_access_and_preserve_history() -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from test_nl2sql_ontology_access import _principal

    from app.features.nl2sql.ontology_capability_router import create_capability_router
    from app.features.nl2sql.ontology_router import _raise_domain_error

    rt, _ = runtime()
    app = FastAPI()
    actor = _principal({"sales"}, profile_manager=True)

    @app.middleware("http")
    async def principal(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.principal = actor
        return await call_next(request)

    app.include_router(create_capability_router(lambda: rt, _raise_domain_error))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        root = "/profiles/sales/ontology-capabilities"
        for suffix, body in [
            ("invoke", {"release_id": "historical"}),
            ("preview", {"release_id": "historical"}),
            ("execute", {"preview_id": "prior", "confirmed": True}),
        ]:
            response = await client.post(
                root + "/function/" + suffix, json=body, headers={"Idempotency-Key": "no-execution"}
            )
            assert response.status_code == 410, response.text
        assert (await client.get(root)).status_code == 200
        actor = _principal({"other"})
        denied = await client.post(
            root + "/function/invoke",
            json={"release_id": "historical"},
            headers={"Idempotency-Key": "denied"},
        )
        assert denied.status_code == 403


def test_all_types_publish_edit_and_keep_the_original_snapshot() -> None:
    rt, svc, parser, original = prepared_workspace()
    parser.definitions = all_concepts()
    parser.lines[0]["definition_api_names"] = [d.api_name for d in parser.definitions]
    prepared = svc.prepare("sales", original["draft_etag"], "all-types", None)
    svc.run_preparation("sales", prepared["id"])
    ready = svc.preparation("sales", prepared["id"])
    assert ready["status"] == "ready", ready["findings"]
    assert [c["kind"] for c in ready["coverage"]] == list(CONCEPT_ORDER)
    request = MarkdownConfirmRequest(
        preparation_id=ready["id"], draft_etag=ready["draft_etag"], confirmed=True
    )
    first = svc.publish("sales", request, "all-publish", None)
    old = svc.snapshot("sales", first.id)
    graph = rt.profile_view("sales")[1]
    assert len([n for n in graph.nodes if n.metadata.get("definition")]) == 12
    assert len([e for e in graph.edges if e.kind.value == "link_type"]) == 1
    changed = rt.save_ontology_markdown_draft(
        "sales",
        OntologyMarkdownDraftPatch(
            markdown="受注の定義を更新する。",
            base_etag=rt.ontology_markdown_state("sales").draft_etag,
        ),
    )
    assert changed.draft_revision is not None
    assert changed.draft_version is not None and changed.published_version is not None
    assert changed.draft_revision.id != ready["source_revision_id"]
    assert changed.draft_version > changed.published_version
    assert svc.snapshot("sales", first.id) == old
    assert published_context(rt, "sales", first.id) == original["markdown"]
    assert original["markdown"] not in published_context(rt, "sales", first.id, {})


def test_migration_is_previewed_idempotent_and_recovers_after_response_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql.ontology_markdown_workspace import MarkdownMigrationRequest

    rt, svc, _, original = prepared_workspace()
    svc.save_build(
        profile_id="sales",
        job_id="legacy",
        definitions=model(),
        source_revision_id=original["source_revision_id"],
        schema_fingerprint="schema",
    )
    preview = svc.migration_preview("sales", None)
    assert original["markdown"] in preview["markdown"]
    assert not preview["applied"] and not svc.head("sales")["snapshot_id"]
    request = MarkdownMigrationRequest(preview_id=preview["id"], draft_etag=preview["draft_etag"])
    write = svc._write

    def lost_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if args[2] == "ontology_markdown_migration":
            raise RuntimeError("response lost after draft saved")
        return write(*args, **kwargs)

    monkeypatch.setattr(svc, "_write", lost_result)
    with pytest.raises(RuntimeError):
        svc.apply_migration("sales", request, None)
    saved = rt.ontology_markdown_state("sales").draft_markdown
    monkeypatch.setattr(svc, "_write", write)
    assert svc.apply_migration("sales", request, None).draft_markdown == saved
    assert svc.migration_preview("sales", None)["applied"]
    assert not svc.head("sales")["snapshot_id"]
