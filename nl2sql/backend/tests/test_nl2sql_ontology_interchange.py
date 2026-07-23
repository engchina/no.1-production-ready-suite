"""Ontology 連携(業種テンプレート / OWL RDF import・export)のテスト。

Playground 同様の round-trip 忠実性(export → import で名前/aliases/cardinality が
保存されること)と、未解決エンティティの BUSINESS_TERM 縮退を検証する。
"""

from __future__ import annotations

import pytest
from rdflib import Graph, Namespace
from rdflib.namespace import OWL, RDF

from app.features.nl2sql.models import (
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.ontology_interchange import (
    OntologyTemplate,
    RdfImportError,
    apply_template,
    convert_rdf_graph,
    export_ontology_rdf,
    get_template,
    import_rdf,
    load_templates,
    parse_rdf_graph,
)
from app.features.nl2sql.ontology_models import (
    OntologyProposalKind,
    RelationshipCardinality,
)
from app.features.nl2sql.ontology_router import OntologyApiRuntime, OntologyPublishRequest
from app.features.nl2sql.ontology_store import InMemoryOntologyStore

_ONT = Namespace("urn:nl2sql:ontology:")


class _FakeLegacyNl2SqlService:
    def __init__(self) -> None:
        self._enterprise_ai_client = None
        self.profile = Nl2SqlProfile(
            id="sales",
            name="販売分析",
            allowed_tables=["APP.ORDERS", "APP.CUSTOMERS"],
            default_row_limit=100,
        )
        self.catalog = SchemaCatalog(
            refreshed_at="2026-07-11T00:00:00Z",
            tables=[
                SchemaTable(
                    table_name="ORDERS",
                    logical_name="受注",
                    owner="APP",
                    columns=[
                        SchemaColumn(column_name="ID", logical_name="受注 ID", data_type="NUMBER"),
                        SchemaColumn(
                            column_name="CUSTOMER_ID", logical_name="顧客 ID", data_type="NUMBER"
                        ),
                    ],
                ),
                SchemaTable(
                    table_name="CUSTOMERS",
                    logical_name="顧客",
                    owner="APP",
                    columns=[
                        SchemaColumn(column_name="ID", logical_name="顧客 ID", data_type="NUMBER"),
                        SchemaColumn(
                            column_name="NAME", logical_name="顧客名", data_type="VARCHAR2"
                        ),
                    ],
                ),
            ],
        )

    def get_catalog(self) -> SchemaCatalog:
        return self.catalog

    def get_profile(self, profile_id: str) -> Nl2SqlProfile:
        if profile_id != self.profile.id:
            raise ValueError("profile not found")
        return self.profile


@pytest.fixture
def runtime() -> OntologyApiRuntime:
    return OntologyApiRuntime(
        legacy_service=_FakeLegacyNl2SqlService(), store=InMemoryOntologyStore()
    )


_TEST_TEMPLATE = OntologyTemplate.model_validate(
    {
        "id": "test_sales",
        "metadata": {"name_ja": "テスト販売", "icon": "🧪", "category": "test"},
        "entities": [
            {
                "key": "customer",
                "business_name_ja": "顧客",
                "description_ja": "主識別子は顧客ID。",
                "aliases": ["得意先"],
                "object_name_hint": "CUSTOMERS",
            },
            {
                "key": "order",
                "business_name_ja": "注文",
                "aliases": ["受注"],
                "object_name_hint": "ORDERS",
            },
            {
                "key": "product",
                "business_name_ja": "商品",
                "aliases": ["品目"],
                "object_name_hint": "PRODUCTS",
            },
        ],
        "relationships": [
            {
                "source": "customer",
                "target": "order",
                "relationship_name_ja": "注文する",
                "cardinality": "one_to_many",
                "join_hints": [{"left_column": "ID", "right_column": "CUSTOMER_ID"}],
            },
            {
                "source": "product",
                "target": "order",
                "relationship_name_ja": "含まれる",
                "cardinality": "one_to_many",
                "join_hints": [{"left_column": "ID", "right_column": "PRODUCT_ID"}],
            },
        ],
        "terms": [{"business_name_ja": "客単価", "aliases": ["平均購入額"]}],
    }
)


# --- 同梱テンプレート -------------------------------------------------------------------------


def test_bundled_templates_load_and_validate() -> None:
    templates = load_templates()
    assert [template.id for template in templates] == [
        "finance",
        "healthcare",
        "hr",
        "manufacturing",
        "retail",
    ]
    for template in templates:
        assert template.metadata.name_ja
        assert template.entities
        keys = {entity.key for entity in template.entities}
        for relationship in template.relationships:
            assert relationship.source in keys
            assert relationship.target in keys
            assert relationship.cardinality is not RelationshipCardinality.UNKNOWN
            assert relationship.join_hints
    assert get_template("retail") is not None
    assert get_template("missing") is None


# --- テンプレート適用 -------------------------------------------------------------------------


def test_apply_template_resolves_entities_and_degrades_unresolved_to_terms(
    runtime: OntologyApiRuntime,
) -> None:
    conversion, proposal_ids = apply_template(
        runtime,
        profile_id="sales",
        template=_TEST_TEMPLATE,
        overrides={},
        dry_run=False,
    )

    assert conversion.resolved == {"customer": "APP.CUSTOMERS", "order": "APP.ORDERS"}
    assert conversion.unresolved == ["product"]
    # 未解決 product + terms(客単価)が BUSINESS_TERM へ縮退する
    assert conversion.term_count == 2
    # product が端点の関係は提案化されない
    assert any("含まれる" in warning for warning in conversion.warnings)
    assert proposal_ids

    proposals = runtime.list_profile_proposals("sales")
    assert {proposal.id for proposal in proposals} == set(proposal_ids)
    kinds = [proposal.kind for proposal in proposals]
    assert OntologyProposalKind.RELATIONSHIP in kinds
    assert OntologyProposalKind.MAPPING in kinds
    assert OntologyProposalKind.ALIAS in kinds


def test_apply_template_dry_run_registers_nothing(runtime: OntologyApiRuntime) -> None:
    conversion, proposal_ids = apply_template(
        runtime,
        profile_id="sales",
        template=_TEST_TEMPLATE,
        overrides={},
        dry_run=True,
    )
    assert proposal_ids == []
    assert conversion.drafts
    assert runtime.list_profile_proposals("sales") == []


def test_apply_template_override_redirects_resolution(runtime: OntologyApiRuntime) -> None:
    conversion, _ = apply_template(
        runtime,
        profile_id="sales",
        template=_TEST_TEMPLATE,
        overrides={"product": "APP.ORDERS"},
        dry_run=True,
    )
    assert conversion.resolved["product"] == "APP.ORDERS"
    assert conversion.unresolved == []


# --- OWL RDF export / import(round-trip)---------------------------------------------------


def _publish_template_ontology(runtime: OntologyApiRuntime) -> str:
    """テンプレートを適用し、全提案を承認して publish した revision id を返す。"""

    _conversion, proposal_ids = apply_template(
        runtime,
        profile_id="sales",
        template=_TEST_TEMPLATE,
        overrides={},
        dry_run=False,
    )
    draft = None
    for proposal_id in proposal_ids:
        review = runtime.accept_proposal(proposal_id)
        draft = review.draft or draft
    assert draft is not None
    published = runtime.publish_ontology_revision(
        draft.revision.id,
        OntologyPublishRequest(etag=draft.revision.etag),
    )
    return str(published.revision.id)


def test_export_rdfxml_contains_cardinality_and_reparses(runtime: OntologyApiRuntime) -> None:
    revision_id = _publish_template_ontology(runtime)
    ontology = runtime.ontology_revision(revision_id)

    content = export_ontology_rdf(ontology, format="rdfxml")
    graph = Graph()
    graph.parse(data=content, format="xml")

    assert any(str(s) for s in graph.subjects(RDF.type, OWL.Class))
    cardinalities = {str(value) for value in graph.objects(None, _ONT.cardinality)}
    assert "one_to_many" in cardinalities
    join_conditions = {str(value) for value in graph.objects(None, _ONT.joinCondition)}
    assert "APP.CUSTOMERS.ID=APP.ORDERS.CUSTOMER_ID" in join_conditions
    physical_objects = {str(value) for value in graph.objects(None, _ONT.physicalObject)}
    assert {"APP.CUSTOMERS", "APP.ORDERS"} <= physical_objects

    turtle = export_ontology_rdf(ontology, format="turtle")
    Graph().parse(data=turtle, format="turtle")


def test_rdf_round_trip_preserves_names_aliases_and_cardinality(
    runtime: OntologyApiRuntime,
) -> None:
    revision_id = _publish_template_ontology(runtime)
    ontology = runtime.ontology_revision(revision_id)
    content = export_ontology_rdf(ontology, format="rdfxml").encode("utf-8")

    conversion, counts, proposal_ids = import_rdf(
        runtime,
        profile_id="sales",
        content=content,
        filename="round-trip.rdf",
        terms_fallback=True,
        dry_run=True,
    )

    assert proposal_ids == []
    assert counts["classes"] >= 2
    assert conversion.resolved.get("顧客") == "APP.CUSTOMERS"
    assert conversion.resolved.get("注文") == "APP.ORDERS"

    node_upserts = [
        node
        for draft in conversion.drafts
        if draft.kind == OntologyProposalKind.MAPPING
        for node in draft.payload.values.get("node_upserts", [])
    ]
    entity_by_name = {node["business_name_ja"]: node for node in node_upserts}
    assert "得意先" in entity_by_name["顧客"]["aliases"]

    relationship_drafts = [
        draft for draft in conversion.drafts if draft.kind == OntologyProposalKind.RELATIONSHIP
    ]
    assert relationship_drafts, "join 注釈付き export から関係提案が復元されること"
    edges = relationship_drafts[0].payload.values["edge_upserts"]
    business_edges = [edge for edge in edges if edge["kind"] == "business_relationship"]
    assert business_edges[0]["relationship_name_ja"] == "注文する"
    assert business_edges[0]["cardinality"] == "one_to_many"


def test_import_external_rdf_without_annotations_degrades_safely(
    runtime: OntologyApiRuntime,
) -> None:
    """Playground 形式(join 注釈なし)の外部 RDF は warning + term 縮退で受ける。"""

    external = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
    xmlns:owl="http://www.w3.org/2002/07/owl#"
    xmlns:ont="http://example.org/ont/">
  <owl:Class rdf:about="http://example.org/ont/Customers">
    <rdfs:label xml:lang="ja">顧客</rdfs:label>
  </owl:Class>
  <owl:Class rdf:about="http://example.org/ont/Supplier">
    <rdfs:label xml:lang="ja">仕入先</rdfs:label>
    <rdfs:comment>商品を供給する会社</rdfs:comment>
  </owl:Class>
  <owl:ObjectProperty rdf:about="http://example.org/ont/supplies">
    <rdfs:label xml:lang="ja">供給する</rdfs:label>
    <rdfs:domain rdf:resource="http://example.org/ont/Supplier"/>
    <rdfs:range rdf:resource="http://example.org/ont/Customers"/>
    <ont:cardinality>one-to-many</ont:cardinality>
  </owl:ObjectProperty>
</rdf:RDF>
"""
    graph = parse_rdf_graph(external.encode("utf-8"), filename="external.rdf")
    view, ontology = runtime.profile_view("sales")
    conversion, counts = convert_rdf_graph(
        graph,
        ontology=ontology,
        view=view,
        job_id="rdf_import:test",
        source_name="external.rdf",
    )

    # Customers は local name から APP.CUSTOMERS へ解決、Supplier は term へ縮退
    assert conversion.resolved.get("顧客") == "APP.CUSTOMERS"
    assert conversion.unresolved == ["仕入先"]
    assert counts["term_proposals"] == 1
    # 端点未解決の関係は提案化されず warning になる(既存変換の挙動)
    assert any("SUPPLIER" in warning and "関係候補" in warning for warning in conversion.warnings)
    assert not any(
        draft.kind == OntologyProposalKind.RELATIONSHIP for draft in conversion.drafts
    )


def test_parse_rdf_graph_rejects_bad_inputs() -> None:
    with pytest.raises(RdfImportError):
        parse_rdf_graph(b"<rdf/>", filename="ontology.json")
    with pytest.raises(RdfImportError):
        parse_rdf_graph(b"this is not xml", filename="broken.rdf")
    with pytest.raises(RdfImportError):
        parse_rdf_graph(b"x" * (5 * 1024 * 1024 + 1), filename="huge.rdf")


def test_cardinality_annotation_accepts_playground_separator() -> None:
    from app.features.nl2sql.ontology_interchange import _parse_cardinality

    assert _parse_cardinality("one-to-many") is RelationshipCardinality.ONE_TO_MANY
    assert _parse_cardinality("many_to_one") is RelationshipCardinality.MANY_TO_ONE
    assert _parse_cardinality("nonsense") is RelationshipCardinality.UNKNOWN
