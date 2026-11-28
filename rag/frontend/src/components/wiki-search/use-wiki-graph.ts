import { useCallback, useEffect, useRef, useState } from "react";
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
  unitsToConnect,
  unpickWikiNode,
  type WikiGraphEdge,
  type WikiGraphNode,
  type WikiGraphState,
} from "./wiki-graph-model";
import { connectionEdges, documentLinks, fetchDocumentNeighbors, loadNeighbors, treeNode } from "./wiki-graph-loaders";
import { WIKI_CONNECTIONS_LIMIT, wikiApi, type WikiScope } from "./wiki-api";

/**
 * Wiki の図の状態と操作（展開・折りたたみ・外す・残す・つなぐ・置き換え・足す・位置合わせ）。
 * 読み込みの失敗は `failure` に置き、画面が同じ場所で再試行を出す。
 * 原文を足したら（検索の結果・図内検索の当たり・展開・詳細から足す）、図にある原文の間の関係を 1 回照会して
 * 線を足す（Neo4j Browser の「Connect result nodes」。#5953）。
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

  /**
   * 原文の当たり（検索の結果・図内検索）を図に出す。当たりと図にある原文の間の関係を先に 1 回照会し、節点と線を
   * 一緒に出す（配置が線を考慮できる）。照会に失敗しても節点は出し、失敗は再試行に出す。
   */
  const place = useCallback(async (nodes: readonly WikiGraphNode[], mode: "replace" | "add", matched: readonly string[] = []) => {
    const base = mode === "replace" ? emptyWikiGraph() : current.current;
    const ids = unitsToConnect(base, WIKI_CONNECTIONS_LIMIT, nodes.filter((node) => node.kind === "unit").map((node) => node.id));
    let edges: WikiGraphEdge[] = [];
    let failed: { error: unknown } | null = null;
    try {
      if (ids.length) edges = connectionEdges((await wikiApi.connections(scope, ids)).connections);
    } catch (error) {
      failed = { error };
    }
    update((old) => addToWikiGraph(mode === "replace" ? replaceWikiGraph(old, [], []) : old, nodes, edges, { matched, linked: failed ? [] : ids }));
    if (failed) throw failed.error;
  }, [scope, update]);

  /**
   * Wiki 検索の図を、選んだ原文とその次の段にそろえる（#5981）。選んだ原文は強調（`matched`）して図に置き、外した
   * 原文はその次の段と一緒に外す。次の段（関係の出る向き・入る向き）は、選んだ原文ごとに最初の
   * `selectedNeighbors` 件を出し、残りは「さらに展開」の点にする。関係は 1 件ずつ順に読み、読み終えてからまとめて
   * 図に足す（読み取りを重ねない。#5966）。足すのと関係を読むのは、図を見ている間（`open`）だけ。選択から外した
   * 原文は、図を見ていなくてもすぐ外す（照会は要らない。詳細が、外した原文を図にあるものとして扱わない）。
   * 図に無かった原文を選んだときは、次の段を足すときに配置を最初からやり直す（選んだ原文だけを先に詰めて置いた
   * 位置のまま次の段を足すと、選んだ原文どうしが近すぎる）。図にある節点（次の段）を選んだときは動かさない。
   */
  const firstHop = useRef(new Set<string>());
  const syncPicked = useCallback((picked: readonly WikiGraphNode[], open: boolean) => {
    const wanted = new Set(picked.map((node) => node.id));
    const dropped = [...current.current.matched].filter((id) => !wanted.has(id));
    for (const id of dropped) firstHop.current.delete(id);
    if (dropped.length) update((old) => dropped.reduce(unpickWikiNode, old));
    if (!open) return;
    const added = picked.filter((node) => !current.current.matched.has(node.id));
    const relayout = added.some((node) => !current.current.nodes.has(node.id));
    if (added.length) update((old) => addToWikiGraph(old, added, [], { matched: added.map((node) => node.id) }));
    const waiting = picked.map((node) => node.id).filter((id) => !firstHop.current.has(id));
    if (!waiting.length) return;
    for (const id of waiting) firstHop.current.add(id);
    void run(async () => {
      const loaded: { id: string; neighbors: Awaited<ReturnType<typeof loadNeighbors>> }[] = [];
      try {
        for (const id of waiting) {
          const node = current.current.nodes.get(id);
          // 読む前に選択から外した原文と、既に展開してある原文（次の段の節点を展開してから選んだ）は読まない。
          if (!node || !current.current.matched.has(id) || current.current.expansions.has(id)) continue;
          loaded.push({ id, neighbors: await loadNeighbors(client, scope, node, undefined) });
        }
      } finally {
        update((old) => {
          const next = loaded.reduce((state, item) => {
            if (!state.matched.has(item.id) || state.expansions.has(item.id)) return state;
            const recorded = recordNeighbors(state, item.id, item.neighbors.neighbors, item.neighbors.complete, undefined, item.neighbors.total);
            return showNeighbors(recorded, item.id, WIKI_GRAPH_SETTINGS.selectedNeighbors);
          }, old);
          return relayout && next.nodes.size > old.nodes.size ? { ...next, generation: next.generation + 1 } : next;
        });
      }
    });
  }, [client, run, scope, update]);

  /** 選んだノードの間の関係を足す（単位は同じ文書の依存と別の文書への参照、文書は文書の間の参照）。 */
  const connect = useCallback((ids: readonly string[]) => {
    const nodes = ids.map((id) => current.current.nodes.get(id)).filter((node): node is WikiGraphNode => Boolean(node));
    const units = nodes.filter((node) => node.kind === "unit").map((node) => node.id);
    const documents = nodes.filter((node) => node.kind === "document").map((node) => node.id);
    void run(async () => {
      const edges: WikiGraphEdge[] = [];
      if (units.length > 1) {
        const found = await wikiApi.connections(scope, units);
        edges.push(...connectionEdges(found.connections));
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
      // 足した文書と、図にある文書の間の参照も引く（相手が図に無い線は描かれない）。
      edges.push(...documentLinks(await fetchDocumentNeighbors(client, scope, unit.document_id)));
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
  }, [client, scope, update]);

  // 展開・詳細から原文が図に入ったら、図にある原文との間の関係を 1 回照会して線を足す。
  useEffect(() => {
    // 最新の状態で判定する（照会済みの印を付けた直後にもう一度呼ばれても、同じ照会を重ねない）。
    const ids = unitsToConnect(current.current, WIKI_CONNECTIONS_LIMIT);
    if (!ids.length) return;
    update((old) => addToWikiGraph(old, [], [], { linked: ids }));
    void run(async () => {
      const found = await wikiApi.connections(scope, ids);
      update((old) => connectWikiGraph(old, connectionEdges(found.connections)));
    });
  }, [run, scope, state, update]);

  return { state, pending: pending > 0, failure, run, expand, collapse, remove, keep, connect, replace, add, place, clear, reveal, syncPicked };
}
