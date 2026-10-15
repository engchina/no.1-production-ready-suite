import { describe, expect, it } from "vitest";

import type { RunState } from "@/lib/api";
import {
  backendRunExecutor,
  backendRunStatus,
  commonRunStatus,
  runDurationMs,
  runFinishedAt,
  runStatusLabel,
  runTimelineSteps,
} from "@/lib/run-history";

const NOW = "2026-10-15T09:00:00.000Z";

function run(overrides: Partial<RunState> = {}): RunState {
  return {
    id: "run-1",
    goal: "売上を調べる",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  } as RunState;
}

// 実行履歴の共通の型（#1955）。
describe("実行履歴の状態の写し（run-history.md「状態の写し方」）", () => {
  it("Agent の状態を共通の 7 つに写し、承認待ち・確認待ちだけ Agent の言い方で上書きする", () => {
    expect(commonRunStatus("waiting_approval")).toBe("waiting");
    expect(commonRunStatus("waiting_confirmation")).toBe("waiting");
    expect(commonRunStatus("completed")).toBe("succeeded");
    expect(commonRunStatus("cancelled")).toBe("cancelled");
    expect(runStatusLabel("waiting_approval")).toBe("承認待ち");
    expect(runStatusLabel("waiting_confirmation")).toBe("確認待ち");
    expect(runStatusLabel("completed")).toBeUndefined();
  });

  it("共通の絞り込みの値を backend の条件に写す", () => {
    expect(backendRunStatus("all")).toBeUndefined();
    expect(backendRunStatus("succeeded")).toBe("completed");
    expect(backendRunStatus("waiting")).toBe("waiting");
    expect(backendRunExecutor("self")).toBe("mine");
    expect(backendRunExecutor("others")).toBe("others");
    expect(backendRunExecutor("all")).toBeUndefined();
  });
});

describe("詳細の時間と処理の経過", () => {
  it("終わった実行だけ終了日時と所要時間を持つ（終わりの Event の時刻）", () => {
    const done = run({
      events: [
        { id: "e1", run_id: "run-1", type: "run.completed", message: "", payload: {}, created_at: "2026-10-15T09:00:03.200Z" },
      ],
    });
    expect(runFinishedAt(done)).toBe("2026-10-15T09:00:03.200Z");
    expect(runDurationMs(done)).toBe(3200);
    expect(runFinishedAt(run({ status: "running" }))).toBeNull();
    expect(runDurationMs(run({ status: "waiting_approval" }))).toBeNull();
  });

  it("段の記録の無い Run は step をチャットと同じ名前のツールの段にする", () => {
    const steps = runTimelineSteps(
      run({
        steps: [
          {
            id: "s1",
            run_id: "run-1",
            kind: "tool",
            status: "failed",
            tool_call: { name: "rag__rag_search", arguments: {} },
          } as RunState["steps"][number],
        ],
      }),
      () => "中身"
    );
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ id: "step:s1", status: "failed", label: "ツール rag__rag_search を呼べませんでした" });
    expect(steps[0].children).toBe("中身");
  });
});
