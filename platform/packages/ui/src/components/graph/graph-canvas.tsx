import { useId, type ComponentProps, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Maximize2, Minus, Plus, RotateCcw, type LucideIcon } from "lucide-react";
import {
  Background,
  BaseEdge,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type MiniMapProps,
  type Node,
  type ReactFlowProps,
} from "@xyflow/react";

import { cn } from "../../lib/utils";
import { isImeComposing } from "../../lib/keyboard";
import { graphParallelEdgeGeometry } from "../../lib/graph-edges";
import { GRAPH_CANVAS_JA } from "../../lib/graph-labels";
import { Button } from "../ui/button";
import { SearchField } from "../ui/search-field";
import { StatusBadge } from "../data/status-badge";

export { ReactFlowProvider as GraphCanvasProvider };

/**
 * 小地図の大きさ（10rem × 6.5rem。ルート 14px）。React Flow は `style` の幅・高さで viewBox を割り算するので、
 * 数値で渡す（"10rem" のような文字列では viewBox が NaN になり、ノードの無い四角になる。#4078）。
 */
const GRAPH_MINIMAP_SIZE = { width: 140, height: 91 } as const;

export function GraphCanvasFrame({
  compact = false,
  className,
  ...props
}: ComponentProps<"div"> & { compact?: boolean }) {
  // 画布の中の指の操作は図の移動になるので、狭い画面では画布の外を指で送れる高さに抑える。
  return <div {...props} className={cn("relative min-h-80 overflow-hidden rounded-md border border-border bg-surface-sunken", compact ? "h-[26rem]" : "h-[56rem] max-sm:h-[65dvh]", className)} />;
}

export function GraphCanvas<NodeType extends Node = Node, EdgeType extends Edge = Edge>({
  children,
  miniMap,
  proOptions,
  ariaLabelConfig,
  onSelectNode,
  onSelectEdge,
  ...props
}: ReactFlowProps<NodeType, EdgeType> & {
  miniMap?: MiniMapProps<NodeType> | false;
  onSelectNode?: (id: string) => void;
  onSelectEdge?: (id: string) => void;
}) {
  return (
    <ReactFlow<NodeType, EdgeType>
      fitView
      fitViewOptions={{ padding: 0.18 }}
      minZoom={0.05}
      maxZoom={1.8}
      nodeDragThreshold={4}
      nodesConnectable={false}
      nodesFocusable
      edgesFocusable
      {...props}
      proOptions={{ ...proOptions, hideAttribution: true }}
      ariaLabelConfig={{ ...GRAPH_CANVAS_JA, ...ariaLabelConfig }}
      onNodeClick={(event, node) => { onSelectNode?.(node.id); props.onNodeClick?.(event, node); }}
      onEdgeClick={(event, edge) => { onSelectEdge?.(edge.id); props.onEdgeClick?.(event, edge); }}
      onKeyDownCapture={(event) => {
        props.onKeyDownCapture?.(event);
        if (event.defaultPrevented || isImeComposing(event) || (event.key !== "Enter" && event.key !== " ")) return;
        const target = event.target;
        if (!(target instanceof Element) || target.closest("input, button, textarea, select, a")) return;
        const item = target.closest(".react-flow__node, .react-flow__edge");
        const id = item?.getAttribute("data-id");
        const select = item?.classList.contains("react-flow__node") ? onSelectNode : onSelectEdge;
        if (id && select) { event.preventDefault(); event.stopPropagation(); select(id); }
      }}
    >
      <Background gap={20} size={1} />
      {miniMap ? <MiniMap<NodeType>
        pannable zoomable position="bottom-right"
        bgColor="var(--color-surface)"
        maskColor="color-mix(in srgb, var(--color-border) 45%, transparent)"
        nodeStrokeColor="var(--color-border-control)"
        {...miniMap}
        className={cn("hidden sm:block", miniMap.className)}
        style={{ ...miniMap.style, ...GRAPH_MINIMAP_SIZE }}
        ariaLabel={miniMap.ariaLabel ?? miniMap["aria-label"] ?? GRAPH_CANVAS_JA["minimap.ariaLabel"]}
      /> : null}
      {children}
    </ReactFlow>
  );
}

export interface GraphControlsLabels {
  zoomIn: string;
  zoomOut: string;
  fit: string;
  reset: string;
}

export function GraphCanvasControls({
  labels,
  onFit,
  onReset,
  resetDisabled = false,
  resetTestId,
}: {
  labels: GraphControlsLabels;
  onFit?: () => void;
  onReset?: () => void;
  resetDisabled?: boolean;
  resetTestId?: string;
}) {
  const flow = useReactFlow();
  return (
    <div className="absolute right-3 top-3 z-10 flex gap-1 rounded-md border border-border bg-surface p-1 shadow-sm">
      <Button type="button" size="sm" variant="ghost" icon={Plus} aria-label={labels.zoomIn} tooltip={labels.zoomIn} onClick={() => void flow.zoomIn({ duration: 0 })} />
      <Button type="button" size="sm" variant="ghost" icon={Minus} aria-label={labels.zoomOut} tooltip={labels.zoomOut} onClick={() => void flow.zoomOut({ duration: 0 })} />
      <Button type="button" size="sm" variant="ghost" icon={Maximize2} aria-label={labels.fit} tooltip={labels.fit} onClick={onFit ?? (() => void flow.fitView({ padding: 0.18, duration: 0 }))} />
      {onReset ? <Button type="button" size="sm" variant="ghost" icon={RotateCcw} aria-label={labels.reset} tooltip={labels.reset} onClick={onReset} disabled={resetDisabled} data-testid={resetTestId} /> : null}
    </div>
  );
}

export interface GraphSearchLabels {
  search: string;
  clear: string;
  previous: string;
  next: string;
  noMatch: string;
  scope: string;
}

export function GraphSearchNavigation({
  labels,
  value,
  onChange,
  count,
  cursor,
  onPrevious,
  onNext,
  scopeHintHidden = false,
  debounceMs = 0,
  testId,
}: {
  labels: GraphSearchLabels;
  value: string;
  onChange: (value: string) => void;
  count: number;
  cursor: number;
  onPrevious: () => void;
  onNext: () => void;
  scopeHintHidden?: boolean;
  /** サーバーで検索するときの待ち（読み込み済みの範囲の検索は 0）。 */
  debounceMs?: number;
  testId?: string;
}) {
  const searchId = useId();
  const scopeId = `${searchId}-scope`;
  const result = count ? `${Math.min(cursor + 1, count)} / ${count}` : labels.noMatch;
  return (
    <div className="grid w-full min-w-0 max-w-full gap-1 sm:w-auto">
      <div className="flex min-w-0 max-w-full flex-wrap items-center gap-1">
        <div className="w-full min-w-0 max-w-full sm:w-auto" data-testid={testId ? `${testId}-field` : undefined}>
          <SearchField id={searchId} label={labels.search} labelHidden size="lg" width="md" value={value} onSearch={onChange} debounceMs={debounceMs}
            aria-label={labels.search} placeholder={labels.search} clearLabel={labels.clear}
            aria-describedby={scopeId} data-testid={testId} />
        </div>
        {value.trim() ? <div className="flex h-[var(--control-height-lg)] items-center gap-0.5 rounded-md border border-border bg-surface px-1 shadow-sm" data-testid={testId ? `${testId}-nav` : undefined}>
          <span className="px-1 text-xs tabular-nums text-fg-muted" aria-live="polite">{result}</span>
          <Button type="button" size="sm" variant="ghost" icon={ChevronLeft} aria-label={labels.previous} tooltip={labels.previous} disabled={!count} onClick={onPrevious} data-testid={testId ? `${testId}-prev` : undefined} />
          <Button type="button" size="sm" variant="ghost" icon={ChevronRight} aria-label={labels.next} tooltip={labels.next} disabled={!count} onClick={onNext} data-testid={testId ? `${testId}-next` : undefined} />
        </div> : null}
      </div>
      <p id={scopeId} className={scopeHintHidden ? "sr-only" : "text-xs text-fg-muted"}>{labels.scope}</p>
    </div>
  );
}

/** 種別ごとの内容は製品が渡し、ノードの面・選択表示・字組みを共有する。 */
export const GRAPH_NODE_CARD_SIZE = { width: 220, height: 76 } as const;

export function GraphNodeFrame({ selected, matched, children, className, ...props }: ComponentProps<"div"> & { selected?: boolean; matched?: boolean; children: ReactNode }) {
  return <div {...props} className={cn("min-h-[5.5rem] w-64 rounded-lg border bg-surface px-3 py-2 text-sm leading-normal text-fg shadow-sm", selected ? "border-accent-fg" : "border-border-control", matched && "outline-2 outline-accent-fg", className)}>{children}</div>;
}

const HIDDEN_HANDLE_STYLE = { visibility: "hidden" } as const;

/** 4 方向 × source/target の隠しハンドル。辺の側が `selectGraphEdgeHandles` で接続方向を選ぶ。 */
export function GraphNodeHandles() {
  return <>
    <Handle id="t-left" type="target" position={Position.Left} style={HIDDEN_HANDLE_STYLE} />
    <Handle id="t-top" type="target" position={Position.Top} style={HIDDEN_HANDLE_STYLE} />
    <Handle id="t-right" type="target" position={Position.Right} style={HIDDEN_HANDLE_STYLE} />
    <Handle id="t-bottom" type="target" position={Position.Bottom} style={HIDDEN_HANDLE_STYLE} />
    <Handle id="s-left" type="source" position={Position.Left} style={HIDDEN_HANDLE_STYLE} />
    <Handle id="s-top" type="source" position={Position.Top} style={HIDDEN_HANDLE_STYLE} />
    <Handle id="s-right" type="source" position={Position.Right} style={HIDDEN_HANDLE_STYLE} />
    <Handle id="s-bottom" type="source" position={Position.Bottom} style={HIDDEN_HANDLE_STYLE} />
  </>;
}

/**
 * アイコン・種類・主な表示名・補助の表示名・統計のカード。
 * 種類 = 塗り、状態 = 枠のチャネルを分け、色は製品が `--color-graph-*` などのトークンで渡す。
 */
export function GraphNodeCard({
  icon: Icon, kindLabel, primary, secondary, stats = [], fill = "var(--color-graph-default)", stroke = "var(--color-graph-line)",
  emphasized = false, radius = "8px", selected, matched = false, dimmed = false, kindLabelTestId, className, style, ...props
}: ComponentProps<"div"> & {
  icon: LucideIcon;
  kindLabel: string;
  primary: string;
  secondary?: string | null;
  stats?: string[];
  fill?: string;
  stroke?: string;
  emphasized?: boolean;
  radius?: string;
  selected?: boolean;
  matched?: boolean;
  dimmed?: boolean;
  kindLabelTestId?: string;
}) {
  return (
    <GraphNodeFrame
      {...props}
      selected={selected}
      className={cn("grid h-full grid-cols-[auto_minmax(0,1fr)] items-center gap-2", className)}
      style={{
        width: GRAPH_NODE_CARD_SIZE.width,
        minHeight: GRAPH_NODE_CARD_SIZE.height,
        border: `${emphasized || selected ? 2 : 1}px solid ${selected ? "var(--color-accent-fg)" : stroke}`,
        borderRadius: radius,
        background: fill,
        color: "var(--color-graph-fg)",
        opacity: dimmed ? 0.35 : 1,
        // 検索一致は枠と区別できるリングで示す
        boxShadow: matched
          ? // グラフ座標系のリング幅（ズームに追従する幾何値）なので px で指定する。
            // oxlint-disable-next-line design-system/restricted-syntax
            "0 0 0 3px color-mix(in srgb, var(--color-warning-fg) 65%, transparent)"
          : undefined,
        ...style,
      }}
    >
      <GraphNodeHandles />
      {/* グラフの節点の印の箱（名前と種類の 2 行のまとまりの中央。文字の 1 行目にそろえる規則の例外。#2371）。 */}
      <span
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
        style={{ background: "color-mix(in srgb, currentColor 12%, transparent)" }}
        data-icon-align-skip=""
      >
        <Icon size={16} aria-hidden="true" />
      </span>
      <span className="grid min-w-0 gap-0.5">
        <span className="min-w-0 justify-self-start" data-testid={kindLabelTestId}>
          <StatusBadge variant="info" icon={false} label={kindLabel} className="max-w-full whitespace-normal text-left" />
        </span>
        <span className="block truncate text-sm font-semibold leading-5">{primary}</span>
        {secondary ? <span className="block truncate text-xs leading-4 opacity-70" title={secondary}>{secondary}</span> : null}
        {stats.length ? (
          <span className="mt-0.5 flex min-w-0 flex-wrap gap-1 text-xs leading-3">
            {stats.map((stat) => <span key={stat} className="rounded border border-current/15 px-1 py-0.5 opacity-75">{stat}</span>)}
          </span>
        ) : null}
      </span>
    </GraphNodeFrame>
  );
}

/** 辺の矢印。 */
export const GRAPH_EDGE_MARKER = { type: MarkerType.ArrowClosed, color: "var(--color-graph-line)" } as const;

/**
 * 同一ノードペア間に複数ある横向きの辺。既定 bezier は同じ y だと直線に潰れて重なるため、
 * `data.parallelOffset` に応じた弧で分離する。
 */
export function GraphParallelEdge({
  id, sourceX, sourceY, targetX, targetY, markerEnd, style, label, labelStyle, labelShowBg, labelBgStyle, labelBgPadding, labelBgBorderRadius, interactionWidth, data,
}: EdgeProps) {
  const offset = typeof data?.parallelOffset === "number" ? data.parallelOffset : 0;
  const { path, labelX, labelY } = graphParallelEdgeGeometry({ x: sourceX, y: sourceY }, { x: targetX, y: targetY }, offset);
  return (
    <BaseEdge
      id={id} path={path} markerEnd={markerEnd} style={style} label={label} labelX={labelX} labelY={labelY} labelStyle={labelStyle}
      labelShowBg={labelShowBg} labelBgStyle={labelBgStyle} labelBgPadding={labelBgPadding} labelBgBorderRadius={labelBgBorderRadius} interactionWidth={interactionWidth}
    />
  );
}

/** 凡例の種類の色の印。 */
export function GraphLegendSwatch({ fill }: { fill: string }) {
  return <span className="h-2.5 w-2.5 shrink-0 rounded-sm border border-current/20" style={{ background: fill }} aria-hidden="true" />;
}

export function GraphLegend({
  label, items, disabledIds, onToggle, children, testId,
}: {
  label: string;
  items: { id: string; label: string; marker?: ReactNode; hint?: string }[];
  disabledIds: ReadonlySet<string>;
  onToggle: (id: string) => void;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="absolute bottom-3 left-3 z-10 max-w-[calc(100%-1.5rem)] rounded-md border border-border bg-surface px-2 py-1.5 shadow-sm" role="group" aria-label={label} data-testid={testId}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-4 text-fg-muted">
        {items.map((item) => <Button type="button" size="sm" variant="ghost" key={item.id}
          aria-pressed={!disabledIds.has(item.id)} tooltip={item.hint} onClick={() => onToggle(item.id)}
          className={cn("text-xs", disabledIds.has(item.id) && "opacity-40 line-through")}
          data-testid={testId ? `${testId}-${item.id}` : undefined}>
          {item.marker}{item.label}
        </Button>)}
        {children}
      </div>
    </div>
  );
}

export function GraphSelectionDetails({ title, closeLabel, onClose, fields, children }: {
  title: string;
  closeLabel: string;
  onClose: () => void;
  fields: { label: string; value: ReactNode; technical?: boolean }[];
  children?: ReactNode;
}) {
  const titleId = useId();
  return (
    <section className="space-y-3 rounded-md border border-border bg-surface p-3" aria-labelledby={titleId}>
      <div className="flex items-center justify-between gap-2">
        <h3 id={titleId} className="text-sm font-semibold text-fg">{title}</h3>
        <Button size="sm" variant="ghost" type="button" onClick={onClose}>{closeLabel}</Button>
      </div>
      <dl className="grid gap-2">
        {fields.map((field, index) => <div key={index} className="grid gap-1 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)] sm:gap-3">
          <dt className="text-sm text-fg-muted">{field.label}</dt>
          <dd className={cn("min-w-0 break-words text-sm text-fg", field.technical && "font-mono")}>{field.value}</dd>
        </div>)}
      </dl>
      {children}
    </section>
  );
}
