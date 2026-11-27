import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { WIKI_GRAPH_SETTINGS } from "./wiki-graph-settings";
import {
  addToWikiGraph,
  collapseWikiNode,
  connectWikiGraph,
  edgeKey,
  emptyWikiGraph,
  keepWikiNodes,
  recordNeighbors,
  remainingNeighbors,
  removeWikiNodes,
  replaceWikiGraph,
  showNeighbors,
  type WikiGraphEdge,
  type WikiGraphNode,
  type WikiGraphState,
} from "./wiki-graph-model";
import { loadNeighbors, documentLinks, treeNode } from "./wiki-graph-loaders";
import { wikiApi, type WikiScope } from "./wiki-api";

/**
 * Wiki の図の状態と操作（展開・折りたたみ・外す・残す・つなぐ・置き換え・足す・位置合わせ）。
 * 読み込みの失敗は `failure` に置き、画面が同じ場所で再試行を出す。
 */
export function useWikiGraph(scope: WikiScope) {
  const client = useQueryClient();
  const [state, setState] = useState<WikiGraphState>(emptyWikiGraph);
  // 読み込みの中で最新の状態を読む（展開を続けて押しても古い状態に重ねない）。
  const current = useRef(state);
  const update = useCallback((change: (old: WikiGraphState) => WikiGraphState) => {
    current.current = change(current.current);
    setState(current.current);
  }, []);
  const [pending, setPending] = useState(0);
  const [failure, setFailure] = useState<{ error: unknown; retry: () => void } | null>(null);

  const run = useCallback(async function attempt(work: () => Promise<void>): Promise<void> {
    setPending((value) => value + 1);
    setFailure(null);
    try {
      await work();
    } catch (error) {
      setFailure({ error, retry: () => void attempt(work) });
    } finally {
      setPending((value) => value - 1);
    }
  }, []);

  const expand = useCallback((id: string, kinds?: readonly string[]) => {
    const owner = id.startsWith("more:") ? id.slice("more:".length) : id;
    const node = current.current.nodes.get(owner);
    if (!node) return;
    void run(async () => {
      const known = current.current.expansions.get(owner);
      const sameKinds = known && JSON.stringify(known.kinds ?? null) === JSON.stringify(kinds ?? null);
      const known_remaining = sameKinds ? remainingNeighbors(current.current, owner) : 0;
      const loadedMissing = sameKinds ? known!.neighbors.filter((item) => !current.current.nodes.has(item.node.id)).length : 0;
      if (!sameKinds || (loadedMissing === 0 && !known!.complete)) {
        const loaded = await loadNeighbors(client, scope, node, sameKinds ? known : undefined, kinds);
        update((old) => recordNeighbors(old, owner, loaded.neighbors, loaded.complete, kinds, loaded.total));
      } else if (!known_remaining) {
        return;
      }
      update((old) => showNeighbors(old, owner, WIKI_GRAPH_SETTINGS.expandBatch));
    });
  }, [client, run, scope, update]);

  const collapse = useCallback((id: string) => update((old) => collapseWikiNode(old, id)), [update]);
  const remove = useCallback((ids: readonly string[]) => update((old) => removeWikiNodes(old, ids)), [update]);
  const keep = useCallback((ids: readonly string[]) => update((old) => keepWikiNodes(old, ids)), [update]);
  const replace = useCallback((nodes: readonly WikiGraphNode[], edges: readonly WikiGraphEdge[], matched: readonly string[] = []) =>
    update((old) => replaceWikiGraph(old, nodes, edges, matched)), [update]);
  const add = useCallback((nodes: readonly WikiGraphNode[], edges: readonly WikiGraphEdge[], matched: readonly string[] = []) =>
    update((old) => addToWikiGraph(old, nodes, edges, { matched })), [update]);
  const clear = useCallback(() => update((old) => replaceWikiGraph(old, [], [])), [update]);

  /** 選んだノードの間の関係を足す（単位は同じ文書の依存と別の文書への参照、文書は文書の間の参照）。 */
  const connect = useCallback((ids: readonly string[]) => {
    const nodes = ids.map((id) => current.current.nodes.get(id)).filter((node): node is WikiGraphNode => Boolean(node));
    const units = nodes.filter((node) => node.kind === "unit").map((node) => node.id);
    const documents = nodes.filter((node) => node.kind === "document").map((node) => node.id);
    void run(async () => {
      const edges: WikiGraphEdge[] = [];
      if (units.length > 1) {
        const found = await wikiApi.connections(scope, units);
        edges.push(...found.connections.map((link) => ({
          id: edgeKey(link.source_id, link.relation, link.target_id), source: link.source_id, target: link.target_id, kind: link.relation, scope: link.scope,
        })));
      }
      const wanted = new Set(documents);
      for (const id of documents.slice(0, WIKI_GRAPH_SETTINGS.sampleDocuments)) {
        const graph = await wikiApi.documentGraph(scope, 0, 100, id);
        edges.push(...documentLinks(graph).filter((edge) => wanted.has(edge.source) && wanted.has(edge.target)));
      }
      update((old) => connectWikiGraph(old, edges));
    });
  }, [run, scope, update]);

  /** 単位を、文書から章節をたどった位置に出す（図内検索で当たった単位。#5881 で消えた位置合わせ）。 */
  const reveal = useCallback(async (unit: WikiGraphNode) => {
    const nodes: WikiGraphNode[] = [];
    const edges: WikiGraphEdge[] = [];
    if (!current.current.nodes.has(unit.document_id)) {
      nodes.push({ id: unit.document_id, kind: "document", label: unit.document_name, document_id: unit.document_id, document_name: unit.document_name });
    }
    const { path } = await wikiApi.locate(scope, unit.document_id, unit.id);
    let parent = unit.document_id;
    for (const step of path) {
      if (!current.current.nodes.has(step.id) && step.id !== unit.id) {
        const page = await wikiApi.tree(scope, unit.document_id, parent === unit.document_id ? null : parent, step.index, 1);
        const item = page.items[0];
        if (item?.id === step.id) nodes.push(treeNode(item));
      }
      edges.push({ id: edgeKey(parent, "contains", step.id), source: parent, target: step.id, kind: "contains" });
      parent = step.id;
    }
    update((old) => addToWikiGraph(old, nodes, edges));
  }, [scope, update]);

  return { state, pending: pending > 0, failure, run, expand, collapse, remove, keep, connect, replace, add, clear, reveal };
}
