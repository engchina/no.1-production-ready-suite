import { useEffect, useMemo, useRef } from "react";
import { BookOpen, FileText, Heading } from "lucide-react";
import { useReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import {
  GraphCanvas,
  GraphCanvasControls,
  GraphCanvasFrame,
  GraphCanvasProvider,
  GraphNodeCard,
  GraphParallelEdge,
  graphEdgeStyle,
  selectGraphEdgeHandles,
  GRAPH_EDGE_LABEL_PROPS,
  GRAPH_EDGE_MARKER,
  GRAPH_NODE_CARD_SIZE,
} from "@production-ready/ui/graph";
import "@xyflow/react/dist/style.css";
import { t } from "@/lib/i18n";
import { wikiRelationText } from "@/components/knowledge-bases/wiki-graph-labels";
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

type CardNode = Node<{ item: WikiNode }>;
function NodeCard({ data, selected }: NodeProps<CardNode>) {
  const n = data.item;
  return (
    <GraphNodeCard
      icon={n.kind === "document" ? FileText : n.kind === "section" ? Heading : BookOpen}
      kindLabel={t(`wiki.graph.kind.${n.kind}`)}
      primary={n.label}
      fill={
        n.kind === "section" ? "var(--color-graph-teal, var(--color-surface-sunken))" : "var(--color-surface)"
      }
      selected={selected}
    />
  );
}
const nodeTypes = { wiki: NodeCard },
  edgeTypes = { parallel: GraphParallelEdge };
interface Props {
  scene: Scene;
  root: string;
  selected?: string;
  edge?: string;
  onNode: (node: WikiNode) => void;
  onEdge: (e: Connection) => void;
}
function Canvas({ scene, root, selected, edge, onNode, onEdge }: Props) {
  const flow = useReactFlow();
  const lastRoot = useRef("");
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    const { width, height } = GRAPH_NODE_CARD_SIZE;
    scene.nodes.forEach((node, index) => {
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
      const theta =
        ((indexInRing % 2 === 0 ? Math.floor(indexInRing / 2) : 3 * ring + Math.floor(indexInRing / 2)) *
          Math.PI) /
          (3 * ring) -
        Math.PI / 6;
      map.set(node.id, { x: Math.cos(theta) * ring * width * 1.6, y: Math.sin(theta) * ring * height * 2.3 });
    });
    return map;
  }, [scene.nodes]);
  const nodes: CardNode[] = scene.nodes.map((item) => ({
    id: item.id,
    type: "wiki",
    data: { item },
    position: positions.get(item.id)!,
    selected: item.id === selected,
    style: { width: GRAPH_NODE_CARD_SIZE.width },
    ariaLabel: `${t(`wiki.graph.kind.${item.kind}`)} · ${item.label}`,
  }));
  const pairs = new Map<string, Connection[]>();
  for (const e of scene.edges) {
    const k = [e.source, e.target].sort().join(":");
    pairs.set(k, [...(pairs.get(k) ?? []), e]);
  }
  const edges: Edge[] = scene.edges.map((e) => {
    const id = edgeId(e);
    const { sourceHandle, targetHandle, orientation } = selectGraphEdgeHandles(
      positions.get(e.source)!,
      positions.get(e.target)!,
    );
    const group = pairs.get([e.source, e.target].sort().join(":"))!;
    return {
      id,
      source: e.source,
      target: e.target,
      sourceHandle,
      targetHandle,
      selected: id === edge,
      label: edgeName(e),
      ariaLabel: edgeName(e),
      markerEnd: GRAPH_EDGE_MARKER,
      ...(group.length > 1
        ? {
            type: "parallel",
            data: {
              parallelOffset:
                (group.indexOf(e) - (group.length - 1) / 2) *
                (orientation === "vertical" ? 110 : 42) *
                (e.source < e.target ? 1 : -1),
            },
          }
        : {}),
      style: graphEdgeStyle({
        emphasized: id === edge,
        width: e.cross ? 2 : 1.25,
        dash: e.kind === "contains" ? "4 3" : undefined,
      }),
      ...GRAPH_EDGE_LABEL_PROPS,
    };
  });
  useEffect(() => {
    if (!root || lastRoot.current === root) return;
    lastRoot.current = root;
    const frame = requestAnimationFrame(() => {
      void flow.setCenter(GRAPH_NODE_CARD_SIZE.width / 2, GRAPH_NODE_CARD_SIZE.height / 2, { zoom: 0.8 });
    });
    return () => {
      cancelAnimationFrame(frame);
      lastRoot.current = "";
    };
  }, [flow, root]);
  return (
    <GraphCanvasFrame className="h-full min-h-80 rounded-none border-0">
      <GraphCanvas
        nodes={nodes}
        edges={edges}
        fitView={false}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        miniMap={false}
        onSelectNode={(id) => {
          const n = scene.nodes.find((x) => x.id === id);
          if (n) onNode(n);
        }}
        onSelectEdge={(id) => {
          const e = scene.edges.find((x) => edgeId(x) === id);
          if (e) onEdge(e);
        }}
      >
        <GraphCanvasControls
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
      <Canvas {...props} />
    </GraphCanvasProvider>
  );
}
