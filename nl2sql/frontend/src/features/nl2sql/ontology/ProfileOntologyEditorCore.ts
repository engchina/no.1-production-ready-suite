// Profile Ontology エディタの純ロジック。
// グラフの正本は backend の GET /nl2sql/profiles/{id}/ontology-view(profile スコープ済み)であり、
// ここではフロント側でグラフを再構築しない(旧: カタログ全件からのローカル構築は廃止)。
import type {
  OntologyCardinality,
  OntologyGraph,
  OntologyNode,
} from "./types";

export type ProfileOntologyObjectType = "table" | "view";

export interface ProfileOntologyNodeDraft {
  business_name_ja?: string;
  table_usage?: string;
}

export interface ProfileOntologyEdgeDraft {
  cardinality?: OntologyCardinality;
  allowed_path?: boolean;
}

export interface ProfileOntologyDraftState {
  nodes: Record<string, ProfileOntologyNodeDraft>;
  edges: Record<string, ProfileOntologyEdgeDraft>;
}

export interface ProfileOntologyNodeOverride {
  node_id: string;
  business_name_ja: string;
  table_usage: string;
}

export interface ProfileOntologyEdgeOverride {
  edge_id: string;
  cardinality: OntologyCardinality;
  allowed_path: boolean;
}

export interface ProfileOntologyDraftPayload {
  profile_id: string;
  schema_fingerprint: string;
  table_usage: Record<string, string>;
  allowed_path_ids: string[];
  node_overrides: ProfileOntologyNodeOverride[];
  edge_overrides: ProfileOntologyEdgeOverride[];
  physical_scope: {
    table_names: string[];
    view_names: string[];
  };
}

export interface ProfileOntologyObjectReference {
  owner: string;
  objectName: string;
  objectType: ProfileOntologyObjectType;
  qualifiedName: string;
}

export const EMPTY_PROFILE_ONTOLOGY_DRAFT: ProfileOntologyDraftState = {
  nodes: {},
  edges: {},
};

function normalizedIdentifier(value: string | null | undefined): string {
  return (value ?? "").trim().toLocaleUpperCase("en-US");
}

export function profileOntologyObjectFromNode(
  node: OntologyNode | null | undefined
): ProfileOntologyObjectReference | null {
  const mapping = node?.physical_mappings?.[0]?.object_ref;
  const fallback = node?.physical_mapping;
  const objectName = normalizedIdentifier(mapping?.object_name ?? fallback?.object_name);
  if (!objectName) return null;
  const owner = normalizedIdentifier(mapping?.owner ?? fallback?.owner) || "APP";
  const objectType = normalizedIdentifier(
    mapping?.object_type ?? fallback?.object_type
  ).includes("VIEW")
    ? "view"
    : "table";
  return {
    owner,
    objectName,
    objectType,
    qualifiedName: `${owner}.${objectName}`,
  };
}

// 業務モデル表示では表・ビュー・業務概念を主役にし、列や schema コンテナは省く
// (100 ノード表示上限を実データで使い切らないため)。
const HIDDEN_NODE_KINDS = new Set(["column", "schema"]);

export function displayProfileOntologyGraph(
  graph: OntologyGraph | null,
  draft: ProfileOntologyDraftState
): OntologyGraph {
  if (!graph) return { nodes: [], edges: [] };
  const nodes = graph.nodes
    .filter((node) => !HIDDEN_NODE_KINDS.has(node.kind))
    .map((node) => {
      const override = draft.nodes[node.id]?.business_name_ja?.trim();
      return override ? { ...node, business_name_ja: override } : node;
    });
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const edges = graph.edges
    .filter(
      (edge) =>
        visibleNodeIds.has(edge.source_node_id) && visibleNodeIds.has(edge.target_node_id)
    )
    .map((edge) => {
      const override = draft.edges[edge.id];
      if (!override) return edge;
      return {
        ...edge,
        cardinality: override.cardinality ?? edge.cardinality,
        enabled: override.allowed_path ?? edge.enabled,
      };
    });
  return {
    revision_id: graph.revision_id ?? graph.revision?.id,
    revision: graph.revision,
    nodes,
    edges,
  };
}

export function createProfileOntologyDraftPayload(
  profileId: string,
  graph: OntologyGraph | null,
  selectedTables: string[],
  selectedViews: string[],
  draft: ProfileOntologyDraftState
): ProfileOntologyDraftPayload {
  const nodeIds = new Set((graph?.nodes ?? []).map((node) => node.id));
  const edgeById = new Map((graph?.edges ?? []).map((edge) => [edge.id, edge]));

  const nodeOverrides = Object.entries(draft.nodes)
    .filter(([nodeId]) => nodeIds.has(nodeId))
    .map(([nodeId, value]) => ({
      node_id: nodeId,
      business_name_ja: value.business_name_ja?.trim() ?? "",
      table_usage: value.table_usage?.trim() ?? "",
    }))
    .filter((value) => value.business_name_ja || value.table_usage)
    .sort((left, right) => left.node_id.localeCompare(right.node_id));

  const edgeOverrides = Object.entries(draft.edges)
    .filter(([edgeId]) => edgeById.has(edgeId))
    .map(([edgeId, value]) => ({
      edge_id: edgeId,
      cardinality: value.cardinality ?? edgeById.get(edgeId)?.cardinality ?? "unknown",
      allowed_path: value.allowed_path ?? false,
    }))
    .sort((left, right) => left.edge_id.localeCompare(right.edge_id));

  const tableUsage = Object.fromEntries(
    nodeOverrides
      .filter((override) => override.table_usage)
      .map((override) => [override.node_id, override.table_usage])
  );

  // backend の PATCH は承認済み関係だけを allowed path として受理する。
  const allowedPathIds = edgeOverrides
    .filter(
      (override) =>
        override.allowed_path &&
        edgeById.get(override.edge_id)?.review_status === "approved"
    )
    .map((override) => override.edge_id)
    .sort();

  // Profile Ontology view の範囲外の物理 object は PATCH が拒否するため送らない。
  const accepted = { table: new Set<string>(), view: new Set<string>() };
  (graph?.nodes ?? [])
    .filter((node) => node.kind === "table" || node.kind === "view")
    .forEach((node) => {
      const object = profileOntologyObjectFromNode(node);
      if (!object) return;
      const bucket = object.objectType === "view" ? accepted.view : accepted.table;
      bucket.add(object.objectName);
      bucket.add(object.qualifiedName);
    });
  const normalize = (value: string) => normalizedIdentifier(value.replaceAll('"', ""));

  return {
    profile_id: profileId,
    schema_fingerprint: graph?.revision?.schema_fingerprint ?? "",
    table_usage: tableUsage,
    allowed_path_ids: allowedPathIds,
    node_overrides: nodeOverrides,
    edge_overrides: edgeOverrides,
    physical_scope: {
      table_names: selectedTables.filter((name) => accepted.table.has(normalize(name))),
      view_names: selectedViews.filter((name) => accepted.view.has(normalize(name))),
    },
  };
}
