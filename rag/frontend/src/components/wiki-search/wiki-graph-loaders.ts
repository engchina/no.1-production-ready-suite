import type { QueryClient } from "@tanstack/react-query";
import type { WikiGraphRelation, WikiGraphRelationItem, WikiGraphTreeNode } from "@/lib/api";
import { WIKI_GRAPH_SETTINGS } from "./wiki-graph-settings";
import { edgeKey, type WikiExpansion, type WikiGraphEdge, type WikiGraphNode, type WikiNeighbor } from "./wiki-graph-model";
import { wikiApi, type WikiDocumentGraph, type WikiRelations, type WikiScope } from "./wiki-api";

/** 関係の種類（「構成」と、案内の依存・別の文書への参照の種類）。 */
export const WIKI_RELATION_KINDS: readonly WikiGraphRelation[] = ["reference", "prerequisite", "exception", "continuation", "expected_result", "warning"];

export const relationsKey = (scope: WikiScope, id: string) => ["wiki-explorer", scope, "all-relations", id] as const;
export const documentNeighborsKey = (scope: WikiScope, id: string) => ["wiki-explorer", scope, "document-neighbors", id] as const;

/** 単位の関係を 1 回で取り切り、同じ単位では使い回す（図と詳細で共有。#5916）。 */
export function fetchAllRelations(client: QueryClient, scope: WikiScope, id: string): Promise<WikiRelations> {
  return client.fetchQuery({ queryKey: relationsKey(scope, id), queryFn: ({ signal }) => wikiApi.allRelations(scope, id, signal), staleTime: 60_000 });
}

export function unitNode(unit: { id: string; label: string; document_id: string; document_name: string; path?: string[] }): WikiGraphNode {
  return { id: unit.id, kind: "unit", label: unit.label, document_id: unit.document_id, document_name: unit.document_name, path: unit.path };
}

export function treeNode(item: WikiGraphTreeNode): WikiGraphNode {
  const label = item.kind === "unit" ? item.label || item.summary || item.excerpt || item.id : item.label;
  return { id: item.id, kind: item.kind, label, document_id: item.document_id, document_name: item.document_name, path: item.path, excerpt: item.excerpt, page: item.page, line: item.line };
}

export function documentNode(doc: { id: string; document_id: string; name: string; reference_count?: number }): WikiGraphNode {
  return { id: doc.id, kind: "document", label: doc.name, document_id: doc.document_id, document_name: doc.name, references: doc.reference_count };
}

/** 単位の関係の行を隣接にする（相手が範囲の外の参照は開けないので除く）。 */
export function relationNeighbors(owner: string, items: readonly WikiGraphRelationItem[], kinds?: readonly string[]): WikiNeighbor[] {
  return items.flatMap((item) => {
    if (!item.unit || item.unit.id === owner || (kinds && !kinds.includes(item.relation))) return [];
    const [source, target] = item.direction === "outgoing" ? [owner, item.unit.id] : [item.unit.id, owner];
    return [{ node: unitNode(item.unit), edge: { id: edgeKey(source, item.relation, target), source, target, kind: item.relation, scope: item.scope } }];
  });
}

/** 文書の間の参照を線にする（向き・種類ごとに件数をまとめた 1 本）。 */
export function documentLinks(graph: WikiDocumentGraph): WikiGraphEdge[] {
  return graph.links.map((link) => ({
    id: edgeKey(link.source_id, link.relation, link.target_id), source: link.source_id, target: link.target_id,
    kind: link.relation, scope: "cross_document", count: link.count,
  }));
}

function containsNeighbors(parent: string, items: readonly WikiGraphTreeNode[]): WikiNeighbor[] {
  return items.map((item) => ({ node: treeNode(item), edge: { id: edgeKey(parent, "contains", item.id), source: parent, target: item.id, kind: "contains" } }));
}

interface Loaded {
  neighbors: WikiNeighbor[];
  complete: boolean;
  total?: number;
}

/**
 * 展開元の隣接の次の分を読む。単位は関係を 1 回で取り切る。章節は構成を 1 段ずつ、文書は構成と
 * 参照でつながる文書（構成を読み終えてから）。`kinds` は「関係の種類を選んで展開」で選んだ種類。
 */
export async function loadNeighbors(client: QueryClient, scope: WikiScope, node: WikiGraphNode, previous: WikiExpansion | undefined, kinds?: readonly string[]): Promise<Loaded> {
  const batch = WIKI_GRAPH_SETTINGS.expandBatch;
  if (node.kind === "unit") {
    const relations = await fetchAllRelations(client, scope, node.id);
    const neighbors = relationNeighbors(node.id, relations.items, kinds);
    return { neighbors, complete: true, total: new Set(neighbors.map((item) => item.node.id)).size };
  }
  const wantsContains = !kinds || kinds.includes("contains");
  const loadedChildren = previous?.neighbors.filter((item) => item.edge.kind === "contains").length ?? 0;
  let neighbors: WikiNeighbor[] = [];
  // 構成は 1 段を 25 件ずつ読む（読み終えたかは前の展開の記録で分かる）。
  let treeComplete = !wantsContains || Boolean(previous?.complete);
  let total: number | undefined;
  if (wantsContains && !treeComplete) {
    const page = await wikiApi.tree(scope, node.document_id, node.kind === "section" ? node.id : null, loadedChildren, batch);
    neighbors = containsNeighbors(node.id, page.items);
    total = page.total;
    treeComplete = loadedChildren + page.items.length >= page.total || !page.items.length;
  }
  if (node.kind !== "document") return { neighbors, complete: treeComplete, total };
  const relationKinds = kinds?.filter((kind) => kind !== "contains");
  if (kinds && !relationKinds?.length) return { neighbors, complete: treeComplete, total };
  const graph = await client.fetchQuery({
    queryKey: documentNeighborsKey(scope, node.id),
    queryFn: ({ signal }) => wikiApi.documentGraph(scope, 0, 100, node.id, signal),
    staleTime: 60_000,
  });
  const docs = new Map(graph.documents.map((doc) => [doc.id, documentNode(doc)]));
  const linked = documentLinks(graph).flatMap((edge) => {
    if (relationKinds && !relationKinds.includes(edge.kind)) return [];
    const other = edge.source === node.id ? edge.target : edge.source;
    const target = docs.get(other);
    return target ? [{ node: target, edge }] : [];
  });
  return { neighbors: [...neighbors, ...linked], complete: treeComplete, total: total === undefined ? undefined : total + docs.size };
}

/** 「関係の種類を選んで展開」の選択肢。単位は読み込み済みの関係にある種類（未読ならすべて）。 */
export function expandKinds(client: QueryClient, scope: WikiScope, node: WikiGraphNode | undefined): string[] {
  if (!node || node.kind === "more") return [];
  if (node.kind === "section") return [];
  if (node.kind === "document") return ["contains", ...WIKI_RELATION_KINDS];
  const cached = client.getQueryData<WikiRelations>(relationsKey(scope, node.id));
  if (!cached) return [...WIKI_RELATION_KINDS];
  return WIKI_RELATION_KINDS.filter((kind) => cached.items.some((item) => item.relation === kind && item.unit));
}
