"""Issue #497: 統合後の Profile 所有境界・利用契約・旧定義保持。"""

import json
from types import SimpleNamespace

import pytest
from test_nl2sql_markdown_unification import all_concepts, prepared_workspace
from test_nl2sql_ontology_build import _FakeEnterpriseAiClient
from test_nl2sql_ontology_definitions import runtime
from test_nl2sql_ontology_workspace import model

from app.features.nl2sql.models import AllowedObjects
from app.features.nl2sql.ontology_build import OntologyBuildService
from app.features.nl2sql.ontology_catalog import (
    migrate_profile_ontology_view,
    retrieve_ontology_nodes,
)
from app.features.nl2sql.ontology_clarification import (
    _question_for_ambiguity,
    apply_clarification_answer,
    enrich_guided_intent,
)
from app.features.nl2sql.ontology_definitions import InterfaceDefinition, InterfaceImplementation
from app.features.nl2sql.ontology_markdown_workspace import (
    MarkdownConfirmRequest,
    MarkdownOntologyWorkspace,
)
from app.features.nl2sql.ontology_models import (
    BusinessRuleDefinition,
    BusinessRuleExpression,
    ClarificationAnswer,
    IntentAmbiguity,
    IntentEntity,
    IntentMetric,
    OntologyNode,
    OntologyProvenance,
    QuerySession,
    QuestionIntentGraph,
)
from app.features.nl2sql.ontology_router import (
    OntologyApiRuntime,
    QueryRuntimeContext,
    QuerySessionApiCreate,
)
from app.features.nl2sql.ontology_unified_model import (
    legacy_definitions,
    merge_definitions,
    project_graph,
    render_concepts,
)
from app.settings import get_settings


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "app_auth_enabled", False)
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_worker_mode", "external")


@pytest.mark.parametrize("restart", [False, True])
def test_builds_and_migration_do_not_copy_another_profiles_concepts(
    monkeypatch: pytest.MonkeyPatch,
    restart: bool,
) -> None:
    rt, legacy = runtime()
    profiles = {
        name: legacy.profile.model_copy(update={"id": name}) for name in ["sales", "support"]
    }
    monkeypatch.setattr(legacy, "get_profile", lambda profile_id: profiles[profile_id])
    service = OntologyBuildService(rt)
    for profile_id, concept in [
        ("sales", "SalesPrivate"),
        ("support", "SupportPrivate"),
        ("sales", "SalesNew"),
    ]:
        client = _FakeEnterpriseAiClient(
            json.dumps(
                {
                    "definitions": [
                        {
                            "kind": "business_rule",
                            "api_name": concept,
                            "name_ja": concept,
                            "description_ja": f"{profile_id} 専用の内部規則",
                        }
                    ]
                }
            )
        )
        legacy._enterprise_ai_client = client
        queued = service.start(profile_id, business_text="業務定義を構築")
        service.run_persisted(queued.id)
        job = service.get(queued.id)
        assert job is not None and job.status == "succeeded", job
        other = "SupportPrivate" if profile_id == "sales" else "SalesPrivate"
        assert other not in job.markdown_output
        assert all(other not in prompt for prompt in client.calls)
        assert concept in job.markdown_output
        if concept == "SalesPrivate":
            preview = MarkdownOntologyWorkspace(rt).migration_preview("support", None)
            assert "SalesPrivate" not in preview["markdown"]
        if concept == "SalesNew":
            assert "SalesPrivate" in job.markdown_output  # 同じ Profile の定義は保持する。
        if restart:
            rt = OntologyApiRuntime(legacy_service=legacy, store=rt.store)
            service = OntologyBuildService(rt)


def test_object_type_selection_is_applied_and_physical_duplicate_is_removed() -> None:
    rt, legacy = runtime()
    definitions, _ = merge_definitions("sales", model())
    graph = project_graph(
        rt.profile_view("sales")[1], definitions, "ontology_markdown_snapshot_test"
    )
    view = migrate_profile_ontology_view(legacy.profile, graph, strict=False)
    ambiguity = IntentAmbiguity(
        id="choose", code="ontology_term_ambiguous", message_ja="対象を選択", options=["Order"]
    )
    intent = QuestionIntentGraph(
        profile_view_id=view.id,
        ontology_revision_id=graph.revision.id,
        question_original="対象一覧",
        question_effective="対象一覧",
        ambiguities=[ambiguity],
    )
    question = _question_for_ambiguity(ambiguity, intent, graph, view)
    updated = apply_clarification_answer(
        intent,
        question,
        ClarificationAnswer(question_id=question.id, selected_option_ids=[question.options[0].id]),
        graph,
    )
    assert updated.ambiguities[0].resolved
    assert len(updated.entities) == 1
    assert updated.entities[0].ontology_node_id == definitions[0].id
    physical_id = updated.entities[0].physical_object_ids[0]
    updated.entities.append(
        IntentEntity(id="physical", ontology_node_id=physical_id, name_ja="受注表")
    )
    assert len(enrich_guided_intent(updated, graph).entities) == 1


def test_published_snapshot_session_loads_without_global_revision_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "nl2sql_ontology_profile_confirmation_required", False)
    rt, workspace, _, preparation = prepared_workspace()
    job = workspace.publish(
        "sales",
        MarkdownConfirmRequest(
            preparation_id=preparation["id"],
            draft_etag=preparation["draft_etag"],
            confirmed=True,
        ),
        "publish-session",
        None,
    )
    monkeypatch.setattr(rt.legacy_service, "_enterprise_ai_client", None)
    monkeypatch.setattr(
        rt.legacy_service,
        "resolve_allowed_objects",
        lambda *_: AllowedObjects(table_names=["APP.ORDERS"], enforce_table_scope=True),
        raising=False,
    )
    created = rt.create_session(
        QuerySessionApiCreate(
            profile_id="sales", question="受注の一覧", clarification_mode="guided"
        )
    )
    assert created.session.ontology_revision_id == job.revision_id
    assert created.clarification is not None
    assert any(n.kind.value == "object_type" for n in created.ontology_graph.nodes)
    restored = OntologyApiRuntime(legacy_service=rt.legacy_service, store=rt.store)
    loaded = restored.get_session(created.session.id)
    assert loaded.ontology_graph == created.ontology_graph
    assert loaded.session.business_release_id == job.revision_id
    assert (
        job.revision_id not in restored._ontologies
    )  # Profile snapshot を global 公開候補にしない。
    global_id = restored._sync_ontology().revision.id
    global_nodes_before = restored.store.list_documents("nodes", {"revision_id": global_id})
    snapshot_before = restored.store.get_artifact(job.revision_id)
    monkeypatch.setattr(
        rt.legacy_service,
        "_embedding_client",
        SimpleNamespace(
            is_configured=lambda: True, embed_texts=lambda texts: [[1.0, 0.0] for _ in texts]
        ),
        raising=False,
    )
    concept = next(n for n in loaded.ontology_graph.nodes if n.kind.value == "object_type")
    assert restored._embedding_hits(job.revision_id, "受注", [concept], 5)
    assert restored.store.list_documents("nodes", {"revision_id": global_id}) == global_nodes_before
    assert restored.store.get_document(
        "nodes", {"revision_id": job.revision_id, "node_id": concept.id}
    )
    assert restored.store.get_artifact(job.revision_id) == snapshot_before


@pytest.mark.parametrize("old_snapshot", [False, True])
def test_sql_context_retains_published_metric_contract(old_snapshot: bool) -> None:
    rt, legacy = runtime()
    definitions, _ = merge_definitions("sales", all_concepts())
    graph = project_graph(
        rt.profile_view("sales")[1], definitions, "ontology_markdown_snapshot_test"
    )
    view = migrate_profile_ontology_view(legacy.profile, graph, strict=False)
    metric = next(n for n in graph.nodes if n.kind.value == "metric")
    if old_snapshot:
        metric.metadata["metric_definition"] = metric.metadata["definition"]
    original = json.dumps(metric.metadata, sort_keys=True)
    intent = QuestionIntentGraph(
        profile_view_id=view.id,
        ontology_revision_id=graph.revision.id,
        question_original="Total",
        question_effective="Total",
        metrics=[IntentMetric(id="metric", ontology_node_id=metric.id, name_ja="Total")],
    )
    session = QuerySession(
        id="session",
        profile_id="sales",
        profile_view_id=view.id,
        ontology_revision_id=graph.revision.id,
        original_question="Total",
        intents=[intent],
    )
    context = rt._compile_sql_generation_context(
        session=session,
        intent=intent,
        view=view,
        ontology=graph,
        runtime_context=QueryRuntimeContext(allowed_objects=AllowedObjects(), row_limit=100),
    )
    assert not context.warnings_ja
    result = context.metric_definitions[0]
    assert result.expression_sql == "COUNT(APP.ORDERS.ID)"
    assert result.aggregation == "count"
    assert result.grain_node_ids == [next(d.id for d in definitions if d.api_name == "Order.id")]
    assert result.base_column_node_ids
    assert json.dumps(metric.metadata, sort_keys=True) == original


@pytest.mark.parametrize("unresolved", [False, True])
def test_legacy_rule_keeps_statement_expression_and_conversion_diagnostics(
    unresolved: bool,
) -> None:
    rt, _ = runtime()
    definitions, _ = merge_definitions("sales", model())
    graph = project_graph(rt.profile_view("sales")[1], definitions)
    obj = next(n for n in graph.nodes if n.kind.value == "object_type")
    prop = next(n for n in graph.nodes if n.kind.value == "property")
    rule = OntologyNode(
        id="legacy-rule",
        revision_id=graph.revision.id,
        kind="business_rule",
        technical_name="PositiveId",
        business_name_ja="識別子制約",
        provenance=OntologyProvenance(source_kind="manual"),
        business_rule_definition=BusinessRuleDefinition(
            rule_kind="constraint",
            statement_ja="識別子は100以上",
            applies_to_node_ids=[obj.id],
            expression=BusinessRuleExpression(
                operator="gte", property_node_id="missing" if unresolved else prop.id, value=100
            ),
            execution_mode="shacl",
        ),
    )
    graph.nodes.append(rule)
    values = legacy_definitions(graph)
    converted = next(d for d in values if d.id == rule.id)
    assert converted.kind == "business_rule"
    markdown = render_concepts(values)
    assert "識別子は100以上" in markdown and '"value":100' in markdown
    if unresolved:
        assert not converted.predicate_sql
        assert converted.missing_information_ja
    else:
        assert converted.predicate_sql == '"APP"."ORDERS"."ID" >= 100'
        assert not converted.missing_information_ja


def test_interface_grounding_follows_inheritance_to_only_visible_implementers() -> None:
    rt, legacy = runtime()
    obj, prop = model()
    obj.implements = [InterfaceImplementation(interface="Child")]
    definitions, _ = merge_definitions(
        "sales",
        [
            obj,
            prop,
            InterfaceDefinition(api_name="Identified", name_ja="識別可能"),
            InterfaceDefinition(api_name="Child", name_ja="継承先", extends=["Identified"]),
        ],
    )
    graph = project_graph(
        rt.profile_view("sales")[1], definitions, "ontology_markdown_snapshot_test"
    )
    view = migrate_profile_ontology_view(legacy.profile, graph, strict=False)
    object_id = next(d.id for d in definitions if d.kind == "object_type")
    assert object_id in {h.node_id for h in retrieve_ontology_nodes("識別可能", graph, view)}
    restricted = view.model_copy(update={"node_ids": [n for n in view.node_ids if n != object_id]})
    assert object_id not in {
        h.node_id for h in retrieve_ontology_nodes("識別可能", graph, restricted)
    }
