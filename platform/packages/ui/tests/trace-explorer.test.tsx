// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import type { RunTimelineStep } from "../src/components/data/run-detail";
import { TraceExplorer } from "../src/components/data/trace-explorer";
import { traceCategory, traceMeasuredTiming, traceOrderedSteps, traceSearchText } from "../src/lib/trace-explorer";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const steps: RunTimelineStep[] = [
  { id: "root", label: "合成の実行", status: "done", operationName: "agent.execute", startedAt: "2026-01-01T00:00:00Z", finishedAt: "2026-01-01T00:00:10Z" },
  { id: "model-a", parentId: "root", level: 1, label: "合成のモデル", status: "done", operationName: "gen_ai.chat", startedAt: "2026-01-01T00:00:02Z", finishedAt: "2026-01-01T00:00:08Z", details: { call: {
    input: {status: "recorded", text: "synthetic-input"}, output: {status: "withheld", text: "private-hidden"}, schema: {status: "not_recorded", text: ""},
  } } },
  { id: "tool-a", parentId: "root", level: 1, label: "合成のツール", status: "failed", operationName: "gen_ai.execute_tool", startedAt: "2026-01-01T00:00:01Z", finishedAt: "2026-01-01T00:00:04Z", details: {lines: ["synthetic-error"]} },
];
describe("記録した値だけを使う軌跡", () => {
  it("並列の時間を足さず、時刻不明を推定しない", () => {
    const timing = traceMeasuredTiming([...steps, {id: "old", label: "旧記録", status: "done", durationMs: 30000}]);
    expect(timing.duration).toBe(10000);
    expect(timing.bars[1]).toEqual({offset: 20, width: 60});
    expect(timing.bars[2]).toEqual({offset: 10, width: 30});
    expect(timing.bars[3]).toBeNull();
    expect(traceMeasuredTiming([{...steps[1], status: "running", finishedAt: null}]).bars[0]).toEqual({offset: 0, width: null});
  });
  it("記録の operation で分類し、時刻順では安定して並ぶ", () => {
    expect(steps.map(traceCategory)).toEqual(["stage", "model", "tool"]);
    expect(traceOrderedSteps(steps, "time").map(step => step.id)).toEqual(["root", "tool-a", "model-a"]);
    expect(traceOrderedSteps(steps, "hierarchy")).toEqual(steps);
    expect(traceSearchText(steps[1])).toContain("synthetic-input");
    expect(traceSearchText(steps[1])).not.toContain("private-hidden");
  });
  it("選択と詳細を同期し、タブを保って前後の呼び出しを確認する", () => {
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    const click = (selector: string) => act(() => host.querySelector<HTMLButtonElement>(selector)!.click());
    try {
      act(() => root.render(<TraceExplorer steps={steps} onClose={() => {}} />));
      expect(host.querySelector('[data-trace-inspector]')?.textContent).toContain("synthetic-error");
      click('[data-trace-record="model-a"] button');
      const inputTab = Array.from(host.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find(tab => tab.textContent === "入力")!;
      act(() => inputTab.click());
      expect(host.querySelector('[data-trace-inspector]')?.textContent).toContain("synthetic-input");
      expect(host.textContent).not.toContain("private-hidden");
      click('button[aria-label="次の呼び出し"]');
      expect(host.querySelector('[data-trace-inspector]')?.textContent).toContain("synthetic-error");
      click('button[aria-label="前の呼び出し"]');
      expect(host.querySelector('[data-trace-inspector]')?.textContent).toContain("synthetic-input");
    } finally { act(() => root.unmount()); host.remove(); }
  });
  it("長い軌跡を段階的に表示し、選択した詳細だけを描画する", () => {
    const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
    try {
      act(() => root.render(<TraceExplorer steps={Array.from({length: 120}, (_, i) => ({ ...steps[1], id: `model-${i}` }))} onClose={() => {}} />));
      expect(host.querySelectorAll('[data-trace-record]')).toHaveLength(50);
      const more = Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find(button => button.textContent?.includes("さらに表示"))!;
      act(() => more.click());
      expect(host.querySelectorAll('[data-trace-record]')).toHaveLength(100);
      expect(host.querySelectorAll('[data-call-details]')).toHaveLength(1);
    } finally { act(() => root.unmount()); host.remove(); }
  });
});
