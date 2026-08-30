import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Boxes,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
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
  cardinalityShortLabel,
  isOntologyDetailNodeKind,
  isOntologyJoinEdge,
  isOntologyMappingEdge,
  ontologyGraphForViewMode,
  ontologyGraphWithDetailVisibility,
  selectOntologyEdgeHandles,
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

const HIDDEN_HANDLE_STYLE = { visibility: "hidden" } as const;

function nodeShape(node: OntologyNode): string {
  if (node.kind === "metric") return "18px";
  if (node.kind === "validation_finding") return "3px";
  return "8px";
}

interface OntologyNodeData extends Record<string, unknown> {
  node: OntologyNode;
  highlighted: boolean;
  /** グラフ内検索の一致(接地ハイライトとは別チャネル: リングで表現)。 */
  searchMatched: boolean;
  dimmed: boolean;
  stats?: {
    columnCount: number;
    joinCount: number;
  };
}

/** 凡例と kind フィルタで使う表示グループ。 */
const LEGEND_GROUPS: Array<{
  id: string;
  labelKey: string;
  swatchKind: string;
  kinds: string[];
}> = [
  {
    id: "business",
    labelKey: "nl2sql.ontology.legend.business",
    swatchKind: "business_entity",
    kinds: ["business_entity", "business_event", "business_term", "business_rule"],
  },
  {
    id: "physical",
    labelKey: "nl2sql.ontology.legend.physical",
    swatchKind: "table",
    kinds: ["schema", "table", "view"],
  },
  {
    id: "attribute",
    labelKey: "nl2sql.ontology.legend.attribute",
    swatchKind: "property",
    kinds: ["property", "column", "enum_value"],
  },
  {
    id: "metric",
    labelKey: "nl2sql.ontology.legend.metric",
    swatchKind: "metric",
    kinds: ["metric"],
  },
];

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** アイコン + 業務名 + 技術名のカード。型=塗り/状態=枠のチャネル分離は graphPalette を踏襲。 */
function OntologyNodeCard({ data, selected }: NodeProps<Node<OntologyNodeData>>) {
  const { node, highlighted, searchMatched, dimmed, stats } = data;
  const Icon = KIND_ICONS[node.kind] ?? Circle;
  const display = ontologyNodeDisplay(node, { highlighted });
  const emphasizedBorder = selected || highlighted || node.validation_status === "blocked";
  const showStats =
    (node.kind === "table" || node.kind === "view") &&
    Boolean(stats && (stats.columnCount > 0 || stats.joinCount > 0));
  const hoverTitle = [
    display.primaryLabel,
    display.secondaryLabel,
    node.description_ja || node.description || "",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <div
      className="grid h-full grid-cols-[auto_minmax(0,1fr)] items-center gap-2 px-3 py-2"
      data-testid={`ontology-node-card-${node.id}`}
      data-ontology-node-kind={node.kind}
      title={hoverTitle}
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
        // 検索一致は接地ハイライト(枠)と区別できるリングで示す
        boxShadow: searchMatched
          ? `0 0 0 3px color-mix(in srgb, ${cssVar("--warning")} 65%, transparent)`
          : undefined,
      }}
    >
      {/* 4 方向 × source/target の隠しハンドル。エッジ側が相対位置で接続方向を選ぶ
          (固定 Left/Right だけだと直下ノードへのエッジが自己ループ状になるため)。 */}
      <Handle id="t-left" type="target" position={Position.Left} style={HIDDEN_HANDLE_STYLE} />
      <Handle id="t-top" type="target" position={Position.Top} style={HIDDEN_HANDLE_STYLE} />
      <Handle id="t-right" type="target" position={Position.Right} style={HIDDEN_HANDLE_STYLE} />
      <Handle id="t-bottom" type="target" position={Position.Bottom} style={HIDDEN_HANDLE_STYLE} />
      <Handle id="s-left" type="source" position={Position.Left} style={HIDDEN_HANDLE_STYLE} />
      <Handle id="s-top" type="source" position={Position.Top} style={HIDDEN_HANDLE_STYLE} />
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
      <Handle id="s-right" type="source" position={Position.Right} style={HIDDEN_HANDLE_STYLE} />
      <Handle id="s-bottom" type="source" position={Position.Bottom} style={HIDDEN_HANDLE_STYLE} />
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
        onClick={() => void flow.fitView({ padding: 0.18, duration: 0 })}
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

/**
 * 凡例: 表示中グラフに存在する種別グループのみを表示し、クリックで種別フィルタを
 * トグルする(Bloom 流)。凡例=フィルタなので role="group" + aria-pressed を付ける。
 */
function OntologyGraphLegend({
  presentGroupIds,
  disabledGroupIds,
  onToggleGroup,
}: {
  presentGroupIds: Set<string>;
  disabledGroupIds: Set<string>;
  onToggleGroup: (groupId: string) => void;
}) {
  const groups = LEGEND_GROUPS.filter((group) => presentGroupIds.has(group.id));
  if (groups.length === 0) return null;
  return (
    <div
      className="absolute bottom-3 left-3 z-10 max-w-[calc(100%-1.5rem)] rounded-md border border-border bg-card px-2 py-1.5 shadow-sm"
      role="group"
      aria-label={t("nl2sql.ontology.legendLabel")}
      data-testid="ontology-graph-legend"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] leading-4 text-muted">
        {groups.map((group) => {
          const enabled = !disabledGroupIds.has(group.id);
          return (
            <button
              key={group.id}
              type="button"
              aria-pressed={enabled}
              title={t("nl2sql.ontology.legendToggleHint")}
              className={cn(
                "inline-flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/40 motion-reduce:transition-none",
                enabled ? "hover:bg-background" : "opacity-40 line-through hover:opacity-60"
              )}
              onClick={() => onToggleGroup(group.id)}
              data-testid={`ontology-graph-legend-${group.id}`}
            >
              <LegendSwatch kind={group.swatchKind} />
              {t(group.labelKey)}
            </button>
          );
        })}
        <span className="inline-flex items-center gap-1 whitespace-normal px-1">
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
  const flow = useReactFlow();
  const [search, setSearch] = useState("");
  const [searchCursor, setSearchCursor] = useState(0);
  const [showDetails, setShowDetails] = useState(false);
  const [disabledLegendGroups, setDisabledLegendGroups] = useState<Set<string>>(new Set());
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
  const query = normalize(search.trim());

  // view-mode 適用後・詳細フィルタ前のグラフ。検索は非表示の詳細ノードも対象にする。
  const scopedGraph = useMemo<OntologyGraph>(
    () => ontologyGraphForViewMode(graph, currentViewMode, highlightNodeIds, highlightEdgeIds),
    [graph, currentViewMode, highlightNodeIds, highlightEdgeIds]
  );
  const searchMatchedNodeIds = useMemo(() => {
    if (!query) return [] as string[];
    return scopedGraph.nodes
      .filter((node) =>
        ontologyNodeSearchValues(node).some((name) => normalize(name).includes(query))
      )
      .map((node) => node.id);
  }, [query, scopedGraph]);

  const disabledKinds = useMemo(() => {
    const kinds = new Set<string>();
    for (const group of LEGEND_GROUPS) {
      if (!disabledLegendGroups.has(group.id)) continue;
      for (const kind of group.kinds) kinds.add(kind);
    }
    return kinds;
  }, [disabledLegendGroups]);

  const visibleGraph = useMemo<OntologyGraph>(() => {
    // 検索一致した詳細ノードは強制表示する(検索が「見えないものに当たって無反応」にならない)
    const forcedDetails = new Set<string>([
      ...(highlightNodeIds ?? []),
      ...searchMatchedNodeIds,
    ]);
    if (selectedNodeId) forcedDetails.add(selectedNodeId);
    const withDetails = ontologyGraphWithDetailVisibility(
      scopedGraph,
      showDetails || detailCount === 0,
      forcedDetails
    );
    if (disabledKinds.size === 0) return withDetails;
    const keep = new Set(
      withDetails.nodes.filter((node) => !disabledKinds.has(node.kind)).map((node) => node.id)
    );
    return {
      ...withDetails,
      nodes: withDetails.nodes.filter((node) => keep.has(node.id)),
      edges: withDetails.edges.filter(
        (edge) => keep.has(edge.source_node_id) && keep.has(edge.target_node_id)
      ),
    };
  }, [
    scopedGraph,
    highlightNodeIds,
    searchMatchedNodeIds,
    selectedNodeId,
    showDetails,
    detailCount,
    disabledKinds,
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

  // 強調は 2 チャネルを合成する: 接地ハイライト(枠・塗り)と検索一致(リング)。
  // 以前は接地表示中に検索が無反応だったため、両者を独立チャネルとして常に効かせる。
  const emphasis = useMemo(() => {
    const highlightSet = new Set(highlightNodeIds ?? []);
    const highlightEdgeSet = new Set(highlightEdgeIds ?? []);
    const visibleIds = new Set(visibleGraph.nodes.map((node) => node.id));
    const searchSet = new Set(searchMatchedNodeIds.filter((id) => visibleIds.has(id)));
    const searchEdgeSet = new Set(
      query
        ? visibleGraph.edges
            .filter(
              (edge) =>
                (searchSet.has(edge.source_node_id) && searchSet.has(edge.target_node_id)) ||
                normalize(edge.relationship_name_ja).includes(query)
            )
            .map((edge) => edge.id)
        : []
    );
    return {
      active: externalHighlight || Boolean(query),
      highlightNodes: highlightSet,
      highlightEdges: highlightEdgeSet,
      searchNodes: searchSet,
      searchEdges: searchEdgeSet,
    };
  }, [
    externalHighlight,
    highlightNodeIds,
    highlightEdgeIds,
    query,
    searchMatchedNodeIds,
    visibleGraph,
  ]);

  // 検索一致のジャンプナビゲーション(レイアウト位置順)
  const orderedSearchMatches = useMemo(() => {
    return searchMatchedNodeIds
      .filter((id) => semanticLayout.positions.has(id))
      .sort((a, b) => {
        const pa = semanticLayout.positions.get(a)!;
        const pb = semanticLayout.positions.get(b)!;
        return pa.y - pb.y || pa.x - pb.x;
      });
  }, [searchMatchedNodeIds, semanticLayout.positions]);
  useEffect(() => {
    setSearchCursor(0);
  }, [query]);
  const jumpToSearchMatch = (direction: 1 | -1) => {
    if (orderedSearchMatches.length === 0) return;
    const next =
      (searchCursor + direction + orderedSearchMatches.length) % orderedSearchMatches.length;
    setSearchCursor(next);
    const nodeId = orderedSearchMatches[next];
    const position = semanticLayout.positions.get(nodeId);
    if (!position) return;
    onSelectNode?.(nodeId);
    void flow.setCenter(position.x + NODE_WIDTH / 2, position.y + NODE_HEIGHT / 2, {
      zoom: Math.max(flow.getZoom(), 0.9),
      duration: prefersReducedMotion() ? 0 : 250,
    });
  };

  // 可視ノード集合・接地ハイライトが変わったら自動リフィットする
  // (React Flow の fitView prop は初期化時のみのため、モード切替や接地実行の
  //  結果が画面外に残る問題への対処)。
  const visibleSignature = useMemo(
    () => visibleGraph.nodes.map((node) => node.id).sort().join("|"),
    [visibleGraph.nodes]
  );
  const highlightSignature = useMemo(
    () => [...(highlightNodeIds ?? [])].sort().join("|"),
    [highlightNodeIds]
  );
  const layoutPositionsRef = useRef(semanticLayout.positions);
  layoutPositionsRef.current = semanticLayout.positions;
  const highlightNodeIdsRef = useRef(highlightNodeIds);
  highlightNodeIdsRef.current = highlightNodeIds;
  useEffect(() => {
    // React Flow が新しいノード集合を測り終えるのを待ってからフィットする
    const timer = window.setTimeout(() => {
      const highlightTargets = (highlightNodeIdsRef.current ?? []).filter((id) =>
        layoutPositionsRef.current.has(id)
      );
      void flow.fitView({
        padding: 0.18,
        duration: prefersReducedMotion() ? 0 : 300,
        ...(highlightTargets.length > 0
          ? { nodes: highlightTargets.map((id) => ({ id })) }
          : {}),
      });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [visibleSignature, highlightSignature, flow]);

  const presentLegendGroupIds = useMemo(() => {
    const present = new Set<string>();
    for (const node of scopedGraph.nodes) {
      for (const group of LEGEND_GROUPS) {
        if (group.kinds.includes(node.kind)) present.add(group.id);
      }
    }
    return present;
  }, [scopedGraph.nodes]);
  const toggleLegendGroup = (groupId: string) => {
    setDisabledLegendGroups((current) => {
      const next = new Set(current);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  };

  const nodes = useMemo<Node<OntologyNodeData>[]>(
    () =>
      visibleGraph.nodes.map((node) => {
        const highlighted = emphasis.highlightNodes.has(node.id);
        const searchMatched = emphasis.searchNodes.has(node.id);
        return {
          id: node.id,
          type: "ontology",
          position: semanticLayout.positions.get(node.id) ?? { x: 0, y: 0 },
          data: {
            node,
            highlighted,
            searchMatched,
            dimmed: emphasis.active && !highlighted && !searchMatched,
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
        const highlighted =
          emphasis.highlightEdges.has(edge.id) || emphasis.searchEdges.has(edge.id);
        const selected = edge.id === selectedEdgeId;
        const showFullLabel = highlighted || selected || edge.id === hoveredEdgeId;
        // ER 図流: join を持つ関係は hover を待たずカーディナリティを常時表示する
        const persistentLabel = isOntologyJoinEdge(edge)
          ? cardinalityShortLabel(edge.cardinality)
          : "";
        // 相対位置でハンドルを選ぶ(レーン間は上下・同一レーンは左右。自己ループ防止)
        const sourcePosition = semanticLayout.positions.get(edge.source_node_id);
        const targetPosition = semanticLayout.positions.get(edge.target_node_id);
        const handles =
          sourcePosition && targetPosition
            ? selectOntologyEdgeHandles(sourcePosition, targetPosition)
            : null;
        const mappingEdge = isOntologyMappingEdge(edge);
        return {
          id: edge.id,
          source: edge.source_node_id,
          target: edge.target_node_id,
          ...(handles
            ? {
                sourceHandle: handles.sourceHandle,
                targetHandle: handles.targetHandle,
                // レーン間(縦)は直角の smoothstep で ER 図らしく描く
                ...(handles.orientation === "vertical" ? { type: "smoothstep" as const } : {}),
              }
            : {}),
          label: showFullLabel ? edge.relationship_name_ja : persistentLabel || undefined,
          ariaLabel: `${edge.relationship_name_ja}${
            emphasis.highlightEdges.has(edge.id) ? t("nl2sql.ontology.edgeGroundedSuffix") : ""
          }`,
          markerEnd: { type: MarkerType.ArrowClosed, color: cssVar("--graph-line") },
          style: {
            stroke: highlighted || selected ? cssVar("--primary") : edgeStroke(edge),
            strokeWidth: highlighted || selected
              ? 2.5
              : edge.validation_status === "blocked"
                ? 2
                : mappingEdge
                  ? 1
                  : 1.25,
            // 「対応」(maps_to)は点線で FK join の実線と区別。proposed の点線は従来どおり優先
            strokeDasharray:
              edge.review_status === "proposed" ? "5 4" : mappingEdge ? "4 3" : undefined,
            opacity: emphasis.active && !highlighted ? 0.3 : 1,
          },
          labelStyle: { fill: cssVar("--muted"), fontSize: 11, fontWeight: 600 },
          labelBgStyle: { fill: cssVar("--card"), fillOpacity: 0.92 },
        };
      }),
    [visibleGraph.edges, emphasis, hoveredEdgeId, selectedEdgeId, semanticLayout.positions]
  );

  return (
    <div className="grid gap-2">
      {/* ツールバーはキャンバス外(上部)に置き、フィット時にノードと重ならないようにする */}
      <div className="flex flex-wrap items-center gap-2">
        <GraphModeControl mode={currentViewMode} onChange={changeViewMode} />
        <div className="flex min-w-0 items-center gap-1">
          <GraphToolbarSearchField value={search} onChange={setSearch} />
          {query ? (
            <div
              className="flex h-[44px] items-center gap-0.5 rounded-md border border-border bg-card px-1 shadow-sm sm:h-[40px]"
              data-testid="ontology-graph-search-nav"
            >
              <span className="px-1 text-xs tabular-nums text-muted" aria-live="polite">
                {orderedSearchMatches.length === 0
                  ? t("nl2sql.ontology.graphSearchNoMatch")
                  : `${searchCursor + 1} / ${orderedSearchMatches.length}`}
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={t("nl2sql.ontology.graphSearchPrev")}
                title={t("nl2sql.ontology.graphSearchPrev")}
                disabled={orderedSearchMatches.length === 0}
                onClick={() => jumpToSearchMatch(-1)}
                data-testid="ontology-graph-search-prev"
              >
                <ChevronLeft size={15} aria-hidden="true" />
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={t("nl2sql.ontology.graphSearchNext")}
                title={t("nl2sql.ontology.graphSearchNext")}
                disabled={orderedSearchMatches.length === 0}
                onClick={() => jumpToSearchMatch(1)}
                data-testid="ontology-graph-search-next"
              >
                <ChevronRight size={15} aria-hidden="true" />
              </Button>
            </div>
          ) : null}
        </div>
        {detailCount > 0 ? (
          <label
            className="flex h-[44px] cursor-pointer items-center gap-2 rounded-md border border-border bg-card px-3 text-xs text-foreground shadow-sm transition-colors focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/40 sm:h-[40px]"
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
      <div className="relative h-[32rem] min-h-80 overflow-hidden rounded-md border border-border bg-background">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.25}
        maxZoom={1.8}
        // レイアウトは決定論(semantic matrix)で管理し、onNodesChange を持たない
        // controlled flow のためドラッグ/内部選択は無効(選択は onNodeClick + selected prop で制御)
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
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
      <OntologyGraphLegend
        presentGroupIds={presentLegendGroupIds}
        disabledGroupIds={disabledLegendGroups}
        onToggleGroup={toggleLegendGroup}
      />
      </div>
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
