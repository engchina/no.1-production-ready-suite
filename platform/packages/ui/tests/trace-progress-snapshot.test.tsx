// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTraceProgressSnapshot, type TraceProgressSnapshot } from "../src";

// #3226（案 A）: 処理の経過を共通の span の表の snapshot から読む。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const span = (seq: number, id: string, status: "running" | "done", extra: Record<string, unknown> = {}) => ({
  schema_version: 1, seq, target_id: "run-1", attempt: 0, emitted_at: "2026-01-01T00:00:00Z", type: "step",
  step_id: id, status, name: "gen_ai.chat", trace_id: "a".repeat(32), started_at: "2026-01-01T00:00:00Z",
  ...(status === "done" ? { finished_at: "2026-01-01T00:00:01Z" } : {}), ...extra,
});

let host: HTMLDivElement;
let root: Root;
let latest: TraceProgressSnapshot | null = null;

function Probe(props: { active: boolean; fetchEvents: (signal: AbortSignal) => Promise<unknown> }) {
  latest = useTraceProgressSnapshot({ key: "run-1", definitions: {}, intervalMs: 1000, ...props });
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  host = document.createElement("div");
  root = createRoot(host);
  latest = null;
});

afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
});

describe("useTraceProgressSnapshot", () => {
  it("実行中は読み直して木を更新し、終わったら読み直さない", async () => {
    const snapshots = [
      [span(1, "r", "running", { root: true }), span(2, "c", "running", { parent_id: "r" })],
      [span(1, "r", "running", { root: true }), span(2, "c", "done", { parent_id: "r" })],
    ];
    const fetchEvents = vi.fn(async () => snapshots[Math.min(fetchEvents.mock.calls.length - 1, 1)]);
    await act(async () => root.render(<Probe active fetchEvents={fetchEvents} />));
    expect(latest?.steps).toMatchObject([{ status: "running" }]);
    expect(latest?.traceId).toBe("a".repeat(32));
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(latest?.steps).toMatchObject([{ status: "done" }]);
    await act(async () => root.render(<Probe active={false} fetchEvents={fetchEvents} />));
    const calls = fetchEvents.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchEvents.mock.calls.length).toBe(calls);
  });

  it("読めなかったら前の内容を残し、span の無い記録は古い形式とする", async () => {
    let fail = false;
    const fetchEvents = vi.fn(async () => {
      if (fail) throw new Error("synthetic");
      return [{ schema_version: 1, seq: 1, target_id: "run-1", attempt: 0, emitted_at: "x", type: "step", step_id: "s1", status: "done" }];
    });
    await act(async () => root.render(<Probe active fetchEvents={fetchEvents} />));
    expect(latest?.legacy).toBe(true);
    fail = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(latest?.failed).toBe(true);
    expect(latest?.events).toHaveLength(1);
  });
});
