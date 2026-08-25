import type {
  OntologyEdge,
  OntologyGraph,
  OntologyJoinCondition,
  OntologyJsonValue,
  OntologyNode,
} from "./types";

export type OntologyErKeyRole = "none" | "pk" | "fk" | "pk_fk";

export interface OntologyErColumnDetail {
  id: string;
  columnName: string;
  dataType: string;
  businessNameJa: string;
  descriptionJa: string;
  keyRole: OntologyErKeyRole;
  ordinal: number | null;
}

export interface OntologyErJoinDetail {
  id: string;
  relationshipNameJa: string;
  sourceLabel: string;
  targetLabel: string;
  cardinality: string;
  joinCondition: string;
}

export interface OntologyErDetails {
  selectedNodeId: string;
  objectNodeId: string | null;
  objectName: string;
  objectType: "table" | "view" | "unknown";
  displayNameJa: string;
  columns: OntologyErColumnDetail[];
  joins: OntologyErJoinDetail[];
}

interface ObjectIdentity {
  owner: string;
  objectName: string;
  objectType: "table" | "view" | "unknown";
  nodeId?: string;
}

interface ColumnIdentity extends ObjectIdentity {
  columnName: string;
}

type JoinEndpoint = Partial<NonNullable<OntologyJoinCondition["left"]>>;

const OBJECT_KINDS = new Set(["table", "view"]);
const MAPPABLE_KINDS = new Set([
  "business_entity",
  "business_event",
  "property",
  "metric",
  "business_term",
]);

function jsonString(value: OntologyJsonValue | undefined): string {
  return typeof value === "string" ? value.trim() : "";
}

function jsonNumber(value: OntologyJsonValue | undefined): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function jsonBoolean(value: OntologyJsonValue | undefined): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    return ["true", "yes", "1", "pk"].includes(value.trim().toLowerCase());
  }
  return false;
}

function normalizeIdentifier(value: string | undefined): string {
  return (value ?? "").trim().toLocaleUpperCase("en-US");
}

function objectType(value: string | undefined): ObjectIdentity["objectType"] {
  const normalized = (value ?? "").trim().toLowerCase();
  if (normalized.includes("view")) return "view";
  if (normalized.includes("table")) return "table";
  return "unknown";
}

function objectName(identity: ObjectIdentity): string {
  return identity.owner ? `${identity.owner}.${identity.objectName}` : identity.objectName;
}

function splitQualifiedName(value: string | undefined): {
  owner: string;
  objectName: string;
  columnName: string;
} {
  const parts = (value ?? "")
    .split(".")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length >= 3) {
    return {
      owner: parts[parts.length - 3],
      objectName: parts[parts.length - 2],
      columnName: parts[parts.length - 1],
    };
  }
  if (parts.length === 2) return { owner: parts[0], objectName: parts[1], columnName: "" };
  return { owner: "", objectName: parts[0] ?? "", columnName: "" };
}

function firstMappingObject(node: OntologyNode) {
  return node.physical_mappings?.[0]?.object_ref;
}

function objectIdentityFromNode(node: OntologyNode): ObjectIdentity | null {
  const mapping = firstMappingObject(node);
  const fallback = node.physical_mapping;
  const technical = splitQualifiedName(node.technical_name);
  const metadata = node.metadata ?? {};
  const allowTechnicalFallback = OBJECT_KINDS.has(node.kind) || node.kind === "column";
  const owner = normalizeIdentifier(
    mapping?.owner ||
      jsonString(metadata.owner) ||
      fallback?.owner ||
      (allowTechnicalFallback ? technical.owner : "")
  );
  const objectNameValue =
    mapping?.object_name ||
    jsonString(metadata.object_name) ||
    fallback?.object_name ||
    (allowTechnicalFallback ? technical.objectName : "");
  const normalizedObjectName = normalizeIdentifier(objectNameValue);
  if (!normalizedObjectName) return null;
  const resolvedType = objectType(
    mapping?.object_type || jsonString(metadata.object_type) || fallback?.object_type || node.kind
  );
  return {
    owner,
    objectName: normalizedObjectName,
    objectType: resolvedType,
    nodeId: mapping?.node_id || (OBJECT_KINDS.has(node.kind) ? node.id : undefined),
  };
}

function columnIdentityFromNode(node: OntologyNode): ColumnIdentity | null {
  if (node.kind !== "column") return null;
  const object = objectIdentityFromNode(node);
  if (!object) return null;
  const columnRef = node.physical_mappings?.[0]?.column_refs?.[0];
  const technical = splitQualifiedName(node.technical_name);
  const metadata = node.metadata ?? {};
  const columnName = normalizeIdentifier(
    columnRef?.column_name ||
      jsonString(metadata.column_name) ||
      node.physical_mapping?.column_name ||
      technical.columnName
  );
  if (!columnName) return null;
  return { ...object, columnName };
}

function objectMatches(left: ObjectIdentity, right: ObjectIdentity): boolean {
  if (left.nodeId && right.nodeId && left.nodeId === right.nodeId) return true;
  return left.owner === right.owner && left.objectName === right.objectName;
}

function endpointIdentity(endpoint: JoinEndpoint | undefined): ObjectIdentity | null {
  const objectNameValue = normalizeIdentifier(endpoint?.object_name);
  if (!objectNameValue) return null;
  return {
    owner: normalizeIdentifier(endpoint?.owner),
    objectName: objectNameValue,
    objectType: "unknown",
  };
}

function endpointColumn(endpoint: JoinEndpoint | undefined): string {
  return normalizeIdentifier(endpoint?.column_name);
}

function nodeLabel(node: OntologyNode | undefined, fallback: string): string {
  return node?.business_name_ja || node?.technical_name || fallback;
}

function edgeEndpoint(
  graph: OntologyGraph,
  edge: OntologyEdge,
  condition: OntologyJoinCondition,
  side: "left" | "right"
): ColumnIdentity | null {
  const explicit = side === "left" ? condition.left : condition.right;
  const explicitObject = endpointIdentity(explicit);
  const explicitColumn = endpointColumn(explicit);
  if (explicitObject && explicitColumn) return { ...explicitObject, columnName: explicitColumn };

  const nodeId = side === "left" ? edge.source_node_id : edge.target_node_id;
  const fallbackNode = graph.nodes.find((node) => node.id === nodeId);
  const fallbackObject = fallbackNode ? objectIdentityFromNode(fallbackNode) : null;
  const fallbackColumn = normalizeIdentifier(
    side === "left" ? condition.source_column : condition.target_column
  );
  if (fallbackObject && fallbackColumn) return { ...fallbackObject, columnName: fallbackColumn };
  return null;
}

function endpointLabel(endpoint: ColumnIdentity | null): string {
  if (!endpoint) return "?";
  const qualified = objectName(endpoint);
  return qualified ? `${qualified}.${endpoint.columnName}` : endpoint.columnName;
}

function joinConditionLabel(graph: OntologyGraph, edge: OntologyEdge): string {
  return (edge.join_conditions ?? [])
    .slice()
    .sort((left, right) => (left.ordinal ?? 0) - (right.ordinal ?? 0))
    .map((condition) => {
      const left = edgeEndpoint(graph, edge, condition, "left");
      const right = edgeEndpoint(graph, edge, condition, "right");
      return `${endpointLabel(left)} ${condition.operator ?? "="} ${endpointLabel(right)}`;
    })
    .join(" AND ");
}

function explicitPrimaryKey(node: OntologyNode): boolean {
  const metadata = node.metadata ?? {};
  const keyRole = jsonString(metadata.key_role).toLowerCase();
  return (
    jsonBoolean(metadata.primary_key) ||
    jsonBoolean(metadata.is_primary_key) ||
    jsonBoolean(metadata.pk) ||
    keyRole === "pk" ||
    keyRole.includes("primary")
  );
}

function columnRole(column: OntologyNode, fkColumnNames: Set<string>): OntologyErKeyRole {
  const identity = columnIdentityFromNode(column);
  const isPk = explicitPrimaryKey(column);
  const isFk = Boolean(identity && fkColumnNames.has(identity.columnName));
  if (isPk && isFk) return "pk_fk";
  if (isPk) return "pk";
  if (isFk) return "fk";
  return "none";
}

function objectNodeForIdentity(
  graph: OntologyGraph,
  identity: ObjectIdentity
): OntologyNode | undefined {
  return graph.nodes.find((node) => {
    if (!OBJECT_KINDS.has(node.kind)) return false;
    const nodeIdentity = objectIdentityFromNode(node);
    return Boolean(nodeIdentity && objectMatches(identity, nodeIdentity));
  });
}

function sortableOrdinal(node: OntologyNode): number | null {
  const columnRefOrdinal = node.physical_mappings?.[0]?.column_refs?.[0]?.ordinal ?? null;
  return columnRefOrdinal ?? jsonNumber(node.metadata?.ordinal);
}

export function deriveOntologyErDetails(
  graph: OntologyGraph,
  selectedNodeId: string | null | undefined
): OntologyErDetails | null {
  if (!selectedNodeId) return null;
  const selected = graph.nodes.find((node) => node.id === selectedNodeId);
  if (
    !selected ||
    (!OBJECT_KINDS.has(selected.kind) &&
      !MAPPABLE_KINDS.has(selected.kind) &&
      selected.kind !== "column")
  ) {
    return null;
  }

  const selectedObject = objectIdentityFromNode(selected);
  if (!selectedObject) return null;
  const objectNode = objectNodeForIdentity(graph, selectedObject);
  const resolvedObject = objectNode ? objectIdentityFromNode(objectNode) : selectedObject;
  if (!resolvedObject) return null;

  const fkColumnNames = new Set<string>();
  for (const edge of graph.edges) {
    for (const condition of edge.join_conditions ?? []) {
      const left = edgeEndpoint(graph, edge, condition, "left");
      if (left && objectMatches(resolvedObject, left)) fkColumnNames.add(left.columnName);
    }
  }

  const columns = graph.nodes
    .filter((node) => {
      const column = columnIdentityFromNode(node);
      return Boolean(column && objectMatches(resolvedObject, column));
    })
    .sort((left, right) => {
      const leftOrdinal = sortableOrdinal(left);
      const rightOrdinal = sortableOrdinal(right);
      if (leftOrdinal !== null && rightOrdinal !== null && leftOrdinal !== rightOrdinal) {
        return leftOrdinal - rightOrdinal;
      }
      if (leftOrdinal !== null && rightOrdinal === null) return -1;
      if (leftOrdinal === null && rightOrdinal !== null) return 1;
      const leftColumn = columnIdentityFromNode(left)?.columnName ?? "";
      const rightColumn = columnIdentityFromNode(right)?.columnName ?? "";
      return leftColumn.localeCompare(rightColumn, "en-US");
    })
    .map<OntologyErColumnDetail>((node) => {
      const metadata = node.metadata ?? {};
      const column = columnIdentityFromNode(node);
      const comment = jsonString(metadata.comment);
      return {
        id: node.id,
        columnName: column?.columnName || node.business_name_ja,
        dataType: jsonString(metadata.data_type) || "-",
        businessNameJa: node.business_name_ja,
        descriptionJa: node.description_ja || comment,
        keyRole: columnRole(node, fkColumnNames),
        ordinal: sortableOrdinal(node),
      };
    });

  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const joins = graph.edges
    .filter((edge) => (edge.join_conditions ?? []).length > 0)
    .filter((edge) =>
      (edge.join_conditions ?? []).some((condition) => {
        const left = edgeEndpoint(graph, edge, condition, "left");
        const right = edgeEndpoint(graph, edge, condition, "right");
        return Boolean(
          (left && objectMatches(resolvedObject, left)) ||
            (right && objectMatches(resolvedObject, right))
        );
      })
    )
    .sort((left, right) => left.id.localeCompare(right.id, "en-US"))
    .map<OntologyErJoinDetail>((edge) => ({
      id: edge.id,
      relationshipNameJa: edge.relationship_name_ja,
      sourceLabel: nodeLabel(nodeById.get(edge.source_node_id), edge.source_node_id),
      targetLabel: nodeLabel(nodeById.get(edge.target_node_id), edge.target_node_id),
      cardinality: edge.cardinality ?? "unknown",
      joinCondition: joinConditionLabel(graph, edge) || "-",
    }));

  return {
    selectedNodeId,
    objectNodeId: objectNode?.id ?? null,
    objectName: objectName(resolvedObject),
    objectType: resolvedObject.objectType,
    displayNameJa: objectNode?.business_name_ja || selected.business_name_ja,
    columns,
    joins,
  };
}
