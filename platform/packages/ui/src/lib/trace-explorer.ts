/** 軌跡の調査に使う、表示名に依存しない分類と実測の時間座標。 */
import type { RunTimelineStep } from "../components/data/run-detail";
import { operationTimestampMs } from "./operation-timing";

export type TraceCategory = "model" | "tool" | "stage";
export type TraceOrder = "hierarchy" | "time";
export function traceCategory(step: RunTimelineStep): TraceCategory {
  const name = step.operationName ?? "";
  if (name === "gen_ai.chat" || name === "oracle.select_ai") return "model";
  if (name === "gen_ai.execute_tool" || name.startsWith("mcp.")) return "tool";
  return "stage";
}

export function traceSearchText(step: RunTimelineStep): string {
  const call = step.details?.call;
  return [step.id, step.label, step.detail, step.outcome, step.operationName, step.product,
    ...(step.details?.fields ?? []).flatMap(field => [field.label, field.value]),
    ...(step.details?.lines ?? []), ...(step.details?.code ?? []).map(block => block.text),
    ...[call?.input, call?.output, call?.schema].map(content =>
      content && (content.status === "recorded" || content.status === "truncated") ? content.text : ""),
  ].filter(Boolean).join("\n").toLocaleLowerCase();
}

export { measuredRunTiming as traceMeasuredTiming } from "./run-step-presentation";

export function traceOrderedSteps(steps: readonly RunTimelineStep[], order: TraceOrder): RunTimelineStep[] {
  const sorted = steps.map((step, index) => ({step, index})).sort((a, b) => {
    const startA = operationTimestampMs(a.step.startedAt);
    const startB = operationTimestampMs(b.step.startedAt);
    if (startA == null) return startB == null ? a.index - b.index : 1;
    return startB == null ? -1 : startA - startB || a.index - b.index;
  }).map(item => item.step);
  if (order === "time") return sorted;
  const ids = new Set(steps.map(step => step.id));
  const children = new Map<string, RunTimelineStep[]>();
  for (const step of sorted) if (step.parentId && ids.has(step.parentId)) {
    const siblings = children.get(step.parentId) ?? []; siblings.push(step); children.set(step.parentId, siblings);
  }
  const result: RunTimelineStep[] = [];
  const visited = new Set<string>();
  const visit = (step: RunTimelineStep) => {
    if (visited.has(step.id)) return;
    visited.add(step.id); result.push(step);
    for (const child of children.get(step.id) ?? []) visit(child);
  };
  for (const step of sorted) if (!step.parentId || !ids.has(step.parentId)) visit(step);
  // 循環・欠けた親がある旧記録でも、段を消さず安定して表示する。
  for (const step of sorted) visit(step);
  return result;
}
