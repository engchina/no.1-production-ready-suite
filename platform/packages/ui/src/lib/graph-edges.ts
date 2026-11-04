import type { GraphPoint } from "./graph-layout";

export interface GraphEdgeHandleSelection {
  sourceHandle: string;
  targetHandle: string;
  orientation: "horizontal" | "vertical";
}

/**
 * ノード中心の相対位置からエッジの接続ハンドルを選ぶ。
 * 縦優勢は上下ハンドル、横優勢は左右ハンドルを使い、
 * 固定 Left/Right だけのときに起きる自己ループ状の曲線を防ぐ。
 */
export function selectGraphEdgeHandles(
  source: GraphPoint,
  target: GraphPoint,
  options: { preferVertical?: boolean } = {}
): GraphEdgeHandleSelection {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  // preferVertical: 同一列の別行は dx 優勢でも上下で結び、途中のノードを真横に貫通する曲線を避ける。
  if (Math.abs(dy) > Math.abs(dx) || (options.preferVertical && dy !== 0)) {
    return dy >= 0
      ? { sourceHandle: "s-bottom", targetHandle: "t-top", orientation: "vertical" }
      : { sourceHandle: "s-top", targetHandle: "t-bottom", orientation: "vertical" };
  }
  return dx >= 0
    ? { sourceHandle: "s-right", targetHandle: "t-left", orientation: "horizontal" }
    : { sourceHandle: "s-left", targetHandle: "t-right", orientation: "horizontal" };
}

/**
 * 同一ノードペア間の並行エッジ用の 2 次ベジェ経路。
 * 同じ y の bezier/smoothstep は直線に潰れて完全に重なるため、進行方向の
 * 単位法線に沿って制御点をずらした弧で分離する。ラベルは曲線の t=0.5 点。
 */
export function graphParallelEdgeGeometry(
  source: GraphPoint,
  target: GraphPoint,
  offset: number
): { path: string; labelX: number; labelY: number } {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const length = Math.hypot(dx, dy) || 1;
  // 2 次ベジェの t=0.5 での実オフセットは制御点オフセットの半分なので 2 倍しておく
  const controlX = (source.x + target.x) / 2 + (-dy / length) * offset * 2;
  const controlY = (source.y + target.y) / 2 + (dx / length) * offset * 2;
  return {
    path: `M ${source.x},${source.y} Q ${controlX},${controlY} ${target.x},${target.y}`,
    labelX: 0.25 * source.x + 0.5 * controlX + 0.25 * target.x,
    labelY: 0.25 * source.y + 0.5 * controlY + 0.25 * target.y,
  };
}

/**
 * ノードドラッグの position change だけを座標上書き Map へ反映する
 * (select/dimensions/remove 等は無視。変更が無ければ同一 Map 参照を返す)。
 * controlled flow でこの反映を怠るとドラッグがスナップバックする。
 */
export function applyGraphNodePositionChanges(
  overrides: Map<string, GraphPoint>,
  changes: ReadonlyArray<{ type: string; id?: string; position?: GraphPoint }>
): Map<string, GraphPoint> {
  let next: Map<string, GraphPoint> | null = null;
  for (const change of changes) {
    if (change.type !== "position" || !change.id || !change.position) continue;
    if (!Number.isFinite(change.position.x) || !Number.isFinite(change.position.y)) continue;
    next ??= new Map(overrides);
    next.set(change.id, { x: change.position.x, y: change.position.y });
  }
  return next ?? overrides;
}

/** ホバー中のノードと、辺で直接つながるノードの id。ホバーが無ければ null。 */
export function graphNeighborIds(
  nodeId: string | null,
  edges: ReadonlyArray<{ source: string; target: string }>
): Set<string> | null {
  if (!nodeId) return null;
  const neighbors = new Set([nodeId]);
  for (const edge of edges) {
    if (edge.source === nodeId) neighbors.add(edge.target);
    else if (edge.target === nodeId) neighbors.add(edge.source);
  }
  return neighbors;
}

/** 辺の線の style。強調は色と太さの両方で示す（color-not-only）。 */
export function graphEdgeStyle({
  stroke = "var(--color-graph-line)",
  width = 1.25,
  dash,
  emphasized = false,
  opacity = 1,
}: {
  stroke?: string;
  width?: number;
  dash?: string;
  emphasized?: boolean;
  opacity?: number;
} = {}) {
  return {
    stroke: emphasized ? "var(--color-accent-fg)" : stroke,
    strokeWidth: emphasized ? 2.5 : width,
    strokeDasharray: dash,
    opacity,
  };
}

/** 辺のラベルの字と地。 */
export const GRAPH_EDGE_LABEL_PROPS = {
  labelStyle: { fill: "var(--color-fg-muted)", fontSize: "var(--font-size-xs)", fontWeight: 600 },
  labelBgStyle: { fill: "var(--color-surface)", fillOpacity: 0.92 },
  labelBgPadding: [6, 3] as [number, number],
  labelBgBorderRadius: 4,
};

export interface GraphChainEdge {
  /** 根から端の節点までの id の列。 */
  source: readonly string[];
  target: readonly string[];
  count: number;
}

export interface GraphAggregatedEdge {
  id: string;
  source: string;
  target: string;
  count: number;
}

const deepestVisible = (chain: readonly string[], visible: ReadonlySet<string>): string | undefined => {
  for (let index = chain.length - 1; index >= 0; index -= 1) {
    if (visible.has(chain[index]!)) return chain[index];
  }
  return undefined;
};

/**
 * 階層の中の辺を、いま見えている最も近い祖先へまとめて件数を足す。
 * 同じ節点に閉じる辺と、見えている祖先が無い辺は線にしない。
 */
export function aggregateGraphEdges(edges: ReadonlyArray<GraphChainEdge>, visible: ReadonlySet<string>): GraphAggregatedEdge[] {
  const merged = new Map<string, GraphAggregatedEdge>();
  for (const edge of edges) {
    const source = deepestVisible(edge.source, visible);
    const target = deepestVisible(edge.target, visible);
    if (!source || !target || source === target) continue;
    const id = `${source}:${target}`;
    const current = merged.get(id);
    if (current) current.count += edge.count;
    else merged.set(id, { id, source, target, count: edge.count });
  }
  return [...merged.values()];
}
