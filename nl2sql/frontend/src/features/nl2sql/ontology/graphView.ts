import type { OntologyGraph } from "./types";

export type OntologyGraphViewMode = "grounding" | "all" | "physical_er";

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

function isGroundingContextEdge(edge: OntologyGraph["edges"][number]): boolean {
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
