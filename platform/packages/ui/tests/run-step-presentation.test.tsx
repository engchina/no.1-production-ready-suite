// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import fixture from "./fixtures/run-step-timing.json";
import { RunTimeline, runTimelineBars, stepDurationMs, type RunTimelineStep } from "../src/components/data/run-detail";
import { TraceExplorer } from "../src/components/data/trace-explorer";
import { traceMeasuredTiming } from "../src/lib/trace-explorer";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const rows = fixture as RunTimelineStep[];

describe("同じ記録からの共通表示", () => {
  it("実測/所要時間だけ/同時刻/終了未記録を両表示が同じ座標にする", () => {
    expect(rows.map(stepDurationMs)).toEqual([1000, 3000, 0, null]);
    expect(runTimelineBars(rows)).toEqual(traceMeasuredTiming(rows).bars);
    expect(runTimelineBars(rows)).toEqual([{offset: 0, width: 100}, null, {offset: 50, width: 0}, {offset: 75, width: null}]);
    expect(stepDurationMs({...rows[0], durationMs: 5000})).toBe(1000);
  });

  it("名前/状態/所要時間/結果/時間の線は同じ部品を使い、所要時間だけの位置を捏造しない", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-01-01T00:00:01Z"));
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    const steps: RunTimelineStep[] = rows.map(step => ({...step, outcome: "合成の採否", details: {fields: [{label: "結果", value: "合成の結果"}]}}));
    const formatDuration = (ms: number) => `合成${ms}`;
    const project = (selector: string, label: string, timing: string, result: string) => Array.from(host.querySelectorAll(selector)).map(row => ({
      id: row.getAttribute("data-run-timeline-step") ?? row.getAttribute("data-trace-record"), label: row.querySelector(label)?.textContent, timing: row.querySelector(timing)?.textContent, result: row.querySelector(result)?.textContent,
      summary: row.querySelector("[data-run-step-summary]") !== null, kind: row.querySelector("[data-duration-kind]")?.getAttribute("data-duration-kind"), bar: row.querySelector("[data-run-step-bar]")?.getAttribute("style"), width: row.querySelector("[data-run-step-bar]")?.getAttribute("data-measured-width"),
    }));
    try {
      act(() => root.render(<RunTimeline steps={steps} formatDuration={formatDuration} />));
      const normal = project("[data-run-timeline-step]", "[data-run-timeline-label]", "[data-run-step-status-duration]", "[data-run-result-summary]");
      act(() => root.render(<TraceExplorer steps={steps} onClose={() => undefined} formatDuration={formatDuration} />));
      const traced = project("[data-trace-record]", "[data-trace-record-label]", "[data-trace-step-status-duration]", "[data-trace-result-summary]");
      expect(traced).toEqual(normal);
      expect(traced.find(row => row.id === "duration-only")?.bar).toBeUndefined();
      expect(traced.find(row => row.id === "zero")?.width).toBe("0");
      expect(traced.find(row => row.id === "inflight")?.kind).toBe("elapsed");
      expect(rows[3].finishedAt).toBeUndefined();
      expect(host.textContent).toContain("4 / 4 件を表示");
    } finally { act(() => root.unmount()); host.remove(); vi.useRealTimers(); }
  });
});
