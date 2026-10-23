import { DEFAULT_TRACE_DETAILS_LABELS, type TraceDetailsLabels } from "./trace-labels";
import type { RunTimelineStep } from "../components/data/run-detail";

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
    const fields = [{ label: "Span ID", value: id, mono: true }];
    for (const key of ["gen_ai.request.model", "gen_ai.tool.name", "pr.attempt", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.reasoning.output_tokens", "error.type"]) {
      const v = attributes[key];
      if (typeof v === "string" || (typeof v === "number" && Number.isFinite(v))) fields.push({ label: key, value: String(v).slice(0, 128), mono: true });
    }
    if (span.operation_name === "gen_ai.chat") {
      for (const key of ["gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"]) {
        if (attributes[key] == null) fields.push({ label: key, value: labels.unknown, mono: false });
      }
    }
    steps.push({ id, label: String(span.operation_name).slice(0, 128), level,
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
