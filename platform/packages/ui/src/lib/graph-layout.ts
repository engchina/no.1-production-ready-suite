/** 製品固有の配置戦略が共有画布へ渡す、測定済みの寸法と位置。 */
export interface GraphPoint {
  x: number;
  y: number;
}

export interface GraphLayoutNode {
  id: string;
  width: number;
  height: number;
}

export interface GraphLayoutEdge {
  source: string;
  target: string;
}

export interface GraphLayoutInput {
  nodes: GraphLayoutNode[];
  edges: GraphLayoutEdge[];
  columnGap: number;
  rowGap: number;
}

export interface GraphLayoutResult {
  positions: Map<string, GraphPoint>;
  /** 循環を配置上だけ切った辺。元の図の関係は削除しない。 */
  brokenCycleEdges: GraphLayoutEdge[];
}

export type GraphLayoutStrategy = (input: GraphLayoutInput) => GraphLayoutResult;

/** 親を左、子を右に配置する。入力順を保ち、循環や部分取得でも有限な位置を返す。 */
export const layoutGraphHierarchy: GraphLayoutStrategy = (input) => {
  if (![input.columnGap, input.rowGap].every((value) => Number.isFinite(value) && value >= 0)) {
    throw new RangeError("図の間隔は有限の非負数で指定してください。");
  }
  const nodes = new Map<string, GraphLayoutNode>();
  for (const node of input.nodes) {
    if (nodes.has(node.id)) throw new Error("図のノードIDが重複しています。");
    if (![node.width, node.height].every((value) => Number.isFinite(value) && value > 0)) {
      throw new RangeError("図の寸法は有限の正数で指定してください。");
    }
    nodes.set(node.id, node);
  }
  const children = new Map(input.nodes.map((node) => [node.id, new Set<string>()]));
  const parents = new Map(input.nodes.map((node) => [node.id, new Set<string>()]));
  for (const edge of input.edges) {
    if (edge.source === edge.target || !nodes.has(edge.source) || !nodes.has(edge.target)) continue;
    children.get(edge.source)!.add(edge.target);
    parents.get(edge.target)!.add(edge.source);
  }
  const pending = new Set(nodes.keys());
  const indegrees = new Map([...parents].map(([id, ids]) => [id, ids.size]));
  const ready = [...pending].filter((id) => indegrees.get(id) === 0);
  const levels = new Map<string, number>();
  const brokenCycleEdges: GraphLayoutEdge[] = [];
  while (pending.size > 0) {
    if (ready.length === 0) {
      const seed = pending.values().next().value!;
      for (const source of parents.get(seed)!) {
        if (pending.has(source)) brokenCycleEdges.push({ source, target: seed });
      }
      ready.push(seed);
    }
    const id = ready.shift()!;
    if (!pending.delete(id)) continue;
    const parentLevels = [...parents.get(id)!].flatMap((parent) => {
      const level = levels.get(parent);
      return level === undefined ? [] : [level + 1];
    });
    levels.set(id, parentLevels.length ? Math.max(...parentLevels) : 0);
    for (const child of children.get(id)!) {
      if (!pending.has(child)) continue;
      const remaining = indegrees.get(child)! - 1;
      indegrees.set(child, remaining);
      if (remaining === 0) ready.push(child);
    }
  }
  const columns = new Map<number, GraphLayoutNode[]>();
  for (const node of input.nodes) {
    const level = levels.get(node.id)!;
    const column = columns.get(level) ?? [];
    column.push(node);
    columns.set(level, column);
  }
  const heightOf = (column: GraphLayoutNode[]) =>
    column.reduce((height, node) => height + node.height, 0) + Math.max(0, column.length - 1) * input.rowGap;
  const maxHeight = Math.max(0, ...[...columns.values()].map(heightOf));
  const positions = new Map<string, GraphPoint>();
  let x = 0;
  for (const [, column] of [...columns].sort(([a], [b]) => a - b)) {
    let y = (maxHeight - heightOf(column)) / 2;
    for (const node of column) {
      if (!Number.isFinite(x) || !Number.isFinite(y)) throw new RangeError("図の寸法が配置可能な範囲を超えています。");
      positions.set(node.id, { x, y });
      y += node.height + input.rowGap;
    }
    x += Math.max(...column.map((node) => node.width)) + input.columnGap;
  }
  return { positions, brokenCycleEdges };
};
