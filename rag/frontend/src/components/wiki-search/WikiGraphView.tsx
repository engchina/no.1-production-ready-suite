import { forwardRef, useMemo, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  GraphExplorer,
  type GraphExplorerEdge,
  type GraphExplorerEdgeKind,
  type GraphExplorerHandle,
  type GraphExplorerLabels,
  type GraphExplorerNode,
  type GraphExplorerNodeKind,
} from "@production-ready/ui/graph-explorer";
import { t } from "@/lib/i18n";
import { wikiRelationText } from "@/components/knowledge-bases/wiki-graph-labels";
import { WIKI_GRAPH_SETTINGS } from "./wiki-graph-settings";
import { drawnWikiGraph, type WikiGraphNode, type WikiGraphState } from "./wiki-graph-model";
import { expandKinds, WIKI_RELATION_KINDS } from "./wiki-graph-loaders";
import type { WikiScope } from "./wiki-api";

/**
 * 種類 = 塗りと大きさ（C 案。文書 44・章節 36・原文 28）。枠は同じ系統の濃い色。
 * 「選んだ原文」は Wiki 検索の図だけの種類（#5981）: 原文と同じ系統の色で大きく（40）、外側の輪（`matched`）も付ける。
 * 凡例に件数つきで出て、押すと選んだ原文をすべて選ぶ。図の選択（太い枠）とは別の印で、色だけに頼らない。
 */
const PICKED_KIND = "picked";
const NODE_KINDS: GraphExplorerNodeKind[] = [
  { id: "document", label: t("wiki.graph.kind.document"), color: "--color-graph-entity", lineColor: "--color-graph-entity-line", size: 44 },
  { id: "section", label: t("wiki.graph.kind.section"), color: "--color-graph-metric", lineColor: "--color-graph-metric-line", size: 36 },
  { id: PICKED_KIND, label: t("wiki.graph.kind.picked"), color: "--color-graph-term", lineColor: "--color-graph-term-line", size: 40 },
  { id: "unit", label: t("wiki.graph.kind.unit"), color: "--color-graph-term", lineColor: "--color-graph-term-line", size: 28 },
  { id: "more", label: t("wiki.graph.kind.more"), color: "--color-graph-default", lineColor: "--color-graph-default-line", size: 26, shape: "round-rectangle", dashed: true, legendHidden: true },
];

/** 構成は実線で文字を出さず、案内の依存・参照は点線で種類の文字を出す（色だけで区別しない）。 */
const EDGE_KINDS: GraphExplorerEdgeKind[] = [
  { id: "contains", label: t("wiki.search.children") },
  ...WIKI_RELATION_KINDS.map((kind) => ({ id: kind, label: wikiRelationText(kind), dashed: true, labelled: true })),
  { id: "more", label: t("wiki.graph.kind.more"), dashed: true },
];

export function wikiNodeTitle(node: WikiGraphNode): string {
  if (node.kind === "more") return node.remaining ? t("wiki.graph.moreNode", { count: node.remaining }) : t("wiki.graph.moreNodeUnknown");
  return node.kind === "unit" && node.document_name ? `${node.label}（${node.document_name}）` : node.label;
}

function explorerLabels(): GraphExplorerLabels {
  return {
    canvas: t("wiki.graph.canvas"),
    keyboardHint: t("wiki.graph.keyboardHint"),
    nodeKinds: t("wiki.graph.nodeKinds"),
    edgeKinds: t("wiki.graph.edgeKinds"),
    selectKindHint: (kind) => t("wiki.graph.selectKind", { kind }),
    zoomIn: t("wiki.graph.zoomIn"),
    zoomOut: t("wiki.graph.zoomOut"),
    fit: t("wiki.graph.fitView"),
    focus: t("wiki.graph.focusSelection"),
    previous: t("wiki.graph.previousView"),
    relayout: t("wiki.graph.reset"),
    menu: t("wiki.graph.menu"),
    expand: t("wiki.graph.expandNeighbors"),
    expandByKind: t("wiki.graph.expandByKind"),
    collapse: t("wiki.graph.collapse"),
    details: t("wiki.graph.openDetail"),
    remove: t("wiki.graph.removeNode"),
    removeSelected: (count) => t("wiki.graph.removeSelected", { count }),
    keepSelected: t("wiki.graph.keepSelected"),
    connectSelected: t("wiki.graph.connectSelected"),
    center: t("wiki.graph.centerNode"),
    announce: (text) => t("wiki.graph.announce", { text }),
    expandableState: t("wiki.graph.expandableState"),
    expandedState: t("wiki.graph.expandedState"),
    selectedCount: (count) => t("wiki.graph.selectedCount", { count }),
  };
}

interface Props {
  scope: WikiScope;
  state: WikiGraphState;
  selected: readonly string[];
  onSelect: (ids: string[]) => void;
  onExpand: (id: string, kinds?: readonly string[]) => void;
  onCollapse: (id: string) => void;
  onRemove: (ids: readonly string[]) => void;
  onKeepOnly?: (ids: readonly string[]) => void;
  onConnect: (ids: readonly string[]) => void;
  onOpen?: (id: string) => void;
  toolbar?: ReactNode;
  /** 強調した原文（`state.matched`）を「選んだ原文」の種類で描く（Wiki 検索の図）。 */
  markPicked?: boolean;
}

/** Wiki の図（Cytoscape の共有画布）。状態は呼び出し側の `useWikiGraph` が持つ。 */
export const WikiGraphView = forwardRef<GraphExplorerHandle, Props>(function WikiGraphView(
  { scope, state, selected, onSelect, onExpand, onCollapse, onRemove, onKeepOnly, onConnect, onOpen, toolbar, markPicked = false },
  ref,
) {
  const client = useQueryClient();
  const labels = useMemo(explorerLabels, []);
  const drawn = useMemo(() => drawnWikiGraph(state), [state]);
  const nodes: GraphExplorerNode[] = useMemo(() => drawn.nodes.map((node) => {
    const expansion = state.expansions.get(node.id);
    return {
      id: node.id,
      kind: markPicked && node.kind === "unit" && state.matched.has(node.id) ? PICKED_KIND : node.kind,
      label: node.kind === "more" ? wikiNodeTitle(node) : node.label,
      title: wikiNodeTitle(node),
      matched: state.matched.has(node.id),
      expandable: node.kind !== "more" && (!expansion || !expansion.complete || expansion.neighbors.some((item) => !state.nodes.has(item.node.id))),
      expanded: Boolean(expansion && [...state.owners.values()].includes(node.id)),
    };
  }), [drawn, markPicked, state]);
  const edges: GraphExplorerEdge[] = useMemo(() => drawn.edges.map((edge) => ({
    id: edge.id, source: edge.source, target: edge.target, kind: edge.kind, strong: edge.scope === "cross_document",
  })), [drawn]);
  return (
    <GraphExplorer
      ref={ref}
      testId="wiki-graph"
      nodes={nodes}
      edges={edges}
      nodeKinds={NODE_KINDS}
      edgeKinds={EDGE_KINDS}
      labels={labels}
      layoutKey={String(state.generation)}
      selectedIds={selected}
      onSelectionChange={onSelect}
      onExpand={WIKI_GRAPH_SETTINGS.activate === "expand" ? onExpand : (id) => onOpen?.(id)}
      onCollapse={onCollapse}
      onRemove={onRemove}
      onKeepOnly={onKeepOnly}
      onConnect={onConnect}
      onOpen={onOpen}
      expandOptions={(id) => expandKinds(client, scope, state.nodes.get(id)).map((kind) => ({
        id: kind, label: kind === "contains" ? t("wiki.search.children") : wikiRelationText(kind as (typeof WIKI_RELATION_KINDS)[number]),
      }))}
      toolbar={toolbar}
    />
  );
});
