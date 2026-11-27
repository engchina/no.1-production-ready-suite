import { forwardRef, useMemo, useState, type ReactNode } from "react";
import { expandedGraphNeighborhood, graphNeighborGroups } from "@production-ready/ui/graph";
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
import { ontologyNodeDisplay } from "./nodeDisplay";
import { ontologyRelationshipKindLabel } from "./relationshipKind";
import type { OntologyGraph } from "./types";

/** 最初に出す同じ種類の隣接の数（残りは「さらに展開」の点）と、1 回の展開で足す数。 */
const FIRST_BATCH = 12;
const EXPAND_BATCH = 25;

/** 種類 = 塗りと大きさ（凡例の 4 つの分類と、そのほか）。 */
const KIND_GROUPS: Record<string, string> = {
  business_entity: "business", object_type: "business", interface: "business", object_set: "business", business_event: "business", business_term: "business", business_rule: "business",
  schema: "physical", table: "physical", view: "physical",
  property: "attribute", shared_property: "attribute", column: "attribute", enum_value: "attribute", value_type: "attribute", enumeration: "attribute",
  metric: "metric", function: "metric",
};
const kindOf = (kind: string) => KIND_GROUPS[kind] ?? "other";

function nodeKinds(): GraphExplorerNodeKind[] {
  return [
    { id: "business", label: t("nl2sql.ontology.legend.business"), color: "--color-graph-entity", lineColor: "--color-graph-entity-line", size: 44 },
    { id: "physical", label: t("nl2sql.ontology.legend.physical"), color: "--color-graph-sql", lineColor: "--color-graph-default-line", size: 40, shape: "round-rectangle" },
    { id: "attribute", label: t("nl2sql.ontology.legend.attribute"), color: "--color-graph-term", lineColor: "--color-graph-term-line", size: 28 },
    { id: "metric", label: t("nl2sql.ontology.legend.metric"), color: "--color-graph-metric", lineColor: "--color-graph-metric-line", size: 36, shape: "round-diamond" },
    { id: "other", label: t("nl2sql.ontology.legend.other"), color: "--color-graph-default", lineColor: "--color-graph-default-line", size: 30 },
    { id: "more", label: t("nl2sql.ontology.graphMore"), color: "--color-graph-default", lineColor: "--color-graph-default-line", size: 26, shape: "round-rectangle", dashed: true, legendHidden: true },
  ];
}

/** 包含は実線で文字なし（数が多い）、ほかは種類の文字。物理への対応は点線で、外部キーの実線と区別する。 */
const SOLID_KINDS = new Set(["contains", "physical_contains", "foreign_key", "joins"]);
const UNLABELLED_KINDS = new Set(["contains", "physical_contains", "more"]);

function labels(): GraphExplorerLabels {
  return {
    canvas: t("nl2sql.ontology.graphCanvas"),
    keyboardHint: t("nl2sql.ontology.graphKeyboardHint"),
    nodeKinds: t("nl2sql.ontology.legendLabel"),
    edgeKinds: t("nl2sql.ontology.graphEdgeKinds"),
    selectKindHint: (kind) => t("nl2sql.ontology.graphSelectKind", { kind }),
    zoomIn: t("nl2sql.ontology.graphZoomIn"),
    zoomOut: t("nl2sql.ontology.graphZoomOut"),
    fit: t("nl2sql.ontology.graphFit"),
    focus: t("nl2sql.ontology.graphFocusSelection"),
    previous: t("nl2sql.ontology.graphPreviousView"),
    relayout: t("nl2sql.ontology.graphResetLayout"),
    menu: t("nl2sql.ontology.graphMenu"),
    expand: t("nl2sql.ontology.graphExpandNeighbors"),
    expandByKind: t("nl2sql.ontology.graphExpandByKind"),
    collapse: t("nl2sql.ontology.graphCollapse"),
    details: t("nl2sql.ontology.graphOpenDetail"),
    remove: t("nl2sql.ontology.graphRemoveNode"),
    removeSelected: (count) => t("nl2sql.ontology.graphRemoveSelected", { count }),
    keepSelected: t("nl2sql.ontology.graphKeepSelected"),
    connectSelected: t("nl2sql.ontology.graphConnectSelected"),
    center: t("nl2sql.ontology.graphCenterNode"),
    announce: (text) => t("nl2sql.ontology.graphSelectionAnnouncement", { label: text }),
    expandableState: t("nl2sql.ontology.graphExpandableState"),
    expandedState: t("nl2sql.ontology.graphExpandedState"),
    selectedCount: (count) => t("nl2sql.ontology.graphSelectedCount", { count }),
  };
}

const groupKind = (edge: OntologyGraph["edges"][number]) => edge.kind || edge.relationship_name_ja;

interface Props {
  graph: OntologyGraph;
  /** 検索の一致・接地の強調（外側の輪）。 */
  matched: ReadonlySet<string>;
  /** 隠さないノード（選択・検索の一致・接地）。 */
  pinned: ReadonlySet<string>;
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
  onSelectNode?: (id: string | null) => void;
  onSelectEdge?: (id: string, members?: readonly string[]) => void;
  toolbar?: ReactNode;
  layoutKey: string;
}

/**
 * 概念図の「全体」（Cytoscape の探索の画布。#5922）。同じ種類の隣接が多いノードは最初に 12 件を出し、
 * 残りは「さらに展開」の点にする。ダブルクリック・Enter・メニューで 25 件ずつ足し、外したノードは
 * 「選択したノードだけ残す」などで減らせる。物理 ER と接地の経路は React Flow のまま。
 */
export const OntologyConceptExplorer = forwardRef<GraphExplorerHandle, Props>(function OntologyConceptExplorer(
  { graph, matched, pinned, selectedNodeId, selectedEdgeId, onSelectNode, onSelectEdge, toolbar, layoutKey },
  ref,
) {
  const kinds = useMemo(nodeKinds, []);
  const text = useMemo(labels, []);
  const [limits, setLimits] = useState<ReadonlyMap<string, number>>(() => new Map());
  const [removed, setRemoved] = useState<ReadonlySet<string>>(() => new Set());
  const [extraEdges, setExtraEdges] = useState<ReadonlySet<string>>(() => new Set());
  const groups = useMemo(() => graphNeighborGroups(graph.edges.map((e) => ({ source: e.source_node_id, target: e.target_node_id, kind: groupKind(e) })), FIRST_BATCH), [graph.edges]);
  const limitOf = (id: string) => limits.get(id) ?? FIRST_BATCH;
  const visible = useMemo(() => {
    const shown = new Map(groups.map((g) => [g.id, limits.get(g.id) ?? FIRST_BATCH]));
    const { nodes: ids, showsRelation } = expandedGraphNeighborhood(graph.nodes.map((n) => n.id), groups, shown, pinned);
    for (const id of removed) if (!pinned.has(id)) ids.delete(id);
    const nodes = graph.nodes.filter((n) => ids.has(n.id));
    const edges = graph.edges.filter((e) => ids.has(e.source_node_id) && ids.has(e.target_node_id)
      && (extraEdges.has(e.id) || showsRelation(e.source_node_id, e.target_node_id, groupKind(e))));
    return { nodes, edges, ids };
  }, [graph, groups, limits, pinned, removed, extraEdges]);

  const more = groups.flatMap((group) => {
    if (!visible.ids.has(group.owner)) return [];
    const remaining = group.neighbors.filter((id) => !visible.ids.has(id)).length;
    return remaining > 0 ? [{ group, remaining }] : [];
  });
  const owners = new Set(more.map((item) => item.group.owner));
  const expandedOwners = new Set(groups.filter((g) => limitOf(g.id) > FIRST_BATCH).map((g) => g.owner));
  const nodes: GraphExplorerNode[] = [
    ...visible.nodes.map((node) => {
      const display = ontologyNodeDisplay(node);
      return {
        id: node.id,
        kind: kindOf(node.kind),
        label: display.primaryLabel,
        title: display.ariaLabel,
        matched: matched.has(node.id),
        expandable: owners.has(node.id),
        expanded: expandedOwners.has(node.id),
        tone: node.validation_status === "blocked" ? "danger" as const : node.validation_status === "warning" ? "warning" as const : undefined,
      };
    }),
    ...more.map(({ group, remaining }) => ({
      id: `more:${group.id}`,
      kind: "more",
      label: t("nl2sql.ontology.graphMoreCount", { count: remaining }),
      title: t(group.outgoing ? "nl2sql.ontology.graphGroupOut" : "nl2sql.ontology.graphGroupIn", {
        name: ontologyNodeDisplay(graph.nodes.find((n) => n.id === group.owner)!).primaryLabel,
        relation: ontologyRelationshipKindLabel(group.kind, group.kind), count: remaining,
      }),
    })),
  ];
  const edges: GraphExplorerEdge[] = [
    ...visible.edges.map((edge) => ({ id: edge.id, source: edge.source_node_id, target: edge.target_node_id, kind: edge.kind || "other", strong: edge.validation_status === "blocked" })),
    ...more.map(({ group }) => ({ id: `more-edge:${group.id}`, source: group.owner, target: `more:${group.id}`, kind: "more" })),
  ];
  const edgeKinds: GraphExplorerEdgeKind[] = useMemo(() => {
    const present = [...new Set(graph.edges.map((e) => e.kind || "other"))];
    return [...present, "more"].map((kind) => ({
      id: kind,
      label: kind === "more" ? t("nl2sql.ontology.graphMore") : kind === "other" ? t("ontologyPlayground.inspector.edgeKindFallback") : ontologyRelationshipKindLabel(kind),
      dashed: !SOLID_KINDS.has(kind),
      labelled: !UNLABELLED_KINDS.has(kind),
    }));
  }, [graph.edges]);

  function expand(id: string, onlyKinds?: readonly string[]) {
    const targets = id.startsWith("more:")
      ? groups.filter((g) => `more:${g.id}` === id)
      : groups.filter((g) => g.owner === id && (!onlyKinds || onlyKinds.includes(g.kind)));
    if (!targets.length) return;
    setLimits((old) => {
      const next = new Map(old);
      for (const g of targets) next.set(g.id, Math.min(g.neighbors.length, (old.get(g.id) ?? FIRST_BATCH) + EXPAND_BATCH));
      return next;
    });
    setRemoved((old) => new Set([...old].filter((removedId) => !targets.some((g) => g.neighbors.includes(removedId)))));
  }
  function collapse(id: string) {
    setLimits((old) => {
      const next = new Map(old);
      for (const g of groups) if (g.owner === id) next.delete(g.id);
      return next;
    });
  }

  return (
    <GraphExplorer
      ref={ref}
      testId="ontology-concept-graph"
      nodes={nodes}
      edges={edges}
      nodeKinds={kinds}
      edgeKinds={edgeKinds}
      labels={text}
      layoutKey={layoutKey}
      selectedIds={selectedEdgeId ? [selectedEdgeId] : selectedNodeId ? [selectedNodeId] : []}
      onSelectionChange={(ids) => {
        const nodeId = ids.find((id) => visible.ids.has(id));
        const edgeId = ids.find((id) => visible.edges.some((e) => e.id === id));
        if (ids.length === 1 && edgeId) onSelectEdge?.(edgeId);
        else if (ids.length === 1 && nodeId) onSelectNode?.(nodeId);
        else if (!ids.length) onSelectNode?.(null);
      }}
      onExpand={expand}
      onCollapse={collapse}
      onRemove={(ids) => setRemoved((old) => new Set([...old, ...ids]))}
      onKeepOnly={(ids) => setRemoved(new Set(visible.nodes.map((n) => n.id).filter((id) => !ids.includes(id))))}
      onConnect={(ids) => setExtraEdges((old) => new Set([...old, ...graph.edges.filter((e) => ids.includes(e.source_node_id) && ids.includes(e.target_node_id)).map((e) => e.id)]))}
      expandOptions={(id) => {
        const owned = groups.filter((g) => g.owner === id && g.neighbors.some((n) => !visible.ids.has(n)));
        return [...new Set(owned.map((g) => g.kind))].map((kind) => ({ id: kind, label: ontologyRelationshipKindLabel(kind, kind) }));
      }}
      toolbar={toolbar}
    />
  );
});
