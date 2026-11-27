import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import cytoscape, { type Core, type ElementDefinition, type LayoutOptions, type NodeSingular, type StylesheetJson } from "cytoscape";
import fcose from "cytoscape-fcose";
import { ChevronRight, Focus, Maximize2, Minus, Plus, RotateCcw, Undo2 } from "lucide-react";

import { cn } from "../../lib/utils";
import { isImeComposing } from "../../lib/keyboard";
import { firstGraphNode, nearestGraphNodeInDirection, seedGraphPositions, wrapGraphLabel, type GraphDirection } from "../../lib/graph-explorer-model";
import { GRAPH_OVERVIEW_MIN_ZOOM, graphReadableViewport, type GraphViewport } from "../../lib/graph-viewport";
import { Button } from "../ui/button";
import { GraphLegend, GraphLegendSwatch } from "./graph-legend";

let registered = false;
function ensureLayout() {
  if (registered) return;
  cytoscape.use(fcose);
  registered = true;
}

/** ノードの種類。塗り・枠は色のトークンの名前（`--color-graph-entity` など）、大きさは図の座標の直径。 */
export interface GraphExplorerNodeKind {
  id: string;
  label: string;
  color: string;
  lineColor: string;
  size: number;
  shape?: "ellipse" | "round-rectangle" | "round-diamond";
  /** 枠を点線にする（「さらに展開」のように実体でない点）。 */
  dashed?: boolean;
  /** 凡例に出さない（「さらに展開」の点など）。 */
  legendHidden?: boolean;
}

/** 関係の種類。`dashed` は点線、`labelled` は線の上に種類の文字を出す（数の多い「構成」は出さない）。 */
export interface GraphExplorerEdgeKind {
  id: string;
  label: string;
  dashed?: boolean;
  labelled?: boolean;
}

export interface GraphExplorerNode {
  id: string;
  kind: string;
  label: string;
  /** hover と読み上げの完全な名前（省略時は label）。 */
  title?: string;
  /** 展開できる隣接がある。 */
  expandable?: boolean;
  /** このノードから展開した隣接が図にある（折りたたむ対象）。 */
  expanded?: boolean;
  /** 検索の一致（外側の輪で示す。色だけにしない）。 */
  matched?: boolean;
}

export interface GraphExplorerEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  /** 読み上げの文（起点・終点・種類）。 */
  title?: string;
  /** 太い線（文書をまたぐ関連など）。 */
  strong?: boolean;
}

export interface GraphExplorerOption {
  id: string;
  label: string;
}

export interface GraphExplorerLabels {
  canvas: string;
  keyboardHint: string;
  nodeKinds: string;
  edgeKinds: string;
  selectKindHint: (kind: string) => string;
  zoomIn: string;
  zoomOut: string;
  fit: string;
  focus: string;
  previous: string;
  relayout: string;
  menu: string;
  expand: string;
  expandByKind: string;
  collapse: string;
  details: string;
  remove: string;
  removeSelected: (count: number) => string;
  keepSelected: string;
  connectSelected: string;
  center: string;
  /** 選択の読み上げ（ノードの種類・名前・状態）。 */
  announce: (text: string) => string;
  expandableState: string;
  expandedState: string;
  selectedCount: (count: number) => string;
}

export interface GraphExplorerHandle {
  /** ノードを選び、画面に入っていなければ中心へ動かす（縮尺は読める大きさ以上）。 */
  focusNode: (id: string) => void;
  fit: () => void;
}

export interface GraphExplorerProps {
  nodes: readonly GraphExplorerNode[];
  edges: readonly GraphExplorerEdge[];
  nodeKinds: readonly GraphExplorerNodeKind[];
  edgeKinds: readonly GraphExplorerEdgeKind[];
  labels: GraphExplorerLabels;
  selectedIds?: readonly string[];
  onSelectionChange?: (ids: string[]) => void;
  /** 隣接を展開する（ダブルクリック・Enter・メニュー）。`kinds` は「関係の種類を選んで展開」で選んだ種類。 */
  onExpand?: (id: string, kinds?: readonly string[]) => void;
  onCollapse?: (id: string) => void;
  /** 詳細を開く（狭い画面の詳細のシート）。 */
  onOpen?: (id: string) => void;
  /** 「このノードを外す」。 */
  onRemove?: (ids: readonly string[]) => void;
  /** 「選択したノードだけ残す」。 */
  onKeepOnly?: (ids: readonly string[]) => void;
  /** 「選択したノードの間の関係を表示」。 */
  onConnect?: (ids: readonly string[]) => void;
  /** 「関係の種類を選んで展開」の選択肢。 */
  expandOptions?: (id: string) => readonly GraphExplorerOption[];
  /** 画布の外の上の行（図内の検索・画面をクリアなど）。凡例と関係の種類の絞り込みはこの行の下。 */
  toolbar?: ReactNode;
  /** 変わると、置いた位置を捨てて最初から配置する（検索の結果で図を置き換えたときなど）。 */
  layoutKey?: string;
  compact?: boolean;
  className?: string;
  testId?: string;
}

interface ResolvedColors {
  fills: Map<string, string>;
  lines: Map<string, string>;
  line: string;
  fg: string;
  fgMuted: string;
  surface: string;
  accent: string;
  match: string;
  font: string;
}

/** 色のトークンを今のテーマの実際の色へ解く（`light-dark()` は計算した値でないと解けない）。 */
function resolveColors(host: HTMLElement, kinds: readonly GraphExplorerNodeKind[]): ResolvedColors {
  const probe = document.createElement("span");
  probe.style.display = "none";
  host.appendChild(probe);
  const read = (token: string) => {
    probe.style.color = `var(${token})`;
    return getComputedStyle(probe).color;
  };
  const colors: ResolvedColors = {
    fills: new Map(kinds.map((kind) => [kind.id, read(kind.color)])),
    lines: new Map(kinds.map((kind) => [kind.id, read(kind.lineColor)])),
    line: read("--color-graph-line"),
    fg: read("--color-graph-fg"),
    fgMuted: read("--color-fg-muted"),
    surface: read("--color-surface-sunken"),
    accent: read("--color-accent-fg"),
    match: read("--color-warning-fg"),
    font: getComputedStyle(host).fontFamily,
  };
  probe.remove();
  return colors;
}

/**
 * 画布の見た目（C 案）。中くらいの円、名前は円の下に 2 行まで（座標の 13px。読める縮尺の下限 0.8 で 10px 前後）。
 * 種類 = 塗りと大きさ、選択 = 太い枠、検索の一致 = 外側の輪。関係は矢印つきで、`dashed` の種類は点線、
 * `labelled` の種類だけ線の上に種類の文字を出す。
 */
export function graphExplorerStyle(colors: ResolvedColors, nodeKinds: readonly GraphExplorerNodeKind[], edgeKinds: readonly GraphExplorerEdgeKind[]): StylesheetJson {
  return [
    {
      selector: "node",
      style: {
        width: 32,
        height: 32,
        "background-color": colors.line,
        "border-width": 2,
        "border-color": colors.line,
        label: "data(display)",
        "font-size": 13,
        "font-weight": 500,
        "font-family": colors.font,
        "line-height": 1.2,
        color: colors.fg,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 5,
        "text-wrap": "wrap",
        "text-max-width": "400px",
        "text-background-color": colors.surface,
        "text-background-opacity": 0.85,
        "text-background-padding": "1px",
        "min-zoomed-font-size": 7,
        "overlay-opacity": 0,
      },
    },
    ...nodeKinds.map((kind) => ({
      selector: `node[kind = ${JSON.stringify(kind.id)}]`,
      style: {
        width: kind.size,
        height: kind.size,
        "background-color": colors.fills.get(kind.id) ?? colors.line,
        "border-color": colors.lines.get(kind.id) ?? colors.line,
        shape: kind.shape ?? "ellipse",
        ...(kind.dashed ? { "border-style": "dashed" as const } : {}),
      },
    })),
    { selector: "node.matched", style: { "underlay-color": colors.match, "underlay-opacity": 0.4, "underlay-padding": 6, "underlay-shape": "ellipse" } },
    { selector: "node:selected", style: { "border-color": colors.accent, "border-width": 4, "border-style": "solid" } },
    {
      selector: "edge",
      style: {
        width: 1.5,
        "line-color": colors.line,
        "target-arrow-color": colors.line,
        "target-arrow-shape": "triangle",
        "arrow-scale": 1.1,
        "curve-style": "bezier",
        label: "data(text)",
        "font-size": 11,
        "font-family": colors.font,
        color: colors.fgMuted,
        "text-rotation": "autorotate",
        "text-background-color": colors.surface,
        "text-background-opacity": 1,
        "text-background-padding": "2px",
        "min-zoomed-font-size": 8,
        "overlay-opacity": 0,
      },
    },
    ...edgeKinds.filter((kind) => kind.dashed).map((kind) => ({
      selector: `edge[kind = ${JSON.stringify(kind.id)}]`,
      style: { "line-style": "dashed" as const },
    })),
    { selector: "edge[?strong]", style: { width: 2.5 } },
    { selector: "edge:selected", style: { "line-color": colors.accent, "target-arrow-color": colors.accent, width: 3.5, color: colors.fg } },
    { selector: ".faded", style: { opacity: 0.2 } },
    { selector: ".hidden", style: { display: "none" } },
  ] as StylesheetJson;
}

/** fcose の配置（名前の大きさを含めて重ならない間隔。C 案の検証の値）。 */
const LAYOUT_BASE = {
  name: "fcose",
  animate: false,
  quality: "proof",
  nodeDimensionsIncludeLabels: true,
  idealEdgeLength: 100,
  nodeRepulsion: 11000,
  nodeSeparation: 85,
  packComponents: true,
  padding: 24,
  fit: false,
} as const;

interface MenuState {
  id: string;
  x: number;
  y: number;
  submenu?: boolean;
}

/**
 * Cytoscape.js（fcose）の探索の画布（#5922）。操作は Neo4j Browser / Bloom にそろえる:
 * 力の配置と、展開しても既存のノードを動かさない差分の配置、クリックで選択（詳細は製品が右に出す）、
 * ダブルクリックと Enter で隣接を展開、種類ごとの塗りと件数つきの凡例（画布の外の上。押すとその種類を選ぶ）、
 * 関係の種類の絞り込み（画布の外の上）、ドラッグ（辺が追従）、Shift＋ドラッグの範囲選択と Shift / Ctrl の
 * クリックの複数選択、右クリック（と ContextMenu・Shift＋F10）のメニュー、全体表示（読める縮尺が下限）。
 * canvas の描画は読み上げに出ないので、製品は同じ内容の一覧の表示を別に持つ。
 */
export const GraphExplorer = forwardRef<GraphExplorerHandle, GraphExplorerProps>(function GraphExplorer({
  nodes,
  edges,
  nodeKinds,
  edgeKinds,
  labels,
  selectedIds,
  onSelectionChange,
  onExpand,
  onCollapse,
  onOpen,
  onRemove,
  onKeepOnly,
  onConnect,
  expandOptions,
  toolbar,
  layoutKey = "",
  compact = false,
  className,
  testId,
}, ref) {
  const hintId = useId();
  const host = useRef<HTMLDivElement>(null);
  const focusTarget = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const laidOut = useRef<string | null>(null);
  const syncingSelection = useRef(false);
  const [hiddenEdgeKinds, setHiddenEdgeKinds] = useState<ReadonlySet<string>>(() => new Set());
  const [history, setHistory] = useState<GraphViewport[]>([]);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [hover, setHover] = useState<{ text: string; x: number; y: number } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const [selection, setSelection] = useState<string[]>([]);
  const menuRef = useRef<HTMLDivElement>(null);

  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  // Cytoscape の購読は作り直さないので、最新の値とコールバックを読む。
  const latest = useRef({ onSelectionChange, onExpand, nodeById, labels, nodeKinds });
  useLayoutEffect(() => {
    latest.current = { onSelectionChange, onExpand, nodeById, labels, nodeKinds };
  });

  const describe = useCallback((id: string) => {
    const { nodeById: byId, nodeKinds: kinds, labels: text } = latest.current;
    const node = byId.get(id);
    if (!node) return "";
    const kind = kinds.find((item) => item.id === node.kind)?.label ?? "";
    const state = node.expanded ? text.expandedState : node.expandable ? text.expandableState : "";
    return [kind, node.title ?? node.label, state].filter(Boolean).join("、");
  }, []);

  const readableFit = useCallback((ids?: readonly string[], maxZoom = 1) => {
    const cy = cyRef.current;
    if (!cy || !cy.width() || !cy.height()) return;
    const visible = cy.elements().not(".hidden");
    const target = ids ? visible.filter((element) => ids.includes(element.id())) : visible;
    if (!target.length) return;
    const box = target.boundingBox({ includeLabels: true });
    const viewport = graphReadableViewport({
      bounds: { x: box.x1, y: box.y1, width: box.w, height: box.h },
      frame: { width: cy.width(), height: cy.height() },
      padding: 32,
      maxZoom,
    });
    cy.viewport({ zoom: viewport.zoom, pan: { x: viewport.x, y: viewport.y } });
  }, []);

  const remember = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const current = { ...cy.pan(), zoom: cy.zoom() };
    setHistory((old) => {
      const last = old.at(-1);
      return last && last.x === current.x && last.y === current.y && last.zoom === current.zoom ? old : [...old.slice(-15), current];
    });
  }, []);

  const centerOn = useCallback((id: string) => {
    const cy = cyRef.current;
    const node = cy?.getElementById(id);
    if (!cy || !node?.length || node.hasClass("hidden")) return;
    const p = node.renderedPosition();
    if (p.x >= 32 && p.y >= 32 && p.x <= cy.width() - 32 && p.y <= cy.height() - 32) return;
    remember();
    cy.zoom(Math.max(cy.zoom(), 0.8));
    cy.center(node);
  }, [remember]);

  const selectOnly = useCallback((id: string) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements(":selected").unselect();
    cy.getElementById(id).select();
    setActive(id);
    setAnnouncement(latest.current.labels.announce(describe(id)));
  }, [describe]);

  useImperativeHandle(ref, () => ({
    focusNode: (id: string) => { selectOnly(id); centerOn(id); },
    fit: () => { remember(); readableFit(); },
  }), [centerOn, readableFit, remember, selectOnly]);

  // 画布を作る（1 度だけ）。
  useEffect(() => {
    ensureLayout();
    const container = host.current;
    if (!container) return;
    const cy = cytoscape({
      container,
      minZoom: GRAPH_OVERVIEW_MIN_ZOOM,
      maxZoom: 2.5,
      boxSelectionEnabled: true,
      selectionType: "single",
      wheelSensitivity: 0.4,
    });
    cyRef.current = cy;
    // 範囲選択などで続けて届く通知を 1 回にまとめ、次の操作の前に呼び出し側へ渡す（古い選択で上書きさせない）。
    let pending = false;
    let alive = true;
    const emitSelection = () => {
      if (syncingSelection.current || pending) return;
      pending = true;
      queueMicrotask(() => {
        pending = false;
        if (!alive) return;
        const ids = cy.elements(":selected").map((element) => element.id());
        setSelection(ids);
        latest.current.onSelectionChange?.(ids);
      });
    };
    cy.on("select unselect", emitSelection);
    cy.on("tap", "node", (event) => {
      const id = (event.target as NodeSingular).id();
      setActive(id);
      setMenu(null);
      setAnnouncement(latest.current.labels.announce(describe(id)));
    });
    cy.on("tap", (event) => { if (event.target === cy) setMenu(null); });
    cy.on("dbltap", "node", (event) => latest.current.onExpand?.((event.target as NodeSingular).id()));
    cy.on("cxttap", "node", (event) => {
      const node = event.target as NodeSingular;
      if (!node.selected()) { cy.elements(":selected").unselect(); node.select(); }
      setActive(node.id());
      setHover(null);
      setMenu({ id: node.id(), x: event.renderedPosition.x, y: event.renderedPosition.y });
    });
    cy.on("mouseover", "node", (event) => {
      const node = event.target as NodeSingular;
      cy.elements().not(node.closedNeighborhood()).addClass("faded");
      const item = latest.current.nodeById.get(node.id());
      const p = node.renderedPosition();
      if (item) setHover({ text: item.title ?? item.label, x: p.x, y: p.y + node.renderedOuterHeight() / 2 + 4 });
    });
    cy.on("mouseout", "node", () => { cy.elements().removeClass("faded"); setHover(null); });
    cy.on("pan zoom drag", () => { setMenu(null); setHover(null); });
    // 描いた数と縮尺を属性に出す（検査と診断用。canvas の中は DOM から読めない）。
    const report = () => {
      const target = focusTarget.current;
      if (!target) return;
      target.dataset.graphZoom = cy.zoom().toFixed(3);
      target.dataset.graphNodes = String(cy.nodes().not(".hidden").length);
      target.dataset.graphEdges = String(cy.edges().not(".hidden").length);
    };
    cy.on("zoom add remove", report);
    cy.on("layoutstop", report);
    return () => {
      alive = false;
      cy.destroy();
      cyRef.current = null;
    };
  }, [describe]);

  // テーマ（ライト・ダーク・OS 追従）が変わったら色を解き直す。
  useEffect(() => {
    const cy = cyRef.current;
    const container = host.current;
    if (!cy || !container) return;
    const apply = () => cy.style(graphExplorerStyle(resolveColors(container, nodeKinds), nodeKinds, edgeKinds));
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    media?.addEventListener("change", apply);
    return () => {
      observer.disconnect();
      media?.removeEventListener("change", apply);
    };
  }, [nodeKinds, edgeKinds]);

  // 要素の差分を反映し、足したノードだけを配置する（既存のノードは動かさない）。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const fresh = laidOut.current !== layoutKey;
    const labelledKinds = new Set(edgeKinds.filter((kind) => kind.labelled).map((kind) => kind.id));
    const edgeLabel = new Map(edgeKinds.map((kind) => [kind.id, kind.label]));
    const nodeIds = new Set(nodes.map((node) => node.id));
    const drawn = edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
    const wanted = new Set([...nodeIds, ...drawn.map((edge) => edge.id)]);
    const existing = new Map<string, { x: number; y: number }>();
    if (!fresh) cy.nodes().forEach((node) => { if (wanted.has(node.id())) existing.set(node.id(), { ...node.position() }); });
    const added = nodes.filter((node) => !existing.has(node.id)).map((node) => node.id);
    const seeds = seedGraphPositions(added, drawn, existing);
    cy.batch(() => {
      cy.elements().filter((element) => !wanted.has(element.id()) || (fresh && element.isNode())).remove();
      const additions: ElementDefinition[] = [];
      for (const node of nodes) {
        const data = { id: node.id, kind: node.kind, display: wrapGraphLabel(node.label) };
        const element = cy.getElementById(node.id);
        if (element.length) element.data(data);
        else additions.push({ group: "nodes", data, position: seeds.get(node.id) });
      }
      for (const edge of drawn) {
        const data = { id: edge.id, source: edge.source, target: edge.target, kind: edge.kind, strong: Boolean(edge.strong), text: labelledKinds.has(edge.kind) ? edgeLabel.get(edge.kind) ?? "" : "" };
        const element = cy.getElementById(edge.id);
        if (element.length) element.data(data);
        else additions.push({ group: "edges", data });
      }
      cy.add(additions);
      cy.nodes().forEach((node) => { node.toggleClass("matched", Boolean(nodeById.get(node.id())?.matched)); });
    });
    if (!nodes.length) {
      laidOut.current = layoutKey;
      return;
    }
    if (fresh || existing.size === 0) {
      laidOut.current = layoutKey;
      cy.layout({ ...LAYOUT_BASE, randomize: true } as LayoutOptions).run();
      setHistory([]);
      readableFit();
      return;
    }
    if (!added.length) return;
    const fixed = [...existing].map(([nodeId, position]) => ({ nodeId, position }));
    cy.layout({ ...LAYOUT_BASE, randomize: false, fixedNodeConstraint: fixed } as LayoutOptions).run();
    // 足したノードが画面の外なら、足したノードとつながる既存のノードに合わせる（縮尺は読める大きさ以上）。
    // 名前を含む範囲が画布の端（上は右上の操作の分）に掛かるものも外とみなす。
    const outside = added.some((id) => {
      const box = cy.getElementById(id).renderedBoundingBox({ includeLabels: true });
      return box.x1 < 16 || box.y1 < 56 || box.x2 > cy.width() - 16 || box.y2 > cy.height() - 16;
    });
    if (outside) {
      const addedSet = new Set(added);
      const anchors = drawn.flatMap((edge) => addedSet.has(edge.source) && existing.has(edge.target) ? [edge.target]
        : addedSet.has(edge.target) && existing.has(edge.source) ? [edge.source] : []);
      remember();
      // 展開では拡大しない（今の縮尺を上限にして、足したノードが入るように動かす）。
      readableFit([...added, ...anchors], Math.max(cy.zoom(), 0.8));
    }
  }, [nodes, edges, edgeKinds, layoutKey, nodeById, readableFit, remember]);

  // 関係の種類の絞り込み。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.edges().forEach((edge) => { edge.toggleClass("hidden", hiddenEdgeKinds.has(edge.data("kind"))); });
    });
  }, [hiddenEdgeKinds, nodes, edges]);

  // 選択を外から合わせる（一覧・詳細から選んだとき）。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !selectedIds) return;
    const wanted = new Set(selectedIds);
    const current = cy.elements(":selected").map((element) => element.id());
    if (current.length === wanted.size && current.every((id) => wanted.has(id))) return;
    syncingSelection.current = true;
    cy.elements(":selected").filter((element) => !wanted.has(element.id())).unselect();
    for (const id of wanted) cy.getElementById(id).select();
    syncingSelection.current = false;
    setSelection([...wanted].filter((id) => cy.getElementById(id).length));
  }, [selectedIds, nodes, edges]);

  useEffect(() => {
    if (menu) menuRef.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
  }, [menu]);

  function visiblePoints() {
    const cy = cyRef.current;
    if (!cy) return [];
    return cy.nodes().not(".hidden").nodes().map((node) => ({ id: node.id(), ...node.position() }));
  }

  function openMenuAt(id: string) {
    const cy = cyRef.current;
    if (!cy) return;
    const p = cy.getElementById(id).renderedPosition();
    setMenu({ id, x: p.x, y: p.y });
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const cy = cyRef.current;
    if (!cy || (event.target !== event.currentTarget && event.target !== host.current) || isImeComposing(event)) return;
    const directions: Record<string, GraphDirection> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
    const direction = directions[event.key];
    const current = active && cy.getElementById(active).length ? active : null;
    if (direction) {
      event.preventDefault();
      const points = visiblePoints();
      const from = current ? cy.getElementById(current).position() : null;
      const next = from ? nearestGraphNodeInDirection(points.filter((p) => p.id !== current), from, direction) : firstGraphNode(points);
      if (next) { selectOnly(next); centerOn(next); }
      return;
    }
    if (event.key === "Enter" && current) {
      event.preventDefault();
      onExpand?.(current);
      return;
    }
    if ((event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) && current) {
      event.preventDefault();
      openMenuAt(current);
      return;
    }
    if (event.key === "Escape") {
      if (menu) setMenu(null);
      else cy.elements(":selected").unselect();
      return;
    }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); zoomBy(1.2); }
    if (event.key === "-") { event.preventDefault(); zoomBy(1 / 1.2); }
  }

  function zoomBy(factor: number) {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor)), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  }

  function relayout() {
    const cy = cyRef.current;
    if (!cy || !cy.nodes().length) return;
    remember();
    cy.layout({ ...LAYOUT_BASE, randomize: true } as LayoutOptions).run();
    readableFit();
  }

  function previous() {
    const cy = cyRef.current;
    const viewport = history.at(-1);
    if (!cy || !viewport) return;
    setHistory(history.slice(0, -1));
    cy.viewport({ zoom: viewport.zoom, pan: { x: viewport.x, y: viewport.y } });
  }

  function closeMenu() {
    setMenu(null);
    focusTarget.current?.focus();
  }

  function selectKind(kind: string) {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements(":selected").unselect();
    cy.nodes(`[kind = ${JSON.stringify(kind)}]`).not(".hidden").select();
  }

  const selectedNodes = selection.filter((id) => nodeById.has(id));
  const menuNode = menu ? nodeById.get(menu.id) : undefined;
  const menuTargets = menu && selectedNodes.includes(menu.id) ? selectedNodes : menu ? [menu.id] : [];
  const options = menuNode && expandOptions ? expandOptions(menuNode.id) : [];
  const menuItems: { id: string; label: string; run: () => void; submenu?: boolean }[] = menuNode ? [
    ...(menuNode.expandable && onExpand ? [{ id: "expand", label: labels.expand, run: () => onExpand(menuNode.id) }] : []),
    ...(options.length && onExpand ? [{ id: "expand-kind", label: labels.expandByKind, submenu: true, run: () => setMenu({ ...menu!, submenu: true }) }] : []),
    ...(menuNode.expanded && onCollapse ? [{ id: "collapse", label: labels.collapse, run: () => onCollapse(menuNode.id) }] : []),
    ...(onOpen ? [{ id: "details", label: labels.details, run: () => onOpen(menuNode.id) }] : []),
    { id: "center", label: labels.center, run: () => { remember(); cyRef.current?.center(cyRef.current.getElementById(menuNode.id)); } },
    ...(onRemove ? [{ id: "remove", label: menuTargets.length > 1 ? labels.removeSelected(menuTargets.length) : labels.remove, run: () => onRemove(menuTargets) }] : []),
    ...(onKeepOnly ? [{ id: "keep", label: labels.keepSelected, run: () => onKeepOnly(menuTargets) }] : []),
    ...(onConnect && menuTargets.length > 1 ? [{ id: "connect", label: labels.connectSelected, run: () => onConnect(menuTargets) }] : []),
  ] : [];
  const shownItems = menu?.submenu
    ? options.map((option) => ({ id: `kind-${option.id}`, label: option.label, run: () => onExpand?.(menu.id, [option.id]), submenu: false }))
    : menuItems;

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const items = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? [])];
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Escape" || (event.key === "ArrowLeft" && menu?.submenu)) {
      event.preventDefault();
      if (menu?.submenu) setMenu({ ...menu, submenu: false });
      else closeMenu();
    }
    if (event.key === "ArrowDown") { event.preventDefault(); items[(index + 1) % items.length]?.focus(); }
    if (event.key === "ArrowUp") { event.preventDefault(); items[(index - 1 + items.length) % items.length]?.focus(); }
    if (event.key === "Tab") setMenu(null);
  }

  const kindCounts = new Map<string, number>();
  for (const node of nodes) kindCounts.set(node.kind, (kindCounts.get(node.kind) ?? 0) + 1);
  const legendKinds = nodeKinds.filter((kind) => !kind.legendHidden && kindCounts.get(kind.id));
  const presentEdgeKinds = edgeKinds.filter((kind) => edges.some((edge) => edge.kind === kind.id && nodeById.has(edge.source) && nodeById.has(edge.target)));

  return (
    <div className={cn("grid min-w-0 gap-2", className)} data-testid={testId}>
      {toolbar && <div className="flex min-w-0 flex-wrap items-center gap-2">{toolbar}</div>}
      {(legendKinds.length > 0 || presentEdgeKinds.length > 0) && (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {legendKinds.length > 0 && (
            <div className="flex min-w-0 flex-wrap items-center gap-1 rounded-md border border-border bg-surface px-2 py-1 text-xs text-fg-muted" role="group" aria-label={labels.nodeKinds} data-testid={testId ? `${testId}-node-kinds` : undefined}>
              {legendKinds.map((kind) => (
                <Button key={kind.id} type="button" size="sm" variant="ghost" className="text-xs" tooltip={labels.selectKindHint(kind.label)}
                  onClick={() => selectKind(kind.id)} data-testid={testId ? `${testId}-kind-${kind.id}` : undefined}>
                  <GraphLegendSwatch fill={`var(${kind.color})`} />
                  {kind.label} {kindCounts.get(kind.id)}
                </Button>
              ))}
            </div>
          )}
          {presentEdgeKinds.length > 0 && (
            <GraphLegend placement="inline" label={labels.edgeKinds} testId={testId ? `${testId}-edge-kinds` : undefined}
              items={presentEdgeKinds.map((kind) => ({
                id: kind.id,
                label: kind.label,
                marker: <span aria-hidden="true" className={cn("w-5 shrink-0 border-t-2", kind.dashed && "border-dashed")} style={{ borderColor: "var(--color-graph-line)" }} />,
              }))}
              disabledIds={hiddenEdgeKinds} onToggle={(id) => setHiddenEdgeKinds((old) => {
                const next = new Set(old);
                if (next.has(id)) next.delete(id); else next.add(id);
                return next;
              })} />
          )}
        </div>
      )}
      <div className={cn("relative min-h-80 overflow-hidden rounded-md border border-border bg-surface-sunken", compact ? "h-[26rem]" : "h-[40rem] max-sm:h-[65dvh]")}>
        <div
          ref={focusTarget}
          role="application"
          aria-label={labels.canvas}
          aria-describedby={hintId}
          tabIndex={0}
          onKeyDown={onKeyDown}
          onFocus={() => { if (!active) { const first = firstGraphNode(visiblePoints()); if (first) setActive(first); } }}
          className="absolute inset-0 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring"
          data-graph-explorer=""
          data-testid={testId ? `${testId}-canvas` : undefined}
        >
          {/* Cytoscape は container に position: relative を付けるので、大きさは外の枠で決める。 */}
          <div ref={host} className="h-full w-full" />
        </div>
        <p id={hintId} className="sr-only">{labels.keyboardHint}</p>
        <span className="sr-only" aria-live="polite" data-testid={testId ? `${testId}-announcement` : undefined}>{announcement}</span>
        {/* 狭い画面は画布の下に 1 行で置き、名前を隠さない。 */}
        <div className="absolute right-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] items-center gap-1 overflow-x-auto rounded-md border border-border bg-surface p-1 shadow-sm max-sm:bottom-3 max-sm:top-auto">
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Plus} aria-label={labels.zoomIn} onClick={() => zoomBy(1.2)} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Minus} aria-label={labels.zoomOut} onClick={() => zoomBy(1 / 1.2)} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Maximize2} aria-label={labels.fit} onClick={() => { remember(); readableFit(); }} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Focus} aria-label={labels.focus} disabled={!selectedNodes.length}
            onClick={() => { remember(); readableFit(selectedNodes); }} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Undo2} aria-label={labels.previous} disabled={!history.length} onClick={previous} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={RotateCcw} aria-label={labels.relayout} disabled={!nodes.length} onClick={relayout} />
        </div>
        {selectedNodes.length > 1 && (
          <p className="absolute left-3 top-3 z-10 rounded-md border border-border bg-surface px-2 py-1 text-xs text-fg-muted shadow-sm" aria-live="polite" data-testid={testId ? `${testId}-selected-count` : undefined}>
            {labels.selectedCount(selectedNodes.length)}
          </p>
        )}
        {hover && !menu && (
          <div role="tooltip" className="pointer-events-none absolute z-20 max-w-[18rem] -translate-x-1/2 rounded-md border border-border bg-surface px-2 py-1 text-xs text-fg shadow-sm [overflow-wrap:anywhere]"
            style={{ left: `${hover.x}px`, top: `${hover.y}px` }}>
            {hover.text}
          </div>
        )}
        {menu && shownItems.length > 0 && (
          <div ref={menuRef} role="menu" aria-label={menu.submenu ? labels.expandByKind : labels.menu} onKeyDown={onMenuKeyDown}
            className="absolute z-30 grid min-w-48 max-w-[calc(100%-1.5rem)] rounded-md border border-border bg-surface p-1 shadow-md"
            style={{ left: `min(${menu.x}px, calc(100% - 14rem))`, top: `min(${menu.y}px, calc(100% - 18rem))` }}
            data-testid={testId ? `${testId}-menu` : undefined}>
            {shownItems.map((item) => (
              <button key={item.id} type="button" role="menuitem" tabIndex={-1} aria-haspopup={item.submenu ? "menu" : undefined}
                className="flex items-center justify-between gap-2 rounded px-3 py-1.5 text-left text-sm text-fg hover:bg-surface-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring"
                onClick={() => { item.run(); if (!item.submenu) closeMenu(); }}>
                {item.label}
                {item.submenu && <ChevronRight size={14} aria-hidden="true" />}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});
