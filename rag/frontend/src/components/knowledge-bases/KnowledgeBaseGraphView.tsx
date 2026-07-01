"use client";

import { useMemo, useState } from "react";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Share2 } from "lucide-react";

import { EmptyState, ErrorState } from "@/components/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import type { KnowledgeBaseGraphData } from "@/lib/api";
import { useKnowledgeBaseGraph } from "@/lib/queries";
import { t } from "@/lib/i18n";

const NODE_STYLE = {
  background: "var(--card)",
  color: "var(--foreground)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  fontSize: 12,
  padding: 6,
  maxWidth: 160,
  textAlign: "center" as const,
};

function toFlow(data: KnowledgeBaseGraphData): { nodes: Node[]; edges: Edge[] } {
  const count = Math.max(1, data.nodes.length);
  const radius = Math.max(140, count * 26);
  const nodes: Node[] = data.nodes.map((node, index) => {
    const angle = (index / count) * 2 * Math.PI;
    return {
      id: node.id,
      position: { x: radius + radius * Math.cos(angle), y: radius + radius * Math.sin(angle) },
      data: { label: node.name || node.id },
      style: NODE_STYLE,
    };
  });
  const edges: Edge[] = data.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.type ?? undefined,
    labelStyle: { fill: "var(--muted)", fontSize: 10 },
  }));
  return { nodes, edges };
}

/**
 * KB の関係情報(GraphRAG)を可視化する読み取り専用ビュー。
 * 展開時のみ subgraph を取得し、@xyflow で entity(node)/relationship(edge) を円形配置で描画する。
 */
export function KnowledgeBaseGraphView({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const [open, setOpen] = useState(false);
  const query = useKnowledgeBaseGraph(open ? knowledgeBaseId : null);
  const flow = useMemo(
    () => (query.data ? toFlow(query.data) : { nodes: [], edges: [] }),
    [query.data]
  );
  const isEmpty = query.data && (query.data.status === "empty" || query.data.nodes.length === 0);

  return (
    <section className="rounded-md border border-border bg-card">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 w-full cursor-pointer items-center gap-2 px-4 text-sm font-semibold text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Share2 size={15} className="text-primary" aria-hidden />
        {open ? t("knowledgeBases.graph.hide") : t("knowledgeBases.graph.show")}
      </button>
      {open ? (
        <div className="space-y-2 px-4 pb-4">
          <p className="text-xs text-muted">{t("knowledgeBases.graph.hint")}</p>
          {query.isPending ? (
            <Skeleton className="h-[360px] w-full rounded-md" />
          ) : query.isError ? (
            <ErrorState message={t("knowledgeBases.graph.error")} onRetry={() => void query.refetch()} />
          ) : isEmpty ? (
            <EmptyState
              title={t("knowledgeBases.graph.empty")}
              hint={t("knowledgeBases.graph.emptyHint")}
            />
          ) : (
            <>
              <div
                role="region"
                aria-label={t("knowledgeBases.graph.title")}
                className="h-[360px] w-full overflow-hidden rounded-md border border-border bg-background"
              >
                <ReactFlow
                  nodes={flow.nodes}
                  edges={flow.edges}
                  fitView
                  nodesDraggable={false}
                  nodesConnectable={false}
                  elementsSelectable={false}
                  zoomOnScroll={false}
                  preventScrolling={false}
                >
                  <Background />
                  <Controls showInteractive={false} />
                </ReactFlow>
              </div>
              {query.data?.truncated ? (
                <p className="text-xs text-muted">{t("knowledgeBases.graph.truncated")}</p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
