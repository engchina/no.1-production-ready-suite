import { GRAPH_NODE_CARD_SIZE, layoutGraphHierarchy, type GraphChainEdge, type GraphNodeDimensions, type GraphPoint } from "@production-ready/ui/graph";

import type { StructureGraphDocument, StructureGraphOverview, StructureGraphReference, StructureGraphTreeNode } from "@/lib/api";

/** 1 段の表示の上限。超えた分は「さらに表示」で読む。 */
export const STRUCTURE_PAGE_SIZE = 100;

export interface StructureTreeState {
  nodes: ReadonlyMap<string, StructureGraphTreeNode>;
  /** 親の節点（文書の節点を含む）→ 読み込み済みの子と、段の全件数。 */
  children: ReadonlyMap<string, { ids: readonly string[]; total: number }>;
  expanded: ReadonlySet<string>;
  /** 開いた文書の版 → 章節の間の参照。 */
  references: ReadonlyMap<string, readonly StructureGraphReference[]>;
}

export const emptyStructureTree = (): StructureTreeState => ({ nodes: new Map(), children: new Map(), expanded: new Set(), references: new Map() });

export interface StructureViewNode {
  id: string;
  kind: string;
  label: string;
  name: string;
  /** この節点が属する文書の節点（配置のまとまり）。 */
  documentNode: string;
  parent: string | null;
  expandable: boolean;
  expanded: boolean;
  document?: StructureGraphDocument;
  tree?: StructureGraphTreeNode;
  /** 「さらに表示」の節点の、まだ読んでいない件数。 */
  remaining?: number;
}

export const moreNodeId = (parent: string) => `more:${parent}`;

/** 開いている段をたどって、図に出す節点を文書の順に並べる。 */
export function visibleStructureNodes(overview: StructureGraphOverview, state: StructureTreeState, kindVisible: (kind: string) => boolean): StructureViewNode[] {
  const result: StructureViewNode[] = [];
  const addChildren = (parent: string, documentNode: string) => {
    if (!state.expanded.has(parent)) return;
    const loaded = state.children.get(parent);
    if (!loaded) return;
    for (const id of loaded.ids) {
      const tree = state.nodes.get(id);
      if (!tree) continue;
      if (kindVisible(tree.type)) {
        result.push({
          id, kind: tree.type, label: tree.label, name: tree.name, documentNode, parent, tree,
          expandable: tree.child_count > 0, expanded: state.expanded.has(id),
        });
      }
      addChildren(id, documentNode);
    }
    if (loaded.ids.length < loaded.total) {
      result.push({
        id: moreNodeId(parent), kind: "more", label: "", name: "", documentNode, parent,
        expandable: false, expanded: false, remaining: loaded.total - loaded.ids.length,
      });
    }
  };
  for (const document of overview.documents) {
    if (kindVisible("document")) {
      result.push({
        id: document.id, kind: "document", label: stripExtension(document.name), name: document.name, documentNode: document.id, parent: null, document,
        expandable: document.has_sections, expanded: state.expanded.has(document.id),
      });
    }
    addChildren(document.id, document.id);
  }
  return result;
}

export function stripExtension(name: string): string {
  return name.replace(/\.[A-Za-z0-9]{1,5}$/, "");
}

/** 図の辺の元。読み込んだ文書の参照は章節の列で、まだ開いていない文書の組は総覧の件数で出す。 */
export function structureReferenceChains(overview: StructureGraphOverview, state: StructureTreeState): GraphChainEdge[] {
  const documentBySet = new Map(overview.documents.map((document) => [document.chunk_set_id, document.id]));
  const setByDocument = new Map(overview.documents.map((document) => [document.id, document.chunk_set_id]));
  const seen = new Set<string>();
  const edges: GraphChainEdge[] = [];
  for (const items of state.references.values()) {
    for (const item of items) {
      const source = documentBySet.get(item.source_set);
      const target = documentBySet.get(item.target_set);
      if (!source || !target) continue;
      // 両端の文書を開くと同じ参照を 2 回読むので、1 回に数える。
      const key = JSON.stringify([item.source_set, item.target_set, item.source_chain, item.target_chain]);
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({ source: [source, ...item.source_chain], target: [target, ...item.target_chain], count: item.count });
    }
  }
  for (const edge of overview.edges) {
    if (state.references.has(setByDocument.get(edge.source) ?? "") || state.references.has(setByDocument.get(edge.target) ?? "")) continue;
    edges.push({ source: [edge.source], target: [edge.target], count: edge.count });
  }
  return edges;
}

const COLUMN_GAP = GRAPH_NODE_CARD_SIZE.width / 4;
const ROW_GAP = GRAPH_NODE_CARD_SIZE.height / 4;
const BLOCK_COLUMN_GAP = GRAPH_NODE_CARD_SIZE.width;
const BLOCK_ROW_GAP = GRAPH_NODE_CARD_SIZE.height;

/**
 * 配置の戦略。文書の中は親を左・子を右の階層にして 1 つのまとまりにし、
 * 参照のある文書は参照の向きで段に並べ、参照の無い文書はその下に格子で置く。
 */
export function layoutStructure(
  nodes: readonly StructureViewNode[],
  documentEdges: ReadonlyArray<{ source: string; target: string }>,
  sizes: ReadonlyMap<string, GraphNodeDimensions>,
): Map<string, GraphPoint> {
  const groups = new Map<string, StructureViewNode[]>();
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
  let bottom = 0;
  if (linked.size) {
    const placed = layoutGraphHierarchy({
      nodes: [...blocks].filter(([id]) => linked.has(id)).map(([id, block]) => ({ id, width: block.width, height: block.height })),
      edges, columnGap: BLOCK_COLUMN_GAP, rowGap: BLOCK_ROW_GAP,
    });
    for (const [id, origin] of placed.positions) {
      origins.set(id, origin);
      bottom = Math.max(bottom, origin.y + blocks.get(id)!.height);
    }
    bottom += BLOCK_ROW_GAP * 2;
  }
  const isolated = [...blocks.keys()].filter((id) => !linked.has(id));
  const columns = Math.max(4, Math.ceil(Math.sqrt(isolated.length)));
  let x = 0, y = bottom, rowHeight = 0;
  isolated.forEach((id, index) => {
    if (index && index % columns === 0) { x = 0; y += rowHeight + BLOCK_ROW_GAP; rowHeight = 0; }
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
