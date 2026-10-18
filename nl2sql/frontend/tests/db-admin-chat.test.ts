import assert from "node:assert/strict";
import test from "node:test";

import {
  DB_ADMIN_CHAT_HISTORY_LIMIT,
  dbAdminChatCanExecute,
  dbAdminChatConfirmed,
  dbAdminChatHistory,
  dbAdminChatProgressSteps,
  dbAdminExecutionSucceeded,
  dbAdminFailureText,
  dbAdminOutcomeSummary,
} from "../src/features/nl2sql/dbAdminChat.ts";
import type { DbAdminChatPlan, DbAdminExecuteData } from "../src/features/nl2sql/types.ts";

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

test("失敗の内容と続きの質問の文脈を作る", () => {
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
  assert.match(dbAdminOutcomeSummary(failed), /^失敗: /);
  assert.equal(dbAdminOutcomeSummary(result()), "成功");
  const turns = Array.from({ length: DB_ADMIN_CHAT_HISTORY_LIMIT + 2 }, (_, index) => ({
    question: `質問 ${index}`,
    sql: "SELECT 1 FROM DUAL",
    outcome: "",
  }));
  const history = dbAdminChatHistory(turns);
  assert.equal(history.length, DB_ADMIN_CHAT_HISTORY_LIMIT);
  assert.equal(history.at(-1)?.question, `質問 ${DB_ADMIN_CHAT_HISTORY_LIMIT + 1}`);
});
