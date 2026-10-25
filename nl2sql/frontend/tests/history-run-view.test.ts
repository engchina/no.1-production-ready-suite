import {chatProgressStepsFromEvents, parseChatProgressEvents, chatProgressTimelineStep} from "@production-ready/ui";
import {CHAT_PROGRESS_STEP_DEFINITIONS, historyTracePath} from "../src/features/nl2sql/chatProgress.ts";
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

const TRACE_ID = "b".repeat(32);
const ROOT = "0".repeat(16);

/** 実行の span の木（実行の全体の下に工程の span。合成の値）。 */
function spanEvents(): unknown[] {
  const at = { emitted_at: "2026-10-23T00:00:02Z", started_at: "2026-10-23T00:00:00Z" };
  const event = (seq: number, value: Record<string, unknown>) => ({ schema_version: 1, target_id: "synthetic", seq, attempt: 0, type: "step", trace_id: TRACE_ID, ...at, ...value });
  return [
    event(1, { step_id: ROOT, name: "nl2sql.execute", root: true, status: "running" }),
    event(2, { step_id: "1".padStart(16, "0"), kind: "generate_sql", name: "nl2sql.generate_sql", parent_id: ROOT, status: "done", finished_at: "2026-10-23T00:00:02Z", params: { engine: "enterprise_ai_direct" } }),
    event(3, { step_id: "2".padStart(16, "0"), name: "gen_ai.chat", parent_id: "1".padStart(16, "0"), status: "done", finished_at: "2026-10-23T00:00:01Z" }),
    event(4, { step_id: "3".padStart(16, "0"), kind: "safety_check", name: "nl2sql.safety_check", parent_id: ROOT, status: "skipped", finished_at: "2026-10-23T00:00:02Z", params: { safety_check_skipped: true }, details: { fields: [{ label: "結果", value: "合成の結果" }] } }),
    event(5, { step_id: ROOT, name: "nl2sql.execute", root: true, status: "done", finished_at: "2026-10-23T00:00:02Z" }),
    { schema_version: 1, target_id: "synthetic", seq: 6, attempt: 0, type: "terminal", status: "done", emitted_at: "2026-10-23T00:00:02Z" },
  ];
}

test("実行の詳細の処理の経過はチャットと同じ span の木（#3226）", () => {
  // 行の処理の経過の API（span の表の snapshot）を、チャットと同じ工程の定義で行にする。
  const events = parseChatProgressEvents(spanEvents());
  const rows = chatProgressStepsFromEvents(events, CHAT_PROGRESS_STEP_DEFINITIONS).map(chatProgressTimelineStep);
  // 実行の全体は行にせず、段の中の span（モデルの呼び出し）は段の下に入れ子。
  assert.deepEqual(rows.map((row) => [row.status, row.level ?? 0]), [["done", 0], ["done", 1], ["skipped", 0]]);
  assert.equal(rows[0].label, "SQL を生成しました");
  assert.equal(historyTracePath(base.id), "/api/nl2sql/history/h1/trace");
});
