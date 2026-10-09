import assert from "node:assert/strict";
import test from "node:test";

import {
  chatProgressStepsFromEvents,
  chatProgressTerminalOf,
  type ChatProgressEvent,
  type ChatProgressParams,
  type ChatProgressStepStatus,
  type ChatProgressTerminalStatus,
} from "@production-ready/ui";

import {
  CHAT_PROGRESS_STEP_DEFINITIONS,
  chatJobElapsedMs,
  chatSubmitProgressSteps,
} from "../src/features/nl2sql/chatProgress.ts";
import type { JobData } from "../src/features/nl2sql/types.ts";

// #1145: チャットのジョブの段階を 3 製品共通の段階の形にする。
// #1359: 段階の状態は backend が処理の段階のイベントとして記録する。画面は段階の定義（名前と補足）だけを持つ。

const created = "2026-10-04T00:00:00.000Z";
const STEPS = ["queue", "prepare_context", "generate_sql", "safety_check", "execute_sql", "format_results"];

interface StepState {
  status: ChatProgressStepStatus;
  params?: ChatProgressParams;
}

/** backend の記録と同じ順のイベント（待機中で出し、前から順に進める）。 */
function events(
  states: Record<string, StepState>,
  terminal?: ChatProgressTerminalStatus,
): ChatProgressEvent[] {
  const list: ChatProgressEvent[] = [];
  const base = () => ({
    schema_version: 1 as const,
    seq: list.length + 1,
    target_id: "j1",
    attempt: 0,
    emitted_at: created,
  });
  for (const step of STEPS) list.push({ ...base(), type: "step", step_id: step, status: "pending" });
  for (const step of STEPS) {
    const state = states[step];
    if (!state || state.status === "pending") continue;
    list.push({ ...base(), type: "step", step_id: step, status: state.status, started_at: created, params: state.params });
  }
  if (terminal) list.push({ ...base(), type: "terminal", status: terminal });
  return list;
}

const view = (list: ChatProgressEvent[]) => chatProgressStepsFromEvents(list, CHAT_PROGRESS_STEP_DEFINITIONS);
const summary = (list: ChatProgressEvent[]) => view(list).map((step) => `${step.id}:${step.status}`);

test("送信の応答待ちは「質問を送信しています」の 1 段階", () => {
  const [step] = chatSubmitProgressSteps(Date.parse(created));
  assert.equal(step.id, "submit");
  assert.equal(step.status, "running");
  assert.equal(step.label, "質問を送信しています");
  assert.equal(step.startedAt, created);
});

test("開始待ちと 5 段階の名前を状態ごとに付ける（並びは backend の記録の順）", () => {
  const waiting = view(events({ queue: { status: "running" } }));
  assert.deepEqual(
    waiting.map((step) => step.id),
    STEPS,
  );
  assert.equal(waiting[0].label, "処理の開始を待っています");
  assert.equal(waiting[1].label, "質問と対象の表の準備");

  const running = view(
    events({
      queue: { status: "done" },
      prepare_context: { status: "done" },
      generate_sql: { status: "running", params: { engine: "select_ai" } },
    }),
  );
  assert.equal(running[0].label, "処理を開始しました");
  assert.equal(running[1].label, "質問と対象の表を準備しました");
  assert.equal(running[2].label, "SQL を生成しています");
  assert.equal(running[2].detail, "Select AI");
  assert.equal(running[3].label, "SQL の安全性の確認");
});

test("完了したジョブは参照した表と取得した行数を補足する", () => {
  const list = events(
    {
      queue: { status: "done" },
      prepare_context: { status: "done" },
      generate_sql: { status: "done", params: { engine: "enterprise_ai_direct" } },
      safety_check: { status: "done", params: { tables: "APP.A, APP.B, APP.C", table_count: 4 } },
      execute_sql: { status: "done", params: { rows: 1234 } },
      format_results: { status: "done" },
    },
    "done",
  );
  const steps = view(list);
  assert.deepEqual(
    steps.map((step) => step.status),
    STEPS.map(() => "done"),
  );
  assert.equal(steps[2].detail, "Enterprise AI");
  assert.equal(steps[3].detail, "APP.A, APP.B, APP.C ほか 1 件");
  assert.equal(steps[4].label, "SQL を実行しました");
  assert.equal(steps[4].detail, "1,234 行");
  assert.equal(steps[5].label, "結果をまとめました");
  assert.equal(chatProgressTerminalOf(list), "done");

  // 表が 3 件以下なら件数を足さない。未知の生成方法・形の違う値は出さない。
  const few = view(
    events({
      generate_sql: { status: "running", params: { engine: "unknown" } },
      safety_check: { status: "done", params: { tables: "APP.A", table_count: 1 } },
      execute_sql: { status: "done", params: { rows: "x" } },
    }),
  );
  assert.equal(few[2].detail, undefined);
  assert.equal(few[3].detail, "APP.A");
  assert.equal(few[4].detail, undefined);
});

test("失敗した段階は「〜できませんでした」、未実行の段階は名詞", () => {
  const steps = view(
    events(
      {
        queue: { status: "done" },
        prepare_context: { status: "done" },
        generate_sql: { status: "failed", params: { engine: "select_ai" } },
        safety_check: { status: "skipped" },
        execute_sql: { status: "skipped" },
        format_results: { status: "skipped" },
      },
      "failed",
    ),
  );
  assert.equal(steps[2].label, "SQL を生成できませんでした");
  assert.equal(steps[3].label, "SQL の安全性の確認");
  assert.equal(steps[3].status, "skipped");
  // 開始する前の失敗は開始待ちの段階の失敗（backend が決める）。
  const beforeStart = view(events({ queue: { status: "failed" } }, "failed"));
  assert.equal(beforeStart[0].label, "処理を開始できませんでした");
});

test("止めた段階（backend の停止の印）は未実行と「停止しました」", () => {
  const list = events(
    {
      queue: { status: "done" },
      prepare_context: { status: "done" },
      generate_sql: { status: "skipped", params: { engine: "select_ai", stopped: true } },
      safety_check: { status: "skipped" },
    },
    "cancelled",
  );
  const steps = view(list);
  assert.ok(!steps.some((step) => step.status === "failed"));
  assert.equal(steps[2].status, "skipped");
  assert.equal(steps[2].detail, "停止しました");
  assert.equal(steps[3].detail, undefined);
  assert.equal(chatProgressTerminalOf(list), "cancelled");
  const queueStopped = view(events({ queue: { status: "skipped", params: { stopped: true } } }, "cancelled"));
  assert.equal(queueStopped[0].detail, "停止しました");
});

test("生成だけのターンは実行を「未実行」にして結果の整形へ進む", () => {
  assert.deepEqual(
    summary(
      events({
        queue: { status: "done" },
        prepare_context: { status: "done" },
        generate_sql: { status: "done" },
        safety_check: { status: "done" },
        execute_sql: { status: "skipped" },
        format_results: { status: "running" },
      }),
    ).slice(4),
    ["execute_sql:skipped", "format_results:running"],
  );
});

test("全体の所要時間はジョブの作成から終了まで（終わっていなければ出さない）", () => {
  const job = (overrides: Partial<JobData>): JobData => ({
    job_id: "j1",
    status: "done",
    created_at: created,
    steps: [],
    ...overrides,
  });
  assert.equal(chatJobElapsedMs(job({ finished_at: "2026-10-04T00:00:12.000Z" })), 12_000);
  assert.equal(chatJobElapsedMs(job({ status: "running" })), null);
  assert.equal(chatJobElapsedMs(job({ finished_at: null, elapsed_ms: 900 })), 900);
});
