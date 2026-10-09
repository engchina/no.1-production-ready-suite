/**
 * チャットの処理の段階（3 製品共通の ChatProgressStep。#1147）を Run から組み立てるロジックの確認。
 * Agent の frontend は Vitest を持たないため、画面を開かずに Playwright の test で関数を確かめる。
 */
import type { RunState, RunStep } from "../src/lib/api";
import { runProgressSteps, type ChatProgressStep } from "../src/lib/chat-progress";

import { expect, test } from "./fixtures/test";

const T0 = "2026-10-04T12:00:00.000Z";
const T1 = "2026-10-04T12:00:02.000Z";
const T2 = "2026-10-04T12:00:05.000Z";
const T3 = "2026-10-04T12:00:09.000Z";
const T4 = "2026-10-04T12:00:12.000Z";

function run(overrides: Partial<RunState> = {}): RunState {
  return {
    id: "run-1",
    goal: "契約の更新条件は？",
    agent_id: "default",
    runtime_id: "builtin",
    status: "running",
    steps: [],
    events: [
      { id: "e1", run_id: "run-1", type: "run.created", message: "", payload: {}, created_at: T0 },
      { id: "e2", run_id: "run-1", type: "run.status_changed", message: "", payload: { status: "running" }, created_at: T1 },
    ],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_at: T0,
    updated_at: T1,
    ...overrides,
  };
}

function toolStep(name: string, status: RunStep["status"], overrides: Partial<RunStep> = {}): RunStep {
  return {
    id: `step-${name}-${status}`,
    run_id: "run-1",
    kind: "tool",
    status,
    tool_call: { name, arguments: {} },
    started_at: T2,
    completed_at: status === "completed" || status === "failed" ? T3 : null,
    ...overrides,
  };
}

function summary(steps: ChatProgressStep[]): string[] {
  return steps.map((step) => `${step.id}:${step.status}`);
}

function endEvent(type: string, at = T4) {
  return { id: `end-${type}`, run_id: "run-1", type, message: "", payload: {}, created_at: at };
}

// 画面幅に関係しないので、題名の「（desktop）」で mobile-375 の project では実行しない（playwright.config.ts の grepInvert）。
test.describe("runProgressSteps（desktop）", () => {
  test("開始直後はモデルが考えている段階だけが実行中", () => {
    const steps = runProgressSteps(run());
    expect(summary(steps)).toEqual(["plan:running", "respond:pending"]);
    expect(steps[0]).toMatchObject({ label: "考えています", startedAt: T1 });
    expect(steps[0].finishedAt).toBeUndefined();
  });

  test("受付待ち（queued）は Run の作成の時刻から考えている段階を出す", () => {
    const steps = runProgressSteps(run({ status: "queued", events: [] }));
    expect(summary(steps)).toEqual(["plan:running", "respond:pending"]);
    expect(steps[0].startedAt).toBe(T0);
  });

  test("ツールを呼んでいる間はツールの段階が実行中で、名前を出す", () => {
    const steps = runProgressSteps(run({ steps: [toolStep("rag__rag_search", "running")] }));
    expect(summary(steps)).toEqual(["plan:done", "tool:rag__rag_search:running", "respond:pending"]);
    expect(steps[0].finishedAt).toBe(T2);
    expect(steps[1]).toMatchObject({ label: "ツール rag__rag_search を呼んでいます", startedAt: T2 });
  });

  test("ツールの結果を受け取った後は回答を作っている段階が実行中", () => {
    const steps = runProgressSteps(run({ steps: [toolStep("rag__rag_search", "completed")] }));
    expect(summary(steps)).toEqual(["plan:done", "tool:rag__rag_search:done", "respond:running"]);
    expect(steps[2]).toMatchObject({ label: "回答を作っています", startedAt: T3 });
  });

  test("同じツールを 2 回呼んだら id は一意", () => {
    const steps = runProgressSteps(
      run({ steps: [toolStep("rag__rag_search", "completed", { id: "a" }), toolStep("rag__rag_search", "running", { id: "b" })] })
    );
    expect(summary(steps)).toEqual([
      "plan:done",
      "tool:rag__rag_search:done",
      "tool:rag__rag_search#2:running",
      "respond:pending",
    ]);
  });

  test("承認待ちはツールの前に実行中で出し、ツールは未開始", () => {
    const steps = runProgressSteps(
      run({
        status: "waiting_approval",
        steps: [toolStep("nl2sql__execute", "waiting_approval", { approval_id: "ap-1", started_at: T2 })],
        approvals: [
          {
            id: "ap-1",
            run_id: "run-1",
            step_id: "step-nl2sql__execute-waiting_approval",
            tool_call: { name: "nl2sql__execute", arguments: {} },
            status: "pending",
            reason: "",
            created_at: T2,
          },
        ],
      })
    );
    expect(summary(steps)).toEqual(["plan:done", "approval_wait:running", "tool:nl2sql__execute:pending", "respond:pending"]);
    expect(steps[1]).toMatchObject({ label: "承認を待っています", detail: "nl2sql__execute", startedAt: T2 });
    // 承認待ちのツールは実行していないので、開始の時刻を出さない。
    expect(steps[2].startedAt).toBeUndefined();
  });

  test("却下したツールは skipped と補足を出し、承認待ちは完了", () => {
    const steps = runProgressSteps(
      run({
        status: "completed",
        steps: [toolStep("nl2sql__execute", "cancelled", { approval_id: "ap-1", completed_at: T3 })],
        approvals: [
          {
            id: "ap-1",
            run_id: "run-1",
            step_id: "s",
            tool_call: { name: "nl2sql__execute", arguments: {} },
            status: "rejected",
            reason: "",
            decided_at: T3,
            created_at: T2,
          },
        ],
        events: [...run().events, endEvent("run.completed")],
      })
    );
    expect(summary(steps)).toEqual(["plan:done", "approval_wait:done", "tool:nl2sql__execute:skipped", "respond:done"]);
    expect(steps[1]).toMatchObject({ startedAt: T2, finishedAt: T3 });
    expect(steps[2].detail).toBe("承認されませんでした");
  });

  test("承認の後に別のツールの承認を求めたら、次の承認待ちの段階にし、完了した承認待ちを実行中に戻さない（#1358）", () => {
    const approval = (id: string, tool: string, status: "pending" | "approved", createdAt: string, decidedAt?: string) => ({
      id,
      run_id: "run-1",
      step_id: `step-${tool}`,
      tool_call: { name: tool, arguments: {} },
      status,
      reason: "",
      created_at: createdAt,
      decided_at: decidedAt ?? null,
    });
    // 1 回目の承認（A）を承認して A を実行した後、モデルが B を呼び、B の承認を求めた。
    const steps = runProgressSteps(
      run({
        status: "waiting_approval",
        steps: [
          toolStep("nl2sql__execute", "completed", { approval_id: "ap-1" }),
          toolStep("rag__rag_search", "waiting_approval", { approval_id: "ap-2", started_at: T4, completed_at: null }),
        ],
        approvals: [
          approval("ap-1", "nl2sql__execute", "approved", T1, T2),
          approval("ap-2", "rag__rag_search", "pending", T4),
        ],
      })
    );
    expect(summary(steps)).toEqual([
      "plan:done",
      "approval_wait:done",
      "tool:nl2sql__execute:done",
      "approval_wait#2:running",
      "tool:rag__rag_search:pending",
      "respond:pending",
    ]);
    expect(steps[3]).toMatchObject({ label: "承認を待っています", detail: "rag__rag_search", startedAt: T4 });

    // 同じ中断で求めた承認（どれも決まる前に求めた）は 1 つの段階にまとめる。
    const together = runProgressSteps(
      run({
        status: "waiting_approval",
        steps: [
          toolStep("nl2sql__execute", "waiting_approval", { approval_id: "ap-1" }),
          toolStep("rag__rag_search", "waiting_approval", { approval_id: "ap-2" }),
        ],
        approvals: [approval("ap-1", "nl2sql__execute", "approved", T2, T3), approval("ap-2", "rag__rag_search", "pending", T2)],
      })
    );
    expect(summary(together)).toEqual([
      "plan:done",
      "approval_wait:running",
      "tool:nl2sql__execute:pending",
      "tool:rag__rag_search:pending",
      "respond:pending",
    ]);
  });

  test("ツールを呼ばずに答えたら、考えている段階と回答の段階が完了", () => {
    const steps = runProgressSteps(run({ status: "completed", events: [...run().events, endEvent("run.completed")] }));
    expect(summary(steps)).toEqual(["plan:done", "respond:done"]);
    expect(steps[0]).toMatchObject({ startedAt: T1, finishedAt: T4 });
    expect(steps[1].finishedAt).toBe(T4);
  });

  test("失敗は実行中の段階を failed にし、始まらなかった段階は未開始のまま", () => {
    const steps = runProgressSteps(
      run({ status: "failed", steps: [toolStep("rag__rag_search", "completed")], events: [...run().events, endEvent("runtime.failed")] })
    );
    expect(summary(steps)).toEqual(["plan:done", "tool:rag__rag_search:done", "respond:failed"]);
    expect(steps[2].finishedAt).toBe(T4);

    const early = runProgressSteps(run({ status: "failed", events: [...run().events, endEvent("runtime.failed")] }));
    expect(summary(early)).toEqual(["plan:failed", "respond:pending"]);
  });

  test("ツールの失敗はその段階を failed にし、モデルは続けて回答を作る", () => {
    const steps = runProgressSteps(run({ steps: [toolStep("rag__rag_search", "failed")] }));
    expect(summary(steps)).toEqual(["plan:done", "tool:rag__rag_search:failed", "respond:running"]);
  });

  test("停止は実行中と未開始の段階を skipped にする", () => {
    const steps = runProgressSteps(
      run({ status: "cancelled", steps: [toolStep("rag__rag_search", "running")], events: [...run().events, endEvent("run.cancelled")] })
    );
    expect(summary(steps)).toEqual(["plan:done", "tool:rag__rag_search:skipped", "respond:skipped"]);
  });
});
