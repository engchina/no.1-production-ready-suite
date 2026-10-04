import assert from "node:assert/strict";
import test from "node:test";

import {
  chatJobElapsedMs,
  chatJobProgressSteps,
  chatSubmitProgressSteps,
} from "../src/features/nl2sql/chatProgress.ts";
import type { JobData } from "../src/features/nl2sql/types.ts";

// #1145: チャットのジョブの段階を 3 製品共通の段階の形にする。

const created = "2026-10-04T00:00:00.000Z";

function job(overrides: Partial<JobData>): JobData {
  return {
    job_id: "j1",
    status: "pending",
    created_at: created,
    generation_only: true,
    steps: [
      { stage: "prepare_context", status: "pending" },
      { stage: "generate_sql", status: "pending" },
      { stage: "safety_check", status: "pending" },
      { stage: "execute_sql", status: "pending" },
      { stage: "format_results", status: "pending" },
    ],
    ...overrides,
  };
}

const summary = (steps: ReturnType<typeof chatJobProgressSteps>) =>
  steps.map((step) => `${step.id}:${step.status}`);

test("送信の応答待ちは「質問を送信しています」の 1 段階", () => {
  const [step] = chatSubmitProgressSteps(Date.parse(created));
  assert.equal(step.id, "submit");
  assert.equal(step.status, "running");
  assert.equal(step.label, "質問を送信しています");
  assert.equal(step.startedAt, created);
});

test("worker が始めていないジョブは「処理の開始を待っています」を今の段階にする（実行・整形は出さない）", () => {
  const steps = chatJobProgressSteps(job({}));
  assert.deepEqual(summary(steps), [
    "queue:running",
    "prepare_context:pending",
    "generate_sql:pending",
    "safety_check:pending",
  ]);
  assert.equal(steps[0].label, "処理の開始を待っています");
  assert.equal(steps[0].startedAt, created);
});

test("実行中は段階の時刻と、生成の段階に生成方法の補足を出す", () => {
  const steps = chatJobProgressSteps(
    job({
      status: "running",
      started_at: "2026-10-04T00:00:01.000Z",
      steps: [
        {
          stage: "prepare_context",
          status: "done",
          started_at: "2026-10-04T00:00:01.000Z",
          finished_at: "2026-10-04T00:00:02.500Z",
        },
        { stage: "generate_sql", status: "running", started_at: "2026-10-04T00:00:02.500Z" },
        { stage: "safety_check", status: "pending" },
        { stage: "execute_sql", status: "skipped" },
        { stage: "format_results", status: "skipped" },
      ],
    }),
    "select_ai",
  );
  assert.deepEqual(summary(steps), [
    "queue:done",
    "prepare_context:done",
    "generate_sql:running",
    "safety_check:pending",
  ]);
  assert.equal(steps[0].label, "処理を開始しました");
  assert.equal(steps[0].finishedAt, "2026-10-04T00:00:01.000Z");
  assert.equal(steps[1].label, "質問と対象の表を準備しました");
  assert.equal(steps[1].finishedAt, "2026-10-04T00:00:02.500Z");
  assert.equal(steps[2].label, "SQL を生成しています");
  assert.equal(steps[2].detail, "Select AI");
  assert.equal(steps[2].finishedAt, undefined);
});

test("完了したジョブは安全性の確認に参照した表を補足し、全体の所要時間は作成から終了まで", () => {
  const done = job({
    status: "done",
    started_at: "2026-10-04T00:00:01.000Z",
    finished_at: "2026-10-04T00:00:12.000Z",
    steps: [
      { stage: "prepare_context", status: "done" },
      { stage: "generate_sql", status: "done" },
      { stage: "safety_check", status: "done" },
      { stage: "execute_sql", status: "skipped" },
      { stage: "format_results", status: "skipped" },
    ],
    result: {
      safety: { referenced_tables: ["APP.A", "APP.B", "APP.C", "APP.D"] },
    } as unknown as JobData["result"],
  });
  const steps = chatJobProgressSteps(done);
  assert.deepEqual(summary(steps), [
    "queue:done",
    "prepare_context:done",
    "generate_sql:done",
    "safety_check:done",
  ]);
  assert.equal(steps[3].detail, "APP.A, APP.B, APP.C ほか 1 件");
  assert.equal(chatJobElapsedMs(done), 12_000);
  assert.equal(chatJobElapsedMs(job({ status: "running" })), null);
});

test("失敗したジョブは失敗した段階を failed、その後ろの待機中の段階を未実行にする", () => {
  const steps = chatJobProgressSteps(
    job({
      status: "error",
      started_at: "2026-10-04T00:00:01.000Z",
      error_message: "Select AI に接続できません。",
      steps: [
        { stage: "prepare_context", status: "done" },
        { stage: "generate_sql", status: "error" },
        { stage: "safety_check", status: "pending" },
        { stage: "execute_sql", status: "pending" },
        { stage: "format_results", status: "pending" },
      ],
    }),
  );
  assert.deepEqual(summary(steps), [
    "queue:done",
    "prepare_context:done",
    "generate_sql:failed",
    "safety_check:skipped",
  ]);
  assert.equal(steps[2].label, "SQL を生成できませんでした");
  assert.equal(steps[3].label, "SQL の安全性の確認");
});

test("開始する前に失敗したジョブは開始の段階を失敗にする", () => {
  const steps = chatJobProgressSteps(job({ status: "error", error_message: "x" }));
  assert.deepEqual(summary(steps), [
    "queue:failed",
    "prepare_context:skipped",
    "generate_sql:skipped",
    "safety_check:skipped",
  ]);
});

test("停止したジョブは失敗にせず、止めた段階を未実行と「停止しました」にする", () => {
  const steps = chatJobProgressSteps(
    job({
      status: "error",
      started_at: "2026-10-04T00:00:01.000Z",
      error_code: "JOB_CANCELLED",
      error_message: "停止しました。",
      steps: [
        { stage: "prepare_context", status: "done" },
        { stage: "generate_sql", status: "error" },
        { stage: "safety_check", status: "pending" },
        { stage: "execute_sql", status: "pending" },
        { stage: "format_results", status: "pending" },
      ],
    }),
  );
  assert.ok(!steps.some((step) => step.status === "failed"));
  assert.equal(steps[2].status, "skipped");
  assert.equal(steps[2].detail, "停止しました");
});
