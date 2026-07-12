import {
  ArrowRight,
  ArrowUpDown,
  Maximize2,
  Network,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { lazy, Suspense, useId, useMemo, useState, type KeyboardEvent } from "react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
  StatusBadge,
  cn,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import { fitLayoutToBounds, layoutOntologyGraph } from "./graphLayout";
import { edgeStroke, nodeFill, nodeStroke } from "./graphPalette";
import {
  boundedOntologyGraph,
  ontologyRelationshipRows,
  sortOntologyRelationshipRows,
  type OntologyEdge,
  type OntologyGraph,
  type OntologyNode,
  type OntologyNodeKind,
  type OntologyRelationshipRow,
  type OntologyValidationStatus,
  type RelationshipSortKey,
  type SortDirection,
} from "./types";

const LazyOntologyGraphCanvas = lazy(() => import("./OntologyGraphCanvas"));

export interface OntologyWorkspaceLabels {
  title: string;
  description: string;
  graphTitle: string;
  relationListTitle: string;
  relationListDescription: string;
  empty: string;
  retry: string;
  nodeCount: (count: number) => string;
  edgeCount: (count: number) => string;
  hiddenSummary: (nodes: number, edges: number) => string;
  hiddenKinds: string;
  source: string;
  relationship: string;
  target: string;
  joinCondition: string;
  validation: string;
  zoomIn: string;
  zoomOut: string;
  fitView: string;
  graphAria: (nodes: number, edges: number) => string;
  selectNode: (label: string) => string;
  selectRelationship: (label: string) => string;
}

export const DEFAULT_ONTOLOGY_WORKSPACE_LABELS: OntologyWorkspaceLabels = {
  title: "業務モデル",
  description: "業務概念と、承認済みの関係・物理マッピングを確認します。",
  graphTitle: "関係グラフ",
  relationListTitle: "関連一覧",
  relationListDescription: "グラフと同じ内容を、並べ替え可能な一覧で確認できます。",
  empty: "表示できる Ontology ノードがありません。",
  retry: "再試行",
  nodeCount: (count) => `${count} ノード`,
  edgeCount: (count) => `${count} 関係`,
  hiddenSummary: (nodes, edges) =>
    `表示上限を超えたため、${nodes} ノードと ${edges} 関係を省略しています。業務領域を絞り込んでください。`,
  hiddenKinds: "省略されたノード種別",
  source: "起点",
  relationship: "関係",
  target: "終点",
  joinCondition: "Join 条件",
  validation: "検証状態",
  zoomIn: "グラフを拡大",
  zoomOut: "グラフを縮小",
  fitView: "グラフ全体を表示",
  graphAria: (nodes, edges) => `Ontology 関係グラフ。${nodes} ノード、${edges} 関係。`,
  selectNode: (label) => `${label} を選択`,
  selectRelationship: (label) => `${label} の関係を選択`,
};

export interface OntologyWorkspaceProps {
  graph: OntologyGraph;
  title?: string;
  description?: string;
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
  onSelectNode?: (node: OntologyNode) => void;
  onSelectEdge?: (edge: OntologyEdge) => void;
  maxVisibleNodes?: number;
  loading?: boolean;
  errorMessage?: string | null;
  onRetry?: () => void;
  labels?: Partial<OntologyWorkspaceLabels>;
  className?: string;
}

interface GraphPosition {
  x: number;
  y: number;
}

const GRAPH_WIDTH = 960;
const GRAPH_HEIGHT = 500;
const NODE_WIDTH = 148;
const NODE_HEIGHT = 68;

const NODE_KIND_LABELS: Record<OntologyNodeKind, string> = {
  schema: "Schema",
  table: "Table",
  view: "View",
  column: "Column",
  business_entity: "業務エンティティ",
  business_event: "業務イベント",
  property: "属性",
  metric: "指標",
  business_term: "業務用語",
  question_intent: "質問意図",
  query_plan: "検索計画",
  cte: "CTE",
  sql_table: "SQL Table",
  sql_column: "SQL Column",
  sql_join: "SQL Join",
  sql_filter: "SQL Filter",
  sql_aggregate: "SQL Aggregate",
  sql_group: "SQL Group",
  sql_having: "SQL Having",
  sql_order: "SQL Order",
  sql_limit: "SQL Limit",
  sql_window: "SQL Window",
  sql_artifact: "SQL",
  validation_finding: "検証結果",
  execution_preview: "実行プレビュー",
  unknown: "その他",
};

function labelsWithDefaults(labels?: Partial<OntologyWorkspaceLabels>): OntologyWorkspaceLabels {
  return { ...DEFAULT_ONTOLOGY_WORKSPACE_LABELS, ...labels };
}

function graphPositions(graph: OntologyGraph): Map<string, GraphPosition> {
  // force-directed の結果を viewBox に収める(React Flow 版と同じ配置ロジックを共有)。
  return fitLayoutToBounds(
    layoutOntologyGraph(graph),
    GRAPH_WIDTH,
    GRAPH_HEIGHT,
    32 + NODE_WIDTH / 2
  );
}

function shortLabel(value: string, maxLength = 18): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 1)}…`;
}

function validationVariant(status: OntologyValidationStatus): StatusVariant {
  if (status === "passed") return "success";
  if (status === "warning") return "warning";
  if (status === "blocked") return "danger";
  return "neutral";
}

function validationLabel(status: OntologyValidationStatus): string {
  if (status === "passed") return "確認済み";
  if (status === "warning") return "要注意";
  if (status === "blocked") return "ブロック";
  return "未確認";
}

function NodeShape({ node, selected }: { node: OntologyNode; selected: boolean }) {
  // 型=塗り / 状態(選択時は primary)=枠。色はトークン(var)なのでダークにも追従。
  const style = { fill: nodeFill(node), stroke: nodeStroke(node, selected) };
  const className = "stroke-2";
  if (node.kind === "business_entity" || node.kind === "business_event") {
    return <ellipse cx="0" cy="0" rx={NODE_WIDTH / 2} ry={NODE_HEIGHT / 2} className={className} style={style} />;
  }
  if (node.kind === "metric") {
    return (
      <polygon
        points={`0,${-NODE_HEIGHT / 2} ${NODE_WIDTH / 2},0 0,${NODE_HEIGHT / 2} ${-NODE_WIDTH / 2},0`}
        className={className}
        style={style}
      />
    );
  }
  if (node.kind === "validation_finding") {
    const halfWidth = NODE_WIDTH / 2;
    const halfHeight = NODE_HEIGHT / 2;
    return (
      <polygon
        points={`${-halfWidth + 12},${-halfHeight} ${halfWidth - 12},${-halfHeight} ${halfWidth},${-halfHeight + 12} ${halfWidth},${halfHeight - 12} ${halfWidth - 12},${halfHeight} ${-halfWidth + 12},${halfHeight} ${-halfWidth},${halfHeight - 12} ${-halfWidth},${-halfHeight + 12}`}
        className={className}
        style={style}
      />
    );
  }
  return (
    <rect
      x={-NODE_WIDTH / 2}
      y={-NODE_HEIGHT / 2}
      width={NODE_WIDTH}
      height={NODE_HEIGHT}
      rx="12"
      className={className}
      style={style}
    />
  );
}

function AccessibleSvgOntologyGraphCanvas({
  graph,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  labels,
}: {
  graph: OntologyGraph;
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
  onSelectNode?: (node: OntologyNode) => void;
  onSelectEdge?: (edge: OntologyEdge) => void;
  labels: OntologyWorkspaceLabels;
}) {
  const markerId = useId().replace(/:/g, "-");
  const [zoom, setZoom] = useState(1);
  const positions = useMemo(() => graphPositions(graph), [graph]);
  const viewWidth = GRAPH_WIDTH / zoom;
  const viewHeight = GRAPH_HEIGHT / zoom;
  const viewBox = `${(GRAPH_WIDTH - viewWidth) / 2} ${(GRAPH_HEIGHT - viewHeight) / 2} ${viewWidth} ${viewHeight}`;
  const showEdgeLabels = graph.edges.length <= 30;
  const activateNode = (event: KeyboardEvent<SVGGElement>, node: OntologyNode) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onSelectNode?.(node);
  };
  const activateEdge = (event: KeyboardEvent<SVGGElement>, edge: OntologyEdge) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onSelectEdge?.(edge);
  };

  return (
    <section className="hidden space-y-3 md:block" aria-labelledby={`${markerId}-graph-title`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id={`${markerId}-graph-title`} className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Network size={16} className="text-primary" aria-hidden="true" />
          {labels.graphTitle}
        </h3>
        <div className="flex items-center gap-2" role="group" aria-label={labels.graphTitle}>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="min-h-11 min-w-11 px-0 motion-reduce:transition-none"
            aria-label={labels.zoomOut}
            disabled={zoom <= 0.75}
            onClick={() => setZoom((current) => Math.max(0.75, current - 0.25))}
          >
            <ZoomOut size={17} aria-hidden="true" />
          </Button>
          <span className="min-w-12 text-center text-xs tabular-nums text-muted" aria-live="polite">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="min-h-11 min-w-11 px-0 motion-reduce:transition-none"
            aria-label={labels.zoomIn}
            disabled={zoom >= 1.5}
            onClick={() => setZoom((current) => Math.min(1.5, current + 0.25))}
          >
            <ZoomIn size={17} aria-hidden="true" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="min-h-11 min-w-11 px-0 motion-reduce:transition-none"
            aria-label={labels.fitView}
            onClick={() => setZoom(1)}
          >
            <Maximize2 size={17} aria-hidden="true" />
          </Button>
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border border-border bg-background">
        <svg
          viewBox={viewBox}
          className="block h-[31rem] w-full motion-reduce:transition-none"
          role="group"
          aria-label={labels.graphAria(graph.nodes.length, graph.edges.length)}
        >
          <defs>
            <marker
              id={`${markerId}-arrow`}
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="3"
              orient="auto"
              markerUnits="strokeWidth"
            >
              <path d="M0,0 L0,6 L8,3 z" className="fill-muted" />
            </marker>
          </defs>
          {graph.edges.map((edge) => {
            const source = positions.get(edge.source_node_id);
            const target = positions.get(edge.target_node_id);
            if (!source || !target) return null;
            const selected = edge.id === selectedEdgeId;
            const midpointX = (source.x + target.x) / 2;
            const midpointY = (source.y + target.y) / 2;
            return (
              <g
                key={edge.id}
                role={onSelectEdge ? "button" : undefined}
                tabIndex={onSelectEdge ? 0 : undefined}
                aria-label={labels.selectRelationship(edge.relationship_name_ja)}
                onClick={() => onSelectEdge?.(edge)}
                onKeyDown={(event) => activateEdge(event, edge)}
                className={cn(onSelectEdge && "cursor-pointer outline-none")}
              >
                <line
                  x1={source.x}
                  y1={source.y}
                  x2={target.x}
                  y2={target.y}
                  style={{ stroke: edgeStroke(edge) }}
                  className={cn(
                    "stroke-2",
                    edge.validation_status === "unreviewed" && "stroke-dasharray-[6_5]",
                    selected && "stroke-[4]"
                  )}
                  markerEnd={`url(#${markerId}-arrow)`}
                />
                {showEdgeLabels ? (
                  <g transform={`translate(${midpointX} ${midpointY})`}>
                    <rect x="-54" y="-13" width="108" height="26" rx="8" className="fill-card stroke-border" />
                    <text textAnchor="middle" dominantBaseline="middle" className="fill-muted text-[11px] font-medium">
                      {shortLabel(edge.relationship_name_ja, 14)}
                    </text>
                  </g>
                ) : null}
              </g>
            );
          })}
          {graph.nodes.map((node) => {
            const position = positions.get(node.id);
            if (!position) return null;
            const selected = node.id === selectedNodeId;
            return (
              <g
                key={node.id}
                transform={`translate(${position.x} ${position.y})`}
                role={onSelectNode ? "button" : undefined}
                tabIndex={onSelectNode ? 0 : undefined}
                aria-label={labels.selectNode(node.business_name_ja)}
                aria-pressed={onSelectNode ? selected : undefined}
                onClick={() => onSelectNode?.(node)}
                onKeyDown={(event) => activateNode(event, node)}
                className={cn(
                  onSelectNode && "cursor-pointer outline-none",
                  "focus-visible:[&>*:first-child]:stroke-primary focus-visible:[&>*:first-child]:stroke-[4]"
                )}
              >
                <title>{`${node.business_name_ja}（${NODE_KIND_LABELS[node.kind]}）`}</title>
                <NodeShape node={node} selected={selected} />
                <text textAnchor="middle" y="-5" className="pointer-events-none fill-foreground text-[12px] font-semibold">
                  {shortLabel(node.business_name_ja)}
                </text>
                <text textAnchor="middle" y="17" className="pointer-events-none fill-muted text-[10px]">
                  {NODE_KIND_LABELS[node.kind]}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="sr-only">
        <p>{labels.graphAria(graph.nodes.length, graph.edges.length)}</p>
        <ul>
          {graph.nodes.map((node) => (
            <li key={node.id}>{`${node.business_name_ja}、種別 ${NODE_KIND_LABELS[node.kind]}`}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function SortHeader({
  sortKey,
  activeKey,
  direction,
  children,
  onSort,
}: {
  sortKey: RelationshipSortKey;
  activeKey: RelationshipSortKey;
  direction: SortDirection;
  children: string;
  onSort: (key: RelationshipSortKey) => void;
}) {
  const active = sortKey === activeKey;
  return (
    <th
      scope="col"
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
      className="px-3 py-2 text-left text-xs font-semibold text-foreground"
    >
      <button
        type="button"
        className="inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-md px-1 text-left outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
        onClick={() => onSort(sortKey)}
      >
        {children}
        <ArrowUpDown size={13} aria-hidden="true" />
      </button>
    </th>
  );
}

function RelationshipStatus({ status }: { status: OntologyValidationStatus }) {
  return <StatusBadge variant={validationVariant(status)} label={validationLabel(status)} />;
}

function MobileRelationshipCard({
  row,
  selected,
  onSelect,
  labels,
}: {
  row: OntologyRelationshipRow;
  selected: boolean;
  onSelect?: (row: OntologyRelationshipRow) => void;
  labels: OntologyWorkspaceLabels;
}) {
  return (
    <button
      type="button"
      className={cn(
        "w-full cursor-pointer rounded-lg border bg-card p-4 text-left outline-none transition-colors motion-reduce:transition-none",
        selected ? "border-primary ring-2 ring-primary/20" : "border-border hover:border-border",
        "focus-visible:ring-2 focus-visible:ring-ring/40"
      )}
      onClick={() => onSelect?.(row)}
      disabled={!onSelect}
      aria-label={labels.selectRelationship(row.relationship_label)}
    >
      <span className="flex min-w-0 items-center gap-2 text-sm font-semibold text-foreground">
        <span className="min-w-0 flex-1 break-words">{row.source_label}</span>
        <ArrowRight size={16} className="shrink-0 text-muted" aria-hidden="true" />
        <span className="min-w-0 flex-1 break-words">{row.target_label}</span>
      </span>
      <span className="mt-3 flex flex-wrap items-center gap-2">
        <StatusBadge variant="info" label={row.relationship_label} />
        <RelationshipStatus status={row.validation_status} />
      </span>
      <span className="mt-3 block break-words font-mono text-xs leading-5 text-muted">
        {labels.joinCondition}: {row.join_condition}
      </span>
    </button>
  );
}

function RelationshipList({
  graph,
  selectedEdgeId,
  onSelectEdge,
  labels,
}: {
  graph: OntologyGraph;
  selectedEdgeId?: string | null;
  onSelectEdge?: (edge: OntologyEdge) => void;
  labels: OntologyWorkspaceLabels;
}) {
  const headingId = useId().replace(/:/g, "-");
  const [sortKey, setSortKey] = useState<RelationshipSortKey>("source");
  const [direction, setDirection] = useState<SortDirection>("asc");
  const rows = useMemo(
    () => sortOntologyRelationshipRows(ontologyRelationshipRows(graph), sortKey, direction),
    [direction, graph, sortKey]
  );
  const edgesById = useMemo(() => new Map(graph.edges.map((edge) => [edge.id, edge])), [graph.edges]);
  const selectRow = (row: OntologyRelationshipRow) => {
    const edge = edgesById.get(row.edge_id);
    if (edge) onSelectEdge?.(edge);
  };
  const changeSort = (key: RelationshipSortKey) => {
    if (key === sortKey) {
      setDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setDirection("asc");
  };

  return (
    <section className="space-y-3" aria-labelledby={`${headingId}-relation-list-title`}>
      <div>
        <h3 id={`${headingId}-relation-list-title`} className="text-sm font-semibold text-foreground">
          {labels.relationListTitle}
        </h3>
        <p className="mt-1 text-sm leading-6 text-muted">{labels.relationListDescription}</p>
      </div>
      {rows.length === 0 ? (
        <Banner severity="info">{labels.empty}</Banner>
      ) : (
        <>
          <div className="grid gap-3 md:hidden">
            {rows.map((row) => (
              <MobileRelationshipCard
                key={row.edge_id}
                row={row}
                selected={row.edge_id === selectedEdgeId}
                onSelect={onSelectEdge ? selectRow : undefined}
                labels={labels}
              />
            ))}
          </div>
          <div className="hidden overflow-hidden rounded-lg border border-border md:block">
            <table className="w-full table-fixed border-collapse text-sm">
              <thead className="bg-background">
                <tr>
                  <SortHeader sortKey="source" activeKey={sortKey} direction={direction} onSort={changeSort}>
                    {labels.source}
                  </SortHeader>
                  <SortHeader sortKey="relationship" activeKey={sortKey} direction={direction} onSort={changeSort}>
                    {labels.relationship}
                  </SortHeader>
                  <SortHeader sortKey="target" activeKey={sortKey} direction={direction} onSort={changeSort}>
                    {labels.target}
                  </SortHeader>
                  <th scope="col" className="px-3 py-2 text-left text-xs font-semibold text-foreground">
                    {labels.joinCondition}
                  </th>
                  <SortHeader sortKey="status" activeKey={sortKey} direction={direction} onSort={changeSort}>
                    {labels.validation}
                  </SortHeader>
                </tr>
              </thead>
              <tbody className="divide-y divide-border bg-card">
                {rows.map((row) => (
                  <tr
                    key={row.edge_id}
                    tabIndex={onSelectEdge ? 0 : undefined}
                    aria-selected={onSelectEdge ? row.edge_id === selectedEdgeId : undefined}
                    className={cn(
                      "outline-none transition-colors motion-reduce:transition-none",
                      row.edge_id === selectedEdgeId ? "bg-primary/10" : "hover:bg-background",
                      onSelectEdge && "cursor-pointer focus-visible:bg-primary/10 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
                    )}
                    onClick={() => selectRow(row)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      selectRow(row);
                    }}
                  >
                    <td className="break-words px-3 py-3 font-medium text-foreground">{row.source_label}</td>
                    <td className="break-words px-3 py-3 text-foreground">{row.relationship_label}</td>
                    <td className="break-words px-3 py-3 font-medium text-foreground">{row.target_label}</td>
                    <td className="break-words px-3 py-3 font-mono text-xs leading-5 text-muted">
                      {row.join_condition}
                    </td>
                    <td className="px-3 py-3">
                      <RelationshipStatus status={row.validation_status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export function OntologyWorkspace({
  graph,
  title,
  description,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
  maxVisibleNodes = 100,
  loading = false,
  errorMessage,
  onRetry,
  labels: labelOverrides,
  className,
}: OntologyWorkspaceProps) {
  const labels = labelsWithDefaults(labelOverrides);
  const visibleGraph = useMemo(
    () => boundedOntologyGraph(graph, maxVisibleNodes),
    [graph, maxVisibleNodes]
  );
  const hiddenKindSummary = Object.entries(visibleGraph.hidden_node_kinds)
    .map(([kind, count]) => `${NODE_KIND_LABELS[kind as OntologyNodeKind]} ${count}`)
    .join("、");

  return (
    <Card className={className}>
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>{title ?? labels.title}</CardTitle>
            <CardDescription className="mt-1 leading-6">
              {description ?? labels.description}
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2" aria-label="Ontology 件数">
            <StatusBadge variant="info" label={labels.nodeCount(visibleGraph.total_node_count)} />
            <StatusBadge variant="neutral" label={labels.edgeCount(visibleGraph.total_edge_count)} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {errorMessage ? (
          <Banner
            severity="danger"
            action={
              onRetry ? (
                <Button type="button" variant="secondary" size="sm" className="min-h-11" onClick={onRetry}>
                  {labels.retry}
                </Button>
              ) : undefined
            }
          >
            {errorMessage}
          </Banner>
        ) : null}
        {visibleGraph.hidden_node_count > 0 ? (
          <Banner severity="warning" title={labels.hiddenSummary(visibleGraph.hidden_node_count, visibleGraph.hidden_edge_count)}>
            {hiddenKindSummary ? `${labels.hiddenKinds}: ${hiddenKindSummary}` : null}
          </Banner>
        ) : null}
        {loading ? (
          <div className="space-y-3" role="status" aria-label="Ontology を読み込み中">
            <Skeleton className="h-11 w-56" />
            <Skeleton className="h-72 w-full" />
            <Skeleton className="h-36 w-full" />
          </div>
        ) : visibleGraph.nodes.length === 0 ? (
          <Banner severity="info">{labels.empty}</Banner>
        ) : (
          <>
            <div className="md:order-2">
              <RelationshipList
                graph={visibleGraph}
                selectedEdgeId={selectedEdgeId}
                onSelectEdge={onSelectEdge}
                labels={labels}
              />
            </div>
            <Suspense
              fallback={
                <AccessibleSvgOntologyGraphCanvas
                  graph={visibleGraph}
                  selectedNodeId={selectedNodeId}
                  selectedEdgeId={selectedEdgeId}
                  onSelectNode={onSelectNode}
                  onSelectEdge={onSelectEdge}
                  labels={labels}
                />
              }
            >
              <section className="hidden space-y-3 md:block" aria-label={labels.graphTitle}>
                <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Network size={16} className="text-primary" aria-hidden="true" />
                  {labels.graphTitle}
                </h3>
                <LazyOntologyGraphCanvas
                  graph={visibleGraph}
                  selectedNodeId={selectedNodeId}
                  onSelectNode={(nodeId) => {
                    const node = visibleGraph.nodes.find((item) => item.id === nodeId);
                    if (node) onSelectNode?.(node);
                  }}
                />
              </section>
            </Suspense>
          </>
        )}
      </CardContent>
    </Card>
  );
}
