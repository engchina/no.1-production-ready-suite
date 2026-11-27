import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useReactFlow, useStore, type Edge, type Node, type NodeProps } from "@xyflow/react";
import {
  directedGraphBundles,
  GraphCanvas,
  GraphCanvasControls,
  GraphCanvasFrame,
  GraphCanvasProvider,
  GraphPointNode,
  GraphSearchNavigation,
  normalizeGraphSearch,
  GraphParallelEdge,
  graphEdgeStyle,
  selectGraphEdgeHandles,
  GRAPH_EDGE_LABEL_PROPS,
  GRAPH_EDGE_MARKER,
} from "@production-ready/ui/graph";
import "@xyflow/react/dist/style.css";
import { t } from "@/lib/i18n";
import { wikiRelationText } from "@/components/knowledge-bases/wiki-graph-labels";
import type { WikiDirectedGroup } from "./wiki-api";
import type { WikiGraphRelation, WikiGraphRelationItem } from "@/lib/api";

export type NodeKind = "document" | "section" | "unit";
export interface WikiNode {
  id: string;
  label: string;
  document_id: string;
  document_name: string;
  kind: NodeKind;
  path?: string[];
}
export interface Connection {
  source: string;
  target: string;
  kind: WikiGraphRelation | "contains";
  cross?: boolean;
}
export interface Scene {
  nodes: WikiNode[];
  edges: Connection[];
}
export const emptyScene: Scene = { nodes: [], edges: [] };
export const edgeId = (e: Connection) => [e.source, e.kind, e.target].join(":");
export const edgeName = (e: Connection) =>
  e.kind === "contains" ? t("wiki.search.children") : wikiRelationText(e.kind);
export function neighbors(node: WikiNode, rows: WikiGraphRelationItem[]): Scene {
  const nodes: WikiNode[] = [node];
  const edges: Connection[] = [];
  for (const row of rows) {
    if (!row.unit) continue;
    nodes.push({ ...row.unit, kind: "unit" });
    const outgoing = row.direction === "outgoing";
    edges.push({
      source: outgoing ? node.id : row.unit.id,
      target: outgoing ? row.unit.id : node.id,
      kind: row.relation,
      cross: row.scope === "cross_document",
    });
  }
  return { nodes, edges };
}
export function joinScenes(...scenes: Scene[]): Scene {
  const nodes = new Map<string, WikiNode>(),
    edges = new Map<string, Connection>();
  for (const scene of scenes) {
    for (const n of scene.nodes) nodes.set(n.id, n);
    for (const e of scene.edges) edges.set(edgeId(e), e);
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

export interface WikiGroupPoint extends WikiDirectedGroup {
  id: string;
  label: string;
  owner: WikiNode;
  remaining: number;
}
type Point = WikiNode | WikiGroupPoint;
const POINT_SIZE = { width: 150, height: 72 };
const relationColors: Record<Connection["kind"], [string, string]> = {
  prerequisite: ["--color-graph-term", "--color-success-fg"],
  continuation: ["--color-graph-metric", "--color-graph-line"],
  exception: ["--color-success-subtle", "--color-success-fg"],
  expected_result: ["--color-warning-subtle", "--color-warning-fg"],
  warning: ["--color-danger-subtle", "--color-danger-fg"],
  reference: ["--color-graph-metric", "--color-graph-line"],
  contains: ["--color-graph-term", "--color-graph-line"],
};
export const wikiGroupKey = (owner: string, relation: string, direction?: string) => JSON.stringify([owner, relation, direction ?? ""]);

export function remainingWikiGroups(scene: Scene, summaries: ReadonlyMap<string, WikiDirectedGroup[]>, batch: number): WikiGroupPoint[] {
  const shown = new Map<string, Set<string>>();
  function remember(owner: string, kind: string, direction: string, neighbor: string) {
    const id = wikiGroupKey(owner, kind, direction);
    const ids = shown.get(id) ?? new Set<string>();
    ids.add(neighbor);
    shown.set(id, ids);
  }
  for (const e of scene.edges) {
    remember(e.source, e.kind, "outgoing", e.target);
    remember(e.target, e.kind, "incoming", e.source);
  }
  const points: WikiGroupPoint[] = [];
  for (const owner of scene.nodes) {
    for (const group of summaries.get(owner.id) ?? []) {
      const id = wikiGroupKey(owner.id, group.relation, group.direction);
      const remaining = group.neighbor_count - (shown.get(id)?.size ?? 0);
      if (group.neighbor_count > batch && remaining > 0) {
        points.push({ ...group, id, owner, remaining, label: wikiRelationText(group.relation) });
      }
    }
  }
  return points;
}
type PointNode = Node<{ item: Point; matched: boolean; color?: Connection["kind"] }>;
function NodePoint({ data, selected }: NodeProps<PointNode>) {
  const { item, color } = data;
  const [fill, stroke] = color ? relationColors[color] : ["--color-graph-entity", "--color-accent-fg"];
  return (
    <GraphPointNode label={item.label} count={"remaining" in item ? item.remaining : undefined}
      selected={selected} matched={data.matched} fill={`var(${fill})`} stroke={`var(${stroke})`} />
  );
}
const nodeTypes = { wiki: NodePoint },
  edgeTypes = { parallel: GraphParallelEdge };
interface Props {
  scene: Scene;
  root: string;
  matches: ReadonlySet<string>;
  selected?: string;
  edge?: string;
  groups: WikiGroupPoint[];
  onGroup: (item: WikiGroupPoint) => void;
  onZoom: (percent: number) => void;
  camera: RefObject<{ focusSelection: () => void; focusNode: (id: string) => void } | null>;
  onNode: (node: WikiNode) => void;
  onEdge: (e: Connection) => void;
}
function Canvas({ scene, root, matches, selected, edge, groups, onGroup, onZoom, camera, onNode, onEdge }: Props) {
  const flow = useReactFlow();
  const canvasWidth = useStore(s => s.width);
  const canvasHeight = useStore(s => s.height);
  const percent = useStore(s => Math.round(s.transform[2] * 100));
  useEffect(() => { onZoom(percent); }, [onZoom, percent]);
  const lastRoot = useRef("");

  const points = useMemo(() => [...scene.nodes, ...groups], [scene.nodes, groups]);
  const links: Connection[] = [...scene.edges, ...groups.map((g) => ({
    source: g.direction === "outgoing" ? g.owner.id : g.id,
    target: g.direction === "outgoing" ? g.id : g.owner.id, kind: g.relation,
  }))];
  const [term, setTerm] = useState("");
  const [cursor, setCursor] = useState(-1);
  const query = normalizeGraphSearch(term);
  const found = points.filter((p) => query && normalizeGraphSearch(p.label).includes(query));
  // 最初の輪は関連元、まとまり、入る関係を続けて並べる。追加入力は外側へ置き、元の輪を動かさない。
  const [firstRingSize] = useState(() => Math.max(0, Math.min(6, points.length - 1)));
  const [slots, setSlots] = useState(() => {
    const incoming = new Set(links.filter(e => e.target === root).map(e => e.source));
    const priority = (point: Point) => point.id === root ? 0 : "owner" in point ? 2 : incoming.has(point.id) ? 3 : 1;
    return new Map([...points].sort((a, b) => priority(a) - priority(b)).map((n, i) => [n.id, i]));
  });
  const added = points.filter((n) => !slots.has(n.id));
  if (added.length) {
    const next = new Map(slots);
    for (const n of added) next.set(n.id, next.size + 6 - firstRingSize);
    setSlots(next);
  }
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    const { width, height } = POINT_SIZE;
    const availableRadiusX = canvasWidth * (canvasWidth < 480 ? .38 : .31);
    points.forEach((node, fallback) => {
      const index = slots.get(node.id) ?? fallback;
      if (index === 0) {
        map.set(node.id, { x: 0, y: 0 });
        return;
      }
      let ring = 1,
        indexInRing = index - 1;
      while (indexInRing >= 6 * ring) {
        indexInRing -= 6 * ring;
        ring++;
      }
      const capacity = ring === 1 ? firstRingSize : 6 * ring;
      const theta = Math.PI * (1.1 - 2 * indexInRing / capacity);
      const radiusX = Math.max(ring === 1 ? width * .6 : width * 1.5, Math.min(availableRadiusX, width * 2.5));
      const radiusY = Math.max(height * 1.8, Math.min(canvasHeight * .27, height * 3));
      map.set(node.id, { x: Math.cos(theta) * ring * radiusX, y: Math.sin(theta) * ring * radiusY });
    });
    return map;
  }, [canvasHeight, canvasWidth, firstRingSize, points, slots]);
  const colors = new Map<string, Connection["kind"]>();
  for (const e of links) {
    if (e.source !== root && !colors.has(e.source)) colors.set(e.source, e.kind);
    if (e.target !== root && !colors.has(e.target)) colors.set(e.target, e.kind);
  }
  const foundIds = new Set(found.map((item) => item.id));
  const nodes: PointNode[] = points.map((item) => ({
    id: item.id, type: "wiki",
    data: { item, matched: query ? foundIds.has(item.id) : matches.has(item.id), color: colors.get(item.id) },
    position: positions.get(item.id)!,
    selected: item.id === selected,
    style: POINT_SIZE, initialWidth: POINT_SIZE.width, initialHeight: POINT_SIZE.height,
    ariaLabel: "remaining" in item
      ? t("wiki.search.groupNode", { relation: item.label, count: item.remaining })
      : `${t(`wiki.graph.kind.${item.kind}`)} · ${item.label}`,
  }));
  const bundles = directedGraphBundles(links, (e) => [e.source, e.target]);
  const edges: Edge[] = [...bundles.values()].map((members) => {
    const e = members[0];
    const id = edgeId(e);
    const { sourceHandle, targetHandle, orientation } = selectGraphEdgeHandles(
      positions.get(e.source)!,
      positions.get(e.target)!,
    );
    const reversed = bundles.has(JSON.stringify([e.target, e.source])) && e.source !== e.target;
    const counted = members.length > 1 || reversed;
    const label = counted ? t("wiki.search.edgeCount", { count: members.length }) : edgeName(e);
    return {
      id,
      source: e.source,
      target: e.target,
      sourceHandle,
      targetHandle,
      selected: id === edge,
      label: counted ? t("wiki.documents.count", { count: members.length }) : label,
      ariaLabel: label,
      className: counted ? "graph-edge-count" : canvasWidth < 480 && percent < 100 ? "graph-edge-label-on-focus" : undefined,
      markerEnd: GRAPH_EDGE_MARKER,
      type: "straight",
      ...(reversed
        ? {
            type: "parallel",
            data: {
              parallelOffset: canvasWidth < 480 ? 20 : orientation === "vertical" ? 44 : 32,
            },
          }
        : {}),
      style: graphEdgeStyle({
        emphasized: id === edge,
        width: e.cross ? 2 : 1.25,
        dash: e.kind === "contains" || groups.some(g => g.id === e.source || g.id === e.target) ? "4 3" : undefined,
      }),
      ...GRAPH_EDGE_LABEL_PROPS,
    };
  });
  useEffect(() => {
    if (!root || !canvasWidth || !canvasHeight || lastRoot.current === root) return;
    const frame = requestAnimationFrame(() => {
      lastRoot.current = root;
      void flow.fitView({ padding: 0.16, minZoom: 0, maxZoom: 1.1, duration: 0 });
    });
    return () => cancelAnimationFrame(frame);
  }, [canvasWidth, canvasHeight, flow, root]);
  function pick(id: string) {
    const item = points.find(p => p.id === id);
    if (item) {
      if ("owner" in item) onGroup(item);
      else onNode(item);
    }
  }
  function moveMatch(step: number) {
    if (!found.length) return;
    const start = cursor < 0 && step < 0 ? 0 : cursor;
    const next = (start + step + found.length) % found.length;
    setCursor(next);
    const target = found[next];
    pick(target.id);
    camera.current?.focusNode(target.id);
  }
  return (
    <GraphCanvasFrame className="@container h-full min-h-80 rounded-none border-0">
      <GraphCanvas
        nodes={nodes}
        edges={edges}
        fitView={false}
        overview
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        miniMap={false}
        onSelectNode={pick}
        onSelectEdge={(id) => {
          const e = scene.edges.find((x) => edgeId(x) === id);
          if (e) onEdge(e);
          else {
            const link = links.find(l => edgeId(l) === id);
            const group = link && groups.find(g => g.id === link.source || g.id === link.target);
            if (group) onGroup(group);
          }
        }}
      >
        <div className="absolute left-3 top-3 z-10 grid w-fit max-w-[calc(100%-1.5rem)] gap-2">
          <GraphSearchNavigation value={term} onChange={(s) => { setTerm(s); setCursor(-1); }}
            count={found.length} cursor={Math.max(0, cursor)} onPrevious={() => moveMatch(-1)} onNext={() => moveMatch(1)} scopeHintHidden
            labels={{ search: t("wiki.graph.search"), clear: t("wiki.search.clearFind"), previous: t("wiki.graph.previousMatch"), next: t("wiki.graph.nextMatch"), noMatch: t("wiki.graph.noMatch"), scope: t("wiki.search.findScope") }} />
          <div className="flex w-fit items-center gap-2 rounded-md border border-border bg-surface px-2 py-1 text-xs text-fg-muted" aria-label={t("wiki.graph.legend")}>
            <span className="inline-flex items-center gap-1"><span aria-hidden data-surface-tint="accent" className="size-3 rounded-full border-2 border-accent-fg bg-accent-muted"/>{t("wiki.search.selectedPoint")}</span>
            <span className="inline-flex items-center gap-1"><span aria-hidden data-surface-tint="warning" className="size-3 rounded-full border border-warning-fg bg-warning-subtle"/>{t("wiki.search.matchedPoint")}</span>
          </div>
        </div>
        <GraphCanvasControls
          key={root} placement="bottom" controlsRef={camera}
          selectedId={selected && positions.has(selected) ? selected : null}
          selectedEdgeId={edges.some((item) => item.id === edge) ? edge : undefined}
          labels={{
            zoomIn: t("wiki.graph.zoomIn"),
            zoomOut: t("wiki.graph.zoomOut"),
            fit: t("wiki.graph.fitView"),
            reset: t("wiki.graph.reset"),
          }}
          onFit={() => void flow.fitView({ padding: 0.15, minZoom: 0, maxZoom: 1 })}
        />
      </GraphCanvas>
    </GraphCanvasFrame>
  );
}
export function WikiScene(props: Props) {
  return (
    <GraphCanvasProvider>
      <Canvas key={props.root} {...props} />
    </GraphCanvasProvider>
  );
}
