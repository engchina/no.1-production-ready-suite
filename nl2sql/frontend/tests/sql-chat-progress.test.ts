import assert from "node:assert/strict";
import test from "node:test";

import {
  isChatProgressActive,
  reduceChatProgressSteps,
  type ChatProgressStepsState,
} from "@production-ready/ui";

import {
  chatJobElapsedMs,
  chatJobProgressKey,
  chatJobProgressSteps,
  chatSubmitProgressSteps,
} from "../src/features/nl2sql/chatProgress.ts";
import type { JobData, JobStepStatus } from "../src/features/nl2sql/types.ts";

// #1145: チャットのジョブの段階を 3 製品共通の段階の形にする。
// #1176: チャットも SQL 生成の画面と同じく実行まで行う。ジョブの全段階（実行・結果の整形を含む）を写す。

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

test("worker が始めていないジョブは「処理の開始を待っています」を今の段階にする", () => {
  const steps = chatJobProgressSteps(job({}));
  assert.deepEqual(summary(steps), [
    "queue:running",
    "prepare_context:pending",
    "generate_sql:pending",
    "safety_check:pending",
    "execute_sql:pending",
    "format_results:pending",
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
        { stage: "execute_sql", status: "pending" },
        { stage: "format_results", status: "pending" },
      ],
    }),
    "select_ai",
  );
  assert.deepEqual(summary(steps), [
    "queue:done",
    "prepare_context:done",
    "generate_sql:running",
    "safety_check:pending",
    "execute_sql:pending",
    "format_results:pending",
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
      { stage: "execute_sql", status: "done" },
      { stage: "format_results", status: "done" },
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
    "execute_sql:done",
    "format_results:done",
  ]);
  assert.equal(steps[3].detail, "APP.A, APP.B, APP.C ほか 1 件");
  assert.equal(steps[4].label, "SQL を実行しました");
  assert.equal(steps[5].label, "結果をまとめました");
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
    "execute_sql:skipped",
    "format_results:skipped",
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
    "execute_sql:skipped",
    "format_results:skipped",
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

// --- #1176: 終端になるまで今の段階の行が必ずある -----------------------------------------------

const STAGES = ["prepare_context", "generate_sql", "safety_check", "execute_sql", "format_results"];

test("安全性の確認の後（実行・結果の整形の間）も今の段階を出す（「N ステップ完了」だけにしない）", () => {
  const formatting = chatJobProgressSteps(
    job({
      status: "running",
      started_at: "2026-10-04T00:00:01.000Z",
      steps: [
        { stage: "prepare_context", status: "done" },
        { stage: "generate_sql", status: "done" },
        { stage: "safety_check", status: "done" },
        { stage: "execute_sql", status: "done" },
        { stage: "format_results", status: "running", started_at: "2026-10-04T00:00:30.000Z" },
      ],
    }),
  );
  const current = formatting.find((step) => step.status === "running");
  assert.equal(current?.id, "format_results");
  assert.equal(current?.label, "結果をまとめています");
  assert.equal(current?.startedAt, "2026-10-04T00:00:30.000Z");

  const executing = chatJobProgressSteps(
    job({
      status: "running",
      steps: [
        { stage: "prepare_context", status: "done" },
        { stage: "generate_sql", status: "done" },
        { stage: "safety_check", status: "done" },
        { stage: "execute_sql", status: "running" },
        { stage: "format_results", status: "pending" },
      ],
    }),
  );
  assert.equal(executing.find((step) => step.status === "running")?.label, "SQL を実行しています");

  // 実行の権限が無い（生成だけ）ターンは、実行を「未実行」にして結果の整形へ進む。
  const generationOnly = chatJobProgressSteps(
    job({
      status: "running",
      steps: [
        { stage: "prepare_context", status: "done" },
        { stage: "generate_sql", status: "done" },
        { stage: "safety_check", status: "done" },
        { stage: "execute_sql", status: "skipped" },
        { stage: "format_results", status: "running" },
      ],
    }),
  );
  assert.deepEqual(summary(generationOnly).slice(4), ["execute_sql:skipped", "format_results:running"]);
});

test("終端でないジョブは、段階の状態の組み合わせのどれでも今の段階（実行中か待機中）がある", () => {
  const statuses: JobStepStatus[] = ["pending", "running", "done", "skipped"];
  // 段階は前から順に進む（完了・未実行 → 実行中 → 待機中）。backend が書く組み合わせをすべて作る。
  const combos: JobStepStatus[][] = [];
  for (let current = 0; current <= STAGES.length; current += 1) {
    for (const finished of ["done", "skipped"] as const) {
      for (const now of statuses) {
        combos.push(
          STAGES.map((_, index) =>
            index < current ? finished : index === current ? now : "pending",
          ),
        );
      }
    }
  }
  for (const jobStatus of ["pending", "running"] as const) {
    for (const combo of combos) {
      const steps = chatJobProgressSteps(
        job({
          status: jobStatus,
          started_at: jobStatus === "running" ? "2026-10-04T00:00:01.000Z" : null,
          steps: STAGES.map((stage, index) => ({ stage, status: combo[index] })),
        }),
      );
      const label = `${jobStatus} ${combo.join(",")}`;
      // backend のジョブの段階はすべて写す（写し漏れがあると、その間は今の段階の行が消える）。
      assert.deepEqual(
        steps.map((step) => step.id),
        ["queue", ...STAGES],
        label,
      );
      assert.ok(
        steps.some((step) => step.status === "running" || step.status === "pending"),
        `今の段階が無い: ${label}`,
      );
      assert.ok(isChatProgressActive(steps), `終端に見える: ${label}`);
    }
  }
});

test("終端のジョブは実行中・待機中の段階を残さない", () => {
  for (const status of ["done", "error"] as const) {
    const steps = chatJobProgressSteps(
      job({
        status,
        started_at: "2026-10-04T00:00:01.000Z",
        error_message: status === "error" ? "x" : null,
        steps: STAGES.map((stage, index) => ({
          stage,
          status: index < 2 ? "done" : index === 2 ? "running" : "pending",
        })),
      }),
    );
    assert.ok(!steps.some((step) => step.status === "running" || step.status === "pending"));
  }
});

// #1358: 会話の polling の応答から作った段階の一覧を、共有の規則（ChatProgress が内部で使う）に流す。
function displayed(jobs: JobData[]): string[][] {
  let state: ChatProgressStepsState | null = null;
  return jobs.map((item) => {
    state = reduceChatProgressSteps(state, {
      key: chatJobProgressKey(item),
      steps: chatJobProgressSteps(item),
      active: item.status === "pending" || item.status === "running",
    });
    return summary(state.steps);
  });
}

const stagesWith = (statuses: JobStepStatus[]) =>
  (["prepare_context", "generate_sql", "safety_check", "execute_sql", "format_results"] as const).map(
    (stage, index) => ({ stage, status: statuses[index] }),
  );

test("古い応答（取り直しの前の snapshot）が届いても、完了した段階を戻さない（#1358）", () => {
  const newer = job({
    status: "running",
    started_at: created,
    attempt: 1,
    steps: stagesWith(["done", "done", "running", "pending", "pending"]),
  });
  const older = job({
    status: "running",
    started_at: created,
    attempt: 1,
    steps: stagesWith(["done", "running", "pending", "pending", "pending"]),
  });
  const [, afterOlder] = displayed([newer, older]);
  assert.deepEqual(afterOlder, [
    "queue:done",
    "prepare_context:done",
    "generate_sql:done",
    "safety_check:running",
    "execute_sql:pending",
    "format_results:pending",
  ]);
});

test("引き継いだ実行（attempt が変わった）では段階の一覧を作り直す（#1358）", () => {
  const first = job({
    status: "running",
    started_at: created,
    attempt: 1,
    steps: stagesWith(["done", "done", "running", "pending", "pending"]),
  });
  const reclaimed = job({
    status: "running",
    started_at: created,
    attempt: 2,
    steps: stagesWith(["running", "pending", "pending", "pending", "pending"]),
  });
  assert.equal(chatJobProgressKey(first), "j1#1");
  assert.equal(chatJobProgressKey({ job_id: "j1" }), "j1#0");
  const [, afterReclaim] = displayed([first, reclaimed]);
  assert.deepEqual(afterReclaim, [
    "queue:done",
    "prepare_context:running",
    "generate_sql:pending",
    "safety_check:pending",
    "execute_sql:pending",
    "format_results:pending",
  ]);
});
