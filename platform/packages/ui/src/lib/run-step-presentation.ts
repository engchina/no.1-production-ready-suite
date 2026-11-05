/** 経過と軌跡が共有する、記録値からの行と実測時間の投影。 */
import type { RunTimelineStep } from "../components/data/run-detail";
import { runStepResultSummary } from "../components/data/run-step-details";
import { operationTimestampMs } from "./operation-timing";

export function recordedStepDuration(step: RunTimelineStep): number | null {
  const start = operationTimestampMs(step.startedAt);
  const end = operationTimestampMs(step.finishedAt);
  if (start != null && end != null && end >= start) return end - start;
  return typeof step.durationMs === "number" && Number.isFinite(step.durationMs) && step.durationMs >= 0 ? step.durationMs : null;
}

export function recordedStepResult(step: RunTimelineStep): string | undefined {
  const values = [step.outcome, runStepResultSummary(step.details)].filter((value): value is string => Boolean(value));
  return values.length ? [...new Set(values)].join(" · ") : undefined;
}

export function runStepPresentation(step: RunTimelineStep, statusLabel: string, formatDuration: (ms: number) => string, elapsedMs?: number) {
  const durationMs = recordedStepDuration(step);
  const shown = durationMs ?? elapsedMs;
  const durationText = shown == null ? undefined : formatDuration(shown);
  return { durationMs, durationText, durationKind: durationMs != null ? "recorded" : elapsedMs != null ? "elapsed" : "unknown", statusDuration: statusLabel + (durationText == null ? "" : ` · ${durationText}`), result: recordedStepResult(step) };
}

/** 実測座標だけを返す。所要時間だけの記録から開始/終了位置を作らない。 */
export function measuredRunTiming(steps: readonly RunTimelineStep[]) {
  const times = steps.map(step => {
    const start = operationTimestampMs(step.startedAt);
    const end = operationTimestampMs(step.finishedAt);
    return { start, end: start != null && end != null && end >= start ? end : null };
  });
  const starts = times.flatMap(t => t.start == null ? [] : [t.start]);
  const ends = times.flatMap(t => t.end == null ? [] : [t.end]);
  const origin = starts.length ? Math.min(...starts) : null;
  const last = starts.length ? Math.max(...ends, ...starts) : null;
  const coordinateRange = origin != null && last != null ? Math.max(0, last - origin) : null;
  // 開始の位置は分かっていても、終了が1件も無ければ確定した合計は未記録。
  const duration = ends.length ? coordinateRange : null;
  return { duration, bars: times.map(t => {
    if (origin == null || coordinateRange == null || t.start == null) return null;
    return { offset: coordinateRange > 0 ? (t.start - origin) / coordinateRange * 100 : 0,
      width: t.end == null ? null : coordinateRange > 0 ? Math.max(0, (t.end - t.start) / coordinateRange * 100) : 0 };
  }) };
}
