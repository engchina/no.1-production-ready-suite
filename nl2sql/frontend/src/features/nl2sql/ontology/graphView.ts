import type { OntologyCardinality, OntologyGraph } from "./types";

export type OntologyGraphViewMode = "grounding" | "all" | "physical_er";

/** 業務⇄物理の「対応」を表すエッジ(点線で描く。FK join の実線と区別する)。 */
const MAPPING_EDGE_KINDS = new Set(["maps_to", "physical_mapping"]);

export function isOntologyMappingEdge(edge: OntologyGraph["edges"][number]): boolean {
  return MAPPING_EDGE_KINDS.has((edge.kind ?? "").trim().toLocaleLowerCase());
}

/** join を持つ関係エッジ(カーディナリティを常時表示する対象)。 */
export function isOntologyJoinEdge(edge: OntologyGraph["edges"][number]): boolean {
  const kind = (edge.kind ?? "").trim().toLocaleLowerCase();
  return kind === "foreign_key" || (edge.join_conditions ?? []).length > 0;
}

/** ER 図流の常時表示用カーディナリティ短縮ラベル(unknown/未設定は空)。 */
export function cardinalityShortLabel(cardinality: OntologyCardinality | undefined): string {
  switch (cardinality) {
    case "one_to_one":
      return "1:1";
    case "one_to_many":
      return "1:N";
    case "many_to_one":
      return "N:1";
    case "many_to_many":
      return "N:N";
    default:
      return "";
  }
}

export interface OntologyEdgeHandleSelection {
  sourceHandle: string;
  targetHandle: string;
  orientation: "horizontal" | "vertical";
}

/**
 * ノード中心の相対位置からエッジの接続ハンドルを選ぶ。
 * 縦優勢(レーン間: 業務概念→物理表 等)は上下ハンドル、横優勢は左右ハンドルを使い、
 * 固定 Left/Right だけのときに起きる自己ループ状の曲線を防ぐ。
 */
export function selectOntologyEdgeHandles(
  source: { x: number; y: number },
  target: { x: number; y: number }
): OntologyEdgeHandleSelection {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  if (Math.abs(dy) > Math.abs(dx)) {
    return dy >= 0
      ? { sourceHandle: "s-bottom", targetHandle: "t-top", orientation: "vertical" }
      : { sourceHandle: "s-top", targetHandle: "t-bottom", orientation: "vertical" };
  }
  return dx >= 0
    ? { sourceHandle: "s-right", targetHandle: "t-left", orientation: "horizontal" }
    : { sourceHandle: "s-left", targetHandle: "t-right", orientation: "horizontal" };
}

const DETAIL_NODE_KINDS = new Set(["column", "enum_value"]);
const PHYSICAL_ER_NODE_KINDS = new Set(["schema", "table", "view", "column", "enum_value"]);
const GROUNDING_CONTEXT_EDGE_KINDS = new Set([
  "maps_to",
  "physical_mapping",
  "physical_contains",
  "contains",
  "column",
  "foreign_key",
]);

export function isOntologyDetailNodeKind(kind: string): boolean {
  return DETAIL_NODE_KINDS.has(kind);
}

function filterGraphByNodeIds(graph: OntologyGraph, nodeIds: Set<string>): OntologyGraph {
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => nodeIds.has(node.id)),
    edges: graph.edges.filter(
      (edge) => nodeIds.has(edge.source_node_id) && nodeIds.has(edge.target_node_id)
    ),
  };
}

/** 物理マッピング/包含(表→列 等)を表すエッジか。属性→親エンティティの逆引きにも使う。 */
export function isGroundingContextEdge(edge: OntologyGraph["edges"][number]): boolean {
  const kind = (edge.kind ?? "").trim().toLocaleLowerCase();
  if (GROUNDING_CONTEXT_EDGE_KINDS.has(kind)) return true;
  if ((edge.join_conditions ?? []).length > 0) return true;
  const label = edge.relationship_name_ja.trim();
  return label.includes("物理マッピング") || label === "列" || label.includes("Join");
}

export function ontologyGraphForViewMode(
  graph: OntologyGraph,
  mode: OntologyGraphViewMode,
  highlightNodeIds: string[] | undefined,
  highlightEdgeIds: string[] | undefined
): OntologyGraph {
  if (mode === "physical_er") {
    return filterGraphByNodeIds(
      graph,
      new Set(
        graph.nodes.filter((node) => PHYSICAL_ER_NODE_KINDS.has(node.kind)).map((node) => node.id)
      )
    );
  }
  const hasGrounding =
    mode === "grounding" &&
    ((highlightNodeIds?.length ?? 0) > 0 || (highlightEdgeIds?.length ?? 0) > 0);
  if (!hasGrounding) return graph;

  const keep = new Set(highlightNodeIds ?? []);
  const highlightedEdges = new Set(highlightEdgeIds ?? []);
  for (const edge of graph.edges) {
    if (!highlightedEdges.has(edge.id)) continue;
    keep.add(edge.source_node_id);
    keep.add(edge.target_node_id);
  }
  const contextAnchor = new Set(keep);
  for (const edge of graph.edges) {
    if (!contextAnchor.has(edge.source_node_id) && !contextAnchor.has(edge.target_node_id)) continue;
    if (!isGroundingContextEdge(edge)) continue;
    keep.add(edge.source_node_id);
    keep.add(edge.target_node_id);
  }
  return filterGraphByNodeIds(graph, keep);
}

export function ontologyGraphWithDetailVisibility(
  graph: OntologyGraph,
  showDetails: boolean,
  forcedNodeIds: Set<string>
): OntologyGraph {
  if (showDetails) return graph;
  return filterGraphByNodeIds(
    graph,
    new Set(
      graph.nodes
        .filter((node) => !isOntologyDetailNodeKind(node.kind) || forcedNodeIds.has(node.id))
        .map((node) => node.id)
    )
  );
}
