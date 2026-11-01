import { useEffect, useId, useMemo, useState } from "react";
import { Handle, Position, useReactFlow, type Edge, type Node, type NodeChange, type NodeProps } from "@xyflow/react";
import { FileText, Heading, Image, Layers, Table2, type LucideIcon } from "lucide-react";
import { SelectField } from "@production-ready/ui";
import { GraphCanvas, GraphCanvasControls, GraphCanvasFrame, GraphCanvasProvider, GraphLegend, GraphNodeFrame, GraphSearchNavigation, GraphSelectionDetails, GRAPH_DEFAULT_NODE_SIZE, applyGraphNodeDimensionChanges, type GraphNodeDimensions, layoutGraphHierarchy, normalizeGraphSearch } from "@production-ready/ui/graph";
import "@xyflow/react/dist/style.css";

import type { KnowledgeBaseGraphData, KnowledgeBaseGraphNode } from "@/lib/api";
import { t } from "@/lib/i18n";

interface StructureNodeData extends Record<string, unknown> {
  item: KnowledgeBaseGraphNode;
  matched: boolean;
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
function edgeLabel(kind: string | null) {
  return t(kind === "contains" ? "knowledgeBases.graph.contains" : "knowledgeBases.graph.related");
}
function StructureNode({ data, selected }: NodeProps<Node<StructureNodeData>>) {
  const Icon = KIND_ICONS[data.item.type ?? ""] ?? Layers;
  return <GraphNodeFrame selected={selected} matched={data.matched} title={data.item.name || data.item.id}>
    <Handle type="target" position={Position.Left} className="invisible" />
    <div className="mb-1 flex items-center gap-1 text-xs text-fg-muted"><Icon aria-hidden size={14} />{kindLabel(data.item.type)}</div>
    <div className={data.item.name ? "line-clamp-2 font-medium" : "line-clamp-2 font-mono"}>{data.item.name || data.item.id}</div>
    <Handle type="source" position={Position.Right} className="invisible" />
  </GraphNodeFrame>;
}
const NODE_TYPES = { structure: StructureNode };

function StructureCanvas({ data }: { data: KnowledgeBaseGraphData }) {
  const flow = useReactFlow<Node<StructureNodeData>, Edge>();
  const filterId = useId();
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [disabledKinds, setDisabledKinds] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Selection>(null);
  const [cursor, setCursor] = useState(0);
  const [sizes, setSizes] = useState<Map<string, GraphNodeDimensions>>(new Map());
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
    nodes: visibleNodes.map((node) => ({ id: node.id, ...(sizes.get(node.id) ?? GRAPH_DEFAULT_NODE_SIZE) })),
    edges: visibleEdges.filter((edge) => edge.type === "contains"), columnGap: GRAPH_DEFAULT_NODE_SIZE.width / 4, rowGap: GRAPH_DEFAULT_NODE_SIZE.height / 2,
  }), [visibleNodes, visibleEdges, sizes]);
  const nodes: Node<StructureNodeData>[] = visibleNodes.map((node) => ({ id: node.id, type: "structure", position: layout.positions.get(node.id)!, ...(sizes.has(node.id) ? { measured: sizes.get(node.id) } : {}), ariaLabel: `${kindLabel(node.type)}：${node.name || node.id}`, data: { item: node, matched: matchedNodes.has(node.id) }, selected: selection?.type === "node" && selection.id === node.id }));
  const edges: Edge[] = visibleEdges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, label: edgeLabel(edge.type), ariaLabel: t("knowledgeBases.graph.connection", { from: data.nodes.find((node) => node.id === edge.source)?.name || edge.source, to: data.nodes.find((node) => node.id === edge.target)?.name || edge.target, relation: edgeLabel(edge.type) }), selected: selection?.type === "edge" && selection.id === edge.id }));
  const focus = (match: Match) => {
    setSelection(match);
    if (match.type === "edge") {
      const edge = visibleEdges.find((item) => item.id === match.id);
      if (edge) void flow.fitView({ nodes: [{ id: edge.source }, { id: edge.target }], padding: 0.3, maxZoom: 1, duration: 0 });
    } else {
      const node = flow.getNode(match.id);
      if (node) void flow.setCenter(node.position.x + (node.measured?.width ?? GRAPH_DEFAULT_NODE_SIZE.width) / 2, node.position.y + (node.measured?.height ?? GRAPH_DEFAULT_NODE_SIZE.height) / 2, { zoom: 1, duration: 0 });
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
  const measured = (changes: NodeChange<Node<StructureNodeData>>[]) => setSizes((before) => applyGraphNodeDimensionChanges(before, changes));
  const selectedNode = selection?.type === "node" ? data.nodes.find((node) => node.id === selection.id) : undefined;
  const selectedEdge = selection?.type === "edge" ? data.edges.find((edge) => edge.id === selection.id) : undefined;
  const nodeName = (id: string) => data.nodes.find((node) => node.id === id)?.name || id;
  const fields = selectedNode ? [
    { label: t("knowledgeBases.graph.name"), value: selectedNode.name || selectedNode.id },
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
    <GraphCanvasFrame role="region" aria-label={t("knowledgeBases.graph.title")}>
      <GraphCanvas nodes={nodes} edges={edges} nodeTypes={NODE_TYPES} onNodesChange={measured} nodesDraggable={false} elementsSelectable={false} zoomOnScroll={false} preventScrolling={false}
        onSelectNode={(id) => setSelection({ type: "node", id })} onSelectEdge={(id) => setSelection({ type: "edge", id })}
        miniMap={visibleNodes.length > 12 ? { "aria-label": t("knowledgeBases.graph.minimap") } : false}>
        <GraphCanvasControls labels={{ zoomIn: t("knowledgeBases.graph.zoomIn"), zoomOut: t("knowledgeBases.graph.zoomOut"), fit: t("knowledgeBases.graph.fitView"), reset: t("knowledgeBases.graph.reset") }} />
        <GraphLegend label={t("knowledgeBases.graph.legend")} items={kinds.map((value) => ({ id: value, label: kindLabel(value) }))} disabledIds={disabledKinds} onToggle={(id) => { setDisabledKinds((before) => { const next = new Set(before); if (next.has(id)) next.delete(id); else next.add(id); return next; }); setSelection(null); setCursor(0); }} testId="knowledge-base-graph-legend" />
      </GraphCanvas>
    </GraphCanvasFrame>
    {fields.length ? <GraphSelectionDetails title={t(selectedNode ? "knowledgeBases.graph.selectedNode" : "knowledgeBases.graph.selectedEdge")} closeLabel={t("knowledgeBases.graph.clearSelection")} onClose={() => setSelection(null)} fields={fields} /> : null}
  </div>;
}

export function KnowledgeBaseGraphCanvas({ data }: { data: KnowledgeBaseGraphData }) {
  return <GraphCanvasProvider><StructureCanvas data={data} /></GraphCanvasProvider>;
}
