"""Ontology-Playground 由来の連携機能(業種テンプレート / OWL RDF import・export)。

- テンプレート適用と RDF import は決定論的に :class:`OntologyBuildExtraction` を組み立て、
  既存の :func:`convert_extraction_to_proposals` → ``create_build_proposal`` パイプへ流す。
  生成物は必ず承認フロー(proposal → accept → draft → publish)を通る。
- export は :func:`serialize_owl_turtle` の出力(非変更)へ ``ont:cardinality`` /
  ``ont:joinCondition`` / ``ont:physicalObject`` 注釈を後付けするだけで、既存 artifact
  hash・renderer version には影響しない。
- 物理 schema へ解決できないエンティティは BUSINESS_TERM proposal(mapping 免除)+
  warning へ縮退する(docs/ontology-playground-study.md §5)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from rdflib import Graph, Namespace, URIRef
from rdflib import Literal as RdfLiteral
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from .ontology_build import (
    ProposalDraft,
    _provenance,
    _ScopeResolver,
    convert_extraction_to_proposals,
)
from .ontology_catalog import SchemaOntology
from .ontology_models import (
    OntologyBuildExtraction,
    OntologyBuildJoinConditionCandidate,
    OntologyEdgeKind,
    OntologyEntityNamingCandidate,
    OntologyNode,
    OntologyNodeKind,
    OntologyProposalKind,
    OntologyProposalPayload,
    OntologyRelationshipCandidate,
    OntologyReviewStatus,
    OntologySynonymCandidate,
    ProfileOntologyView,
    RelationshipCardinality,
)
from .ontology_semantics import serialize_owl_turtle, stable_edge_iri, stable_node_iri
from .ontology_store import stable_ontology_id

_ONT = Namespace("urn:nl2sql:ontology:")
_TEMPLATES_DIR = Path(__file__).parent / "ontology_templates"
_JOIN_OPERATORS = ("<=", ">=", "!=", "=", "<", ">")
RDF_IMPORT_MAX_BYTES = 5 * 1024 * 1024
RDF_IMPORT_EXTENSIONS = {".rdf", ".owl", ".xml", ".ttl"}


# --- 業種テンプレート契約 ---------------------------------------------------------------------


class OntologyTemplateMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_ja: str = Field(min_length=1)
    description_ja: str = ""
    icon: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)


class OntologyTemplateEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    business_name_ja: str = Field(min_length=1)
    description_ja: str = ""
    aliases: list[str] = Field(default_factory=list)
    object_name_hint: str = Field(min_length=1, description="OWNER.OBJECT または OBJECT")


class OntologyTemplateJoinHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_column: str = Field(min_length=1)
    right_column: str = Field(min_length=1)


class OntologyTemplateRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, description="entities[].key")
    target: str = Field(min_length=1, description="entities[].key")
    relationship_name_ja: str = Field(min_length=1)
    cardinality: RelationshipCardinality = RelationshipCardinality.UNKNOWN
    description_ja: str = ""
    join_hints: list[OntologyTemplateJoinHint] = Field(default_factory=list)


class OntologyTemplateTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name_ja: str = Field(min_length=1)
    description_ja: str = ""
    aliases: list[str] = Field(default_factory=list)


class OntologyTemplate(BaseModel):
    """Playground の designerTemplates 相当(決定論データ、LLM 不使用)。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    metadata: OntologyTemplateMetadata
    entities: list[OntologyTemplateEntity] = Field(default_factory=list)
    relationships: list[OntologyTemplateRelationship] = Field(default_factory=list)
    terms: list[OntologyTemplateTerm] = Field(default_factory=list)


def load_templates() -> list[OntologyTemplate]:
    """同梱テンプレートを id 順に読み込む(起動ごとに検証される静的データ)。"""

    templates = [
        OntologyTemplate.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(_TEMPLATES_DIR.glob("*.json"))
    ]
    return sorted(templates, key=lambda item: item.id)


def get_template(template_id: str) -> OntologyTemplate | None:
    return next((item for item in load_templates() if item.id == template_id), None)


# --- 共通: 変換結果とヘルパー -----------------------------------------------------------------


@dataclass
class InterchangeConversion:
    """proposal 登録前の決定論変換結果(dry_run はここまでで止める)。"""

    drafts: list[ProposalDraft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolved: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    term_count: int = 0


def _term_draft(
    *,
    revision_id: str,
    job_id: str,
    inferred_by: str,
    business_name_ja: str,
    description_ja: str,
    aliases: list[str],
) -> ProposalDraft:
    """物理 mapping 不要の BUSINESS_TERM 提案(未解決エンティティ/業務用語の縮退先)。"""

    node = OntologyNode(
        id=stable_ontology_id("business_term", inferred_by, business_name_ja),
        revision_id=revision_id,
        kind=OntologyNodeKind.BUSINESS_TERM,
        technical_name=business_name_ja,
        business_name_ja=business_name_ja,
        description_ja=description_ja,
        aliases=[*dict.fromkeys(aliases)],
        provenance=_provenance(job_id, inferred_by, description_ja),
        review_status=OntologyReviewStatus.PROPOSED,
    )
    return ProposalDraft(
        kind=OntologyProposalKind.ALIAS,
        title_ja=f"業務用語の提案: {business_name_ja}",
        description_ja=description_ja or "、".join(node.aliases) or business_name_ja,
        payload=OntologyProposalPayload(
            kind=OntologyProposalKind.ALIAS,
            values={"node_upserts": [node.model_dump(mode="json")], "edge_upserts": []},
        ),
    )


def _register_drafts(
    runtime: Any,
    *,
    profile_id: str,
    job_id: str,
    conversion: InterchangeConversion,
) -> list[str]:
    proposal_ids: list[str] = []
    for draft in conversion.drafts:
        proposal = runtime.create_build_proposal(
            profile_id=profile_id,
            job_id=job_id,
            title_ja=draft.title_ja,
            description_ja=draft.description_ja,
            kind=draft.kind,
            proposal_payload=draft.payload,
        )
        proposal_ids.append(proposal.id)
    return proposal_ids


# --- 業種テンプレート適用 ---------------------------------------------------------------------


def convert_template(
    template: OntologyTemplate,
    *,
    ontology: SchemaOntology,
    view: ProfileOntologyView,
    overrides: dict[str, str],
    job_id: str,
) -> InterchangeConversion:
    """テンプレートを profile スコープへ名前解決し、proposal 下書きへ決定論変換する。"""

    inferred_by = f"template:{template.id}"
    resolver = _ScopeResolver(ontology, view)
    result = InterchangeConversion()

    entity_objects: dict[str, str] = {}
    entities: list[OntologyEntityNamingCandidate] = []
    for entity in template.entities:
        reference = overrides.get(entity.key, "").strip() or entity.object_name_hint
        node = resolver.resolve_object(reference)
        if node is None:
            result.unresolved.append(entity.key)
            result.warnings.append(
                f"テンプレートのエンティティ「{entity.business_name_ja}」({reference})を "
                "profile 範囲内に解決できないため、業務用語として提案します。"
            )
            result.drafts.append(
                _term_draft(
                    revision_id=ontology.revision.id,
                    job_id=job_id,
                    inferred_by=inferred_by,
                    business_name_ja=entity.business_name_ja,
                    description_ja=entity.description_ja,
                    aliases=entity.aliases,
                )
            )
            result.term_count += 1
            continue
        result.resolved[entity.key] = node.technical_name
        entity_objects[entity.key] = node.technical_name
        entities.append(
            OntologyEntityNamingCandidate(
                object_name=node.technical_name,
                business_name_ja=entity.business_name_ja,
                description_ja=entity.description_ja,
                aliases=entity.aliases,
                confidence=0.9,
            )
        )

    relationships: list[OntologyRelationshipCandidate] = []
    for relationship in template.relationships:
        source = entity_objects.get(relationship.source)
        target = entity_objects.get(relationship.target)
        if source is None or target is None:
            result.warnings.append(
                f"テンプレートの関係「{relationship.relationship_name_ja}」は端点エンティティが"
                "未解決のため提案化しません。"
            )
            continue
        relationships.append(
            OntologyRelationshipCandidate(
                source_object=source,
                target_object=target,
                relationship_name_ja=relationship.relationship_name_ja,
                cardinality=relationship.cardinality,
                join_conditions=[
                    OntologyBuildJoinConditionCandidate(
                        left=f"{source}.{hint.left_column}",
                        right=f"{target}.{hint.right_column}",
                    )
                    for hint in relationship.join_hints
                ],
                evidence_ja=relationship.description_ja
                or f"テンプレート {template.metadata.name_ja} 由来",
                confidence=0.8,
            )
        )

    extraction = OntologyBuildExtraction(entities=entities, relationships=relationships)
    drafts, warnings = convert_extraction_to_proposals(
        extraction,
        ontology=ontology,
        view=view,
        job_id=job_id,
        inferred_by=inferred_by,
    )
    result.drafts.extend(drafts)
    result.warnings.extend(warnings)

    for term in template.terms:
        result.drafts.append(
            _term_draft(
                revision_id=ontology.revision.id,
                job_id=job_id,
                inferred_by=inferred_by,
                business_name_ja=term.business_name_ja,
                description_ja=term.description_ja,
                aliases=term.aliases,
            )
        )
        result.term_count += 1
    return result


def apply_template(
    runtime: Any,
    *,
    profile_id: str,
    template: OntologyTemplate,
    overrides: dict[str, str],
    dry_run: bool,
) -> tuple[InterchangeConversion, list[str]]:
    view, ontology = runtime.profile_view(profile_id)
    job_id = f"template:{template.id}:{uuid4().hex}"
    conversion = convert_template(
        template,
        ontology=ontology,
        view=view,
        overrides=overrides,
        job_id=job_id,
    )
    if dry_run:
        return conversion, []
    return conversion, _register_drafts(
        runtime, profile_id=profile_id, job_id=job_id, conversion=conversion
    )


# --- OWL RDF export ---------------------------------------------------------------------------

RdfExportFormat = Literal["rdfxml", "turtle"]


def export_ontology_rdf(ontology: SchemaOntology, *, format: RdfExportFormat) -> str:
    """承認済み ontology を OWL(RDF/XML または Turtle)へ書き出す。

    既存 Turtle シリアライザの出力へ round-trip 用注釈(cardinality / joinCondition /
    physicalObject)を後付けする。既存 artifact(hash 対象)は変更しない。
    """

    graph = Graph()
    graph.parse(data=serialize_owl_turtle(ontology), format="turtle")
    approved_nodes = {
        node.id: node
        for node in ontology.nodes
        if node.review_status == OntologyReviewStatus.APPROVED
    }
    for node in approved_nodes.values():
        subject = URIRef(stable_node_iri(node.id).strip("<>"))
        if (subject, RDF.type, OWL.Class) in graph and node.physical_mappings:
            object_ref = node.physical_mappings[0].object_ref
            graph.add(
                (
                    subject,
                    _ONT.physicalObject,
                    RdfLiteral(f"{object_ref.owner}.{object_ref.object_name}"),
                )
            )
    for edge in ontology.edges:
        if edge.kind != OntologyEdgeKind.BUSINESS_RELATIONSHIP:
            continue
        predicate = URIRef(stable_edge_iri(edge.id).strip("<>"))
        if (predicate, RDF.type, OWL.ObjectProperty) not in graph:
            continue
        graph.add((predicate, _ONT.cardinality, RdfLiteral(edge.cardinality.value)))
        for condition in edge.join_conditions:
            left = (
                f"{condition.left.owner}.{condition.left.object_name}"
                f".{condition.left.column_name}"
            )
            right = (
                f"{condition.right.owner}.{condition.right.object_name}"
                f".{condition.right.column_name}"
            )
            graph.add(
                (
                    predicate,
                    _ONT.joinCondition,
                    RdfLiteral(f"{left}{condition.operator}{right}"),
                )
            )
    graph.bind("ont", _ONT)
    return graph.serialize(format="pretty-xml" if format == "rdfxml" else "turtle")


# --- OWL RDF import ---------------------------------------------------------------------------


class RdfImportError(ValueError):
    """RDF の parse/検証失敗(HTTP 400 に写像)。"""

    def __init__(self, message_ja: str) -> None:
        super().__init__(message_ja)
        self.message_ja = message_ja


def _local_name(iri: URIRef) -> str:
    text = str(iri)
    for separator in ("#", "/", ":"):
        if separator in text:
            text = text.rsplit(separator, 1)[1]
    return text


def _preferred_label(graph: Graph, subject: URIRef) -> str:
    labels = list(graph.objects(subject, RDFS.label))
    for label in labels:
        if getattr(label, "language", None) == "ja":
            return str(label)
    return str(labels[0]) if labels else ""


def _parse_cardinality(value: str) -> RelationshipCardinality:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return RelationshipCardinality(normalized)
    except ValueError:
        return RelationshipCardinality.UNKNOWN


def _parse_join_condition(value: str) -> OntologyBuildJoinConditionCandidate | None:
    for operator in _JOIN_OPERATORS:
        if operator in value:
            left, _, right = value.partition(operator)
            if left.strip() and right.strip():
                return OntologyBuildJoinConditionCandidate(
                    left=left.strip(), right=right.strip(), operator=operator
                )
    return None


def parse_rdf_graph(content: bytes, *, filename: str) -> Graph:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in RDF_IMPORT_EXTENSIONS:
        raise RdfImportError(
            f"未対応の拡張子です({suffix or '拡張子なし'})。"
            f"対応形式: {', '.join(sorted(RDF_IMPORT_EXTENSIONS))}"
        )
    if len(content) > RDF_IMPORT_MAX_BYTES:
        raise RdfImportError("RDF ファイルが大きすぎます(上限 5MB)。")
    graph = Graph()
    try:
        graph.parse(data=content, format="turtle" if suffix == ".ttl" else "xml")
    except Exception as exc:
        raise RdfImportError(f"RDF の解析に失敗しました: {exc}") from exc
    return graph


def convert_rdf_graph(
    graph: Graph,
    *,
    ontology: SchemaOntology,
    view: ProfileOntologyView,
    job_id: str,
    source_name: str,
    terms_fallback: bool = True,
) -> tuple[InterchangeConversion, dict[str, int]]:
    """RDF graph(owl:Class / owl:ObjectProperty / owl:DatatypeProperty)を proposal 下書きへ。"""

    inferred_by = f"rdf_import:{source_name}"
    resolver = _ScopeResolver(ontology, view)
    result = InterchangeConversion()

    class_hints: dict[URIRef, str] = {}
    class_labels: dict[URIRef, str] = {}
    entities: list[OntologyEntityNamingCandidate] = []
    classes = sorted(set(graph.subjects(RDF.type, OWL.Class)), key=str)
    for subject in classes:
        if not isinstance(subject, URIRef):
            continue
        label = _preferred_label(graph, subject) or _local_name(subject)
        aliases = sorted({str(alias) for alias in graph.objects(subject, SKOS.altLabel)})
        description = str(next(graph.objects(subject, RDFS.comment), ""))
        physical = str(next(graph.objects(subject, _ONT.physicalObject), ""))
        hint = physical or _local_name(subject).upper()
        class_hints[subject] = hint
        class_labels[subject] = label
        node = resolver.resolve_object(hint)
        if node is None:
            result.unresolved.append(label)
            result.warnings.append(
                f"クラス「{label}」({hint})を profile 範囲内に解決できません。"
                + ("業務用語として提案します。" if terms_fallback else "")
            )
            if terms_fallback:
                result.drafts.append(
                    _term_draft(
                        revision_id=ontology.revision.id,
                        job_id=job_id,
                        inferred_by=inferred_by,
                        business_name_ja=label,
                        description_ja=description,
                        aliases=aliases,
                    )
                )
                result.term_count += 1
            continue
        result.resolved[label] = node.technical_name
        class_hints[subject] = node.technical_name
        entities.append(
            OntologyEntityNamingCandidate(
                object_name=node.technical_name,
                business_name_ja=label,
                description_ja=description,
                aliases=aliases,
                confidence=0.8,
            )
        )

    relationships: list[OntologyRelationshipCandidate] = []
    object_properties = sorted(set(graph.subjects(RDF.type, OWL.ObjectProperty)), key=str)
    for subject in object_properties:
        if not isinstance(subject, URIRef):
            continue
        label = _preferred_label(graph, subject) or _local_name(subject)
        domain = next(graph.objects(subject, RDFS.domain), None)
        range_ = next(graph.objects(subject, RDFS.range), None)
        if not isinstance(domain, URIRef) or not isinstance(range_, URIRef):
            result.warnings.append(
                f"関係「{label}」に domain/range が無いため提案化しません。"
            )
            continue
        cardinality = _parse_cardinality(
            str(next(graph.objects(subject, _ONT.cardinality), ""))
        )
        join_conditions = [
            condition
            for value in sorted(str(v) for v in graph.objects(subject, _ONT.joinCondition))
            if (condition := _parse_join_condition(value)) is not None
        ]
        relationships.append(
            OntologyRelationshipCandidate(
                source_object=class_hints.get(domain, _local_name(domain).upper()),
                target_object=class_hints.get(range_, _local_name(range_).upper()),
                relationship_name_ja=label,
                cardinality=cardinality,
                join_conditions=join_conditions,
                evidence_ja=f"RDF import({source_name})由来",
                confidence=0.7,
            )
        )

    synonyms: list[OntologySynonymCandidate] = []
    datatype_properties = sorted(set(graph.subjects(RDF.type, OWL.DatatypeProperty)), key=str)
    for subject in datatype_properties:
        if not isinstance(subject, URIRef):
            continue
        label = _preferred_label(graph, subject)
        domain = next(graph.objects(subject, RDFS.domain), None)
        if not label or not isinstance(domain, URIRef):
            continue
        hint = class_hints.get(domain, _local_name(domain).upper())
        synonyms.append(
            OntologySynonymCandidate(
                target=f"{hint}.{_local_name(subject).upper()}",
                aliases=[label],
                evidence_ja=f"RDF import({source_name})由来",
            )
        )

    extraction = OntologyBuildExtraction(
        entities=entities, relationships=relationships, synonyms=synonyms
    )
    drafts, warnings = convert_extraction_to_proposals(
        extraction,
        ontology=ontology,
        view=view,
        job_id=job_id,
        inferred_by=inferred_by,
    )
    result.drafts.extend(drafts)
    result.warnings.extend(warnings)
    counts = {
        "classes": len(classes),
        "object_properties": len(object_properties),
        "datatype_properties": len(datatype_properties),
        "term_proposals": result.term_count,
    }
    return result, counts


def import_rdf(
    runtime: Any,
    *,
    profile_id: str,
    content: bytes,
    filename: str,
    terms_fallback: bool,
    dry_run: bool,
) -> tuple[InterchangeConversion, dict[str, int], list[str]]:
    graph = parse_rdf_graph(content, filename=filename)
    view, ontology = runtime.profile_view(profile_id)
    job_id = f"rdf_import:{uuid4().hex}"
    conversion, counts = convert_rdf_graph(
        graph,
        ontology=ontology,
        view=view,
        job_id=job_id,
        source_name=Path(filename or "ontology").name,
        terms_fallback=terms_fallback,
    )
    if dry_run:
        return conversion, counts, []
    proposal_ids = _register_drafts(
        runtime, profile_id=profile_id, job_id=job_id, conversion=conversion
    )
    return conversion, counts, proposal_ids
