import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useReactFlow, type Edge, type Node, type NodeChange, type NodeProps } from "@xyflow/react";
import { FileText, Heading, Image, Layers, MoreHorizontal, Table2, type LucideIcon } from "lucide-react";
import { Banner, SelectField, TimedLoadingState } from "@production-ready/ui";
import { aggregateGraphEdges, applyGraphNodeDimensionChanges, applyGraphNodePositionChanges, GRAPH_EDGE_LABEL_PROPS, GRAPH_EDGE_MARKER, GRAPH_NODE_CARD_SIZE, GraphCanvas, GraphCanvasControls, GraphCanvasFrame, GraphCanvasProvider, graphEdgeStyle, GraphLegend, GraphLegendSwatch, graphNeighborIds, GraphNodeCard, GraphParallelEdge, graphPathLabel, GraphSearchNavigation, GraphSelectionDetails, selectGraphEdgeHandles, type GraphNodeDimensions, type GraphPoint } from "@production-ready/ui/graph";
import "@xyflow/react/dist/style.css";

import { DataLoadFailure } from "@/components/DataLoadStates";
import { api, type GraphReferenceEndpoint, type StructureGraphDocument, type StructureGraphOverview } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useStructureNodeMeasurements } from "./use-structure-node-measurements";
import { GraphReferencePanel } from "./GraphReferencePanel";
import { emptyStructureTree, layoutStructure, STRUCTURE_PAGE_SIZE, structureReferenceChains, visibleStructureNodes, type StructureTreeState, type StructureViewNode } from "./structure-graph";

interface StructureNodeData extends Record<string, unknown> {
  item: StructureViewNode;
  matched: boolean;
  dimmed: boolean;
}
type Selection = { type: "node" | "edge"; id: string } | null;

const SECTION_PATH_SEPARATOR = " > ";
const SEARCH_DEBOUNCE_MS = 400;
const SEARCH_PAGE_SIZE = 25;

function kindLabel(kind: string | null) {
  const names: Record<string, Parameters<typeof t>[0]> = {
    document: "knowledgeBases.graph.kind.document", section: "knowledgeBases.graph.kind.section",
    table_section: "knowledgeBases.graph.kind.table", figure_section: "knowledgeBases.graph.kind.figure",
    more: "knowledgeBases.graph.kind.more",
  };
  return t(names[kind ?? ""] ?? "knowledgeBases.graph.kind.other");
}
const KIND_ICONS: Record<string, LucideIcon> = { document: FileText, section: Heading, table_section: Table2, figure_section: Image, more: MoreHorizontal };
// 種類 = 塗り（NL2SQL の図と同じ語彙。状態の枠はこの図には無い）。
const KIND_FILLS: Record<string, string> = {
  document: "var(--color-graph-entity)", section: "var(--color-graph-term)",
  table_section: "var(--color-graph-sql)", figure_section: "var(--color-graph-metric)",
};
const kindFill = (kind: string | null) => KIND_FILLS[kind ?? ""] ?? "var(--color-graph-default)";

function nodeStats(item: StructureViewNode): string[] {
  const stats: string[] = [];
  if (item.document?.outgoing_count) stats.push(t("knowledgeBases.graph.outgoingCount", { count: item.document.outgoing_count }));
  if (item.document?.incoming_count) stats.push(t("knowledgeBases.graph.incomingCount", { count: item.document.incoming_count }));
  if (item.expandable) stats.push(t(item.expanded ? "knowledgeBases.graph.close" : "knowledgeBases.graph.open"));
  return stats;
}

function StructureNode({ data, selected }: NodeProps<Node<StructureNodeData>>) {
  const { item } = data;
  if (item.kind === "more") {
    return <GraphNodeCard icon={MoreHorizontal} kindLabel={kindLabel("more")} primary={t("knowledgeBases.graph.more", { count: item.remaining ?? 0 })} dimmed={data.dimmed} data-testid="structure-graph-more" />;
  }
  const label = item.tree ? graphPathLabel(item.tree.name, SECTION_PATH_SEPARATOR) : { primary: item.label };
  return <GraphNodeCard icon={KIND_ICONS[item.kind] ?? Layers} kindLabel={kindLabel(item.kind)} primary={item.tree ? item.tree.label : label.primary} secondary={item.tree ? label.context : undefined}
    stats={nodeStats(item)} fill={kindFill(item.kind)} selected={selected} matched={data.matched} dimmed={data.dimmed} title={item.name} data-expanded={item.expandable ? String(item.expanded) : undefined} />;
}
const NODE_TYPES = { structure: StructureNode };
const EDGE_TYPES = { parallel: GraphParallelEdge };

function nodeAriaLabel(item: StructureViewNode) {
  if (item.kind === "more") return t("knowledgeBases.graph.more", { count: item.remaining ?? 0 });
  const state = item.expandable ? `（${t(item.expanded ? "knowledgeBases.graph.close" : "knowledgeBases.graph.open")}）` : "";
  return `${kindLabel(item.kind)}：${item.name}${state}`;
}

function StructureCanvas({ overview, knowledgeBaseId }: { overview: StructureGraphOverview; knowledgeBaseId: string }) {
  const flow = useReactFlow<Node<StructureNodeData>, Edge>();
  const filterId = useId();
  const canvasAnchor = useRef<HTMLDivElement>(null);
  const [tree, setTree] = useState<StructureTreeState>(emptyStructureTree);
  // 読み込みの中で最新の木を読む（開く操作の連続で古い state に重ねない）。
  const treeRef = useRef(tree);
  const updateTree = (change: (current: StructureTreeState) => StructureTreeState) => {
    treeRef.current = change(treeRef.current);
    setTree(treeRef.current);
  };
  const [pending, setPending] = useState(0);
  const [failure, setFailure] = useState<{ error: unknown; retry: () => void } | null>(null);
  const [search, setSearch] = useState("");
  const [cursor, setCursor] = useState(0);
  const [kind, setKind] = useState("");
  const [disabledKinds, setDisabledKinds] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Selection>(null);
  const [pendingFocus, setPendingFocus] = useState<string | null>(null);
  const focusedNode = useRef<string | null>(null);
  const [sizes, setSizes] = useState<Map<string, GraphNodeDimensions>>(new Map());
  // ドラッグによる座標の上書き。配置は決定論のまま、差分だけを持つ。
  const [overrides, setOverrides] = useState<Map<string, GraphPoint>>(new Map());
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);

  const documents = useMemo(() => new Map(overview.documents.map((document) => [document.id, document])), [overview]);
  const documentBySet = useMemo(() => new Map(overview.documents.map((document) => [document.chunk_set_id, document])), [overview]);

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

  const documentOf = (id: string): StructureGraphDocument | undefined => {
    const node = treeRef.current.nodes.get(id);
    return node ? documentBySet.get(node.chunk_set_id) : documents.get(id);
  };

  /** 親の段を、`count` 件まで読む（読み込み済みの続きから）。 */
  const loadChildren = async (parent: string, count: number) => {
    const document = documentOf(parent);
    if (!document) return;
    const loaded = treeRef.current.children.get(parent);
    const offset = loaded?.ids.length ?? 0;
    if (loaded && offset >= Math.min(count, loaded.total)) return;
    const page = await api.getStructureGraphTree(knowledgeBaseId, document.document_id, document.chunk_set_id, documents.has(parent) ? null : parent, offset, Math.max(count - offset, 1));
    updateTree((current) => ({
      ...current,
      nodes: new Map([...current.nodes, ...page.items.map((item) => [item.id, item] as const)]),
      children: new Map(current.children).set(parent, { ids: [...new Set([...(current.children.get(parent)?.ids ?? []), ...page.items.map((item) => item.id)])], total: page.total }),
    }));
  };

  const loadReferences = async (document: StructureGraphDocument) => {
    if (treeRef.current.references.has(document.chunk_set_id)) return;
    const { items } = await api.getStructureGraphReferences(knowledgeBaseId, document.document_id, document.chunk_set_id);
    updateTree((current) => ({ ...current, references: new Map(current.references).set(document.chunk_set_id, items) }));
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
      const document = documentOf(id);
      await loadChildren(id, STRUCTURE_PAGE_SIZE);
      if (document) await loadReferences(document);
      setExpanded(id, true);
    });
  };

  /** 文書の中の節点まで段を開く。節点が段の先の方にあるときは、そこまで読む。 */
  const reveal = async (target: { id: string; document_id: string; chunk_set_id: string }) => {
    const document = documentBySet.get(target.chunk_set_id);
    if (!document) return;
    if (target.id !== document.id) {
      const { path } = await api.locateStructureGraphNode(knowledgeBaseId, target.document_id, target.chunk_set_id, target.id);
      let parent = document.id;
      for (const step of path) {
        await loadChildren(parent, (Math.floor(step.index / STRUCTURE_PAGE_SIZE) + 1) * STRUCTURE_PAGE_SIZE);
        setExpanded(parent, true);
        parent = step.id;
      }
      await loadReferences(document);
    }
    setKind(""); setDisabledKinds(new Set());
    setSelection({ type: "node", id: target.id });
    focusedNode.current = null;
    setPendingFocus(target.id);
  };

  const locateReference = (target: GraphReferenceEndpoint) => { void run(() => reveal(target)); };

  const query = search.trim();
  const searchPage = Math.floor(cursor / SEARCH_PAGE_SIZE) * SEARCH_PAGE_SIZE;
  const searchQuery = useQuery({
    queryKey: ["structure-graph-search", knowledgeBaseId, query, searchPage],
    queryFn: () => api.searchStructureGraph(knowledgeBaseId, query, searchPage),
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

  const viewNodes = useMemo(() => visibleStructureNodes(overview, tree, (value) => (!kind || value === kind) && !disabledKinds.has(value)), [overview, tree, kind, disabledKinds]);
  const viewById = useMemo(() => new Map(viewNodes.map((node) => [node.id, node])), [viewNodes]);
  const visibleIds = useMemo(() => new Set(viewNodes.map((node) => node.id)), [viewNodes]);
  useStructureNodeMeasurements(sizes, visibleIds);
  const referenceEdges = useMemo(() => aggregateGraphEdges(structureReferenceChains(overview, tree), visibleIds), [overview, tree, visibleIds]);
  const containsEdges = useMemo(() => viewNodes.flatMap((node) => (node.parent && visibleIds.has(node.parent) ? [{ id: `contains:${node.id}`, source: node.parent, target: node.id }] : [])), [viewNodes, visibleIds]);
  const documentEdges = useMemo(() => referenceEdges.map((edge) => ({ source: viewById.get(edge.source)!.documentNode, target: viewById.get(edge.target)!.documentNode })), [referenceEdges, viewById]);
  const layout = useMemo(() => layoutStructure(viewNodes, documentEdges, sizes), [viewNodes, documentEdges, sizes]);
  const positionOf = (id: string) => overrides.get(id) ?? layout.get(id) ?? { x: 0, y: 0 };

  // 種類の切替だけで表示範囲を合わせ、開閉や検索の移動では今の見え方を保つ。
  useEffect(() => { void flow.fitView({ padding: 0.18, duration: 0 }); }, [flow, kind, disabledKinds]);

  useEffect(() => {
    if (!pendingFocus || focusedNode.current === pendingFocus) return;
    const position = layout.get(pendingFocus);
    if (!position || !sizes.has(pendingFocus)) return;
    const size = sizes.get(pendingFocus)!;
    canvasAnchor.current?.scrollIntoView({ block: "center", behavior: "instant" });
    const point = overrides.get(pendingFocus) ?? position;
    void flow.setCenter(point.x + size.width / 2, point.y + size.height / 2, { zoom: 1, duration: 0 });
    focusedNode.current = pendingFocus;
  }, [pendingFocus, layout, overrides, sizes, flow]);

  const allEdges = useMemo(() => [...containsEdges.map((edge) => ({ ...edge, isReference: false, count: 0 })), ...referenceEdges.map((edge) => ({ ...edge, isReference: true }))], [containsEdges, referenceEdges]);
  const neighbors = graphNeighborIds(hoveredNode, allEdges);
  const nodes: Node<StructureNodeData>[] = viewNodes.map((item) => ({
    id: item.id, type: "structure", position: positionOf(item.id),
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
  const edgeLabel = (edge: { isReference: boolean; count: number }) => (edge.isReference ? t("knowledgeBases.graph.referenceEdge", { count: edge.count }) : t("knowledgeBases.graph.contains"));
  const nodeName = (id: string) => viewById.get(id)?.name || id;
  const edges: Edge[] = allEdges.map((edge) => {
    const selected = selection?.type === "edge" && selection.id === edge.id;
    // 包含は親が左・子が右の配置なので、常に横につなぐ（縦に回すと兄弟の節点を貫通する）。
    const { sourceHandle, targetHandle, orientation } = edge.isReference
      ? selectGraphEdgeHandles(positionOf(edge.source), positionOf(edge.target))
      : ({ sourceHandle: "s-right", targetHandle: "t-left", orientation: "horizontal" } as const);
    const key = pairKey(edge);
    const parallelCount = pairCounts.get(key) ?? 1;
    const parallelIndex = pairSeen.get(key) ?? 0;
    pairSeen.set(key, parallelIndex + 1);
    return {
      id: edge.id, source: edge.source, target: edge.target, sourceHandle, targetHandle, markerEnd: GRAPH_EDGE_MARKER,
      ...(orientation === "vertical" ? { type: "smoothstep" } : parallelCount > 1 ? { type: "parallel", data: { parallelOffset: (parallelIndex - (parallelCount - 1) / 2) * 20 } } : {}),
      // 参照は実線で件数を常に出す。包含は多いので点線で、選択と hover のときだけラベルを出す。
      label: edge.isReference || selected || hoveredEdge === edge.id ? edgeLabel(edge) : undefined,
      ariaLabel: t("knowledgeBases.graph.connection", { from: nodeName(edge.source), to: nodeName(edge.target), relation: edgeLabel(edge) }),
      selected,
      style: graphEdgeStyle({ emphasized: selected, width: edge.isReference ? 1.25 : 1, dash: edge.isReference ? undefined : "4 3", opacity: neighbors && edge.source !== hoveredNode && edge.target !== hoveredNode ? 0.35 : 1 }),
      ...GRAPH_EDGE_LABEL_PROPS,
    };
  });

  const nodesChanged = (changes: NodeChange<Node<StructureNodeData>>[]) => {
    setOverrides((before) => applyGraphNodePositionChanges(before, changes));
    setSizes((before) => applyGraphNodeDimensionChanges(before, changes));
  };
  const selectNode = (id: string) => {
    const item = viewById.get(id);
    if (!item) return;
    if (item.kind === "more") {
      const parent = item.parent!;
      const loaded = tree.children.get(parent)?.ids.length ?? 0;
      void run(() => loadChildren(parent, loaded + STRUCTURE_PAGE_SIZE));
      return;
    }
    setSelection({ type: "node", id });
    if (item.expandable) toggle(id);
  };

  const legendKinds = useMemo(() => {
    const present = new Set<string>(["document"]);
    for (const node of tree.nodes.values()) present.add(node.type);
    return [...present];
  }, [tree.nodes]);
  const selectedNode = selection?.type === "node" ? viewById.get(selection.id) : undefined;
  const selectedEdge = selection?.type === "edge" ? allEdges.find((edge) => edge.id === selection.id) : undefined;
  const fields = selectedNode ? [
    { label: t("knowledgeBases.graph.name"), value: selectedNode.name },
    ...(selectedNode.tree ? [{ label: t("knowledgeBases.structure.build.document"), value: selectedNode.tree.document_name }] : []),
    { label: t("knowledgeBases.graph.kind"), value: kindLabel(selectedNode.kind) },
    { label: t("knowledgeBases.graph.identifier"), value: selectedNode.id, technical: true },
  ] : selectedEdge ? [
    { label: t("knowledgeBases.graph.relationship"), value: edgeLabel(selectedEdge) },
    { label: t("knowledgeBases.graph.from"), value: nodeName(selectedEdge.source) },
    { label: t("knowledgeBases.graph.to"), value: nodeName(selectedEdge.target) },
    { label: t("knowledgeBases.graph.identifier"), value: selectedEdge.id, technical: true },
  ] : [];

  return <div className="grid min-w-0 gap-3">
    <div className="flex min-w-0 flex-wrap items-start gap-2">
      <GraphSearchNavigation labels={{ search: t("knowledgeBases.graph.search"), clear: t("common.clearSearch"), previous: t("knowledgeBases.graph.previousMatch"), next: t("knowledgeBases.graph.nextMatch"), noMatch: t("knowledgeBases.graph.noMatch"), scope: t("knowledgeBases.graph.searchScope") }} value={search} onChange={searchChanged} count={matchCount} cursor={cursor} onPrevious={() => jump(-1)} onNext={() => jump(1)} debounceMs={SEARCH_DEBOUNCE_MS} testId="knowledge-base-graph-search" />
      <SelectField id={filterId} label={t("knowledgeBases.graph.kind")} labelHidden size="lg" width="md" value={kind} onValueChange={(value) => { setKind(value); setSelection(null); }} options={[{ value: "", label: t("knowledgeBases.graph.allKinds") }, ...legendKinds.map((value) => ({ value, label: kindLabel(value) }))]} />
    </div>
    {searchQuery.isError && query ? <Banner severity="danger" title={t("knowledgeBases.graph.searchError")} /> : null}
    {failure ? <DataLoadFailure error={failure.error} fallback={t("knowledgeBases.graph.expandError")} onRetry={failure.retry} /> : null}
    {pending > 0 ? <TimedLoadingState label={t("knowledgeBases.graph.expanding")} operationKey="structure-graph-expand" testId="structure-graph-expanding" framed={false}>{null}</TimedLoadingState> : null}
    <div ref={canvasAnchor}><GraphCanvasFrame role="region" aria-label={t("knowledgeBases.graph.title")}>
      <GraphCanvas nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} edgeTypes={EDGE_TYPES} onNodesChange={nodesChanged} elementsSelectable={false} onlyRenderVisibleElements={viewNodes.length > 150}
        onSelectNode={selectNode} onSelectEdge={(id) => setSelection({ type: "edge", id })}
        onNodeMouseEnter={(_event, node) => setHoveredNode(node.id)} onNodeMouseLeave={() => setHoveredNode(null)}
        onEdgeMouseEnter={(_event, edge) => setHoveredEdge(edge.id)} onEdgeMouseLeave={() => setHoveredEdge(null)}
        miniMap={viewNodes.length > 12 ? { "aria-label": t("knowledgeBases.graph.minimap"), nodeColor: (node) => kindFill((node.data as StructureNodeData).item.kind) } : false}>
        <GraphCanvasControls labels={{ zoomIn: t("knowledgeBases.graph.zoomIn"), zoomOut: t("knowledgeBases.graph.zoomOut"), fit: t("knowledgeBases.graph.fitView"), reset: t("knowledgeBases.graph.reset") }} onReset={() => setOverrides(new Map())} resetDisabled={overrides.size === 0} />
        <GraphLegend label={t("knowledgeBases.graph.legend")} items={legendKinds.map((value) => ({ id: value, label: kindLabel(value), marker: <GraphLegendSwatch fill={kindFill(value)} /> }))} disabledIds={disabledKinds} onToggle={(id) => { setDisabledKinds((before) => { const next = new Set(before); if (next.has(id)) next.delete(id); else next.add(id); return next; }); setSelection(null); }} testId="knowledge-base-graph-legend" />
      </GraphCanvas>
    </GraphCanvasFrame></div>
    {selectedNode && !selectedNode.tree?.virtual ? <GraphReferencePanel key={`${knowledgeBaseId}:${selectedNode.id}`} kbId={knowledgeBaseId} nodeId={selectedNode.id} onLocate={locateReference} /> : null}
    {selectedNode?.tree?.virtual ? <p className="text-sm text-fg-muted">{t("knowledgeBases.graph.virtualNode")}</p> : null}
    {fields.length ? <GraphSelectionDetails title={t(selectedNode ? "knowledgeBases.graph.selectedNode" : "knowledgeBases.graph.selectedEdge")} closeLabel={t("knowledgeBases.graph.clearSelection")} onClose={() => setSelection(null)} fields={fields} /> : null}
  </div>;
}

export function KnowledgeBaseGraphCanvas({ overview, knowledgeBaseId }: { overview: StructureGraphOverview; knowledgeBaseId: string }) {
  return <GraphCanvasProvider><StructureCanvas overview={overview} knowledgeBaseId={knowledgeBaseId} /></GraphCanvasProvider>;
}
