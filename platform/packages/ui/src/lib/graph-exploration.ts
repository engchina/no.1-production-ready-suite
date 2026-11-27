/** 二つの端点の組を向きごとにまとめ、詳細で実際の関係を一件ずつ参照できるようにする。 */
export function directedGraphBundles<T>(
  edges: readonly T[],
  endpoints: (edge: T) => readonly [string, string],
): Map<string, T[]> {
  const bundles = new Map<string, T[]>();
  for (const edge of edges) {
    const key = JSON.stringify(endpoints(edge));
    const group = bundles.get(key);
    if (group) group.push(edge);
    else bundles.set(key, [edge]);
  }
  return bundles;
}

export interface GraphNeighborGroup {
  id: string;
  owner: string;
  kind: string;
  outgoing: boolean;
  neighbors: string[];
}

/** 一つの近傍が複数の展開元を持てる。展開元別のまとまりと表示範囲を別々に管理する。 */
export function graphNeighborGroups(
  edges: readonly { source: string; target: string; kind: string }[],
  batch: number,
): GraphNeighborGroup[] {
  const groups = new Map<string, { owner: string; outgoing: boolean; kind: string; neighbors: Set<string> }>();
  function add(owner: string, neighbor: string, kind: string, outgoing: boolean) {
    if (owner === neighbor) return;
    const id = JSON.stringify([owner, kind, outgoing]);
    let group = groups.get(id);
    if (!group) {
      group = { owner, kind, outgoing, neighbors: new Set() };
      groups.set(id, group);
    }
    group.neighbors.add(neighbor);
  }
  for (const { source, target, kind } of edges) {
    add(source, target, kind, true);
    add(target, source, kind, false);
  }
  return [...groups].filter(([, group]) => group.neighbors.size > batch).map(([id, group]) => ({
    ...group, id, neighbors: [...group.neighbors],
  }));
}

export function expandedGraphNeighborhood(
  ids: readonly string[], groups: readonly GraphNeighborGroup[],
  limits: ReadonlyMap<string, number>, protectedIds: ReadonlySet<string>,
) {
  const nodes = new Set(ids);
  const expanded = new Map<string, Set<string>>();
  for (const group of groups) {
    for (const id of group.neighbors) nodes.delete(id);
    expanded.set(group.id, new Set(group.neighbors.slice(0, limits.get(group.id) ?? 0)));
  }
  for (const group of groups) {
    nodes.add(group.owner);
    for (const id of expanded.get(group.id)!) nodes.add(id);
  }
  for (const id of protectedIds) nodes.add(id);

  // 同じ節点に別の展開元から到達できても、閉じた展開元の線だけは消す。
  function showsRelation(source: string, target: string, kind: string) {
    const outgoing = expanded.get(JSON.stringify([source, kind, true]));
    const incoming = expanded.get(JSON.stringify([target, kind, false]));
    if (!outgoing && !incoming) return true;
    return Boolean(outgoing?.has(target) || incoming?.has(source));
  }
  return { nodes, showsRelation };
}

/** 探索の起点から到達できる追加分だけを返す。循環と複数の親を持つ原文も一度だけ辿る。 */
export function reachableGraphBranches<T>(
  seeds: readonly string[], branches: readonly T[], ownerOf: (branch: T) => string,
  childrenOf: (branch: T) => readonly string[],
): T[] {
  const byOwner = new Map<string, T[]>();
  for (const branch of branches) {
    const owner = ownerOf(branch);
    const owned = byOwner.get(owner);
    if (owned) owned.push(branch);
    else byOwner.set(owner, [branch]);
  }
  const visible: T[] = [];
  const queue = [...seeds];
  const visited = new Set(seeds);
  for (let i = 0; i < queue.length; i++) {
    for (const branch of byOwner.get(queue[i]) ?? []) {
      visible.push(branch);
      for (const id of childrenOf(branch)) {
        if (!visited.has(id)) { visited.add(id); queue.push(id); }
      }
    }
  }
  return visible;
}
