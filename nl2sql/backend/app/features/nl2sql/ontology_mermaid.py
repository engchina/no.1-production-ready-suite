"""SchemaOntology を mermaid erDiagram へ決定論変換する serializer。

正本は Oracle 26ai 上の JSON(ontology_store)であり、この module は LLM プロンプト注入と
UI プレビュー用の表現を生成するだけ。network・LLM・DB に依存しない。
"""

from __future__ import annotations

from app.features.nl2sql.ontology_catalog import SchemaOntology
from app.features.nl2sql.ontology_models import (
    OntologyEdge,
    OntologyEdgeKind,
    OntologyNode,
    OntologyNodeKind,
    OntologyReviewStatus,
    ProfileOntologyView,
    RelationshipCardinality,
)

_OBJECT_NODE_KINDS = frozenset({OntologyNodeKind.TABLE, OntologyNodeKind.VIEW})

# mermaid ER 記法(source 側, target 側)。未確認は非識別(点線)で描く。
_CARDINALITY_NOTATION: dict[RelationshipCardinality, str] = {
    RelationshipCardinality.ONE_TO_ONE: "||--||",
    RelationshipCardinality.ONE_TO_MANY: "||--o{",
    RelationshipCardinality.MANY_TO_ONE: "}o--||",
    RelationshipCardinality.MANY_TO_MANY: "}o--o{",
    RelationshipCardinality.UNKNOWN: "}o..o{",
}


def _entity_name(node: OntologyNode) -> str:
    owner = str(node.metadata.get("owner", "")).strip()
    object_name = str(node.metadata.get("object_name", "")).strip()
    if owner and object_name:
        return f"{owner}.{object_name}"
    return node.technical_name or node.business_name_ja


def _quoted(value: str) -> str:
    return '"' + value.replace('"', "'") + '"'


def _attribute_type(node: OntologyNode) -> str:
    data_type = str(node.metadata.get("data_type", "")).strip() or "UNKNOWN"
    return "".join(data_type.split())


def _label(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ").strip()


def _resolve_object_node(
    node: OntologyNode | None,
    object_node_by_key: dict[str, OntologyNode],
) -> OntologyNode | None:
    """endpoint node(物理 or 業務)を対応する表・ビュー node へ解決する。"""

    if node is None:
        return None
    if node.kind in _OBJECT_NODE_KINDS:
        return node
    for mapping in node.physical_mappings:
        ref = mapping.object_ref
        resolved = object_node_by_key.get(f"{ref.owner}.{ref.object_name}".upper())
        if resolved is not None:
            return resolved
    return None


def _join_summary(edge: OntologyEdge) -> str:
    parts = []
    for condition in sorted(edge.join_conditions, key=lambda item: item.ordinal):
        parts.append(
            f"{condition.left.object_name}.{condition.left.column_name} "
            f"{condition.operator} "
            f"{condition.right.object_name}.{condition.right.column_name}"
        )
    return " AND ".join(parts)


def render_mermaid_er(
    ontology: SchemaOntology,
    view: ProfileOntologyView | None = None,
    *,
    max_entities: int = 60,
    max_chars: int = 8000,
) -> str:
    """Profile スコープ(view 指定時)の erDiagram を決定論で生成する。

    - entities: TABLE/VIEW node(ID ソート、max_entities で切り詰め)
    - attributes: COLUMN node(型・FK マーカー・論理名コメント)
    - relationships: FOREIGN_KEY と、承認済みかつ許可 path の BUSINESS_RELATIONSHIP
    - 承認済み業務エンティティ名は %% コメントで併記
    - max_chars 超過時は attributes を落として関係構造を優先する
    """

    scoped_node_ids = set(view.node_ids) if view is not None else None
    scoped_edge_ids = set(view.edge_ids) if view is not None else None
    allowed_path_ids = set(view.allowed_path_ids) if view is not None else None

    def node_in_scope(node: OntologyNode) -> bool:
        return scoped_node_ids is None or node.id in scoped_node_ids

    object_nodes = sorted(
        (
            node
            for node in ontology.nodes
            if node.kind in _OBJECT_NODE_KINDS and node_in_scope(node)
        ),
        key=lambda node: node.id,
    )
    omitted_entities = max(0, len(object_nodes) - max_entities)
    object_nodes = object_nodes[:max_entities]
    object_node_by_key = {_entity_name(node).upper(): node for node in object_nodes}
    included_node_ids = {node.id for node in object_nodes}

    # 表・ビューごとの列(ordinal 順)。view スコープ内の列だけを載せる。
    columns_by_object: dict[str, list[OntologyNode]] = {}
    for node in ontology.nodes:
        if node.kind != OntologyNodeKind.COLUMN or not node_in_scope(node):
            continue
        owner = str(node.metadata.get("owner", "")).strip()
        object_name = str(node.metadata.get("object_name", "")).strip()
        key = f"{owner}.{object_name}".upper()
        if key in object_node_by_key:
            columns_by_object.setdefault(key, []).append(node)
    for columns in columns_by_object.values():
        columns.sort(key=lambda node: (node.metadata.get("ordinal") or 0, node.id))

    # FK 側の列名(FK マーカー用)と関係行。
    node_by_id = {node.id: node for node in ontology.nodes}
    fk_column_keys: set[tuple[str, str]] = set()
    relationship_lines: list[str] = []
    for edge in sorted(ontology.edges, key=lambda edge: edge.id):
        if scoped_edge_ids is not None and edge.id not in scoped_edge_ids:
            continue
        if edge.kind == OntologyEdgeKind.FOREIGN_KEY:
            approved = edge.review_status == OntologyReviewStatus.APPROVED
        elif edge.kind == OntologyEdgeKind.BUSINESS_RELATIONSHIP:
            if edge.review_status != OntologyReviewStatus.APPROVED:
                continue
            if allowed_path_ids is not None and edge.id not in allowed_path_ids:
                continue
            approved = True
        else:
            continue
        source = _resolve_object_node(node_by_id.get(edge.source_node_id), object_node_by_key)
        target = _resolve_object_node(node_by_id.get(edge.target_node_id), object_node_by_key)
        if (
            source is None
            or target is None
            or source.id not in included_node_ids
            or target.id not in included_node_ids
        ):
            continue
        for condition in edge.join_conditions:
            fk_column_keys.add(
                (
                    f"{condition.left.owner}.{condition.left.object_name}".upper(),
                    condition.left.column_name.upper(),
                )
            )
        notation = _CARDINALITY_NOTATION[edge.cardinality]
        if not approved:
            notation = notation.replace("--", "..")
        label_parts = [_label(edge.relationship_name_ja)]
        join_summary = _join_summary(edge)
        if join_summary:
            label_parts.append(join_summary)
        relationship_lines.append(
            f"    {_quoted(_entity_name(source))} {notation} "
            f"{_quoted(_entity_name(target))} : {_quoted(' / '.join(label_parts))}"
        )

    # 承認済み業務エンティティ(表・ビューへの mapping を持つもの)は別名コメントで示す。
    business_comments: list[str] = []
    for node in sorted(ontology.nodes, key=lambda node: node.id):
        if node.kind != OntologyNodeKind.BUSINESS_ENTITY or not node_in_scope(node):
            continue
        if node.review_status != OntologyReviewStatus.APPROVED:
            continue
        resolved = _resolve_object_node(node, object_node_by_key)
        if resolved is None or resolved.id not in included_node_ids:
            continue
        business_comments.append(
            f"    %% {_label(node.business_name_ja)} = {_entity_name(resolved)}"
        )

    def entity_block(node: OntologyNode, *, with_attributes: bool) -> list[str]:
        name = _entity_name(node)
        header = f"    {_quoted(name)}"
        logical = _label(node.business_name_ja)
        comment = f" %% {logical}" if logical and logical != name.split(".")[-1] else ""
        columns = columns_by_object.get(name.upper(), []) if with_attributes else []
        if not columns:
            return [f"{header}{comment}"]
        lines = [f"{header} {{{comment}"]
        for column in columns:
            column_name = str(column.metadata.get("column_name", "")).strip() or column.id
            owner_key = (
                f"{column.metadata.get('owner', '')}.{column.metadata.get('object_name', '')}"
            ).upper()
            marker = " FK" if (owner_key, column_name.upper()) in fk_column_keys else ""
            logical_name = _label(column.business_name_ja)
            comment_part = (
                f" {_quoted(logical_name)}"
                if logical_name and logical_name.upper() != column_name.upper()
                else ""
            )
            lines.append(f"        {_attribute_type(column)} {column_name}{marker}{comment_part}")
        lines.append("    }")
        return lines

    def render(*, with_attributes: bool) -> str:
        lines = ["erDiagram"]
        lines.extend(business_comments)
        for node in object_nodes:
            lines.extend(entity_block(node, with_attributes=with_attributes))
        lines.extend(relationship_lines)
        if omitted_entities:
            lines.append(f"    %% omitted: {omitted_entities} entities")
        return "\n".join(lines)

    result = render(with_attributes=True)
    if len(result) > max_chars:
        result = render(with_attributes=False)
    if len(result) > max_chars:
        result = result[:max_chars].rsplit("\n", 1)[0] + "\n    %% truncated"
    return result
