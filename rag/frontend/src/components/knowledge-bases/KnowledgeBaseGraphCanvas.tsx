import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useReactFlow, type Edge, type Node, type NodeChange, type NodeProps } from "@xyflow/react";
import { FileText, Heading, Image, Layers, Table2, type LucideIcon } from "lucide-react";
import { SelectField } from "@production-ready/ui";
import { applyGraphNodeDimensionChanges, applyGraphNodePositionChanges, GRAPH_EDGE_LABEL_PROPS, GRAPH_EDGE_MARKER, GRAPH_NODE_CARD_SIZE, GraphCanvas, GraphCanvasControls, GraphCanvasFrame, GraphCanvasProvider, graphEdgeStyle, GraphLegend, GraphLegendSwatch, graphNeighborIds, GraphNodeCard, graphPathLabel, GraphSearchNavigation, GraphSelectionDetails, layoutGraphHierarchy, normalizeGraphSearch, selectGraphEdgeHandles, type GraphNodeDimensions, type GraphPoint } from "@production-ready/ui/graph";
import "@xyflow/react/dist/style.css";

import type { GraphReferenceItem, KnowledgeBaseGraphData, KnowledgeBaseGraphNode } from "@/lib/api";
import { t } from "@/lib/i18n";
import { GraphReferencePanel } from "./GraphReferencePanel";

interface StructureNodeData extends Record<string, unknown> {
  item: KnowledgeBaseGraphNode;
  matched: boolean;
  dimmed: boolean;
}
type Selection = { type: "node" | "edge"; id: string } | null;
type Match = NonNullable<Selection>;

function kindLabel(kind: string | null) {
  const names: Record<string, Parameters<typeof t>[0]> = {
    document: "knowledgeBases.graph.kind.document", section: "knowledgeBases.graph.kind.section",
    table_section: "knowledgeBases.graph.kind.table", figure_section: "knowledgeBases.graph.kind.figure",
  };
  return t(names[kind ?? ""] ?? "knowledgeBases.graph.kind.other");
}
const KIND_ICONS: Record<string, LucideIcon> = { document: FileText, section: Heading, table_section: Table2, figure_section: Image };
// 種類 = 塗り（NL2SQL の図と同じ語彙。状態の枠はこの図には無い）。
const KIND_FILLS: Record<string, string> = {
  document: "var(--color-graph-entity)", section: "var(--color-graph-term)",
  table_section: "var(--color-graph-sql)", figure_section: "var(--color-graph-metric)",
};
const kindFill = (kind: string | null) => KIND_FILLS[kind ?? ""] ?? "var(--color-graph-default)";
function edgeLabel(kind: string | null) {
  return t(kind === "contains" ? "knowledgeBases.graph.contains" : kind === "references" ? "knowledgeBases.graph.references" : "knowledgeBases.graph.related");
}
const SECTION_PATH_SEPARATOR = " > ";
function nodeLabel(item: KnowledgeBaseGraphNode) {
  // 文書の節点の名前はファイル名で、階層のパスではない。
  return item.name && item.type !== "document" ? graphPathLabel(item.name, SECTION_PATH_SEPARATOR) : { primary: item.name || item.id };
}
function StructureNode({ data, selected }: NodeProps<Node<StructureNodeData>>) {
  const label = nodeLabel(data.item);
  return <GraphNodeCard icon={KIND_ICONS[data.item.type ?? ""] ?? Layers} kindLabel={kindLabel(data.item.type)} primary={label.primary}
    secondary={data.item.document_name ?? label.context} fill={kindFill(data.item.type)} selected={selected} matched={data.matched} dimmed={data.dimmed}
    title={data.item.name || data.item.id} />;
}
const NODE_TYPES = { structure: StructureNode };

function StructureCanvas({ data: initialData, knowledgeBaseId }: { data: KnowledgeBaseGraphData; knowledgeBaseId: string }) {
  const [extraNodes, setExtraNodes] = useState<KnowledgeBaseGraphNode[]>([]);
  const [referenceEdge, setReferenceEdge] = useState<KnowledgeBaseGraphData["edges"][number] | null>(null);
  const focusedReference = useRef<string | null>(null);
  const canvasAnchor = useRef<HTMLDivElement>(null);
  const [pendingFocus, setPendingFocus] = useState<string | null>(null);
  const data = useMemo(() => ({ ...initialData, nodes: [...new Map([...initialData.nodes, ...extraNodes].map((node) => [node.id, node])).values()], edges: referenceEdge ? [...initialData.edges.filter((edge) => edge.id !== referenceEdge.id), referenceEdge] : initialData.edges }), [initialData, extraNodes, referenceEdge]);
  const flow = useReactFlow<Node<StructureNodeData>, Edge>();
  const filterId = useId();
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [disabledKinds, setDisabledKinds] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Selection>(null);
  const [cursor, setCursor] = useState(0);
  const [sizes, setSizes] = useState<Map<string, GraphNodeDimensions>>(new Map());
  // ドラッグによる座標の上書き。配置は決定論のまま、差分だけを持つ。
  const [overrides, setOverrides] = useState<Map<string, GraphPoint>>(new Map());
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
  const kinds = useMemo(() => [...new Set(data.nodes.map((node) => node.type ?? "other"))], [data]);
  const visibleNodes = useMemo(() => data.nodes.filter((node) => (!kind || (node.type ?? "other") === kind) && !disabledKinds.has(node.type ?? "other")), [data, kind, disabledKinds]);
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(() => data.edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)), [data, visibleIds]);
  // 種類の切替だけで表示範囲を合わせ、検索中の移動や拡大率は保持する。
  useEffect(() => { void flow.fitView({ padding: 0.18, duration: 0 }); }, [flow, visibleIds]);
  const matchesFor = (value: string): Match[] => {
    const query = normalizeGraphSearch(value.trim());
    if (!query) return [];
    return [
      ...visibleNodes.filter((node) => normalizeGraphSearch(`${node.name} ${kindLabel(node.type)} ${node.id}`).includes(query)).map((node) => ({ type: "node" as const, id: node.id })),
      ...visibleEdges.filter((edge) => normalizeGraphSearch(`${edgeLabel(edge.type)} ${edge.type ?? ""} ${edge.id}`).includes(query)).map((edge) => ({ type: "edge" as const, id: edge.id })),
    ];
  };
  const matches = matchesFor(search);
  const matchedNodes = new Set(matches.filter((match) => match.type === "node").map((match) => match.id));
  const layout = useMemo(() => layoutGraphHierarchy({
    nodes: visibleNodes.map((node) => ({ id: node.id, ...(sizes.get(node.id) ?? GRAPH_NODE_CARD_SIZE) })),
    edges: visibleEdges.filter((edge) => edge.type === "contains"), columnGap: GRAPH_NODE_CARD_SIZE.width / 4, rowGap: GRAPH_NODE_CARD_SIZE.height / 2,
  }), [visibleNodes, visibleEdges, sizes]);
  const positionOf = (id: string) => overrides.get(id) ?? layout.positions.get(id)!;
  const neighbors = graphNeighborIds(hoveredNode, visibleEdges);
  const nodes: Node<StructureNodeData>[] = visibleNodes.map((node) => ({ id: node.id, type: "structure", position: positionOf(node.id), ...(sizes.has(node.id) ? { measured: sizes.get(node.id) } : {}), ariaLabel: `${kindLabel(node.type)}：${node.name || node.id}`, data: { item: node, matched: matchedNodes.has(node.id), dimmed: Boolean(neighbors && !neighbors.has(node.id)) }, selected: selection?.type === "node" && selection.id === node.id, style: { width: GRAPH_NODE_CARD_SIZE.width, minHeight: GRAPH_NODE_CARD_SIZE.height, padding: 0, border: "none" } }));
  const edges: Edge[] = visibleEdges.map((edge) => {
    const selected = selection?.type === "edge" && selection.id === edge.id;
    const isReference = edge.type === "references";
    const { sourceHandle, targetHandle } = selectGraphEdgeHandles(positionOf(edge.source), positionOf(edge.target));
    return {
      id: edge.id, source: edge.source, target: edge.target, sourceHandle, targetHandle, markerEnd: GRAPH_EDGE_MARKER,
      // 参照は実線で常にラベルを出す。包含は多いので点線で、選択と hover のときだけラベルを出す。
      label: isReference || selected || hoveredEdge === edge.id ? edgeLabel(edge.type) : undefined,
      ariaLabel: t("knowledgeBases.graph.connection", { from: data.nodes.find((node) => node.id === edge.source)?.name || edge.source, to: data.nodes.find((node) => node.id === edge.target)?.name || edge.target, relation: edgeLabel(edge.type) }),
      selected,
      style: graphEdgeStyle({ emphasized: selected, width: isReference ? 1.25 : 1, dash: isReference ? undefined : "4 3", opacity: neighbors && edge.source !== hoveredNode && edge.target !== hoveredNode ? 0.35 : 1 }),
      ...GRAPH_EDGE_LABEL_PROPS,
    };
  });
  useEffect(() => {
    if (!pendingFocus || focusedReference.current === pendingFocus) return;
    const position = overrides.get(pendingFocus) ?? layout.positions.get(pendingFocus);
    const size = sizes.get(pendingFocus);
    if (position && size) {
      canvasAnchor.current?.scrollIntoView({ block: "center", behavior: "instant" });
      void flow.setCenter(position.x + size.width / 2, position.y + size.height / 2, { zoom: 1, duration: 0 });
      focusedReference.current = pendingFocus;
    }
  }, [pendingFocus, layout, overrides, sizes, flow]);
  const locateReference = (item: GraphReferenceItem, target: KnowledgeBaseGraphNode) => {
    focusedReference.current = null;
    setKind(""); setDisabledKinds(new Set());
    setExtraNodes((before) => [...new Map([...before, item.source, ...(item.target ? [item.target] : [])].map((node) => [node.id, node])).values()].slice(-100));
    if (item.target) setReferenceEdge({ id: item.id, source: item.source.id, target: item.target.id, type: "references", confidence: 1 });
    setSelection({ type: "node", id: target.id }); setPendingFocus(target.id);
  };
  const focus = (match: Match) => {
    setSelection(match);
    if (match.type === "edge") {
      const edge = visibleEdges.find((item) => item.id === match.id);
      if (edge) void flow.fitView({ nodes: [{ id: edge.source }, { id: edge.target }], padding: 0.3, maxZoom: 1, duration: 0 });
    } else {
      const node = flow.getNode(match.id);
      if (node) void flow.setCenter(node.position.x + (node.measured?.width ?? GRAPH_NODE_CARD_SIZE.width) / 2, node.position.y + (node.measured?.height ?? GRAPH_NODE_CARD_SIZE.height) / 2, { zoom: 1, duration: 0 });
    }
  };
  const searchChanged = (value: string) => {
    setSearch(value); setCursor(0);
    const first = matchesFor(value)[0];
    if (first) focus(first); else setSelection(null);
  };
  const jump = (direction: number) => {
    if (!matches.length) return;
    const next = (cursor + direction + matches.length) % matches.length;
    setCursor(next); focus(matches[next]!);
  };
  const nodesChanged = (changes: NodeChange<Node<StructureNodeData>>[]) => {
    setOverrides((before) => applyGraphNodePositionChanges(before, changes));
    setSizes((before) => applyGraphNodeDimensionChanges(before, changes));
  };
  const selectedNode = selection?.type === "node" ? data.nodes.find((node) => node.id === selection.id) : undefined;
  const selectedEdge = selection?.type === "edge" ? data.edges.find((edge) => edge.id === selection.id) : undefined;
  const nodeName = (id: string) => data.nodes.find((node) => node.id === id)?.name || id;
  const fields = selectedNode ? [
    { label: t("knowledgeBases.graph.name"), value: selectedNode.name || selectedNode.id },
    ...(selectedNode.document_name ? [{ label: t("knowledgeBases.structure.build.document"), value: selectedNode.document_name }] : []),
    { label: t("knowledgeBases.graph.kind"), value: kindLabel(selectedNode.type) },
    { label: t("knowledgeBases.graph.identifier"), value: selectedNode.id, technical: true },
  ] : selectedEdge ? [
    { label: t("knowledgeBases.graph.relationship"), value: edgeLabel(selectedEdge.type) },
    { label: t("knowledgeBases.graph.from"), value: nodeName(selectedEdge.source) },
    { label: t("knowledgeBases.graph.to"), value: nodeName(selectedEdge.target) },
    { label: t("knowledgeBases.graph.identifier"), value: selectedEdge.id, technical: true },
  ] : [];
  return <div className="grid min-w-0 gap-3">
    <div className="flex min-w-0 flex-wrap items-start gap-2">
      <GraphSearchNavigation labels={{ search: t("knowledgeBases.graph.search"), clear: t("common.clearSearch"), previous: t("knowledgeBases.graph.previousMatch"), next: t("knowledgeBases.graph.nextMatch"), noMatch: t("knowledgeBases.graph.noMatch"), scope: t("knowledgeBases.graph.searchScope") }} value={search} onChange={searchChanged} count={matches.length} cursor={cursor} onPrevious={() => jump(-1)} onNext={() => jump(1)} testId="knowledge-base-graph-search" />
      <SelectField id={filterId} label={t("knowledgeBases.graph.kind")} labelHidden size="lg" width="md" value={kind} onValueChange={(value) => { setKind(value); setSelection(null); setCursor(0); }} options={[{ value: "", label: t("knowledgeBases.graph.allKinds") }, ...kinds.map((value) => ({ value, label: kindLabel(value) }))]} />
    </div>
    <div ref={canvasAnchor}><GraphCanvasFrame role="region" aria-label={t("knowledgeBases.graph.title")}>
      <GraphCanvas nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} onNodesChange={nodesChanged} elementsSelectable={false} onlyRenderVisibleElements={visibleNodes.length > 150}
        onSelectNode={(id) => setSelection({ type: "node", id })} onSelectEdge={(id) => setSelection({ type: "edge", id })}
        onNodeMouseEnter={(_event, node) => setHoveredNode(node.id)} onNodeMouseLeave={() => setHoveredNode(null)}
        onEdgeMouseEnter={(_event, edge) => setHoveredEdge(edge.id)} onEdgeMouseLeave={() => setHoveredEdge(null)}
        miniMap={visibleNodes.length > 12 ? { "aria-label": t("knowledgeBases.graph.minimap"), nodeColor: (node) => kindFill((node.data as StructureNodeData).item.type) } : false}>
        <GraphCanvasControls labels={{ zoomIn: t("knowledgeBases.graph.zoomIn"), zoomOut: t("knowledgeBases.graph.zoomOut"), fit: t("knowledgeBases.graph.fitView"), reset: t("knowledgeBases.graph.reset") }} onReset={() => setOverrides(new Map())} resetDisabled={overrides.size === 0} />
        <GraphLegend label={t("knowledgeBases.graph.legend")} items={kinds.map((value) => ({ id: value, label: kindLabel(value), marker: <GraphLegendSwatch fill={kindFill(value)} /> }))} disabledIds={disabledKinds} onToggle={(id) => { setDisabledKinds((before) => { const next = new Set(before); if (next.has(id)) next.delete(id); else next.add(id); return next; }); setSelection(null); setCursor(0); }} testId="knowledge-base-graph-legend" />
      </GraphCanvas>
    </GraphCanvasFrame></div>
    {selectedNode ? <GraphReferencePanel key={`${knowledgeBaseId}:${selectedNode.id}`} kbId={knowledgeBaseId} nodeId={selectedNode.id} referenceId={selectedNode.reference_id} onLocate={locateReference} /> : null}
    {fields.length ? <GraphSelectionDetails title={t(selectedNode ? "knowledgeBases.graph.selectedNode" : "knowledgeBases.graph.selectedEdge")} closeLabel={t("knowledgeBases.graph.clearSelection")} onClose={() => setSelection(null)} fields={fields} /> : null}
  </div>;
}

export function KnowledgeBaseGraphCanvas({ data, knowledgeBaseId }: { data: KnowledgeBaseGraphData; knowledgeBaseId: string }) {
  return <GraphCanvasProvider><StructureCanvas data={data} knowledgeBaseId={knowledgeBaseId} /></GraphCanvasProvider>;
}
