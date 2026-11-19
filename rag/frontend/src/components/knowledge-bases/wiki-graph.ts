import { GRAPH_NODE_CARD_SIZE, layoutGraphHierarchy, type GraphNodeDimensions, type GraphPoint } from "@production-ready/ui/graph";

import type { WikiGraphDocument, WikiGraphOverview, WikiGraphRelation, WikiGraphScope, WikiGraphTreeNode } from "@/lib/api";

/** 1 段の表示の上限。超えた分は「さらに表示」で読む。 */
export const WIKI_GRAPH_PAGE_SIZE = 100;

/** 関係の種類の並び（凡例・一覧の順。backend の辿る優先の順と同じ）。 */
export const WIKI_RELATIONS: readonly WikiGraphRelation[] = ["exception", "prerequisite", "warning", "continuation", "expected_result", "reference"];

export type WikiNodeKind = "document" | "section" | "unit" | "more";

export interface WikiTreeState {
  nodes: ReadonlyMap<string, WikiGraphTreeNode>;
  /** 親の節点（文書の節点を含む）→ 読み込み済みの子と、段の全件数。 */
  children: ReadonlyMap<string, { ids: readonly string[]; total: number }>;
  expanded: ReadonlySet<string>;
}

export const emptyWikiTree = (): WikiTreeState => ({ nodes: new Map(), children: new Map(), expanded: new Set() });

export interface WikiViewNode {
  id: string;
  kind: WikiNodeKind;
  label: string;
  name: string;
  /** この節点が属する文書の節点（配置のまとまり）。 */
  documentNode: string;
  parent: string | null;
  expandable: boolean;
  expanded: boolean;
  document?: WikiGraphDocument;
  tree?: WikiGraphTreeNode;
  /** 「さらに表示」の節点の、まだ読んでいない件数。 */
  remaining?: number;
}

export interface WikiRelationEdge {
  id: string;
  source: string;
  target: string;
  relation: WikiGraphRelation;
  scope: WikiGraphScope;
}

export const moreNodeId = (parent: string) => `more:${parent}`;

export function stripExtension(name: string): string {
  return name.replace(/\.[A-Za-z0-9]{1,5}$/, "");
}

/** 開いている段をたどって、図に出す節点を文書の順に並べる。 */
export function visibleWikiNodes(overview: WikiGraphOverview, state: WikiTreeState, kindVisible: (kind: WikiNodeKind) => boolean): WikiViewNode[] {
  const result: WikiViewNode[] = [];
  const addChildren = (parent: string, documentNode: string) => {
    if (!state.expanded.has(parent)) return;
    const loaded = state.children.get(parent);
    if (!loaded) return;
    for (const id of loaded.ids) {
      const tree = state.nodes.get(id);
      if (!tree) continue;
      if (kindVisible(tree.kind)) {
        const name = tree.kind === "unit" && tree.summary ? `${tree.label}：${tree.summary}` : tree.path.join(" > ") || tree.label;
        result.push({ id, kind: tree.kind, label: tree.kind === "unit" ? tree.summary || tree.label : tree.label, name, documentNode, parent, tree, expandable: tree.child_count > 0, expanded: state.expanded.has(id) });
      }
      addChildren(id, documentNode);
    }
    if (loaded.ids.length < loaded.total) {
      result.push({ id: moreNodeId(parent), kind: "more", label: "", name: "", documentNode, parent, expandable: false, expanded: false, remaining: loaded.total - loaded.ids.length });
    }
  };
  for (const document of overview.documents) {
    if (kindVisible("document")) {
      result.push({ id: document.id, kind: "document", label: stripExtension(document.name), name: document.name, documentNode: document.id, parent: null, document, expandable: document.unit_count > 0, expanded: state.expanded.has(document.id) });
    }
    addChildren(document.id, document.id);
  }
  return result;
}

/**
 * 表示中の単位の間だけの線（同じ文書の依存と文書をまたぐ関連。起点 → 終点）。
 * 片方の端が表示されていない関連は線にせず、節点の件数で示す。
 */
export function wikiRelationEdges(nodes: readonly WikiViewNode[], relationVisible: (relation: WikiGraphRelation) => boolean): WikiRelationEdge[] {
  const visible = new Set(nodes.map((node) => node.id));
  const seen = new Set<string>();
  const edges: WikiRelationEdge[] = [];
  for (const node of nodes) {
    if (node.kind !== "unit" || !node.tree) continue;
    for (const edge of node.tree.outgoing) {
      if (!visible.has(edge.target) || !relationVisible(edge.relation)) continue;
      const id = `${edge.scope}:${node.id}:${edge.target}`;
      if (seen.has(id)) continue;
      seen.add(id);
      edges.push({ id, source: node.id, target: edge.target, relation: edge.relation, scope: edge.scope });
    }
  }
  return edges;
}

const COLUMN_GAP = GRAPH_NODE_CARD_SIZE.width / 4;
const ROW_GAP = GRAPH_NODE_CARD_SIZE.height / 4;
const BLOCK_COLUMN_GAP = GRAPH_NODE_CARD_SIZE.width;
const BLOCK_ROW_GAP = GRAPH_NODE_CARD_SIZE.height;

/**
 * 配置の戦略。文書の中は親を左・子を右の階層にして 1 つのまとまりにし、
 * 表示中の関連でつながる文書は関連の向きで段に並べ、つながらない文書はその右に格子で置く（横長の画布に収める）。
 */
export function layoutWiki(
  nodes: readonly WikiViewNode[],
  documentEdges: ReadonlyArray<{ source: string; target: string }>,
  sizes: ReadonlyMap<string, GraphNodeDimensions>,
): Map<string, GraphPoint> {
  const groups = new Map<string, WikiViewNode[]>();
  for (const node of nodes) groups.set(node.documentNode, [...(groups.get(node.documentNode) ?? []), node]);
  const blocks = new Map<string, { width: number; height: number; positions: Map<string, GraphPoint> }>();
  for (const [documentNode, members] of groups) {
    const present = new Set(members.map((node) => node.id));
    const inner = layoutGraphHierarchy({
      nodes: members.map((node) => ({ id: node.id, ...(sizes.get(node.id) ?? GRAPH_NODE_CARD_SIZE) })),
      edges: members.flatMap((node) => (node.parent && present.has(node.parent) ? [{ source: node.parent, target: node.id }] : [])),
      columnGap: COLUMN_GAP, rowGap: ROW_GAP,
    });
    let width = 0, height = 0;
    for (const node of members) {
      const position = inner.positions.get(node.id)!;
      const size = sizes.get(node.id) ?? GRAPH_NODE_CARD_SIZE;
      width = Math.max(width, position.x + size.width);
      height = Math.max(height, position.y + size.height);
    }
    blocks.set(documentNode, { width, height, positions: inner.positions });
  }
  const linked = new Set<string>();
  const edges = documentEdges.filter((edge) => edge.source !== edge.target && blocks.has(edge.source) && blocks.has(edge.target));
  for (const edge of edges) { linked.add(edge.source); linked.add(edge.target); }
  const origins = new Map<string, GraphPoint>();
  let left = 0;
  if (linked.size) {
    const placed = layoutGraphHierarchy({
      nodes: [...blocks].filter(([id]) => linked.has(id)).map(([id, block]) => ({ id, width: block.width, height: block.height })),
      edges, columnGap: BLOCK_COLUMN_GAP, rowGap: BLOCK_ROW_GAP,
    });
    for (const [id, origin] of placed.positions) {
      origins.set(id, origin);
      left = Math.max(left, origin.x + blocks.get(id)!.width);
    }
    left += BLOCK_COLUMN_GAP;
  }
  const isolated = [...blocks.keys()].filter((id) => !linked.has(id));
  const columns = Math.max(4, Math.ceil(Math.sqrt(isolated.length * 2)));
  let x = left, y = 0, rowHeight = 0;
  isolated.forEach((id, index) => {
    if (index && index % columns === 0) { x = left; y += rowHeight + BLOCK_ROW_GAP; rowHeight = 0; }
    const block = blocks.get(id)!;
    origins.set(id, { x, y });
    x += block.width + BLOCK_ROW_GAP;
    rowHeight = Math.max(rowHeight, block.height);
  });
  const positions = new Map<string, GraphPoint>();
  for (const [id, block] of blocks) {
    const origin = origins.get(id)!;
    for (const [nodeId, position] of block.positions) positions.set(nodeId, { x: origin.x + position.x, y: origin.y + position.y });
  }
  return positions;
}
