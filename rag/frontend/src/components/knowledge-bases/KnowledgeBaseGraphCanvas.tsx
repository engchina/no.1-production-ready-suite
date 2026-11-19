import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useReactFlow, type Edge, type Node, type NodeChange, type NodeProps } from "@xyflow/react";
import { FileText, Heading, MoreHorizontal, TextQuote, type LucideIcon } from "lucide-react";
import { Banner, ProcessingIndicator, SelectField } from "@production-ready/ui";
import { applyGraphNodeDimensionChanges, applyGraphNodePositionChanges, GRAPH_EDGE_LABEL_PROPS, GRAPH_EDGE_MARKER, GRAPH_NODE_CARD_SIZE, GraphCanvas, GraphCanvasControls, GraphCanvasFrame, GraphCanvasProvider, graphEdgeStyle, GraphLegend, graphNeighborIds, GraphNodeCard, GraphParallelEdge, GraphSearchNavigation, GraphSelectionDetails, selectGraphEdgeHandles, type GraphNodeDimensions, type GraphPoint } from "@production-ready/ui/graph";
import "@xyflow/react/dist/style.css";

import { DataLoadFailure } from "@/components/DataLoadStates";
import { api, type WikiGraphOverview, type WikiGraphRelation, type WikiGraphUnitRef } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useStructureNodeMeasurements } from "./use-structure-node-measurements";
import { WikiGraphUnitPanel } from "./WikiGraphUnitPanel";
import { WIKI_RELATION_ICONS, wikiRelationText } from "./wiki-graph-labels";
import { emptyWikiTree, layoutWiki, WIKI_GRAPH_PAGE_SIZE, WIKI_RELATIONS, visibleWikiNodes, wikiRelationEdges, type WikiNodeKind, type WikiTreeState, type WikiViewNode } from "./wiki-graph";

interface WikiNodeData extends Record<string, unknown> {
  item: WikiViewNode;
  matched: boolean;
  dimmed: boolean;
}
type Selection = { type: "node" | "edge"; id: string } | null;

const SEARCH_DEBOUNCE_MS = 400;
const SEARCH_PAGE_SIZE = 25;
const NODE_KINDS: readonly WikiNodeKind[] = ["document", "section", "unit"];

function kindLabel(kind: WikiNodeKind) {
  return t(`wiki.graph.kind.${kind}` as I18nKey);
}
const KIND_ICONS: Record<WikiNodeKind, LucideIcon> = { document: FileText, section: Heading, unit: TextQuote, more: MoreHorizontal };
// 種類 = 塗り（文書構造グラフ・NL2SQL の図と同じ語彙）。
const KIND_FILLS: Record<WikiNodeKind, string> = {
  document: "var(--color-graph-entity)", section: "var(--color-graph-term)", unit: "var(--color-graph-metric)", more: "var(--color-graph-default)",
};
const relationLegendId = (relation: WikiGraphRelation) => relation;

function nodeStats(item: WikiViewNode): string[] {
  const stats: string[] = [];
  const counts = item.document ?? item.tree;
  if (item.document) stats.push(t("wiki.graph.unitCount", { count: item.document.unit_count }));
  else if (item.tree?.kind === "section") stats.push(t("wiki.graph.unitCount", { count: item.tree.unit_count }));
  // 文書をまたぐ関連は、端が図に無くても件数で示す（線は表示中の節点の間だけ）。
  if (counts?.outgoing_links) stats.push(t("wiki.graph.outgoingLinks", { count: counts.outgoing_links }));
  if (counts?.incoming_links) stats.push(t("wiki.graph.incomingLinks", { count: counts.incoming_links }));
  if (item.expandable) stats.push(t(item.expanded ? "wiki.graph.close" : "wiki.graph.open"));
  return stats;
}

function WikiNode({ data, selected }: NodeProps<Node<WikiNodeData>>) {
  const { item } = data;
  if (item.kind === "more") {
    return <GraphNodeCard icon={MoreHorizontal} kindLabel={kindLabel("more")} primary={t("wiki.graph.more", { count: item.remaining ?? 0 })} dimmed={data.dimmed} data-testid="wiki-graph-more" />;
  }
  return <GraphNodeCard icon={KIND_ICONS[item.kind]} kindLabel={kindLabel(item.kind)} primary={item.label} secondary={item.kind === "unit" ? item.tree?.label : undefined}
    stats={nodeStats(item)} fill={KIND_FILLS[item.kind]} selected={selected} matched={data.matched} dimmed={data.dimmed} title={item.name}
    data-kind={item.kind} data-expanded={item.expandable ? String(item.expanded) : undefined} />;
}
const NODE_TYPES = { wiki: WikiNode };
const EDGE_TYPES = { parallel: GraphParallelEdge };

function nodeAriaLabel(item: WikiViewNode) {
  if (item.kind === "more") return t("wiki.graph.more", { count: item.remaining ?? 0 });
  const state = item.expandable ? `（${t(item.expanded ? "wiki.graph.close" : "wiki.graph.open")}）` : "";
  return `${kindLabel(item.kind)}：${item.name}${state}`;
}

function WikiCanvas({ overview, knowledgeBaseId }: { overview: WikiGraphOverview; knowledgeBaseId: string }) {
  const flow = useReactFlow<Node<WikiNodeData>, Edge>();
  const filterId = useId();
  const canvasAnchor = useRef<HTMLDivElement>(null);
  const [tree, setTree] = useState<WikiTreeState>(emptyWikiTree);
  // 読み込みの中で最新の木を読む（開く操作の連続で古い state に重ねない）。
  const treeRef = useRef(tree);
  const updateTree = (change: (current: WikiTreeState) => WikiTreeState) => {
    treeRef.current = change(treeRef.current);
    setTree(treeRef.current);
  };
  const [pending, setPending] = useState(0);
  const [failure, setFailure] = useState<{ error: unknown; retry: () => void } | null>(null);
  const [search, setSearch] = useState("");
  const [cursor, setCursor] = useState(0);
  const [kind, setKind] = useState("");
  const [disabledLegend, setDisabledLegend] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Selection>(null);
  const [pendingFocus, setPendingFocus] = useState<string | null>(null);
  const focusedNode = useRef<string | null>(null);
  const [sizes, setSizes] = useState<Map<string, GraphNodeDimensions>>(new Map());
  // ドラッグによる座標の上書き。配置は決定論のまま、差分だけを持つ。
  const [overrides, setOverrides] = useState<Map<string, GraphPoint>>(new Map());
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);

  const documents = useMemo(() => new Map(overview.documents.map((document) => [document.id, document])), [overview]);

  async function run(work: () => Promise<void>): Promise<void> {
    setPending((value) => value + 1);
    setFailure(null);
    try {
      await work();
    } catch (error) {
      setFailure({ error, retry: () => void run(work) });
    } finally {
      setPending((value) => value - 1);
    }
  }

  const documentIdOf = (id: string): string | undefined => documents.get(id)?.document_id ?? treeRef.current.nodes.get(id)?.document_id;

  /** 親の段を、`count` 件まで読む（読み込み済みの続きから）。 */
  const loadChildren = async (parent: string, count: number) => {
    const documentId = documentIdOf(parent);
    if (!documentId) return;
    let loaded = treeRef.current.children.get(parent);
    while (!loaded || loaded.ids.length < Math.min(count, loaded.total)) {
      const offset = loaded?.ids.length ?? 0;
      const page = await api.getWikiGraphTree(knowledgeBaseId, documentId, documents.has(parent) ? null : parent, offset, Math.min(WIKI_GRAPH_PAGE_SIZE, Math.max(count - offset, 1)));
      updateTree((current) => ({
        ...current,
        nodes: new Map([...current.nodes, ...page.items.map((item) => [item.id, item] as const)]),
        children: new Map(current.children).set(parent, { ids: [...new Set([...(current.children.get(parent)?.ids ?? []), ...page.items.map((item) => item.id)])], total: page.total }),
      }));
      loaded = treeRef.current.children.get(parent)!;
      if (loaded.ids.length <= offset && loaded.ids.length < Math.min(count, loaded.total)) {
        throw new Error(t("wiki.graph.expandError"));
      }
    }
  };

  const setExpanded = (id: string, expanded: boolean) => {
    updateTree((current) => {
      const next = new Set(current.expanded);
      if (expanded) next.add(id); else next.delete(id);
      return { ...current, expanded: next };
    });
  };

  const toggle = (id: string) => {
    if (treeRef.current.expanded.has(id)) { setExpanded(id, false); return; }
    void run(async () => {
      await loadChildren(id, WIKI_GRAPH_PAGE_SIZE);
      setExpanded(id, true);
    });
  };

  /** 文書の中の節点まで段を開く。節点が段の先の方にあるときは、そこまで読む。 */
  const reveal = async (target: { id: string; document_id: string }) => {
    if (!documents.has(target.document_id)) return;
    if (target.id !== target.document_id) {
      const { path } = await api.locateWikiGraphNode(knowledgeBaseId, target.document_id, target.id);
      let parent = target.document_id;
      for (const step of path) {
        await loadChildren(parent, (Math.floor(step.index / WIKI_GRAPH_PAGE_SIZE) + 1) * WIKI_GRAPH_PAGE_SIZE);
        setExpanded(parent, true);
        parent = step.id;
      }
    }
    setKind("");
    setSelection({ type: "node", id: target.id });
    focusedNode.current = null;
    setPendingFocus(target.id);
  };

  const locateUnit = (unit: WikiGraphUnitRef) => { void run(() => reveal(unit)); };

  const query = search.trim();
  const searchPage = Math.floor(cursor / SEARCH_PAGE_SIZE) * SEARCH_PAGE_SIZE;
  const searchQuery = useQuery({
    queryKey: ["wiki-graph-search", knowledgeBaseId, query, searchPage],
    queryFn: () => api.searchWikiGraph(knowledgeBaseId, query, searchPage),
    enabled: query.length > 0, retry: false,
  });
  const matches = useMemo(() => (query ? searchQuery.data?.items ?? [] : []), [query, searchQuery.data]);
  const matchCount = query ? searchQuery.data?.total ?? 0 : 0;
  const current = matches[cursor - searchPage];
  // 検索語と移動の結果へ開く。結果が読めたときに 1 度だけ動かす。
  const revealedMatch = useRef<string | null>(null);
  useEffect(() => {
    if (!current) return;
    const key = `${query}:${cursor}`;
    if (revealedMatch.current === key) return;
    revealedMatch.current = key;
    void run(() => reveal(current));
  });
  const matchedNodes = useMemo(() => new Set(matches.map((item) => item.id)), [matches]);
  const jump = (direction: number) => {
    if (!matchCount) return;
    setCursor((value) => (value + direction + matchCount) % matchCount);
  };
  const searchChanged = (value: string) => { setSearch(value); setCursor(0); };

  const viewNodes = useMemo(() => visibleWikiNodes(overview, tree, (value) => !kind || value === kind || value === "more"), [overview, tree, kind]);
  const viewById = useMemo(() => new Map(viewNodes.map((node) => [node.id, node])), [viewNodes]);
  const visibleIds = useMemo(() => new Set(viewNodes.map((node) => node.id)), [viewNodes]);
  useStructureNodeMeasurements(sizes, visibleIds);
  const relationEdges = useMemo(() => wikiRelationEdges(viewNodes, (relation) => !disabledLegend.has(relationLegendId(relation))), [viewNodes, disabledLegend]);
  const containsEdges = useMemo(() => viewNodes.flatMap((node) => (node.parent && visibleIds.has(node.parent) ? [{ id: `contains:${node.id}`, source: node.parent, target: node.id }] : [])), [viewNodes, visibleIds]);
  const documentEdges = useMemo(() => relationEdges.filter((edge) => edge.scope === "cross_document").map((edge) => ({ source: viewById.get(edge.source)!.documentNode, target: viewById.get(edge.target)!.documentNode })), [relationEdges, viewById]);
  const layout = useMemo(() => layoutWiki(viewNodes, documentEdges, sizes), [viewNodes, documentEdges, sizes]);
  const positionOf = (id: string) => overrides.get(id) ?? layout.get(id) ?? { x: 0, y: 0 };

  // 種類の切替だけで表示範囲を合わせ、開閉や検索の移動では今の見え方を保つ。
  useEffect(() => {
    if (pendingFocus && focusedNode.current !== pendingFocus) return;
    void flow.fitView({ padding: 0.18, duration: 0 });
  }, [flow, kind, pendingFocus]);

  useEffect(() => {
    if (!pendingFocus || focusedNode.current === pendingFocus) return;
    const position = layout.get(pendingFocus);
    if (!position) return;
    // 画面外の未描画ノードは測れないため、配置と同じ既定寸法で先に表示位置へ移す。
    const size = sizes.get(pendingFocus) ?? GRAPH_NODE_CARD_SIZE;
    canvasAnchor.current?.scrollIntoView({ block: "center", behavior: "instant" });
    const point = overrides.get(pendingFocus) ?? position;
    void flow.setCenter(point.x + size.width / 2, point.y + size.height / 2, { zoom: 1, duration: 0 });
    if (sizes.has(pendingFocus)) focusedNode.current = pendingFocus;
  }, [pendingFocus, layout, overrides, sizes, flow]);

  type ViewEdge = { id: string; source: string; target: string; relation?: WikiGraphRelation; scope?: "same_document" | "cross_document" };
  const allEdges: ViewEdge[] = useMemo(() => [...containsEdges, ...relationEdges], [containsEdges, relationEdges]);
  const neighbors = graphNeighborIds(hoveredNode, allEdges);
  const nodes: Node<WikiNodeData>[] = viewNodes.map((item) => ({
    id: item.id, type: "wiki", position: positionOf(item.id),
    ...(sizes.has(item.id) ? { measured: sizes.get(item.id) } : {}),
    ariaLabel: nodeAriaLabel(item),
    data: { item, matched: matchedNodes.has(item.id), dimmed: Boolean(neighbors && !neighbors.has(item.id)) },
    selected: selection?.type === "node" && selection.id === item.id,
    style: { width: GRAPH_NODE_CARD_SIZE.width, minHeight: GRAPH_NODE_CARD_SIZE.height, padding: 0, border: "none" },
  }));
  const pairCounts = new Map<string, number>();
  const pairKey = (edge: { source: string; target: string }) => [edge.source, edge.target].sort().join("::");
  for (const edge of allEdges) pairCounts.set(pairKey(edge), (pairCounts.get(pairKey(edge)) ?? 0) + 1);
  const pairSeen = new Map<string, number>();
  const edgeLabel = (edge: ViewEdge) => (edge.relation ? wikiRelationText(edge.relation) : t("wiki.graph.contains"));
  const edgeScope = (edge: ViewEdge) => (edge.scope ? t(edge.scope === "cross_document" ? "wiki.graph.scope.cross_document" : "wiki.graph.scope.same_document") : "");
  const nodeName = (id: string) => viewById.get(id)?.name || id;
  const edges: Edge[] = allEdges.map((edge) => {
    const selected = selection?.type === "edge" && selection.id === edge.id;
    // 包含は親が左・子が右の配置なので、常に横につなぐ（縦に回すと兄弟の節点を貫通する）。
    const { sourceHandle, targetHandle, orientation } = edge.relation
      ? selectGraphEdgeHandles(positionOf(edge.source), positionOf(edge.target))
      : ({ sourceHandle: "s-right", targetHandle: "t-left", orientation: "horizontal" } as const);
    const key = pairKey(edge);
    const parallelCount = pairCounts.get(key) ?? 1;
    const parallelIndex = pairSeen.get(key) ?? 0;
    pairSeen.set(key, parallelIndex + 1);
    return {
      id: edge.id, source: edge.source, target: edge.target, sourceHandle, targetHandle, markerEnd: GRAPH_EDGE_MARKER,
      ...(orientation === "vertical" ? { type: "smoothstep" } : parallelCount > 1 ? { type: "parallel", data: { parallelOffset: (parallelIndex - (parallelCount - 1) / 2) * 20 } } : {}),
      // 関係は矢印（起点 → 終点）と種類の文字を常に出す。包含は多いので点線で、選択と hover のときだけ文字を出す。
      label: edge.relation || selected || hoveredEdge === edge.id ? edgeLabel(edge) : undefined,
      ariaLabel: t("wiki.graph.connection", { from: nodeName(edge.source), to: nodeName(edge.target), relation: [edgeLabel(edge), edgeScope(edge)].filter(Boolean).join("・") }),
      selected,
      // 文書をまたぐ関連は太い実線、同じ文書の依存は実線、包含は点線（色だけで区別しない）。
      style: graphEdgeStyle({ emphasized: selected, width: edge.scope === "cross_document" ? 2 : edge.relation ? 1.25 : 1, dash: edge.relation ? undefined : "4 3", opacity: neighbors && edge.source !== hoveredNode && edge.target !== hoveredNode ? 0.35 : 1 }),
      ...GRAPH_EDGE_LABEL_PROPS,
    };
  });

  const nodesChanged = (changes: NodeChange<Node<WikiNodeData>>[]) => {
    setOverrides((before) => applyGraphNodePositionChanges(before, changes));
    setSizes((before) => applyGraphNodeDimensionChanges(before, changes));
  };
  const selectNode = (id: string) => {
    const item = viewById.get(id);
    if (!item) return;
    if (item.kind === "more") {
      const parent = item.parent!;
      const loaded = tree.children.get(parent)?.ids.length ?? 0;
      void run(() => loadChildren(parent, loaded + WIKI_GRAPH_PAGE_SIZE));
      return;
    }
    setSelection({ type: "node", id });
    if (item.expandable) toggle(id);
  };
  const toggleLegend = (id: string) => {
    setDisabledLegend((before) => { const next = new Set(before); if (next.has(id)) next.delete(id); else next.add(id); return next; });
    setSelection(null);
  };

  const selectedNode = selection?.type === "node" ? viewById.get(selection.id) : undefined;
  const selectedEdge = selection?.type === "edge" ? allEdges.find((edge) => edge.id === selection.id) : undefined;
  const fields = selectedNode && selectedNode.kind !== "unit" ? [
    { label: t("wiki.graph.name"), value: selectedNode.name },
    ...(selectedNode.tree ? [{ label: t("wiki.graph.document"), value: selectedNode.tree.document_name }] : []),
    { label: t("wiki.graph.kind"), value: kindLabel(selectedNode.kind) },
    { label: t("wiki.graph.units"), value: String(selectedNode.document?.unit_count ?? selectedNode.tree?.unit_count ?? 0) },
  ] : selectedEdge ? [
    { label: t("wiki.graph.relation"), value: edgeLabel(selectedEdge) },
    ...(selectedEdge.scope ? [{ label: t("wiki.graph.scope"), value: edgeScope(selectedEdge) }] : []),
    { label: t("wiki.graph.from"), value: nodeName(selectedEdge.source) },
    { label: t("wiki.graph.to"), value: nodeName(selectedEdge.target) },
  ] : [];
  const unitSelected = selectedNode?.kind === "unit";
  // 案内を横に出すと画布の幅が変わるので、選んだ単位を画布の中央に戻す（拡大率は保つ）。
  const selectedUnitId = unitSelected ? selectedNode.id : null;
  useEffect(() => {
    if (!selectedUnitId) return;
    const frame = requestAnimationFrame(() => {
      const point = flow.getNode(selectedUnitId)?.position;
      if (!point) return;
      const size = sizes.get(selectedUnitId) ?? GRAPH_NODE_CARD_SIZE;
      void flow.setCenter(point.x + size.width / 2, point.y + size.height / 2, { zoom: flow.getZoom(), duration: 0 });
    });
    return () => cancelAnimationFrame(frame);
    // 選んだときだけ動かす（配置・寸法の更新では動かさない）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedUnitId, flow]);

  return <div className="grid min-w-0 gap-3">
    <div className="flex min-w-0 flex-wrap items-start gap-2">
      <GraphSearchNavigation labels={{ search: t("wiki.graph.search"), clear: t("common.clearSearch"), previous: t("wiki.graph.previousMatch"), next: t("wiki.graph.nextMatch"), noMatch: t("wiki.graph.noMatch"), scope: t("wiki.graph.searchScope") }} value={search} onChange={searchChanged} count={matchCount} cursor={cursor} onPrevious={() => jump(-1)} onNext={() => jump(1)} debounceMs={SEARCH_DEBOUNCE_MS} testId="wiki-graph-search" />
      <SelectField id={filterId} label={t("wiki.graph.kind")} labelHidden size="lg" width="md" value={kind} onValueChange={(value) => { setKind(value); setSelection(null); }} options={[{ value: "", label: t("wiki.graph.allKinds") }, ...NODE_KINDS.map((value) => ({ value, label: kindLabel(value) }))]} />
    </div>
    {searchQuery.isError && query ? <Banner severity="danger" title={t("wiki.graph.searchError")} /> : null}
    {failure ? <DataLoadFailure error={failure.error} fallback={t("wiki.graph.expandError")} onRetry={failure.retry} /> : null}
    {/* 単位を選ぶと案内を横に出す。置き場所が狭い（375px など）ときは図の下に回す。 */}
    <div className="@container min-w-0">
      <div className={unitSelected ? "grid min-w-0 gap-3 @4xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]" : "grid min-w-0"}>
        <div ref={canvasAnchor} className="relative min-w-0">
          <GraphCanvasFrame role="region" aria-label={t("wiki.graph.title")}>
            <GraphCanvas nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES} onNodesChange={nodesChanged} elementsSelectable={false} onlyRenderVisibleElements={viewNodes.length > 150}
              onSelectNode={selectNode} onSelectEdge={(id) => setSelection({ type: "edge", id })}
              onNodeMouseEnter={(_event, node) => setHoveredNode(node.id)} onNodeMouseLeave={() => setHoveredNode(null)}
              onEdgeMouseEnter={(_event, edge) => setHoveredEdge(edge.id)} onEdgeMouseLeave={() => setHoveredEdge(null)}
              miniMap={viewNodes.length > 12 ? { "aria-label": t("wiki.graph.minimap"), nodeColor: (node) => KIND_FILLS[(node.data as WikiNodeData).item.kind] } : false}>
              <GraphCanvasControls labels={{ zoomIn: t("wiki.graph.zoomIn"), zoomOut: t("wiki.graph.zoomOut"), fit: t("wiki.graph.fitView"), reset: t("wiki.graph.reset") }} onReset={() => setOverrides(new Map())} resetDisabled={overrides.size === 0} />
              {/* 凡例は関係の種類（アイコンと文字）。節点の種類は節点の上の文字と「種類」の絞り込みで示す（375px で画布を覆わない）。 */}
              <GraphLegend label={t("wiki.graph.legend")} items={WIKI_RELATIONS.map((relation) => {
                const Icon = WIKI_RELATION_ICONS[relation];
                return { id: relationLegendId(relation), label: wikiRelationText(relation), marker: <Icon className="size-4 shrink-0" aria-hidden /> };
              })} disabledIds={disabledLegend} onToggle={toggleLegend} testId="wiki-graph-legend" />
            </GraphCanvas>
          </GraphCanvasFrame>
          {/* 節点を開く間の表示は画布の上に重ね、画布を押し下げない（#4649）。 */}
          {pending > 0 ? <div className="pointer-events-none sticky bottom-3 z-20 h-0" data-testid="wiki-graph-expanding-anchor">
            <div className="absolute inset-x-3 bottom-20">
              <ProcessingIndicator active label={t("wiki.graph.expanding")} operationKey="wiki-graph-expand" testId="wiki-graph-expanding" className="shadow-sm" />
            </div>
          </div> : null}
        </div>
        {unitSelected && selectedNode ? <WikiGraphUnitPanel key={selectedNode.id} kbId={knowledgeBaseId} unitId={selectedNode.id} onClose={() => setSelection(null)} onLocate={locateUnit} /> : null}
      </div>
    </div>
    {fields.length ? <GraphSelectionDetails title={t(selectedNode ? "wiki.graph.selectedNode" : "wiki.graph.selectedEdge")} closeLabel={t("wiki.graph.clearSelection")} onClose={() => setSelection(null)} fields={fields} /> : null}
  </div>;
}

export function KnowledgeBaseGraphCanvas({ overview, knowledgeBaseId }: { overview: WikiGraphOverview; knowledgeBaseId: string }) {
  return <GraphCanvasProvider><WikiCanvas overview={overview} knowledgeBaseId={knowledgeBaseId} /></GraphCanvasProvider>;
}
