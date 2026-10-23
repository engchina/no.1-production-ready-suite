import {chatProgressStepsFromEvents, parseChatProgressEvents, chatProgressTimelineStep} from "@production-ready/ui";
import {CHAT_PROGRESS_STEP_DEFINITIONS} from "../src/features/nl2sql/chatProgress.ts";
// 実行履歴（#1955）の取得条件と、行 → 共通の実行履歴の形への写し。
import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_HISTORY_FILTERS,
  DEFAULT_HISTORY_KIND,
  HISTORY_KIND_FILTERS,
  hasActiveHistoryFilters,
  historyConversationHref,
  historyDetailPath,
  historyExecutor,
  historyKind,
  historyKindDisplay,
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
    // 種類の既定は「品質評価以外」（品質評価の Run が MCP で呼んだ実行を既定で隠す。#1986）。
    "/api/nl2sql/history?limit=25&offset=0&kind=not_evaluation"
  );
  assert.equal(
    historyRequestUrl({ offset: 0, limit: 25, filters: { ...DEFAULT_HISTORY_FILTERS, kind: "all" } }, now),
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
  // 種類は既定（品質評価以外）が基準。「すべて」を選ぶと絞り込みとして数える（#1986）。
  assert.equal(DEFAULT_HISTORY_FILTERS.kind, DEFAULT_HISTORY_KIND);
  assert.equal(hasActiveHistoryFilters({ ...DEFAULT_HISTORY_FILTERS, kind: "all" }), true);
  assert.deepEqual(HISTORY_KIND_FILTERS, ["not_evaluation", "all", "chat", "query", "mcp", "evaluation"]);
  assert.equal(normalizeHistoryFilters({ kind: "evaluation" }).kind, "evaluation");
});

test("history rows map to the common status, kind, executor and owner", () => {
  assert.equal(historyRunStatus(base), "succeeded");
  assert.equal(historyRunStatus({ ...base, safety_is_safe: false }), "blocked");
  assert.equal(historyKind({ ...base, kind: "chat" }), "chat");
  // `kind` の無い古い応答は会話の持ち主の有無で決める。
  assert.equal(historyKind({ ...base, conversation_owner_display_name: "山田" }), "chat");
  assert.equal(historyKind(base), "query");
  // MCP・品質評価（#1986）。`kind` の無い応答はきっかけ（`run_source`）を先に見る。
  assert.equal(historyKind({ ...base, kind: "evaluation" }), "evaluation");
  assert.equal(historyKind({ ...base, run_source: "mcp" }), "mcp");
  assert.equal(historyKindDisplay({ ...base, kind: "evaluation", agent_run_id: "run-1" }), "品質評価");
  assert.equal(historyKindDisplay({ ...base, kind: "mcp", api_key_name: "会社の PC" }), "MCP（会社の PC）");
  assert.equal(historyKindDisplay({ ...base, kind: "mcp", agent_run_id: "run-1" }), "MCP（Agent）");
  assert.equal(historyKindDisplay({ ...base, kind: "mcp" }), "MCP");
  assert.equal(historyKindDisplay(base), "SQL 生成");

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
    steps.map((step) => [step.id, step.status, step.level, step.durationMs, step.offsetMs]),
    [
      ["prepare_context-0", "done", 0, 20, 0],
      ["generate_sql-1", "done", 0, 190, 20],
      // 試行は生成の段の中に重ねる（後の段の位置をずらさない）。
      ["engine-0", "failed", 1, 80, 20],
      ["engine-1", "done", 1, 110, 100],
      // ブロックした行は安全性の確認を失敗にする。未知の段は出さない（位置には数える）。
      ["safety_check-3", "failed", 0, 15, 211],
    ]
  );
  // 段を開いて見る詳細（#2294）: 試行の失敗の文・安全性の確認の結果。書き換えの無い準備は詳細を付けない。
  assert.deepEqual(steps[2].details, { fields: [{ label: "エラー", value: "team execution failed" }] });
  assert.equal(steps[3].details, undefined);
  assert.equal(steps[0].details, undefined);
  assert.deepEqual(steps[4].details, { fields: [{ label: "結果", value: "遮断" }] });
  assert.ok(steps.every((step) => !step.label.includes("_")));
  assert.deepEqual(historyTimelineSteps(base), []);
});

test("timeline step details come from the recorded rewritten question, safety and rows", () => {
  const steps = historyTimelineSteps({
    ...base,
    rewritten_question: "書き換えた質問 A",
    result_row_count: 1234,
    result_columns: ["COL_A", "COL_B"],
    stage_timings: [
      { stage: "prepare_context", elapsed_ms: 20 },
      { stage: "safety_check", elapsed_ms: 15 },
      { stage: "execute_sql", elapsed_ms: 40 },
    ],
  });
  assert.deepEqual(
    steps.map((step) => step.details),
    [
      { fields: [{ label: "生成に使った質問", value: "書き換えた質問 A" }] },
      { fields: [{ label: "結果", value: "安全" }] },
      {
        fields: [
          { label: "取得した行数", value: "1,234 行" },
          { label: "列", value: "COL_A, COL_B", mono: true },
        ],
      },
    ]
  );
});

test("detail path opens the run by id", () => {
  assert.equal(historyDetailPath("/history", "run 1"), "/history?id=run+1");
});

test("「会話を開く」はチャットの往復だけ、会話を見られる人に出す (#2343)", () => {
  const chat: HistoryItem = { ...base, kind: "chat", session_id: "conv-1", owned_by_me: true };
  const canOpen = { seesAllUsers: false, canOpenChat: true };
  assert.equal(historyConversationHref(chat, canOpen), "/chat?profile_id=sales&conversation_id=conv-1");
  // SQL 生成・MCP・品質評価の実行は開く元の会話が無い。
  for (const kind of ["query", "mcp", "evaluation"] as const) {
    assert.equal(historyConversationHref({ ...chat, kind }, canOpen), null);
  }
  assert.equal(historyConversationHref({ ...chat, session_id: "" }, canOpen), null);
  assert.equal(historyConversationHref({ ...chat, owned_by_me: false }, canOpen), null);
  assert.equal(historyConversationHref(chat, { seesAllUsers: false, canOpenChat: false }), null);
  // system_admin は他の利用者の会話も開ける。
  assert.equal(
    historyConversationHref({ ...chat, owned_by_me: false }, { seesAllUsers: true, canOpenChat: true }),
    "/chat?profile_id=sales&conversation_id=conv-1"
  );
});

test("履歴はチャットと同じイベントを使い、旧い計時表から工程を追加しない", () => {
  const events = parseChatProgressEvents(["generate_sql", "safety_check", "execute_sql"].map((kind, index) => ({
    schema_version: 1, target_id: "synthetic", seq: index + 1, attempt: 0, type: "step", step_id: kind, kind,
    status: index === 1 ? "skipped" : "done", emitted_at: "2026-10-23T00:00:02Z", started_at: "2026-10-23T00:00:00Z", finished_at: "2026-10-23T00:00:02Z",
    params: {engine: "enterprise_ai_direct", rows: 1, safety_check_skipped: true}, details: {fields: [{label: "結果", value: "合成の結果"}]},
  })));
  const rows = historyTimelineSteps({...base, progress_events: events});
  const actual = chatProgressStepsFromEvents(events, CHAT_PROGRESS_STEP_DEFINITIONS).map(chatProgressTimelineStep);
  assert.deepEqual(rows.map(({offsetMs: _offset, durationMs: _duration, level: _level, ...row}) => row), actual);
  assert.deepEqual(rows.map((row) => row.status), ["done", "skipped", "done"]);
});
