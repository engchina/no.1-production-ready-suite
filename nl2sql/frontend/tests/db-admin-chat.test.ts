import assert from "node:assert/strict";
import test from "node:test";

import {
  dbAdminChatCanExecute,
  dbAdminChatConfirmed,
  dbAdminChatProgressSteps,
  dbAdminChatSavedExecution,
  dbAdminExecutionSucceeded,
  dbAdminFailureText,
} from "../src/features/nl2sql/dbAdminChat.ts";
import type {
  DbAdminChatPlan,
  DbAdminChatTurn,
  DbAdminExecuteData,
} from "../src/features/nl2sql/types.ts";

function plan(overrides: Partial<DbAdminChatPlan> = {}): DbAdminChatPlan {
  return {
    sql: "SELECT 1 FROM DUAL",
    statements: [
      {
        index: 1,
        statement_type: "SELECT",
        sql: "SELECT 1 FROM DUAL",
        select_only: true,
        implicit_commit: false,
        blocked_reason: "",
      },
    ],
    read_only: true,
    requires_confirmation: false,
    confirmation_target: "",
    implicit_commit: false,
    blocked: false,
    blocked_reasons: [],
    ...overrides,
  };
}

function result(overrides: Partial<DbAdminExecuteData> = {}): DbAdminExecuteData {
  return {
    executed: true,
    runtime: "oracle",
    statements: [
      {
        index: 1,
        statement_type: "SELECT",
        status: "executed",
        sql: "SELECT 1 FROM DUAL",
        message: "1 rows",
        elapsed_ms: 1,
      },
    ],
    committed: false,
    rolled_back: false,
    warnings: [],
    timing: { created_at: "2026-10-18T00:00:00Z", elapsed_ms: 5 },
    ...overrides,
  } as DbAdminExecuteData;
}

test("SELECT は確認語なしで実行でき、それ以外は確認語が一致したときだけ実行できる", () => {
  assert.equal(dbAdminChatCanExecute(plan(), ""), true);
  const mutation = plan({ read_only: false, requires_confirmation: true, confirmation_target: "ADMIN_EXECUTE" });
  assert.equal(dbAdminChatCanExecute(mutation, ""), false);
  assert.equal(dbAdminChatCanExecute(mutation, "admin_execute"), false);
  assert.equal(dbAdminChatCanExecute(mutation, " ADMIN_EXECUTE "), true);
  const drop = plan({ read_only: false, requires_confirmation: true, confirmation_target: "APP.T1" });
  assert.equal(dbAdminChatConfirmed(drop, "ADMIN_EXECUTE"), false);
  assert.equal(dbAdminChatConfirmed(drop, "APP.T1"), true);
  assert.equal(dbAdminChatCanExecute(plan({ blocked: true }), ""), false);
  assert.equal(dbAdminChatCanExecute(plan({ statements: [] }), ""), false);
});

test("処理の経過は生成・SQL の確認・実行の段階をチャットと同じ名前で出す", () => {
  const steps = dbAdminChatProgressSteps({
    sentAtMs: 1_000,
    generatedAtMs: 4_000,
    plan: plan(),
    execution: {
      status: "done",
      startedAtMs: 5_000,
      finishedAtMs: 6_000,
      result: result({
        select_result: { columns: ["N"], rows: [{ N: 1 }], total: 1, returned_count: 1 },
      }),
    },
  });
  assert.deepEqual(
    steps.map((step) => [step.id, step.status]),
    [
      ["generate_sql", "done"],
      ["safety_check", "done"],
      ["execute_sql", "done"],
    ],
  );
  assert.equal(steps[0]?.label, "SQL を生成しました");
  assert.equal(steps[2]?.detail, "1 行");
  const blocked = dbAdminChatProgressSteps({
    sentAtMs: 1_000,
    generatedAtMs: 2_000,
    plan: plan({ blocked: true }),
  });
  assert.equal(blocked[1]?.status, "failed");
  const empty = dbAdminChatProgressSteps({ sentAtMs: 1_000, generatedAtMs: 2_000, plan: plan({ statements: [] }) });
  assert.equal(empty.length, 1);
});

test("失敗の内容を AI のエラー分析に渡す形にする", () => {
  const failed = result({
    executed: false,
    statements: [
      {
        index: 1,
        statement_type: "DROP",
        status: "error",
        sql: "DROP TABLE T9",
        error_message: "ORA-00942: table or view does not exist",
        message: "",
        elapsed_ms: 1,
      },
    ],
    warnings: ["rollback しました"],
  });
  assert.equal(dbAdminExecutionSucceeded(failed), false);
  assert.match(dbAdminFailureText(failed), /#1 DROP: ORA-00942/);
  assert.match(dbAdminFailureText(failed), /rollback しました/);
});

function savedTurn(overrides: Partial<DbAdminChatTurn> = {}): DbAdminChatTurn {
  return {
    turn_id: "turn-1",
    question: "数量",
    created_at: "2026-10-18T00:00:00Z",
    generated_at: "2026-10-18T00:00:02Z",
    explanation: "",
    warnings: [],
    context_tables: [],
    model: "test-text-model",
    plan: plan(),
    revision: 0,
    corrected: false,
    last_execution: {
      executed_at: "2026-10-18T00:00:10Z",
      revision: 0,
      confirmation_given: false,
      succeeded: true,
      row_count: 12,
      column_count: 3,
      has_more: false,
      outcome: "成功（12 行）",
      result: result({ timing: { created_at: "2026-10-18T00:00:09Z", elapsed_ms: 400 } as DbAdminExecuteData["timing"] }),
    },
    ...overrides,
  };
}

test("開き直したターンは、保存した最後の実行の要約から実行の状態を作る（行は持たない）", () => {
  const execution = dbAdminChatSavedExecution(savedTurn());
  assert.ok(execution);
  assert.equal(execution.status, "done");
  assert.equal(execution.rowCount, 12);
  assert.equal(execution.finishedAtMs, Date.parse("2026-10-18T00:00:10Z"));
  assert.equal(execution.startedAtMs, Date.parse("2026-10-18T00:00:10Z") - 400);
  const steps = dbAdminChatProgressSteps({ sentAtMs: 0, generatedAtMs: 2_000, plan: plan(), execution });
  // 行は保存していないので、行数は要約から出す。
  assert.equal(steps[2]?.detail, "12 行");
  // AI のエラー分析で直した（版が進んだ）ターンの前の版の実行は使わない。
  assert.equal(dbAdminChatSavedExecution(savedTurn({ revision: 1 })), null);
  assert.equal(dbAdminChatSavedExecution(savedTurn({ last_execution: null })), null);
  const failed = savedTurn();
  if (failed.last_execution) failed.last_execution.succeeded = false;
  assert.equal(dbAdminChatSavedExecution(failed)?.status, "failed");
});
