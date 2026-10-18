// DB 管理チャット（#2264）の画面のロジック。会話は保存せず、画面の中だけに持つ。
//
// 処理の経過は NL2SQL のチャットと同じ段階の名前（`CHAT_PROGRESS_STEP_DEFINITIONS`）と共通の
// `ChatProgress` で出す（生成 → SQL の確認（サーバーの分類）→ 実行）。
import type { ChatProgressStep, ChatProgressStepStatus } from "@production-ready/ui";

// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import { t } from "../../lib/i18n";
import { CHAT_PROGRESS_STEP_DEFINITIONS } from "./chatProgress";
import type {
  DbAdminChatHistoryTurn,
  DbAdminChatPlan,
  DbAdminExecuteData,
} from "./types";

/** 続きの質問の文脈に渡す前の往復の数（backend の HISTORY_TURN_LIMIT と同じ）。 */
export const DB_ADMIN_CHAT_HISTORY_LIMIT = 6;
/** 会話の往復の上限（画面の中だけ。超えたら新しい会話を勧める）。 */
export const DB_ADMIN_CHAT_TURN_LIMIT = 50;

/** 実行の状態（1 往復の今の SQL について）。 */
export interface DbAdminChatExecution {
  status: "running" | "done" | "failed";
  startedAtMs: number;
  finishedAtMs?: number;
  result?: DbAdminExecuteData | null;
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
    const rows = execution.result?.select_result?.returned_count;
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

/** 続きの質問の文脈に渡す、実行の結果の短い要約。 */
export function dbAdminOutcomeSummary(result: DbAdminExecuteData | null | undefined): string {
  if (!result) return "";
  if (dbAdminExecutionSucceeded(result)) {
    const rows = result.select_result?.returned_count;
    return typeof rows === "number"
      ? t("dbAdminChat.outcome.rows", { count: rows })
      : t("dbAdminChat.outcome.success");
  }
  return t("dbAdminChat.outcome.failed", { message: dbAdminFailureText(result).slice(0, 300) });
}

/** 画面の中の前の往復（新しいものほど後ろ）を、続きの質問の文脈の形にする。 */
export function dbAdminChatHistory(
  turns: ReadonlyArray<{ question: string; sql: string; outcome: string }>,
): DbAdminChatHistoryTurn[] {
  return turns
    .slice(-DB_ADMIN_CHAT_HISTORY_LIMIT)
    .map((turn) => ({ question: turn.question, sql: turn.sql, outcome: turn.outcome }));
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
