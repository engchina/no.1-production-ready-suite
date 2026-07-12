import { useMemo } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import {
  Background,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { Button } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { layoutOntologyGraph } from "./graphLayout";
import { cssVar, edgeStroke, nodeFill, nodeStroke } from "./graphPalette";
import type { OntologyGraph, OntologyNode } from "./types";

interface OntologyGraphCanvasProps {
  graph: OntologyGraph;
  selectedNodeId?: string | null;
  onSelectNode?: (nodeId: string) => void;
}

function nodeShape(node: OntologyNode): string {
  if (node.kind === "metric") return "18px";
  if (node.kind === "validation_finding") return "3px";
  return "8px";
}

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

function OntologyFlow({ graph, selectedNodeId, onSelectNode }: OntologyGraphCanvasProps) {
  const layout = useMemo(() => layoutOntologyGraph(graph), [graph]);
  const nodes = useMemo<Node[]>(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        position: layout.get(node.id) ?? { x: 0, y: 0 },
        data: { label: node.business_name_ja },
        ariaLabel: `${node.business_name_ja}、${node.kind}、${node.validation_status ?? "未検証"}`,
        selected: node.id === selectedNodeId,
        style: {
          width: 190,
          minHeight: 54,
          // 型=塗り / 状態=枠色（選択時は primary）。blocked/選択は枠を太く（color-not-only）。
          border: `${node.id === selectedNodeId || node.validation_status === "blocked" ? 2 : 1}px solid ${nodeStroke(node, node.id === selectedNodeId)}`,
          borderRadius: nodeShape(node),
          background: nodeFill(node),
          color: cssVar("--graph-fg"),
          fontSize: 13,
          fontWeight: 600,
          padding: "9px 12px",
        },
      })),
    [graph.nodes, layout, selectedNodeId]
  );
  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((edge) => ({
        id: edge.id,
        source: edge.source_node_id,
        target: edge.target_node_id,
        label: edge.relationship_name_ja,
        ariaLabel: edge.relationship_name_ja,
        markerEnd: { type: MarkerType.ArrowClosed, color: cssVar("--graph-line") },
        style: {
          stroke: edgeStroke(edge),
          strokeWidth: edge.validation_status === "blocked" ? 2 : 1.25,
          strokeDasharray: edge.review_status === "proposed" ? "5 4" : undefined,
        },
        labelStyle: { fill: cssVar("--muted"), fontSize: 11, fontWeight: 600 },
        labelBgStyle: { fill: cssVar("--card"), fillOpacity: 0.92 },
      })),
    [graph.edges]
  );

  return (
    <div className="relative h-[32rem] min-h-80 overflow-hidden rounded-md border border-border bg-background">
      <ReactFlow
        nodes={nodes}
        edges={edges}
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
        proOptions={{ hideAttribution: true }}
      >
        <Background color={cssVar("--border")} gap={20} size={1} />
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
