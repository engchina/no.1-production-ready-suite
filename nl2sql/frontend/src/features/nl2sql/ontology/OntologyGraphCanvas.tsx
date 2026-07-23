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
  Plus,
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
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Button } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { layoutOntologyGraphLayered } from "./graphLayout";
import { cssVar, edgeStroke, nodeFill, nodeStroke } from "./graphPalette";
import type { OntologyGraph, OntologyNode } from "./types";

interface OntologyGraphCanvasProps {
  graph: OntologyGraph;
  selectedNodeId?: string | null;
  onSelectNode?: (nodeId: string) => void;
  /** 決定論 NL Playground 等のハイライト。指定時は非対象を減光し、対象の枠を強調する。 */
  highlightNodeIds?: string[];
  highlightEdgeIds?: string[];
}

const NODE_WIDTH = 200;
const NODE_HEIGHT = 64;
// 小規模グラフはエッジラベルを常時表示、混みだしたら hover/強調時のみ表示する
const ALWAYS_LABEL_EDGE_LIMIT = 12;
// 既定で畳む詳細ノード(物理列・列挙値)。トグルで展開できる。
const DETAIL_KINDS = new Set(["column", "enum_value"]);

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
}

/** アイコン + 業務名 + 技術名のカード。型=塗り/状態=枠のチャネル分離は graphPalette を踏襲。 */
function OntologyNodeCard({ data, selected }: NodeProps<Node<OntologyNodeData>>) {
  const { node, highlighted, dimmed } = data;
  const Icon = KIND_ICONS[node.kind] ?? Circle;
  const emphasizedBorder = selected || highlighted || node.validation_status === "blocked";
  return (
    <div
      className="grid h-full grid-cols-[auto_minmax(0,1fr)] items-center gap-2 px-3 py-2"
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
      <span className="min-w-0">
        <span className="block truncate text-[13px] font-semibold leading-5">
          {node.business_name_ja}
        </span>
        {node.technical_name && node.technical_name !== node.business_name_ja ? (
          <span className="block truncate text-[10px] leading-4 opacity-70">
            {node.technical_name}
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
        onClick={() => void flow.fitView({ padding: 0.16, duration: 0 })}
      >
        <Maximize2 size={15} aria-hidden="true" />
      </Button>
    </div>
  );
}

function normalize(text: string): string {
  return text.normalize("NFKC").toLowerCase();
}

function OntologyFlow({
  graph,
  selectedNodeId,
  onSelectNode,
  highlightNodeIds,
  highlightEdgeIds,
}: OntologyGraphCanvasProps) {
  const [search, setSearch] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);

  const detailCount = useMemo(
    () => graph.nodes.filter((node) => DETAIL_KINDS.has(node.kind)).length,
    [graph.nodes]
  );
  const visibleGraph = useMemo<OntologyGraph>(() => {
    if (showDetails || detailCount === 0) return graph;
    const nodes = graph.nodes.filter((node) => !DETAIL_KINDS.has(node.kind));
    const visibleIds = new Set(nodes.map((node) => node.id));
    return {
      ...graph,
      nodes,
      edges: graph.edges.filter(
        (edge) => visibleIds.has(edge.source_node_id) && visibleIds.has(edge.target_node_id)
      ),
    };
  }, [graph, showDetails, detailCount]);

  const layout = useMemo(
    () =>
      layoutOntologyGraphLayered(visibleGraph, {
        nodeWidth: NODE_WIDTH,
        nodeHeight: NODE_HEIGHT,
      }),
    [visibleGraph]
  );

  // 強調対象: 外部ハイライト(Playground 等)が最優先、なければ検索一致。
  const externalHighlight =
    (highlightNodeIds?.length ?? 0) > 0 || (highlightEdgeIds?.length ?? 0) > 0;
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
          [node.business_name_ja, node.technical_name ?? "", ...(node.aliases ?? [])].some(
            (name) => normalize(name).includes(query)
          )
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
          position: layout.get(node.id) ?? { x: 0, y: 0 },
          data: {
            node,
            highlighted,
            dimmed: emphasis.active && !highlighted,
          },
          ariaLabel: `${node.business_name_ja}、${node.kind}、${node.validation_status ?? "未検証"}${highlighted ? "、質問に一致" : ""}`,
          selected: node.id === selectedNodeId,
          style: { width: NODE_WIDTH, minHeight: NODE_HEIGHT, padding: 0, border: "none" },
        };
      }),
    [visibleGraph.nodes, layout, selectedNodeId, emphasis]
  );

  const alwaysShowLabels = visibleGraph.edges.length <= ALWAYS_LABEL_EDGE_LIMIT;
  const edges = useMemo<Edge[]>(
    () =>
      visibleGraph.edges.map((edge) => {
        const highlighted = emphasis.edges.has(edge.id);
        const showLabel = alwaysShowLabels || highlighted || edge.id === hoveredEdgeId;
        return {
          id: edge.id,
          source: edge.source_node_id,
          target: edge.target_node_id,
          label: showLabel ? edge.relationship_name_ja : undefined,
          ariaLabel: `${edge.relationship_name_ja}${highlighted ? "、質問に一致" : ""}`,
          markerEnd: { type: MarkerType.ArrowClosed, color: cssVar("--graph-line") },
          style: {
            stroke: highlighted ? cssVar("--primary") : edgeStroke(edge),
            strokeWidth: highlighted ? 2.5 : edge.validation_status === "blocked" ? 2 : 1.25,
            strokeDasharray: edge.review_status === "proposed" ? "5 4" : undefined,
            opacity: emphasis.active && !highlighted ? 0.3 : 1,
          },
          labelStyle: { fill: cssVar("--muted"), fontSize: 11, fontWeight: 600 },
          labelBgStyle: { fill: cssVar("--card"), fillOpacity: 0.92 },
        };
      }),
    [visibleGraph.edges, emphasis, alwaysShowLabels, hoveredEdgeId]
  );

  return (
    <div className="relative h-[32rem] min-h-80 overflow-hidden rounded-md border border-border bg-background">
      <div className="absolute left-3 top-3 z-10 flex flex-wrap items-center gap-2">
        <label className="flex h-9 items-center gap-1 rounded-md border border-border bg-card px-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/40">
          <Search size={14} aria-hidden="true" className="shrink-0 text-muted" />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            placeholder={t("nl2sql.ontology.graphSearch")}
            aria-label={t("nl2sql.ontology.graphSearch")}
            className="w-32 bg-transparent text-xs text-foreground outline-none placeholder:text-muted sm:w-40"
            data-testid="ontology-graph-search"
          />
        </label>
        {detailCount > 0 ? (
          <label className="flex h-9 cursor-pointer items-center gap-2 rounded-md border border-border bg-card px-2 text-xs text-foreground shadow-sm">
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
        fitViewOptions={{ padding: 0.16 }}
        minZoom={0.25}
        maxZoom={1.8}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        nodesFocusable
        edgesFocusable
        onNodeClick={(_event, node) => onSelectNode?.(node.id)}
        onEdgeMouseEnter={(_event, edge) => setHoveredEdgeId(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background color={cssVar("--border")} gap={20} size={1} />
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
