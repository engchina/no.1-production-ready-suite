// 関係グラフの force-directed(neo4j 風)レイアウト。
// d3-force は初期配置(phyllotaxis)も jiggle も決定論のため、同じグラフは常に同じ座標になる。
// UI の外で同期 tick するので、コンポーネントは結果の Map を描画するだけでよい。
import dagre from "@dagrejs/dagre";
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationNodeDatum,
} from "d3-force";

import type { OntologyGraph, OntologyJsonValue, OntologyNode, OntologyNodeKind } from "./types";

export interface GraphPoint {
  x: number;
  y: number;
}

export type OntologyGraphSemanticLaneId = "business" | "attribute" | "physical" | "detail";

export interface OntologyGraphSemanticLane {
  id: OntologyGraphSemanticLaneId;
  y: number;
  height: number;
  nodeCount: number;
}

export interface OntologyGraphSemanticLayout {
  positions: Map<string, GraphPoint>;
  lanes: OntologyGraphSemanticLane[];
  laneByNodeId: Map<string, OntologyGraphSemanticLaneId>;
  clusterByNodeId: Map<string, string>;
}

interface LayoutNode extends SimulationNodeDatum {
  id: string;
}

interface LayoutLink {
  source: string;
  target: string;
}

// ノード実寸(React Flow: 190×54 前後)から算出した衝突半径・目標リンク長。
const COLLIDE_RADIUS = 118;
const LINK_DISTANCE = 230;
const SIMULATION_TICKS = 300;

const SEMANTIC_LANES: OntologyGraphSemanticLaneId[] = [
  "business",
  "attribute",
  "physical",
  "detail",
];
const BUSINESS_KINDS = new Set<OntologyNodeKind>([
  "business_entity",
  "business_event",
  "business_term",
  "business_rule",
  "question_intent",
]);
const ATTRIBUTE_KINDS = new Set<OntologyNodeKind>([
  "property",
  "metric",
  "query_plan",
  "cte",
  "sql_column",
  "sql_join",
  "sql_filter",
  "sql_aggregate",
  "sql_group",
  "sql_having",
  "sql_order",
  "sql_limit",
  "sql_window",
  "sql_artifact",
  "validation_finding",
  "execution_preview",
]);
const PHYSICAL_KINDS = new Set<OntologyNodeKind>(["schema", "table", "view", "sql_table"]);
const DETAIL_KINDS = new Set<OntologyNodeKind>(["column", "enum_value"]);

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

function normalizeIdentifier(value: string | undefined): string {
  return (value ?? "").trim().toLocaleUpperCase("en-US");
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

function stableClusterId(prefix: string, value: string): string {
  const normalized = value
    .trim()
    .toLocaleUpperCase("en-US")
    .replace(/[^A-Z0-9_.:-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return `${prefix}:${normalized || "UNKNOWN"}`;
}

function firstObjectMapping(node: OntologyNode) {
  return node.physical_mappings?.[0]?.object_ref;
}

export function ontologyGraphSemanticLaneForKind(
  kind: OntologyNodeKind
): OntologyGraphSemanticLaneId {
  if (DETAIL_KINDS.has(kind)) return "detail";
  if (PHYSICAL_KINDS.has(kind)) return "physical";
  if (ATTRIBUTE_KINDS.has(kind)) return "attribute";
  if (BUSINESS_KINDS.has(kind)) return "business";
  return "business";
}

export function ontologyGraphObjectClusterKey(node: OntologyNode): string | null {
  const mapping = firstObjectMapping(node);
  const fallback = node.physical_mapping;
  const metadata = node.metadata ?? {};
  const technical = splitQualifiedName(node.technical_name);
  const owner = normalizeIdentifier(
    mapping?.owner || jsonString(metadata.owner) || fallback?.owner || technical.owner
  );
  const objectName = normalizeIdentifier(
    mapping?.object_name ||
      jsonString(metadata.object_name) ||
      fallback?.object_name ||
      technical.objectName
  );
  if (!objectName) return null;
  return stableClusterId("object", owner ? `${owner}.${objectName}` : objectName);
}

function enumParentClusterKey(node: OntologyNode, clusterByNodeId: Map<string, string>): string | null {
  const parentId = node.enum_value_definition?.property_node_id;
  return parentId ? clusterByNodeId.get(parentId) ?? null : null;
}

function nodeOrdinal(node: OntologyNode): number | null {
  const columnOrdinal = node.physical_mappings?.[0]?.column_refs?.[0]?.ordinal ?? null;
  return columnOrdinal ?? jsonNumber(node.metadata?.ordinal);
}

function sortNodesForSemanticCell(left: OntologyNode, right: OntologyNode): number {
  const leftOrdinal = nodeOrdinal(left);
  const rightOrdinal = nodeOrdinal(right);
  if (leftOrdinal !== null && rightOrdinal !== null && leftOrdinal !== rightOrdinal) {
    return leftOrdinal - rightOrdinal;
  }
  if (leftOrdinal !== null && rightOrdinal === null) return -1;
  if (leftOrdinal === null && rightOrdinal !== null) return 1;
  const kindCompare = left.kind.localeCompare(right.kind, "en-US");
  if (kindCompare !== 0) return kindCompare;
  return (left.business_name_ja || left.technical_name || left.id).localeCompare(
    right.business_name_ja || right.technical_name || right.id,
    "ja"
  );
}

export function layoutOntologyGraph(graph: OntologyGraph): Map<string, GraphPoint> {
  if (graph.nodes.length === 0) return new Map();
  const nodes: LayoutNode[] = graph.nodes.map((node) => ({ id: node.id }));
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const links: LayoutLink[] = graph.edges
    .filter(
      (edge) => nodeIds.has(edge.source_node_id) && nodeIds.has(edge.target_node_id)
    )
    .map((edge) => ({ source: edge.source_node_id, target: edge.target_node_id }));

  const simulation = forceSimulation(nodes)
    .force(
      "link",
      forceLink<LayoutNode, LayoutLink>(links)
        .id((node) => node.id)
        .distance(LINK_DISTANCE)
        .strength(0.6)
    )
    .force("charge", forceManyBody().strength(-620))
    .force("collide", forceCollide(COLLIDE_RADIUS))
    // 横長キャンバスに合わせ、縦方向をやや強めに引き寄せて楕円状に広げる。
    .force("x", forceX(0).strength(0.04))
    .force("y", forceY(0).strength(0.07))
    .stop();
  simulation.tick(SIMULATION_TICKS);

  return new Map(nodes.map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]));
}

// ER 図のような「エンティティ+関係」には force より階層(layered)配置の方が読みやすい。
// dagre は同期・決定論なので useMemo でそのまま使える(React Flow のノード実寸で配置)。
export function layoutOntologyGraphLayered(
  graph: OntologyGraph,
  options: { nodeWidth?: number; nodeHeight?: number } = {}
): Map<string, GraphPoint> {
  if (graph.nodes.length === 0) return new Map();
  const nodeWidth = options.nodeWidth ?? 200;
  const nodeHeight = options.nodeHeight ?? 64;
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setGraph({ rankdir: "LR", nodesep: 28, ranksep: 90, marginx: 16, marginy: 16 });
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  for (const node of graph.nodes) {
    dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight });
  }
  for (const edge of graph.edges) {
    if (nodeIds.has(edge.source_node_id) && nodeIds.has(edge.target_node_id)) {
      dagreGraph.setEdge(edge.source_node_id, edge.target_node_id);
    }
  }
  dagre.layout(dagreGraph);
  return new Map(
    graph.nodes.map((node) => {
      const placed = dagreGraph.node(node.id);
      // dagre は中心座標を返す。React Flow は左上原点なので変換する。
      return [
        node.id,
        { x: (placed?.x ?? 0) - nodeWidth / 2, y: (placed?.y ?? 0) - nodeHeight / 2 },
      ];
    })
  );
}

export function layoutOntologyGraphSemanticMatrix(
  graph: OntologyGraph,
  options: {
    nodeWidth?: number;
    nodeHeight?: number;
    nodeGap?: number;
    clusterGap?: number;
    laneGap?: number;
    lanePaddingY?: number;
    left?: number;
    top?: number;
  } = {}
): OntologyGraphSemanticLayout {
  const positions = new Map<string, GraphPoint>();
  const laneByNodeId = new Map<string, OntologyGraphSemanticLaneId>();
  const clusterByNodeId = new Map<string, string>();
  if (graph.nodes.length === 0) {
    return { positions, lanes: [], laneByNodeId, clusterByNodeId };
  }

  const nodeWidth = options.nodeWidth ?? 200;
  const nodeHeight = options.nodeHeight ?? 64;
  const nodeGap = options.nodeGap ?? 14;
  const clusterGap = options.clusterGap ?? 72;
  const laneGap = options.laneGap ?? 44;
  const lanePaddingY = options.lanePaddingY ?? 22;
  const left = options.left ?? 220;
  const top = options.top ?? 34;
  const incomingObjectCluster = new Map(
    graph.nodes
      .map((node) => [node.id, ontologyGraphObjectClusterKey(node)] as const)
      .filter((entry): entry is readonly [string, string] => Boolean(entry[1]))
  );

  for (const node of graph.nodes) {
    laneByNodeId.set(node.id, ontologyGraphSemanticLaneForKind(node.kind));
    const directCluster = incomingObjectCluster.get(node.id);
    if (directCluster) clusterByNodeId.set(node.id, directCluster);
  }

  for (const node of graph.nodes) {
    if (clusterByNodeId.has(node.id)) continue;
    const parentCluster = enumParentClusterKey(node, clusterByNodeId);
    if (parentCluster) {
      clusterByNodeId.set(node.id, parentCluster);
      continue;
    }
    const connectedCluster = graph.edges
      .flatMap((edge) => {
        if (edge.source_node_id === node.id) return [edge.target_node_id];
        if (edge.target_node_id === node.id) return [edge.source_node_id];
        return [];
      })
      .map((nodeId) => clusterByNodeId.get(nodeId) ?? incomingObjectCluster.get(nodeId) ?? null)
      .find((cluster): cluster is string => Boolean(cluster));
    clusterByNodeId.set(
      node.id,
      connectedCluster ?? stableClusterId(ontologyGraphSemanticLaneForKind(node.kind), node.id)
    );
  }

  const clusterOrder = new Map<string, number>();
  graph.nodes.forEach((node, index) => {
    const cluster = clusterByNodeId.get(node.id) ?? stableClusterId("node", node.id);
    clusterOrder.set(cluster, Math.min(clusterOrder.get(cluster) ?? index, index));
  });
  const clusters = [...clusterOrder]
    .sort((leftEntry, rightEntry) => {
      const indexCompare = leftEntry[1] - rightEntry[1];
      return indexCompare || leftEntry[0].localeCompare(rightEntry[0], "en-US");
    })
    .map(([cluster]) => cluster);
  const clusterIndex = new Map(clusters.map((cluster, index) => [cluster, index]));
  const cells = new Map<string, OntologyNode[]>();
  for (const node of graph.nodes) {
    const lane = laneByNodeId.get(node.id) ?? "business";
    const cluster = clusterByNodeId.get(node.id) ?? stableClusterId("node", node.id);
    const key = `${lane}\u0000${cluster}`;
    const cell = cells.get(key) ?? [];
    cell.push(node);
    cells.set(key, cell);
  }
  for (const cell of cells.values()) {
    cell.sort(sortNodesForSemanticCell);
  }

  const laneHeights = new Map<OntologyGraphSemanticLaneId, number>();
  for (const lane of SEMANTIC_LANES) {
    let maxRows = 1;
    for (const cluster of clusters) {
      maxRows = Math.max(maxRows, cells.get(`${lane}\u0000${cluster}`)?.length ?? 0);
    }
    laneHeights.set(
      lane,
      lanePaddingY * 2 + maxRows * nodeHeight + Math.max(0, maxRows - 1) * nodeGap
    );
  }

  const lanes: OntologyGraphSemanticLane[] = [];
  let y = top;
  for (const lane of SEMANTIC_LANES) {
    const height = laneHeights.get(lane) ?? nodeHeight;
    lanes.push({
      id: lane,
      y,
      height,
      nodeCount: graph.nodes.filter((node) => laneByNodeId.get(node.id) === lane).length,
    });
    y += height + laneGap;
  }

  for (const [cellKey, cellNodes] of cells) {
    const [lane, cluster] = cellKey.split("\u0000") as [OntologyGraphSemanticLaneId, string];
    const laneMeta = lanes.find((item) => item.id === lane);
    const x = left + (clusterIndex.get(cluster) ?? 0) * (nodeWidth + clusterGap);
    cellNodes.forEach((node, index) => {
      positions.set(node.id, {
        x,
        y: (laneMeta?.y ?? top) + lanePaddingY + index * (nodeHeight + nodeGap),
      });
    });
  }

  return { positions, lanes, laneByNodeId, clusterByNodeId };
}

// SVG viewBox など固定領域向けに、レイアウト結果を領域内へ平行移動・縮小(拡大はしない)する。
export function fitLayoutToBounds(
  positions: Map<string, GraphPoint>,
  width: number,
  height: number,
  margin: number
): Map<string, GraphPoint> {
  if (positions.size === 0) return new Map();
  const points = [...positions.values()];
  const minX = Math.min(...points.map((point) => point.x));
  const maxX = Math.max(...points.map((point) => point.x));
  const minY = Math.min(...points.map((point) => point.y));
  const maxY = Math.max(...points.map((point) => point.y));
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const innerWidth = Math.max(1, width - margin * 2);
  const innerHeight = Math.max(1, height - margin * 2);
  const scale = Math.min(innerWidth / spanX, innerHeight / spanY, 1);
  const offsetX = (width - spanX * scale) / 2 - minX * scale;
  const offsetY = (height - spanY * scale) / 2 - minY * scale;
  return new Map(
    [...positions].map(([id, point]) => [
      id,
      { x: point.x * scale + offsetX, y: point.y * scale + offsetY },
    ])
  );
}
