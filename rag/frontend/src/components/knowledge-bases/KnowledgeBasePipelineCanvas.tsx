"use client";

import { useMemo, useState } from "react";
import { Background, Controls, Position, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Workflow } from "lucide-react";

import type { KnowledgeBaseAdapterConfig } from "@/lib/api";
import { ja, t, type I18nKey } from "@/lib/i18n";
import { parserBackendLabel } from "@/lib/source-profile-labels";

const NODE_STYLE = {
  background: "var(--card)",
  color: "var(--foreground)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  fontSize: 12,
  width: 168,
  padding: 8,
  textAlign: "left" as const,
};

/** 動的 i18n キーが無ければ生値へフォールバック(t は未定義キーで undefined を返すため)。 */
function valueLabel(key: I18nKey, raw: string | null): string {
  if (!raw) return "—";
  return key in ja ? t(key) : raw;
}

function nodeLabel(name: string, value: string) {
  return (
    <div>
      <div style={{ fontWeight: 600 }}>{name}</div>
      <div style={{ marginTop: 2, fontSize: 11, color: "var(--muted)" }}>{value}</div>
    </div>
  );
}

function buildGraph(config: KnowledgeBaseAdapterConfig): { nodes: Node[]; edges: Edge[] } {
  const ing = config.ingestion;
  const nodes: Node[] = [
    {
      id: "preprocess",
      position: { x: 0, y: 60 },
      data: {
        label: nodeLabel(
          t("settings.pipelineCanvas.stage.preprocess"),
          valueLabel(
            `settings.preprocess.profile.${ing.preprocess_profile}` as I18nKey,
            ing.preprocess_profile
          )
        ),
      },
      style: NODE_STYLE,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    },
    {
      id: "parser",
      position: { x: 210, y: 60 },
      data: {
        label: nodeLabel(
          t("settings.pipelineCanvas.stage.parser"),
          ing.parser_adapter_backend ? parserBackendLabel(ing.parser_adapter_backend) : "—"
        ),
      },
      style: NODE_STYLE,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    },
    {
      id: "chunking",
      position: { x: 420, y: 60 },
      data: {
        label: nodeLabel(
          t("settings.pipelineCanvas.stage.chunking"),
          valueLabel(
            `settings.chunking.strategy.${ing.chunking_strategy}` as I18nKey,
            ing.chunking_strategy
          )
        ),
      },
      style: NODE_STYLE,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    },
    {
      id: "index",
      position: { x: 630, y: 60 },
      data: {
        label: nodeLabel(
          t("settings.pipelineCanvas.stage.index"),
          t("settings.pipelineCanvas.indexValue")
        ),
      },
      style: NODE_STYLE,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    },
  ];
  const edges: Edge[] = [
    { id: "e1", source: "preprocess", target: "parser" },
    { id: "e2", source: "parser", target: "chunking" },
    { id: "e3", source: "chunking", target: "index" },
  ];

  // 索引から派生する任意レイヤ(有効時のみ)。
  const optional: { id: string; key: I18nKey; on: boolean; value?: string }[] = [
    {
      id: "graph",
      key: "settings.pipelineCanvas.stage.graph",
      on: Boolean(ing.graph_profile && ing.graph_profile !== "off"),
      value: ing.graph_profile ?? undefined,
    },
    { id: "field", key: "settings.pipelineCanvas.stage.field", on: Boolean(ing.field_extraction_enabled) },
    { id: "asset", key: "settings.pipelineCanvas.stage.asset", on: Boolean(ing.asset_summary_enabled) },
    {
      id: "navigation",
      key: "settings.pipelineCanvas.stage.navigation",
      on: Boolean(ing.navigation_summary_enabled),
    },
  ];
  const enabled = optional.filter((item) => item.on);
  enabled.forEach((item, index) => {
    nodes.push({
      id: item.id,
      position: { x: 850, y: index * 80 },
      data: { label: nodeLabel(t(item.key), item.value ?? "") },
      style: NODE_STYLE,
      targetPosition: Position.Left,
    });
    edges.push({ id: `eo-${item.id}`, source: "index", target: item.id });
  });

  return { nodes, edges };
}

/**
 * 取込パイプラインの読み取り専用ノード図(高度な診断)。
 * 既存の effective adapter config を可視化するだけ(編集は通常の構築設定 UI で行う)。
 */
export function KnowledgeBasePipelineCanvas({ config }: { config: KnowledgeBaseAdapterConfig }) {
  const [open, setOpen] = useState(false);
  const { nodes, edges } = useMemo(() => buildGraph(config), [config]);

  return (
    <section className="rounded-md border border-border bg-card">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 w-full cursor-pointer items-center gap-2 px-4 text-sm font-semibold text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Workflow size={15} className="text-primary" aria-hidden />
        {open ? t("settings.pipelineCanvas.hide") : t("settings.pipelineCanvas.show")}
      </button>
      {open ? (
        <div className="space-y-2 px-4 pb-4">
          <p className="text-xs text-muted">{t("settings.pipelineCanvas.hint")}</p>
          <div
            role="region"
            className="h-[360px] w-full overflow-hidden rounded-md border border-border bg-background"
            aria-label={t("settings.pipelineCanvas.title")}
          >
            <ReactFlow
              nodes={nodes}
              edges={edges}
              fitView
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              zoomOnScroll={false}
            >
              <Background />
              <Controls showInteractive={false} />
            </ReactFlow>
          </div>
        </div>
      ) : null}
    </section>
  );
}
