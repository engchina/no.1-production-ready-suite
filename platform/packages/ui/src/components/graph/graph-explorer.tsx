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
import {
  GRAPH_EDGE_LABELS_ALWAYS_MAX,
  firstGraphNode,
  graphEdgeElasticity,
  nearestGraphNodeInDirection,
  seedGraphPositions,
  wrapGraphLabel,
  type GraphDirection,
} from "../../lib/graph-explorer-model";
import { GRAPH_OVERVIEW_MIN_ZOOM, GRAPH_READABLE_MIN_ZOOM, graphReadableViewport, type GraphViewport } from "../../lib/graph-viewport";
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
  /** 状態の枠（種類 = 塗り / 状態 = 線。検証の警告・阻止など）。省略時は種類の枠。 */
  tone?: "warning" | "danger";
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
  /** 図の全体を画布に収める。 */
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
  warning: string;
  danger: string;
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
    warning: read("--color-warning-fg"),
    danger: read("--color-danger-fg"),
    font: getComputedStyle(host).fontFamily,
  };
  probe.remove();
  return colors;
}

/** 節点の名前と辺の種類の文字の大きさ（図の座標の px）と、画面上でこれを切ったら文字を隠す大きさ（px）。 */
const NODE_FONT_SIZE = 13;
const EDGE_FONT_SIZE = 11;
const LABEL_MIN_PX = 7;
/**
 * 開いた直後の縮尺の下限（#5973）。名前が画面上で 8px 強になり、節点の名前を読める。名前を隠す縮尺（0.54）
 * ちょうどでは 7px で読みにくいので、それより大きくする。全体表示・手の操作はこれより縮められる。
 */
const OPEN_MIN_ZOOM = 0.65;

/**
 * 画布の見た目（C 案）。中くらいの円、名前は円の下に 2 行まで（座標の 13px。読める縮尺の下限 0.8 で 10px 前後）。
 * 種類 = 塗りと大きさ、選択 = 太い枠、検索の一致 = 外側の輪。関係は矢印つきで、`dashed` の種類は点線、
 * `labelled` の種類だけ線の上に種類の文字を出す。文字は水平で、画布の背景色の下地を持つ（すべてを辺に沿って回すと
 * 密なところで重なって読めない）。全体を収めて縮んだときに画面上で 7px を切る文字は `unreadable` で隠す（辺の
 * 文字は縮尺 0.64 未満、節点の名前は 0.54 未満。Cytoscape の `min-zoomed-font-size` は 2 の冪の段と画素密度で
 * 丸めるので使わない）。隠すときは文字を消さずに透明にする（名前を含む範囲が変わると、全体を収める縮尺が押す
 * たびに変わる）。
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
        "font-size": NODE_FONT_SIZE,
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
    { selector: "node[tone = \"warning\"]", style: { "border-color": colors.warning, "border-width": 3 } },
    { selector: "node[tone = \"danger\"]", style: { "border-color": colors.danger, "border-width": 3, "border-style": "double" } },
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
        "font-size": EDGE_FONT_SIZE,
        "font-family": colors.font,
        color: colors.fgMuted,
        "text-background-color": colors.surface,
        "text-background-opacity": 1,
        "text-background-padding": "2px",
        "overlay-opacity": 0,
      },
    },
    ...edgeKinds.filter((kind) => kind.dashed).map((kind) => ({
      selector: `edge[kind = ${JSON.stringify(kind.id)}]`,
      style: { "line-style": "dashed" as const },
    })),
    { selector: "edge[?strong]", style: { width: 2.5 } },
    { selector: "edge:selected", style: { "line-color": colors.accent, "target-arrow-color": colors.accent, width: 3.5, color: colors.fg } },
    // 文字つきの辺が多い図で、選んだ・ホバーした節点につながらない辺（太い線にせず、薄くして、文字を出さない）。
    { selector: "edge.quiet", style: { width: 1.5, opacity: 0.5, "text-opacity": 0, "text-background-opacity": 0 } },
    // 同じ組に文字つきの辺が複数あるときは、文字をそれぞれの曲線に沿わせる（水平のままだと、縦に近い辺で重なる。#5959）。
    { selector: "edge.parallel", style: { "text-rotation": "autorotate" as const } },
    { selector: ".unreadable", style: { "text-opacity": 0, "text-background-opacity": 0 } },
    { selector: ".faded", style: { opacity: 0.2 } },
    { selector: ".hidden", style: { display: "none" } },
  ] as StylesheetJson;
}

/**
 * fcose の配置（名前の大きさを含めて重ならない間隔。C 案の検証の値）。辺の数が節点の数に対して多い図は、
 * ばねを `graphEdgeElasticity` で弱め、節点と名前が重ならない間隔を保つ（広がった分は全体を収める表示で縮めて見せる）。
 * `gravityRange` は、つながっていないまとまりを中心へ寄せる重力が効き始める距離（fcose の既定は 3.8）。既定では
 * まとまりの間が大きく空き、全体を収めると名前が読めない縮尺になるので、最初からの配置では名前が重ならない範囲で
 * 寄せる（#5953）。足したノードだけの配置（`fixed`）では既定のままにする（動かせない既存のノードに重ねない）。
 */
function layoutOptions(cy: Core, fixed?: { nodeId: string; position: { x: number; y: number } }[]): LayoutOptions {
  const base = {
    name: "fcose",
    animate: false,
    quality: "proof",
    nodeDimensionsIncludeLabels: true,
    idealEdgeLength: 100,
    edgeElasticity: graphEdgeElasticity(cy.nodes().length, cy.edges().length),
    nodeRepulsion: 11000,
    nodeSeparation: 85,
    packComponents: true,
    padding: 24,
    fit: false,
  };
  const options = fixed ? { ...base, randomize: false, fixedNodeConstraint: fixed } : { ...base, randomize: true, gravityRange: 0.8 };
  return options as LayoutOptions;
}

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
 * クリックの複数選択、右クリック（と ContextMenu・Shift＋F10）のメニュー、全体表示（図の全体を画布に収める。#5953）。
 * 文字つきの辺が多い図は、選んだ・ホバーした節点につながる辺だけ文字を出し、ほかを薄くする（Bloom の選択の強調）。
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
  const controls = useRef<HTMLDivElement>(null);
  const hovered = useRef<string | null>(null);
  /** 全体を収めたときの表示。利用者が動かすまでは、画布の大きさが変わったとき・ノードを足したときに収め直す。 */
  const allFitted = useRef<(GraphViewport & { overview: boolean }) | null>(null);
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

  /**
   * 範囲を画布に合わせる。`ids` を省くと図の全体。縮尺の下限の既定は読める縮尺（選択に合わせる・展開の後）。
   * 余白は、画布に重ねた操作の行（広い画面は上、狭い画面は下）の分を空ける。
   */
  const fitTo = useCallback((ids?: readonly string[], maxZoom = 1, minZoom = GRAPH_READABLE_MIN_ZOOM, centered = false) => {
    const cy = cyRef.current;
    if (!cy || !cy.width() || !cy.height()) return;
    const visible = cy.elements().not(".hidden");
    const target = ids ? visible.filter((element) => ids.includes(element.id())) : visible;
    if (!target.length) return;
    const box = target.boundingBox({ includeLabels: true });
    const padding = { top: 32, right: 32, bottom: 32, left: 32 };
    const bar = controls.current;
    if (bar && bar.offsetTop < cy.height() / 2) padding.top = bar.offsetTop + bar.offsetHeight + 8;
    else if (bar) padding.bottom = cy.height() - bar.offsetTop + 8;
    const viewport = graphReadableViewport({
      bounds: { x: box.x1, y: box.y1, width: box.w, height: box.h },
      frame: { width: cy.width(), height: cy.height() },
      padding,
      minZoom,
      maxZoom,
      align: centered ? { x: "center", y: "center" } : undefined,
    });
    cy.viewport({ zoom: viewport.zoom, pan: { x: viewport.x, y: viewport.y } });
  }, []);
  /**
   * 図の全体に合わせる。初期表示・自動配置に戻すは、節点の名前を読める縮尺（`OPEN_MIN_ZOOM`）より縮めない（全体が収まらない図は
   * 中央を表示し、残りは動かして見る。名前の無い丸だけの図にしない。#5973）。`overview`（「全体表示」）は
   * 手で縮める下限まで縮めて全体を収める（#5953）。
   */
  const fitAll = useCallback((overview = false) => {
    fitTo(undefined, 1, overview ? GRAPH_OVERVIEW_MIN_ZOOM : OPEN_MIN_ZOOM, true);
    const cy = cyRef.current;
    allFitted.current = cy ? { ...cy.pan(), zoom: cy.zoom(), overview } : null;
  }, [fitTo]);
  /** 全体に合わせたまま（利用者が動かしていない）か。 */
  const stillFitted = useCallback(() => {
    const cy = cyRef.current;
    const last = allFitted.current;
    return Boolean(cy && last && last.zoom === cy.zoom() && last.x === cy.pan().x && last.y === cy.pan().y);
  }, []);

  /**
   * 強調と文字の出し方を付け直す。文字つきの辺が多い図は、選んだ・ホバーした節点につながる辺だけ文字を出し、
   * ほかを薄くする。少ない図は今までどおりすべての辺に文字を出し、ホバーした節点の隣接以外だけを薄くする。
   * 画面上で読めない大きさ（7px 未満）になる文字は隠す。
   */
  const emphasize = useCallback(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const zoom = cy.zoom();
    const edges = cy.edges().not(".hidden");
    const dense = edges.filter("[?text]").length > GRAPH_EDGE_LABELS_ALWAYS_MAX;
    const hover = hovered.current ? cy.getElementById(hovered.current) : cy.collection();
    const origin = hover.length ? hover : dense ? cy.elements(":selected") : cy.collection();
    const lit = origin.union(origin.nodes().connectedEdges().not(".hidden"));
    cy.batch(() => {
      cy.elements().removeClass("faded quiet unreadable parallel");
      const labelled = edges.filter("[?text]");
      labelled.edges().filter((edge) => edge.parallelEdges().intersection(labelled).length > 1).addClass("parallel");
      if (origin.length) cy.elements().not(lit.union(lit.edges().connectedNodes())).addClass("faded");
      if (dense) edges.not(lit).addClass("quiet");
      if (zoom * EDGE_FONT_SIZE < LABEL_MIN_PX) cy.edges().addClass("unreadable");
      if (zoom * NODE_FONT_SIZE < LABEL_MIN_PX) cy.nodes().addClass("unreadable");
    });
    if (focusTarget.current) {
      focusTarget.current.dataset.graphEdgeLabels = dense ? "focus" : "all";
      focusTarget.current.dataset.graphParallelLabels = String(cy.edges(".parallel").length);
    }
  }, []);

  /** 描いた数・縮尺・図の全体が画布に収まっているかを属性に出す（検査と診断用。canvas の中は DOM から読めない）。 */
  const report = useCallback(() => {
    const cy = cyRef.current;
    const target = focusTarget.current;
    if (!cy || !target) return;
    const shown = cy.elements().not(".hidden");
    const box = shown.renderedBoundingBox({ includeLabels: true });
    target.dataset.graphZoom = cy.zoom().toFixed(3);
    target.dataset.graphNodes = String(shown.nodes().length);
    target.dataset.graphEdges = String(shown.edges().length);
    target.dataset.graphContained = String(shown.length > 0 && box.x1 >= 0 && box.y1 >= 0 && box.x2 <= cy.width() && box.y2 <= cy.height());
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
    cy.zoom(Math.max(cy.zoom(), GRAPH_READABLE_MIN_ZOOM));
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
    fit: () => { remember(); fitAll(true); },
  }), [centerOn, fitAll, remember, selectOnly]);

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
        emphasize();
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
      hovered.current = node.id();
      emphasize();
      const item = latest.current.nodeById.get(node.id());
      const p = node.renderedPosition();
      if (item) setHover({ text: item.title ?? item.label, x: p.x, y: p.y + node.renderedOuterHeight() / 2 + 4 });
    });
    cy.on("mouseout", "node", () => { hovered.current = null; emphasize(); setHover(null); });
    cy.on("pan zoom drag", () => { setMenu(null); setHover(null); });
    // 文字を出す・隠すの境の縮尺をまたいだときだけ、文字の出し方を付け直す。
    let textScale = "";
    cy.on("zoom", () => {
      const next = `${cy.zoom() * EDGE_FONT_SIZE < LABEL_MIN_PX}:${cy.zoom() * NODE_FONT_SIZE < LABEL_MIN_PX}`;
      if (next === textScale) return;
      textScale = next;
      emphasize();
    });
    // 全体を収めたまま（利用者が動かしていない）なら、画布の大きさが変わったときに収め直す（隠れていた画布が
    // 見えたとき・全画面・詳細の開閉）。
    cy.on("resize", () => { if (stillFitted()) fitAll(allFitted.current?.overview); });
    // 要素の増減は、反映の effect の終わりで出す（追加の batch の中で範囲を測ると、名前の大きさが決まる前の
    // 範囲が cache に残り、最初の配置が名前を含まない大きさで計算される）。
    cy.on("viewport layoutstop", report);
    return () => {
      alive = false;
      cy.destroy();
      cyRef.current = null;
    };
  }, [describe, emphasize, fitAll, report, stillFitted]);

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
        const data = { id: node.id, kind: node.kind, display: wrapGraphLabel(node.label), tone: node.tone ?? "" };
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
      cy.layout(layoutOptions(cy)).run();
      setHistory([]);
      fitAll();
      return;
    }
    if (!added.length) return;
    const fixed = [...existing].map(([nodeId, position]) => ({ nodeId, position }));
    cy.layout(layoutOptions(cy, fixed)).run();
    // 全体に合わせたまま（利用者が動かしていない）なら、足した後も同じ合わせ方にする。全体が収まれば、足した
    // ノードも画面の中にある。名前を読める縮尺で収まらない図は中央を表示するので、足したノードが外なら下で合わせる。
    if (stillFitted()) {
      fitAll(allFitted.current?.overview);
      const box = cy.elements().not(".hidden").renderedBoundingBox({ includeLabels: true });
      if (box.x1 >= 0 && box.y1 >= 0 && box.x2 <= cy.width() && box.y2 <= cy.height()) return;
    }
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
      // 展開では拡大しない（今の縮尺を上限にして、足したノードが入るように動かす。読める縮尺より縮めない）。
      fitTo([...added, ...anchors], cy.zoom(), Math.min(cy.zoom(), GRAPH_READABLE_MIN_ZOOM));
    }
  }, [nodes, edges, edgeKinds, layoutKey, nodeById, fitAll, fitTo, remember, stillFitted]);

  // 関係の種類の絞り込み。
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.edges().forEach((edge) => { edge.toggleClass("hidden", hiddenEdgeKinds.has(edge.data("kind"))); });
    });
    // 見えている辺の数で、辺の文字の出し方（すべて / 選んだ節点の辺だけ）が変わる。
    emphasize();
    report();
  }, [hiddenEdgeKinds, nodes, edges, emphasize, report]);

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
    emphasize();
  }, [selectedIds, nodes, edges, emphasize]);

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
    cy.layout(layoutOptions(cy)).run();
    fitAll();
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
        <div ref={controls} className="absolute right-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] items-center gap-1 overflow-x-auto rounded-md border border-border bg-surface p-1 shadow-sm max-sm:bottom-3 max-sm:top-auto">
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Plus} aria-label={labels.zoomIn} onClick={() => zoomBy(1.2)} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Minus} aria-label={labels.zoomOut} onClick={() => zoomBy(1 / 1.2)} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Maximize2} aria-label={labels.fit} onClick={() => { remember(); fitAll(true); }} />
          <Button type="button" size="sm" variant="ghost" iconOnly icon={Focus} aria-label={labels.focus} disabled={!selectedNodes.length}
            onClick={() => { remember(); fitTo(selectedNodes); }} />
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
