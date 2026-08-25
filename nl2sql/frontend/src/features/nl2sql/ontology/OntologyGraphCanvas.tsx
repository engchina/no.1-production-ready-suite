import { useMemo, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Boxes,
  CalendarClock,
  Circle,
  Columns3,
  Database,
  Layers,
  ListOrdered,
  Maximize2,
  MessageSquare,
  Minus,
  Network,
  Plus,
  Route,
  Search,
  ShieldCheck,
  Sigma,
  Table2,
  Tag,
  type LucideIcon,
} from "lucide-react";
import {
  Background,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  ViewportPortal,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { t } from "@/lib/i18n";
import {
  layoutOntologyGraphSemanticMatrix,
  ontologyGraphObjectClusterKey,
  type OntologyGraphSemanticLane,
  type OntologyGraphSemanticLaneId,
} from "./graphLayout";
import { cssVar, edgeStroke, nodeFill, nodeFillVar, nodeStroke } from "./graphPalette";
import {
  isOntologyDetailNodeKind,
  ontologyGraphForViewMode,
  ontologyGraphWithDetailVisibility,
  type OntologyGraphViewMode,
} from "./graphView";
import { ontologyNodeDisplay, ontologyNodeSearchValues } from "./nodeDisplay";
import type { OntologyGraph, OntologyNode } from "./types";

interface OntologyGraphCanvasProps {
  graph: OntologyGraph;
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
  onSelectNode?: (nodeId: string) => void;
  onSelectEdge?: (edgeId: string) => void;
  /** 決定論 NL Playground 等のハイライト。指定時は非対象を減光し、対象の枠を強調する。 */
  highlightNodeIds?: string[];
  highlightEdgeIds?: string[];
  viewMode?: OntologyGraphViewMode;
  defaultViewMode?: OntologyGraphViewMode;
  onViewModeChange?: (mode: OntologyGraphViewMode) => void;
}

const NODE_WIDTH = 220;
const NODE_HEIGHT = 76;
const GRAPH_VIEW_MODES: Array<{ id: OntologyGraphViewMode; labelKey: string; icon: LucideIcon }> = [
  { id: "grounding", labelKey: "nl2sql.ontology.graphMode.grounding", icon: Route },
  { id: "all", labelKey: "nl2sql.ontology.graphMode.all", icon: Network },
  { id: "physical_er", labelKey: "nl2sql.ontology.graphMode.physicalEr", icon: Table2 },
];
const LANE_LABEL_KEYS: Record<OntologyGraphSemanticLaneId, string> = {
  business: "nl2sql.ontology.graphLane.business",
  attribute: "nl2sql.ontology.graphLane.attribute",
  physical: "nl2sql.ontology.graphLane.physical",
  detail: "nl2sql.ontology.graphLane.detail",
};

const KIND_ICONS: Record<string, LucideIcon> = {
  schema: Database,
  table: Table2,
  view: Layers,
  column: Columns3,
  business_entity: Boxes,
  business_event: CalendarClock,
  property: Tag,
  metric: Sigma,
  business_term: BookOpen,
  business_rule: ShieldCheck,
  enum_value: ListOrdered,
  question_intent: MessageSquare,
  validation_finding: AlertTriangle,
};

function nodeShape(node: OntologyNode): string {
  if (node.kind === "metric") return "18px";
  if (node.kind === "validation_finding") return "3px";
  return "8px";
}

interface OntologyNodeData extends Record<string, unknown> {
  node: OntologyNode;
  highlighted: boolean;
  dimmed: boolean;
  stats?: {
    columnCount: number;
    joinCount: number;
  };
}

/** アイコン + 業務名 + 技術名のカード。型=塗り/状態=枠のチャネル分離は graphPalette を踏襲。 */
function OntologyNodeCard({ data, selected }: NodeProps<Node<OntologyNodeData>>) {
  const { node, highlighted, dimmed, stats } = data;
  const Icon = KIND_ICONS[node.kind] ?? Circle;
  const display = ontologyNodeDisplay(node, { highlighted });
  const emphasizedBorder = selected || highlighted || node.validation_status === "blocked";
  const showStats =
    (node.kind === "table" || node.kind === "view") &&
    Boolean(stats && (stats.columnCount > 0 || stats.joinCount > 0));
  return (
    <div
      className="grid h-full grid-cols-[auto_minmax(0,1fr)] items-center gap-2 px-3 py-2"
      data-testid={`ontology-node-card-${node.id}`}
      data-ontology-node-kind={node.kind}
      style={{
        width: NODE_WIDTH,
        minHeight: NODE_HEIGHT,
        border: `${emphasizedBorder ? 2 : 1}px solid ${
          highlighted ? cssVar("--primary") : nodeStroke(node, Boolean(selected))
        }`,
        borderRadius: nodeShape(node),
        background: nodeFill(node),
        color: cssVar("--graph-fg"),
        opacity: dimmed ? 0.35 : 1,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ visibility: "hidden" }} />
      <span
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
        style={{ background: "color-mix(in srgb, currentColor 12%, transparent)" }}
      >
        <Icon size={15} aria-hidden="true" />
      </span>
      <span className="grid min-w-0 gap-0.5">
        <span
          className="inline-flex max-w-full items-center justify-self-start truncate rounded border border-current/20 px-1.5 py-0.5 text-[10px] font-semibold leading-3 opacity-75"
          data-testid="ontology-node-kind-label"
        >
          {display.kindLabel}
        </span>
        <span className="block truncate text-[13px] font-semibold leading-5">
          {display.primaryLabel}
        </span>
        {display.secondaryLabel ? (
          <span
            className="block truncate text-[10px] leading-4 opacity-70"
            title={display.secondaryLabel}
          >
            {display.secondaryLabel}
          </span>
        ) : null}
        {showStats ? (
          <span className="mt-0.5 flex min-w-0 flex-wrap gap-1 text-[10px] leading-3">
            <span className="rounded border border-current/15 px-1 py-0.5 opacity-75">
              {t("nl2sql.ontology.nodeStats.columns", { count: stats?.columnCount ?? 0 })}
            </span>
            {stats?.joinCount ? (
              <span className="rounded border border-current/15 px-1 py-0.5 opacity-75">
                {t("nl2sql.ontology.nodeStats.joins", { count: stats.joinCount })}
              </span>
            ) : null}
          </span>
        ) : null}
      </span>
      <Handle type="source" position={Position.Right} style={{ visibility: "hidden" }} />
    </div>
  );
}

const NODE_TYPES = { ontology: OntologyNodeCard };

function FlowControls() {
  const flow = useReactFlow();
  return (
    <div className="absolute right-3 top-3 z-10 flex gap-1 rounded-md border border-border bg-card p-1 shadow-sm">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-label={t("nl2sql.ontology.graphZoomIn")}
        title={t("nl2sql.ontology.graphZoomIn")}
        onClick={() => void flow.zoomIn({ duration: 0 })}
      >
        <Plus size={15} aria-hidden="true" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-label={t("nl2sql.ontology.graphZoomOut")}
        title={t("nl2sql.ontology.graphZoomOut")}
        onClick={() => void flow.zoomOut({ duration: 0 })}
      >
        <Minus size={15} aria-hidden="true" />
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-label={t("nl2sql.ontology.graphFit")}
        title={t("nl2sql.ontology.graphFit")}
        onClick={() => void flow.fitView({ padding: 0.28, duration: 0 })}
      >
        <Maximize2 size={15} aria-hidden="true" />
      </Button>
    </div>
  );
}

function LegendSwatch({ kind }: { kind: string }) {
  return (
    <span
      className="h-2.5 w-2.5 shrink-0 rounded-[3px] border border-current/20"
      style={{ background: cssVar(nodeFillVar(kind)) }}
      aria-hidden="true"
    />
  );
}

function OntologyGraphLegend() {
  return (
    <div
      className="absolute bottom-3 left-3 z-10 max-w-[calc(100%-1.5rem)] rounded-md border border-border bg-card px-2 py-1.5 shadow-sm"
      aria-label={t("nl2sql.ontology.legendLabel")}
      data-testid="ontology-graph-legend"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] leading-4 text-muted">
        <span className="inline-flex items-center gap-1">
          <LegendSwatch kind="business_entity" />
          {t("nl2sql.ontology.legend.business")}
        </span>
        <span className="inline-flex items-center gap-1">
          <LegendSwatch kind="table" />
          {t("nl2sql.ontology.legend.physical")}
        </span>
        <span className="inline-flex items-center gap-1">
          <LegendSwatch kind="property" />
          {t("nl2sql.ontology.legend.attribute")}
        </span>
        <span className="inline-flex items-center gap-1">
          <LegendSwatch kind="metric" />
          {t("nl2sql.ontology.legend.metric")}
        </span>
        <span className="inline-flex items-center gap-1 whitespace-normal">
          <span
            className="h-px w-5 shrink-0 border-t"
            style={{ borderColor: cssVar("--graph-line") }}
            aria-hidden="true"
          />
          {t("nl2sql.ontology.legend.mapping")}
        </span>
      </div>
    </div>
  );
}

function normalize(text: string): string {
  return text.normalize("NFKC").toLowerCase();
}

function objectNodeStats(graph: OntologyGraph): Map<string, { columnCount: number; joinCount: number }> {
  const stats = new Map<string, { columnCount: number; joinCount: number }>();
  const ensure = (key: string) => {
    const current = stats.get(key) ?? { columnCount: 0, joinCount: 0 };
    stats.set(key, current);
    return current;
  };
  for (const node of graph.nodes) {
    if (node.kind !== "column") continue;
    const objectKey = ontologyGraphObjectClusterKey(node);
    if (objectKey) ensure(objectKey).columnCount += 1;
  }
  const objectKeyByNodeId = new Map(
    graph.nodes
      .map((node) => [node.id, ontologyGraphObjectClusterKey(node)] as const)
      .filter((entry): entry is readonly [string, string] => Boolean(entry[1]))
  );
  const counted = new Set<string>();
  for (const edge of graph.edges) {
    if ((edge.join_conditions ?? []).length === 0) continue;
    for (const nodeId of [edge.source_node_id, edge.target_node_id]) {
      const objectKey = objectKeyByNodeId.get(nodeId);
      if (!objectKey) continue;
      const countKey = `${edge.id}\u0000${objectKey}`;
      if (counted.has(countKey)) continue;
      counted.add(countKey);
      ensure(objectKey).joinCount += 1;
    }
  }
  return stats;
}

function GraphModeControl({
  mode,
  onChange,
}: {
  mode: OntologyGraphViewMode;
  onChange: (mode: OntologyGraphViewMode) => void;
}) {
  return (
    <div
      className="pointer-events-auto flex h-[44px] items-center gap-1 rounded-md border border-border bg-card p-1 shadow-sm sm:h-[40px]"
      role="group"
      aria-label={t("nl2sql.ontology.graphMode.label")}
      data-testid="ontology-graph-view-mode"
    >
      {GRAPH_VIEW_MODES.map((item) => {
        const Icon = item.icon;
        const selected = item.id === mode;
        return (
          <button
            key={item.id}
            type="button"
            aria-pressed={selected}
            className={cn(
              "inline-flex h-[36px] cursor-pointer items-center gap-1 rounded px-2 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none sm:h-[32px]",
              selected ? "bg-primary text-primary-foreground" : "text-muted hover:bg-background hover:text-foreground"
            )}
            onClick={() => onChange(item.id)}
            data-testid={`ontology-graph-mode-${item.id}`}
          >
            <Icon size={13} aria-hidden="true" />
            <span>{t(item.labelKey)}</span>
          </button>
        );
      })}
    </div>
  );
}

function GraphToolbarSearchField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const label = t("nl2sql.ontology.graphSearch");
  return (
    <label
      className="pointer-events-auto flex h-[44px] w-full min-w-0 max-w-full items-center gap-2 rounded-md border border-border bg-card px-3 text-sm text-foreground shadow-sm transition-colors focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/40 sm:h-[40px] sm:w-72 sm:max-w-[18rem]"
      data-testid="ontology-graph-search-field"
    >
      <Search size={15} aria-hidden="true" className="shrink-0 text-muted" />
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        placeholder={label}
        aria-label={label}
        className="min-w-0 flex-1 appearance-none border-0 bg-transparent p-0 text-sm leading-5 text-foreground shadow-none outline-none placeholder:text-muted/70 focus:border-transparent focus:shadow-none focus:outline-none focus:ring-0 focus-visible:shadow-none focus-visible:outline-none focus-visible:ring-0 [&::-webkit-search-cancel-button]:appearance-none [&::-webkit-search-decoration]:appearance-none"
        style={{ WebkitAppearance: "none", boxShadow: "none" }}
        data-testid="ontology-graph-search"
      />
    </label>
  );
}

function LaneOverlays({ lanes }: { lanes: OntologyGraphSemanticLane[] }) {
  return (
    <ViewportPortal>
      {lanes.map((lane) => (
        <div
          key={lane.id}
          className="pointer-events-none absolute flex items-start"
          data-testid={`ontology-graph-lane-${lane.id}`}
          style={{
            transform: `translate(18px, ${lane.y + 2}px)`,
            height: lane.height,
            width: 176,
          }}
        >
          <span className="rounded-md border border-border bg-card/95 px-2 py-1 text-[10px] font-semibold leading-4 text-muted shadow-sm">
            {t(LANE_LABEL_KEYS[lane.id])}
          </span>
        </div>
      ))}
    </ViewportPortal>
  );
}

function OntologyFlow({
  graph,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  highlightNodeIds,
  highlightEdgeIds,
  viewMode,
  defaultViewMode,
  onViewModeChange,
}: OntologyGraphCanvasProps) {
  const [search, setSearch] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [internalViewMode, setInternalViewMode] = useState<OntologyGraphViewMode>(
    defaultViewMode ?? "all"
  );
  const currentViewMode = viewMode ?? internalViewMode;
  const changeViewMode = (nextMode: OntologyGraphViewMode) => {
    setInternalViewMode(nextMode);
    onViewModeChange?.(nextMode);
  };

  const detailCount = useMemo(
    () => graph.nodes.filter((node) => isOntologyDetailNodeKind(node.kind)).length,
    [graph.nodes]
  );
  const externalHighlight =
    (highlightNodeIds?.length ?? 0) > 0 || (highlightEdgeIds?.length ?? 0) > 0;
  const visibleGraph = useMemo<OntologyGraph>(() => {
    const scoped = ontologyGraphForViewMode(graph, currentViewMode, highlightNodeIds, highlightEdgeIds);
    const forcedDetails = new Set<string>([...(highlightNodeIds ?? [])]);
    if (selectedNodeId) forcedDetails.add(selectedNodeId);
    return ontologyGraphWithDetailVisibility(scoped, showDetails || detailCount === 0, forcedDetails);
  }, [
    graph,
    currentViewMode,
    highlightNodeIds,
    highlightEdgeIds,
    selectedNodeId,
    showDetails,
    detailCount,
  ]);

  const semanticLayout = useMemo(
    () =>
      layoutOntologyGraphSemanticMatrix(visibleGraph, {
        nodeWidth: NODE_WIDTH,
        nodeHeight: NODE_HEIGHT,
      }),
    [visibleGraph]
  );
  const statsByObject = useMemo(() => objectNodeStats(graph), [graph]);

  // 強調対象: 外部ハイライト(Playground 等)が最優先、なければ検索一致。
  const query = normalize(search.trim());
  const emphasis = useMemo(() => {
    if (externalHighlight) {
      return {
        active: true,
        nodes: new Set(highlightNodeIds ?? []),
        edges: new Set(highlightEdgeIds ?? []),
      };
    }
    if (!query) return { active: false, nodes: new Set<string>(), edges: new Set<string>() };
    const nodes = new Set(
      visibleGraph.nodes
        .filter((node) =>
          ontologyNodeSearchValues(node).some((name) => normalize(name).includes(query))
        )
        .map((node) => node.id)
    );
    const edges = new Set(
      visibleGraph.edges
        .filter(
          (edge) =>
            (nodes.has(edge.source_node_id) && nodes.has(edge.target_node_id)) ||
            normalize(edge.relationship_name_ja).includes(query)
        )
        .map((edge) => edge.id)
    );
    return { active: true, nodes, edges };
  }, [externalHighlight, highlightNodeIds, highlightEdgeIds, query, visibleGraph]);

  const nodes = useMemo<Node<OntologyNodeData>[]>(
    () =>
      visibleGraph.nodes.map((node) => {
        const highlighted = emphasis.nodes.has(node.id);
        return {
          id: node.id,
          type: "ontology",
          position: semanticLayout.positions.get(node.id) ?? { x: 0, y: 0 },
          data: {
            node,
            highlighted,
            dimmed: emphasis.active && !highlighted,
            stats: statsByObject.get(ontologyGraphObjectClusterKey(node) ?? ""),
          },
          ariaLabel: ontologyNodeDisplay(node, { highlighted }).ariaLabel,
          selected: node.id === selectedNodeId,
          style: { width: NODE_WIDTH, minHeight: NODE_HEIGHT, padding: 0, border: "none" },
        };
      }),
    [visibleGraph.nodes, semanticLayout.positions, selectedNodeId, emphasis, statsByObject]
  );

  const edges = useMemo<Edge[]>(
    () =>
      visibleGraph.edges.map((edge) => {
        const highlighted = emphasis.edges.has(edge.id);
        const selected = edge.id === selectedEdgeId;
        const showLabel = highlighted || selected || edge.id === hoveredEdgeId;
        return {
          id: edge.id,
          source: edge.source_node_id,
          target: edge.target_node_id,
          label: showLabel ? edge.relationship_name_ja : undefined,
          ariaLabel: `${edge.relationship_name_ja}${highlighted ? "、質問に一致" : ""}`,
          markerEnd: { type: MarkerType.ArrowClosed, color: cssVar("--graph-line") },
          style: {
            stroke: highlighted || selected ? cssVar("--primary") : edgeStroke(edge),
            strokeWidth: highlighted || selected ? 2.5 : edge.validation_status === "blocked" ? 2 : 1.25,
            strokeDasharray: edge.review_status === "proposed" ? "5 4" : undefined,
            opacity: emphasis.active && !highlighted ? 0.3 : 1,
          },
          labelStyle: { fill: cssVar("--muted"), fontSize: 11, fontWeight: 600 },
          labelBgStyle: { fill: cssVar("--card"), fillOpacity: 0.92 },
        };
      }),
    [visibleGraph.edges, emphasis, hoveredEdgeId, selectedEdgeId]
  );

  return (
    <div className="relative h-[32rem] min-h-80 overflow-hidden rounded-md border border-border bg-background">
      <div className="pointer-events-none absolute left-3 top-3 z-10 flex max-w-[calc(100%-7rem)] flex-wrap items-center gap-2">
        <GraphModeControl mode={currentViewMode} onChange={changeViewMode} />
        <GraphToolbarSearchField value={search} onChange={setSearch} />
        {detailCount > 0 ? (
          <label
            className="pointer-events-auto flex h-[44px] cursor-pointer items-center gap-2 rounded-md border border-border bg-card px-3 text-xs text-foreground shadow-sm transition-colors focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/40 sm:h-[40px]"
            data-testid="ontology-graph-details-toggle-field"
          >
            <input
              type="checkbox"
              className="h-4 w-4 accent-primary"
              checked={showDetails}
              onChange={(event) => setShowDetails(event.currentTarget.checked)}
              data-testid="ontology-graph-details-toggle"
            />
            <span>{t("nl2sql.ontology.graphShowDetails", { count: detailCount })}</span>
          </label>
        ) : null}
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.28 }}
        minZoom={0.25}
        maxZoom={1.8}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        nodesFocusable
        edgesFocusable
        onNodeClick={(_event, node) => onSelectNode?.(node.id)}
        onEdgeClick={(_event, edge) => onSelectEdge?.(edge.id)}
        onEdgeMouseEnter={(_event, edge) => setHoveredEdgeId(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background color={cssVar("--border")} gap={20} size={1} />
        <LaneOverlays lanes={semanticLayout.lanes} />
        {visibleGraph.nodes.length > 20 ? (
          // 小規模グラフでは全体が一目で見えるため出さない(白い矩形ノイズを避ける)
          <MiniMap
            pannable
            zoomable
            position="bottom-right"
            aria-label={t("nl2sql.ontology.graphMinimap")}
            style={{ width: 140, height: 90 }}
            bgColor={cssVar("--card")}
            maskColor="color-mix(in srgb, var(--border) 45%, transparent)"
            nodeColor={cssVar("--graph-line")}
            nodeStrokeColor={cssVar("--graph-line")}
          />
        ) : null}
        <FlowControls />
      </ReactFlow>
      <OntologyGraphLegend />
    </div>
  );
}

export default function OntologyGraphCanvas(props: OntologyGraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <OntologyFlow {...props} />
    </ReactFlowProvider>
  );
}
