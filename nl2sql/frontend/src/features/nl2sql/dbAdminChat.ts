// DB 管理チャット（#2264）の画面のロジック。会話とターンはサーバーが保存し（#2319）、続きの質問の
// 文脈もサーバーが保存した会話から作る。結果の行は保存しない（開き直したら要約と「もう一度実行」）。
//
// 生成した SQL はサーバーが文に分け（PL/SQL のブロックは 1 文）、文ごとに分類・実行する（#2333。
// SQL Developer などのワークシートと同じ）。「すべて実行」は上から順に実行し、最初のエラーで止める。
//
// 処理の経過は NL2SQL のチャットと同じ段階の名前（`CHAT_PROGRESS_STEP_DEFINITIONS`）と共通の
// `ChatProgress` で出す（生成 → SQL の確認（サーバーの分類）→ 実行）。
import type { ChatProgressStep, ChatProgressStepStatus } from "@production-ready/ui";

// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import { t } from "../../lib/i18n";
import { CHAT_PROGRESS_STEP_DEFINITIONS } from "./chatProgress";
import type {
  DbAdminChatExecuteData,
  DbAdminChatPlan,
  DbAdminChatRunAllSummary,
  DbAdminChatStatementLastExecution,
  DbAdminChatStatementOutcome,
  DbAdminChatStatementPlan,
  DbAdminChatTurn,
  DbAdminExecuteData,
} from "./types";

/** 会話の往復の上限（backend の DB_ADMIN_CHAT_TURN_LIMIT と同じ。超えたら新しい会話を勧める）。 */
export const DB_ADMIN_CHAT_TURN_LIMIT = 50;

/** 1 文の実行の状態。 */
export interface DbAdminChatExecution {
  /** `not_run` は「すべて実行」が前の文の失敗で止めたため実行しなかった文。 */
  status: "running" | "done" | "failed" | "not_run";
  startedAtMs: number;
  finishedAtMs?: number;
  result?: DbAdminExecuteData | null;
  /** 開き直したときの保存した要約（行は持たない）。この画面で受け取った結果には無い。 */
  saved?: DbAdminChatStatementLastExecution | null;
  /** 実行の要求そのものの失敗（権限・通信・timeout）。SQL の失敗は `result` の文の状態。 */
  requestError?: unknown;
}

/** ターンの「すべて実行」の状態。 */
export interface DbAdminChatRunAll {
  status: "running" | "done" | "failed";
  startedAtMs: number;
  finishedAtMs?: number;
  data?: DbAdminChatExecuteData | null;
  /** 開き直したときの保存した要約。 */
  saved?: DbAdminChatRunAllSummary | null;
  requestError?: unknown;
}

/** 1 往復の実行の状態（文の番号ごとの実行と「すべて実行」）。 */
export interface DbAdminChatTurnExecutions {
  statements: Record<number, DbAdminChatExecution>;
  runAll: DbAdminChatRunAll | null;
}

/** 文の分類（バッジ）: 読み取り専用 / 変更あり / 実行できない。 */
export type DbAdminChatStatementKind = "readOnly" | "change" | "blocked";

export function dbAdminChatStatementKind(statement: DbAdminChatStatementPlan): DbAdminChatStatementKind {
  if (statement.blocked_reason) return "blocked";
  return statement.select_only ? "readOnly" : "change";
}

function savedTimes(executedAt: string, elapsedMs: number): { startedAtMs: number; finishedAtMs?: number } {
  const finishedAtMs = Date.parse(executedAt);
  return Number.isFinite(finishedAtMs)
    ? { startedAtMs: finishedAtMs - elapsedMs, finishedAtMs }
    : { startedAtMs: Date.now() };
}

/** 保存した 1 文の最後の実行の要約を、画面の実行の状態にする（行は保存していない）。 */
export function dbAdminChatSavedStatementExecution(
  saved: DbAdminChatStatementLastExecution,
): DbAdminChatExecution {
  return {
    status: saved.status === "succeeded" ? "done" : saved.status === "not_run" ? "not_run" : "failed",
    ...savedTimes(saved.executed_at, saved.result?.timing?.elapsed_ms ?? 0),
    result: saved.result,
    saved,
  };
}

/** 保存したターンの文ごとの最後の実行と「すべて実行」の要約を、画面の実行の状態にする。 */
export function dbAdminChatSavedExecutions(turn: DbAdminChatTurn): DbAdminChatTurnExecutions {
  const statements: Record<number, DbAdminChatExecution> = {};
  for (const saved of turn.statement_executions ?? []) {
    statements[saved.index] = dbAdminChatSavedStatementExecution(saved);
  }
  const last = turn.last_run_all;
  return {
    statements,
    runAll: last
      ? { status: last.succeeded ? "done" : "failed", ...savedTimes(last.executed_at, 0), saved: last }
      : null,
  };
}

/** 実行の応答の 1 文の結果を、画面の実行の状態にする。 */
export function dbAdminChatExecutionFromOutcome(
  outcome: DbAdminChatStatementOutcome,
  data: DbAdminChatExecuteData,
  startedAtMs: number,
  finishedAtMs: number,
): DbAdminChatExecution {
  if (outcome.status === "not_run") return { status: "not_run", startedAtMs, finishedAtMs, result: null };
  if (outcome.status === "confirmation_required") {
    // 画面は確認語が合うまで押せないので、ここに来るのは確認語の規則が変わったときだけ。
    return {
      status: "failed",
      startedAtMs,
      finishedAtMs,
      result: null,
      requestError: new Error(data.warnings[0] ?? t("dbAdminChat.execute.failed")),
    };
  }
  return {
    status: outcome.status === "succeeded" ? "done" : "failed",
    startedAtMs,
    finishedAtMs,
    result: outcome.result,
  };
}

/** 処理の経過の「実行」の段に出す、いちばん新しい実行（1 文か「すべて実行」）。 */
export function dbAdminChatLatestExecution(executions: DbAdminChatTurnExecutions): DbAdminChatExecution | null {
  const candidates: DbAdminChatExecution[] = Object.values(executions.statements).filter(
    (item) => item.status !== "not_run",
  );
  let latest: DbAdminChatExecution | null = null;
  for (const item of candidates) {
    if (!latest || item.startedAtMs >= latest.startedAtMs) latest = item;
  }
  const runAll = executions.runAll;
  if (runAll && (!latest || runAll.startedAtMs >= latest.startedAtMs)) {
    // 「すべて実行」の行数は文ごとの結果に出すので、経過の段には出さない。
    return {
      status: runAll.status,
      startedAtMs: runAll.startedAtMs,
      finishedAtMs: runAll.finishedAtMs,
      result: null,
    };
  }
  return latest;
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

/** 段階の名前はチャットと同じ定義から作る（文言を 2 つの画面で分けない）。 */
function stepLabel(stage: "generate_sql" | "safety_check" | "execute_sql", status: ChatProgressStepStatus) {
  return CHAT_PROGRESS_STEP_DEFINITIONS[stage]?.label(status, {}) ?? stage;
}

/** SQL の確認（サーバーの分類）の段階の補足（文の分類ごとの数）。 */
export function planCheckDetail(plan: DbAdminChatPlan): string {
  const counts: Record<DbAdminChatStatementKind, number> = { readOnly: 0, change: 0, blocked: 0 };
  for (const statement of plan.statements) counts[dbAdminChatStatementKind(statement)] += 1;
  if (plan.statements.length === 1) {
    const [only] = plan.statements;
    if (only?.blocked_reason) return t("dbAdminChat.check.blocked");
    if (only?.requires_confirmation) {
      return t("dbAdminChat.check.confirmation", { target: only.confirmation_target });
    }
    return t("dbAdminChat.check.readOnly");
  }
  return (["readOnly", "change", "blocked"] as const)
    .filter((kind) => counts[kind] > 0)
    .map((kind) => t("dbAdminChat.check.count", { kind: t(`dbAdminChat.kind.${kind}`), count: counts[kind] }))
    .join("・");
}

/** 生成の段階（送信中・失敗の表示にも使う）。 */
export function dbAdminChatGenerateStep(
  status: ChatProgressStepStatus,
  startedAtMs: number,
  finishedAtMs?: number,
): ChatProgressStep {
  return {
    id: "generate_sql",
    label: stepLabel("generate_sql", status),
    status,
    detail: t("chat.engine.enterprise"),
    startedAt: iso(startedAtMs),
    finishedAt: finishedAtMs === undefined ? undefined : iso(finishedAtMs),
  };
}

/** 1 往復の処理の経過（生成 → SQL の確認 → 実行）。 */
export function dbAdminChatProgressSteps({
  sentAtMs,
  generatedAtMs,
  plan,
  execution,
}: {
  sentAtMs: number;
  generatedAtMs: number;
  plan: DbAdminChatPlan;
  execution?: DbAdminChatExecution | null;
}): ChatProgressStep[] {
  const steps: ChatProgressStep[] = [dbAdminChatGenerateStep("done", sentAtMs, generatedAtMs)];
  if (plan.statements.length === 0) return steps;
  // 止める文があっても、ほかの文は実行できる。すべての文が止める文のときだけ確認の段を失敗にする。
  const allBlocked = plan.statements.every((statement) => statement.blocked_reason);
  steps.push({
    id: "safety_check",
    label: stepLabel("safety_check", allBlocked ? "failed" : "done"),
    status: allBlocked ? "failed" : "done",
    detail: planCheckDetail(plan),
    startedAt: iso(generatedAtMs),
    finishedAt: iso(generatedAtMs),
  });
  if (execution && execution.status !== "not_run") {
    const status: ChatProgressStepStatus =
      execution.status === "running" ? "running" : execution.status === "failed" ? "failed" : "done";
    const rows = execution.result?.select_result?.returned_count ?? execution.saved?.row_count;
    steps.push({
      id: "execute_sql",
      label: stepLabel("execute_sql", status),
      status,
      detail:
        status === "done" && typeof rows === "number"
          ? t("chat.progress.rows", { count: rows.toLocaleString("ja-JP") })
          : undefined,
      startedAt: iso(execution.startedAtMs),
      finishedAt: execution.finishedAtMs === undefined ? undefined : iso(execution.finishedAtMs),
    });
  }
  return steps;
}

/** 実行の結果が成功か（全部の文が成功・実行済み）。 */
export function dbAdminExecutionSucceeded(result: DbAdminExecuteData): boolean {
  return (
    result.statements.length > 0 &&
    result.statements.every((item) => item.status === "success" || item.status === "executed")
  );
}

/** 実行の結果のうち、AI のエラー分析に渡す失敗の内容（失敗の文と ORA のメッセージ・警告）。 */
export function dbAdminFailureText(result: DbAdminExecuteData): string {
  const lines = result.statements
    .filter((item) => item.error_message)
    .map((item) => `#${item.index} ${item.statement_type}: ${item.error_message}`);
  return [...lines, ...result.warnings].join("\n");
}

/** 確認語が一致したか（前後の空白は無視。確認語が要らないなら常に true）。 */
export function dbAdminChatConfirmed(
  target: { requires_confirmation: boolean; confirmation_target: string },
  value: string,
): boolean {
  if (!target.requires_confirmation) return true;
  return value.trim() === target.confirmation_target;
}

/** 1 文を実行できるか（止める文でなく、確認語が要るなら一致している）。 */
export function dbAdminChatCanRunStatement(statement: DbAdminChatStatementPlan, confirmation: string): boolean {
  return !statement.blocked_reason && dbAdminChatConfirmed(statement, confirmation);
}

/** 「すべて実行」できるか（止める文が無く、確認語が要るなら一致している）。 */
export function dbAdminChatCanRunAll(plan: DbAdminChatPlan, confirmation: string): boolean {
  return plan.statements.length > 0 && !plan.blocked && dbAdminChatConfirmed(plan, confirmation);
}

/** 「すべて実行」を止める文の番号（「#2・#4」）。 */
export function dbAdminChatBlockedStatementLabels(plan: DbAdminChatPlan): string {
  return plan.statements
    .filter((statement) => statement.blocked_reason)
    .map((statement) => `#${statement.index}`)
    .join("・");
}
