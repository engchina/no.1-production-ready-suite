// 関係グラフの force-directed(neo4j 風)レイアウト。
// d3-force は初期配置(phyllotaxis)も jiggle も決定論のため、同じグラフは常に同じ座標になる。
// UI の外で同期 tick するので、コンポーネントは結果の Map を描画するだけでよい。
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationNodeDatum,
} from "d3-force";

import type { OntologyGraph } from "./types";

export interface GraphPoint {
  x: number;
  y: number;
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
