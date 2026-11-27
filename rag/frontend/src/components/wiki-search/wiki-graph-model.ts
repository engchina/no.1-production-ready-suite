import type { WikiGraphRelation, WikiGraphScope } from "@/lib/api";

/**
 * Wiki の図の状態（#5922）。Neo4j Browser と同じく、図は「今出しているノード」の集合で、
 * 展開・図内検索で足し、外す・選んだノードだけ残す・画面をクリアで減らす。描画は共有の GraphExplorer。
 */
export type WikiNodeKind = "document" | "section" | "unit" | "more";
export type WikiEdgeKind = WikiGraphRelation | "contains" | "more";

export interface WikiGraphNode {
  id: string;
  kind: WikiNodeKind;
  label: string;
  document_id: string;
  document_name: string;
  path?: string[];
  excerpt?: string;
  page?: number | string | null;
  line?: number | string | null;
  /** 文書の参照の件数（見本の並び）。 */
  references?: number;
  /** 「さらに展開」の点の展開元と、まだ出していない件数。 */
  owner?: string;
  remaining?: number;
}

export interface WikiGraphEdge {
  id: string;
  source: string;
  target: string;
  kind: WikiEdgeKind;
  scope?: WikiGraphScope;
  /** 文書の間の参照の件数（まとめた線）。 */
  count?: number;
}

export interface WikiNeighbor {
  node: WikiGraphNode;
  edge: WikiGraphEdge;
}

/** 展開元ごとの、分かっている隣接（並びは展開の順）と、出した数。 */
export interface WikiExpansion {
  neighbors: WikiNeighbor[];
  shown: number;
  /** 隣接をすべて読んだ（false なら続きをサーバーから読む）。 */
  complete: boolean;
  /** 「関係の種類を選んで展開」で選んだ種類（無ければすべて）。 */
  kinds?: readonly string[];
  /** サーバーが返した隣接の全件数（読み切っていないときの残りの件数に使う）。 */
  total?: number;
}

export interface WikiGraphState {
  nodes: Map<string, WikiGraphNode>;
  edges: Map<string, WikiGraphEdge>;
  expansions: Map<string, WikiExpansion>;
  /** ノードを足した展開元（折りたたむ対象）。最初の表示・検索で足したノードは持たない。 */
  owners: Map<string, string>;
  matched: Set<string>;
  /** 図にあるほかの原文との関係を照会した原文（足した原文だけを次に照会する。#5953）。 */
  linked: Set<string>;
  /** 図を置き換えた回数（配置をやり直す）。 */
  generation: number;
}

export const edgeKey = (source: string, kind: string, target: string) => [source, kind, target].join(":");
export const moreId = (owner: string) => `more:${owner}`;

export function emptyWikiGraph(): WikiGraphState {
  return { nodes: new Map(), edges: new Map(), expansions: new Map(), owners: new Map(), matched: new Set(), linked: new Set(), generation: 0 };
}

function cloned(state: WikiGraphState): WikiGraphState {
  return {
    ...state, nodes: new Map(state.nodes), edges: new Map(state.edges), expansions: new Map(state.expansions), owners: new Map(state.owners),
    matched: new Set(state.matched), linked: new Set(state.linked),
  };
}

/** 図を置き換える（Wiki 検索の新しい検索・最初の見本）。 */
export function replaceWikiGraph(state: WikiGraphState, nodes: readonly WikiGraphNode[], edges: readonly WikiGraphEdge[], matched: readonly string[] = []): WikiGraphState {
  const next = emptyWikiGraph();
  next.generation = state.generation + 1;
  return addToWikiGraph(next, nodes, edges, { matched });
}

/** ノードと線を足す（既にあるノードは内容だけ更新し、展開元は変えない）。`linked` は関係を照会した原文。 */
export function addToWikiGraph(
  state: WikiGraphState,
  nodes: readonly WikiGraphNode[],
  edges: readonly WikiGraphEdge[],
  options: { owner?: string; matched?: readonly string[]; linked?: readonly string[] } = {},
): WikiGraphState {
  const next = cloned(state);
  for (const node of nodes) {
    if (!next.nodes.has(node.id) && options.owner) next.owners.set(node.id, options.owner);
    next.nodes.set(node.id, { ...next.nodes.get(node.id), ...node });
  }
  for (const edge of edges) next.edges.set(edge.id, edge);
  for (const id of options.matched ?? []) next.matched.add(id);
  for (const id of options.linked ?? []) next.linked.add(id);
  return next;
}

/** 線だけを足す（「選択したノードの間の関係を表示」）。 */
export function connectWikiGraph(state: WikiGraphState, edges: readonly WikiGraphEdge[]): WikiGraphState {
  return addToWikiGraph(state, [], edges);
}

/** 展開元の隣接を覚える（読んだ分を後ろに足し、重複は除く）。 */
export function recordNeighbors(
  state: WikiGraphState,
  owner: string,
  neighbors: readonly WikiNeighbor[],
  complete: boolean,
  kinds?: readonly string[],
  total?: number,
): WikiGraphState {
  const next = cloned(state);
  const previous = next.expansions.get(owner);
  const sameKinds = JSON.stringify(previous?.kinds ?? null) === JSON.stringify(kinds ?? null);
  const known = sameKinds ? [...(previous?.neighbors ?? [])] : [];
  const seen = new Set(known.map((item) => item.edge.id));
  for (const item of neighbors) {
    if (seen.has(item.edge.id)) continue;
    seen.add(item.edge.id);
    known.push(item);
  }
  next.expansions.set(owner, { neighbors: known, shown: sameKinds ? previous?.shown ?? 0 : 0, complete, kinds, total });
  return next;
}

/** 図にまだ無い隣接の数（同じノードへの複数の線は 1 つと数える）。 */
export function remainingNeighbors(state: WikiGraphState, owner: string): number {
  const expansion = state.expansions.get(owner);
  if (!expansion) return 0;
  const ids = new Set(expansion.neighbors.map((item) => item.node.id));
  const missing = [...ids].filter((id) => !state.nodes.has(id)).length;
  if (expansion.complete || expansion.total === undefined) return missing;
  return Math.max(missing, expansion.total - (ids.size - missing));
}

/**
 * 次の `batch` 件の隣接を出す。図にある隣接への線は件数に数えずに足す（つながりを常に見せる）。
 * 足したノードの展開元は `owner`。
 */
export function showNeighbors(state: WikiGraphState, owner: string, batch: number): WikiGraphState {
  const expansion = state.expansions.get(owner);
  if (!expansion) return state;
  const nodes: WikiGraphNode[] = [];
  const edges: WikiGraphEdge[] = [];
  const added = new Set<string>();
  for (const item of expansion.neighbors) {
    const present = state.nodes.has(item.node.id) || added.has(item.node.id);
    if (!present) {
      if (added.size >= batch) continue;
      added.add(item.node.id);
      nodes.push(item.node);
    }
    edges.push(item.edge);
  }
  const next = addToWikiGraph(state, nodes, edges, { owner });
  next.expansions.set(owner, { ...expansion, shown: expansion.shown + added.size });
  return next;
}

/** 展開元から足したノードを外す（その先で足したノードも外す。検索の一致は残す）。 */
export function collapseWikiNode(state: WikiGraphState, owner: string): WikiGraphState {
  const removing = new Set<string>();
  const queue = [owner];
  while (queue.length) {
    const current = queue.shift()!;
    for (const [id, by] of state.owners) {
      if (by === current && !removing.has(id) && !state.matched.has(id)) {
        removing.add(id);
        queue.push(id);
      }
    }
  }
  const next = removeWikiNodes(state, [...removing]);
  next.expansions.delete(owner);
  return next;
}

/** ノードを外す（「このノードを外す」）。そのノードの展開の記録も消す。 */
export function removeWikiNodes(state: WikiGraphState, ids: readonly string[]): WikiGraphState {
  const next = cloned(state);
  for (const id of ids) {
    next.nodes.delete(id);
    next.owners.delete(id);
    next.matched.delete(id);
    next.linked.delete(id);
    next.expansions.delete(id);
  }
  return next;
}

/** 選んだノードだけ残す。 */
export function keepWikiNodes(state: WikiGraphState, ids: readonly string[]): WikiGraphState {
  const keep = new Set(ids);
  return removeWikiNodes(state, [...state.nodes.keys()].filter((id) => !keep.has(id)));
}

export interface WikiGraphRow {
  node: WikiGraphNode;
  /** 図に出ている関係（両端が図にある線）の数。 */
  relations: number;
}

/**
 * 「一覧」の行（#5953）。いま図にある節点（「さらに展開」の点は含まない）と、図に出ている関係の数を名前の順で返す。
 * 関係図と同じ集合の別の見方なので、足した・外した節点がそのまま反映される。
 */
export function wikiGraphRows(state: WikiGraphState): WikiGraphRow[] {
  const relations = new Map<string, number>();
  for (const edge of state.edges.values()) {
    if (!state.nodes.has(edge.source) || !state.nodes.has(edge.target)) continue;
    relations.set(edge.source, (relations.get(edge.source) ?? 0) + 1);
    relations.set(edge.target, (relations.get(edge.target) ?? 0) + 1);
  }
  return [...state.nodes.values()]
    .map((node) => ({ node, relations: relations.get(node.id) ?? 0 }))
    .sort((a, b) => a.node.label.localeCompare(b.node.label, "ja", { numeric: true }));
}

/**
 * 関係を照会する原文（#5953）。`adding`（これから足す原文）と、まだ照会していない図の原文を先頭に、図の原文を
 * `limit` 件（API の上限）まで返す。新しい原文が無い・原文が合わせて 1 件以下なら空（照会しない）。
 */
export function unitsToConnect(state: WikiGraphState, limit: number, adding: readonly string[] = []): string[] {
  const present = [...state.nodes.values()].filter((node) => node.kind === "unit").map((node) => node.id);
  const fresh = [...new Set([...adding, ...present.filter((id) => !state.linked.has(id))])];
  const ids = [...new Set([...fresh, ...present])];
  return fresh.length && ids.length > 1 ? ids.slice(0, limit) : [];
}

/** 描く図。両端がある線だけ、まだ出していない隣接は展開元に「さらに展開」の点を付ける。 */
export function drawnWikiGraph(state: WikiGraphState): { nodes: WikiGraphNode[]; edges: WikiGraphEdge[] } {
  const nodes = [...state.nodes.values()];
  const edges = [...state.edges.values()].filter((edge) => state.nodes.has(edge.source) && state.nodes.has(edge.target));
  for (const [owner, expansion] of state.expansions) {
    if (!state.nodes.has(owner)) continue;
    const remaining = remainingNeighbors(state, owner);
    if (remaining > 0 || !expansion.complete) {
      const node = state.nodes.get(owner)!;
      const id = moreId(owner);
      nodes.push({ id, kind: "more", label: "", owner, remaining, document_id: node.document_id, document_name: node.document_name });
      edges.push({ id: edgeKey(owner, "more", id), source: owner, target: id, kind: "more" });
    }
  }
  return { nodes, edges };
}
