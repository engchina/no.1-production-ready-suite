// DB 管理チャット（#2264）の画面のロジック。会話とターンはサーバーが保存し（#2319）、続きの質問の
// 文脈もサーバーが保存した会話から作る。結果の行は保存しない（開き直したら要約と「もう一度実行」）。
//
// 処理の経過は NL2SQL のチャットと同じ段階の名前（`CHAT_PROGRESS_STEP_DEFINITIONS`）と共通の
// `ChatProgress` で出す（生成 → SQL の確認（サーバーの分類）→ 実行）。
import type { ChatProgressStep, ChatProgressStepStatus } from "@production-ready/ui";

// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import { t } from "../../lib/i18n";
import { CHAT_PROGRESS_STEP_DEFINITIONS } from "./chatProgress";
import type { DbAdminChatPlan, DbAdminChatTurn, DbAdminExecuteData } from "./types";

/** 会話の往復の上限（backend の DB_ADMIN_CHAT_TURN_LIMIT と同じ。超えたら新しい会話を勧める）。 */
export const DB_ADMIN_CHAT_TURN_LIMIT = 50;

/** 実行の状態（1 往復の今の SQL について）。 */
export interface DbAdminChatExecution {
  status: "running" | "done" | "failed";
  startedAtMs: number;
  finishedAtMs?: number;
  result?: DbAdminExecuteData | null;
  /** 結果の行を持たない（開き直した）ときの行数。 */
  rowCount?: number | null;
  /** 実行の要求そのものの失敗（権限・通信・timeout）。SQL の失敗は `result` の文ごとの状態。 */
  requestError?: unknown;
}

/**
 * 保存したターンの最後の実行（今の SQL の版のもの）を、画面の実行の状態にする。行は保存していないので
 * `result.select_result` は空で、行数は `rowCount`。前の版（AI のエラー分析で直す前）の実行は無視する。
 */
export function dbAdminChatSavedExecution(turn: DbAdminChatTurn): DbAdminChatExecution | null {
  const last = turn.last_execution;
  if (!last || last.revision !== turn.revision) return null;
  const finishedAtMs = Date.parse(last.executed_at);
  const elapsed = last.result.timing?.elapsed_ms ?? 0;
  return {
    status: last.succeeded ? "done" : "failed",
    startedAtMs: Number.isFinite(finishedAtMs) ? finishedAtMs - elapsed : Date.now(),
    finishedAtMs: Number.isFinite(finishedAtMs) ? finishedAtMs : undefined,
    result: last.result,
    rowCount: last.row_count,
  };
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

/** 段階の名前はチャットと同じ定義から作る（文言を 2 つの画面で分けない）。 */
function stepLabel(stage: "generate_sql" | "safety_check" | "execute_sql", status: ChatProgressStepStatus) {
  return CHAT_PROGRESS_STEP_DEFINITIONS[stage]?.label(status, {}) ?? stage;
}

/** SQL の確認（サーバーの分類）の段階の補足。 */
export function planCheckDetail(plan: DbAdminChatPlan): string {
  if (plan.blocked) return t("dbAdminChat.check.blocked");
  if (plan.requires_confirmation) {
    return t("dbAdminChat.check.confirmation", { target: plan.confirmation_target });
  }
  return t("dbAdminChat.check.readOnly");
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
  steps.push({
    id: "safety_check",
    label: stepLabel("safety_check", plan.blocked ? "failed" : "done"),
    status: plan.blocked ? "failed" : "done",
    detail: planCheckDetail(plan),
    startedAt: iso(generatedAtMs),
    finishedAt: iso(generatedAtMs),
  });
  if (execution) {
    const status: ChatProgressStepStatus =
      execution.status === "running" ? "running" : execution.status === "failed" ? "failed" : "done";
    const rows = execution.result?.select_result?.returned_count ?? execution.rowCount;
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

/** 確認語が一致したか（前後の空白は無視。確認語が要らない SQL は常に true）。 */
export function dbAdminChatConfirmed(plan: DbAdminChatPlan, value: string): boolean {
  if (!plan.requires_confirmation) return true;
  return value.trim() === plan.confirmation_target;
}

/** 実行できるか（止める文が無く、確認語が要るなら一致している）。 */
export function dbAdminChatCanExecute(plan: DbAdminChatPlan, confirmation: string): boolean {
  return plan.statements.length > 0 && !plan.blocked && dbAdminChatConfirmed(plan, confirmation);
}
