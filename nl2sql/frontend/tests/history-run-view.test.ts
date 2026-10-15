// 実行履歴（#1955）の取得条件と、行 → 共通の実行履歴の形への写し。
import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_HISTORY_FILTERS,
  hasActiveHistoryFilters,
  historyDetailPath,
  historyExecutor,
  historyKind,
  historyOwner,
  historyRequestUrl,
  historyRunStatus,
  historyTimelineSteps,
  normalizeHistoryFilters,
} from "../src/features/nl2sql/historyManagementState.ts";
import type { HistoryItem } from "../src/features/nl2sql/types.ts";

const base: HistoryItem = {
  id: "h1",
  question: "先月の売上は？",
  engine: "select_ai",
  generated_sql: "SELECT 1 FROM DUAL",
  created_at: "2026-10-15T00:00:00+00:00",
  elapsed_ms: 320,
  feedback_rating: null,
  profile_id: "sales",
  profile_name: "販売分析",
  rewritten_question: "",
  executable_sql: "SELECT 1 FROM DUAL",
  safety_is_safe: true,
  result_row_count: 1,
  result_columns: ["1"],
  feedback_comment: "",
};

test("history request URL maps the common filters to the backend parameters", () => {
  const now = new Date("2026-10-15T12:00:00.000Z");
  assert.equal(
    historyRequestUrl({ offset: 0, limit: 25, filters: DEFAULT_HISTORY_FILTERS }, now),
    "/api/nl2sql/history?limit=25&offset=0"
  );
  const url = new URL(
    historyRequestUrl(
      {
        offset: 50,
        limit: 25,
        filters: { search: "  売上 ", status: "blocked", kind: "chat", period: "24h", executor: "self" },
      },
      now
    ),
    "http://localhost"
  );
  assert.equal(url.searchParams.get("q"), "売上");
  assert.equal(url.searchParams.get("status"), "blocked");
  assert.equal(url.searchParams.get("kind"), "chat");
  assert.equal(url.searchParams.get("since"), "2026-10-14T12:00:00.000Z");
  // 画面の「自分が実行」（self）は backend の mine（#1954）。
  assert.equal(url.searchParams.get("executor"), "mine");
  assert.equal(url.searchParams.get("offset"), "50");
});

test("stored filters fall back to the defaults when an old or broken value is read", () => {
  assert.deepEqual(normalizeHistoryFilters(null), DEFAULT_HISTORY_FILTERS);
  // 古い版の「mine」・NL2SQL が写せない状態（failed）は「すべて」として読む。
  assert.deepEqual(
    normalizeHistoryFilters({ search: "x", status: "failed", kind: "api", period: "1y", executor: "mine" }),
    { ...DEFAULT_HISTORY_FILTERS, search: "x" }
  );
  assert.equal(hasActiveHistoryFilters(DEFAULT_HISTORY_FILTERS), false);
  assert.equal(hasActiveHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, search: "  " }), false);
  assert.equal(hasActiveHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, period: "7d" }), true);
});

test("history rows map to the common status, kind, executor and owner", () => {
  assert.equal(historyRunStatus(base), "succeeded");
  assert.equal(historyRunStatus({ ...base, safety_is_safe: false }), "blocked");
  assert.equal(historyKind({ ...base, kind: "chat" }), "chat");
  // `kind` の無い古い応答は会話の持ち主の有無で決める。
  assert.equal(historyKind({ ...base, conversation_owner_display_name: "山田" }), "chat");
  assert.equal(historyKind(base), "query");

  const own = { ...base, actor_user_uuid: "u1", executor_display_name: "山田", executed_by_me: true };
  assert.deepEqual(historyExecutor(own), { name: "山田", isSelf: true });
  assert.deepEqual(historyExecutor({ ...base, actor_user_uuid: "" }), { name: "記録なし", isSelf: true });
  assert.deepEqual(historyExecutor({ ...base, actor_user_uuid: "gone", executed_by_me: false }), {
    name: "削除済みのユーザー",
    isSelf: false,
  });

  // 持ち主は実行者と違うときだけ。
  assert.equal(historyOwner(base), null);
  assert.equal(historyOwner({ ...own, conversation_owner_display_name: "山田", owned_by_me: true }), null);
  assert.deepEqual(
    historyOwner({ ...own, executed_by_me: false, executor_display_name: "管理者", conversation_owner_display_name: "山田", owned_by_me: true }),
    { name: "山田", isSelf: true }
  );
  assert.equal(
    historyOwner({ ...own, executed_by_me: false, conversation_owner_display_name: "山田", owned_by_me: false }),
    null
  );
});

test("timeline steps follow the recorded stages with engine attempts under SQL generation", () => {
  const steps = historyTimelineSteps({
    ...base,
    safety_is_safe: false,
    stage_timings: [
      { stage: "prepare_context", elapsed_ms: 20 },
      { stage: "generate_sql", elapsed_ms: 190 },
      { stage: "unknown_stage", elapsed_ms: 1 },
      { stage: "safety_check", elapsed_ms: 15 },
    ],
    engine_timings: [
      { engine: "select_ai_agent", elapsed_ms: 80, status: "failed", error: "team execution failed" },
      { engine: "enterprise_ai_direct", elapsed_ms: 110, status: "success" },
    ],
  });
  assert.deepEqual(
    steps.map((step) => [step.id, step.status, step.level, step.durationMs]),
    [
      ["prepare_context-0", "done", 0, 20],
      ["generate_sql-1", "done", 0, 190],
      ["engine-0", "failed", 1, 80],
      ["engine-1", "done", 1, 110],
      // ブロックした行は安全性の確認を失敗にする。未知の段は出さない。
      ["safety_check-3", "failed", 0, 15],
    ]
  );
  assert.equal(steps[2].error, "team execution failed");
  assert.ok(steps.every((step) => !step.label.includes("_")));
  assert.deepEqual(historyTimelineSteps(base), []);
});

test("detail path opens the run by id", () => {
  assert.equal(historyDetailPath("/history", "run 1"), "/history?id=run+1");
});
