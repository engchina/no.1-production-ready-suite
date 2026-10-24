import { DEFAULT_TRACE_DETAILS_LABELS, type TraceDetailsLabels, type TraceSpanNames, type TraceSpanStatus } from "./trace-labels";
import type { RunTimelineStep } from "../components/data/run-detail";

function textAttribute(attributes: Readonly<Record<string, unknown>>, key: string): string | undefined {
  const value = attributes[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

/** 技術の span の名前と、名前に添える対象（モデル・ツール）の属性。 */
const TECHNICAL_SPANS: Readonly<Record<string, { name: Exclude<keyof TraceSpanNames, "attempt" | "root">; target?: string }>> = {
  "gen_ai.chat": { name: "chat", target: "gen_ai.request.model" },
  "gen_ai.embeddings": { name: "embeddings", target: "gen_ai.request.model" },
  "gen_ai.rerank": { name: "rerank", target: "gen_ai.request.model" },
  "gen_ai.execute_tool": { name: "executeTool", target: "gen_ai.tool.name" },
  "mcp.request": { name: "mcpRequest" },
  "oracle.select_ai": { name: "selectAi" },
  "http.request": { name: "httpRequest" },
};

interface SpanNameInput {
  operationName: string;
  status: TraceSpanStatus;
  root: boolean;
  attributes: Readonly<Record<string, unknown>>;
}

/**
 * span の行の名前（3 製品の 9 画面で共通の規則。#3219）。root は「実行の全体」、段（`pr.stage`）は製品の段の名前
 * （処理の経過と同じ。無ければ `pr.stage` の値）、`gen_ai.*`・MCP・Select AI・HTTP は日本語の名前に対象を添え、
 * それ以外は `operation_name`。2 回目以降の試行（`pr.attempt`）は回数を付ける。元の名前は詳細の `Span name` に残す。
 */
function spanDisplayName(span: SpanNameInput, labels: TraceDetailsLabels): string {
  const names = labels.spanNames;
  const stage = textAttribute(span.attributes, "pr.stage");
  let name: string;
  if (span.root) {
    name = names.root;
  } else if (stage) {
    name = (labels.stageName?.(stage, span.status)?.trim() || stage).slice(0, 128);
  } else {
    const technical = TECHNICAL_SPANS[span.operationName] ?? (span.operationName.startsWith("gen_ai.") ? { name: "genAi" as const, target: "gen_ai.request.model" } : undefined);
    if (!technical) return span.operationName;
    const target = technical.target ? textAttribute(span.attributes, technical.target) : undefined;
    name = target ? `${names[technical.name]} · ${target.slice(0, 128)}` : names[technical.name];
  }
  const attempt = span.attributes["pr.attempt"];
  return typeof attempt === "number" && Number.isInteger(attempt) && attempt > 1 ? names.attempt(name, attempt) : name;
}

/** 外部の summary を検証し、親子関係を有限回で並べる。本文や任意の属性は描画しない。 */
export function traceTimeline(value: unknown, labels: TraceDetailsLabels = DEFAULT_TRACE_DETAILS_LABELS): { steps: RunTimelineStep[]; partial: boolean; traceId?: string } {
  const empty = { steps: [], partial: true };
  if (!value || typeof value !== "object") return empty;
  const data = value as Record<string, unknown>;
  if (!Array.isArray(data.spans) || (data.schema_version != null && data.schema_version !== 1)) return empty;
  const spans = new Map<string, Record<string, unknown>>();
  let partial = data.completeness !== "complete" || data.truncated === true || data.spans.length > 1000;
  for (const raw of data.spans.slice(0, 1000)) {
    if (!raw || typeof raw !== "object") { partial = true; continue; }
    const span = raw as Record<string, unknown>;
    if (typeof span.span_id !== "string" || !/^[0-9a-f]{16}$/.test(span.span_id) || spans.has(span.span_id) ||
        typeof span.trace_id !== "string" || !/^[0-9a-f]{32}$/.test(span.trace_id) ||
        typeof span.operation_name !== "string" || !["done", "failed", "cancelled", "skipped"].includes(String(span.status))) {
      partial = true; continue;
    }
    spans.set(span.span_id, span);
  }
  const ordered = [...spans.values()].sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)));
  const visited = new Set<string>();
  const steps: RunTimelineStep[] = [];
  const pending: { span: Record<string, unknown>; level: number }[] = [];
  function emit(span: Record<string, unknown>, level: number) {
    const id = String(span.span_id);
    if (visited.has(id)) { partial = true; return; }
    visited.add(id);
    const attributes = span.attributes && typeof span.attributes === "object" ? span.attributes as Record<string, unknown> : {};
    const operationName = String(span.operation_name).slice(0, 128);
    const label = spanDisplayName({
      operationName,
      status: span.status as TraceSpanStatus,
      root: data.root_span_id != null ? id === data.root_span_id : !span.parent_span_id,
      attributes,
    }, labels);
    // 行の名前は日本語にし、実測の記録を引けるように元の span の名前を詳細に残す。
    const fields = [{ label: "Span ID", value: id, mono: true }, { label: "Span name", value: operationName, mono: true }];
    for (const key of ["pr.stage", "gen_ai.request.model", "gen_ai.tool.name", "pr.attempt", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.reasoning.output_tokens", "error.type"]) {
      const v = attributes[key];
      if (typeof v === "string" || (typeof v === "number" && Number.isFinite(v))) fields.push({ label: key, value: String(v).slice(0, 128), mono: true });
    }
    if (span.operation_name === "gen_ai.chat") {
      for (const key of ["gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"]) {
        if (attributes[key] == null) fields.push({ label: key, value: labels.unknown, mono: false });
      }
    }
    steps.push({ id, label, level,
      status: span.status === "cancelled" ? "skipped" : span.status as "done" | "failed" | "skipped",
      detail: span.status === "cancelled" ? labels.cancelled : undefined,
      startedAt: typeof span.started_at === "string" ? span.started_at : null,
      finishedAt: typeof span.finished_at === "string" ? span.finished_at : null,
      durationMs: typeof span.duration_ms === "number" && Number.isFinite(span.duration_ms) && span.duration_ms >= 0 ? span.duration_ms : null,
      details: { fields },
    });
    const children = ordered.filter(child => child.parent_span_id === id);
    for (const child of children.reverse()) pending.push({ span: child, level: level + 1 });
  }
  const roots = ordered.filter(span => !spans.has(String(span.parent_span_id)));
  for (const root of roots) {
    if (root.span_id !== data.root_span_id && root.parent_span_id) partial = true;
    pending.push({ span: root, level: 0 });
    while (pending.length) { const item = pending.pop()!; emit(item.span, item.level); }
  }
  // cycle や孤立した記録も捨てずに出し、完全な記録とは表示しない。
  for (const span of ordered) if (!visited.has(String(span.span_id))) {
    partial = true; pending.push({ span, level: 0 });
    while (pending.length) { const item = pending.pop()!; emit(item.span, item.level); }
  }
  return { steps, partial, traceId: typeof data.telemetry_trace_id === "string" && /^[0-9a-f]{32}$/.test(data.telemetry_trace_id) ? data.telemetry_trace_id : undefined };
}
