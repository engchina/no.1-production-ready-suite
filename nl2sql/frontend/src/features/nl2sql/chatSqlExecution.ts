// チャットのターンの SQL の実行（#1154）の、NL2SQL 固有の変換と文言。
//
// 結果の表の表示（要約・プレビュー・打ち切りの明示・すべての行・CSV・NULL・数値の右寄せ）は 3 製品で共通の
// `ChatResultTable`（@engchina/production-ready-ui）が持つ。ここは NL2SQL の API の形（列名をキーにした行）を
// 部品の形（列の順の値の配列）にする変換と、会話を開き直したときの要約・「SELECT SQL を実行」への受け渡しだけ。

// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import { t } from "../../lib/i18n";
import type { QueryResults, SqlChatExecutionSummary } from "./types";

const integerFormatter = new Intl.NumberFormat("ja-JP");

/** 行数・列数などの整数の表示（3 桁区切り）。 */
export function formatCount(value: number): string {
  return integerFormatter.format(value);
}

/** NL2SQL の結果（列名をキーにした行）を、共通の結果の表の形（列・列の順の値の配列）にする。 */
export function toChatResultTable(results: Pick<QueryResults, "columns" | "rows">): {
  columns: { name: string }[];
  rows: unknown[][];
} {
  return {
    columns: results.columns.map((name) => ({ name })),
    rows: results.rows.map((row) => results.columns.map((column) => row[column] ?? null)),
  };
}

/** 結果が上限（行数・応答のバイト数）で打ち切られたか。 */
export function isIncompleteResult(results: Pick<QueryResults, "has_more" | "truncated">): boolean {
  return Boolean(results.has_more || results.truncated);
}

/** 会話を開き直したときの前回の実行の要約（行は保存していない）。 */
export function lastExecutionText(summary: SqlChatExecutionSummary, executedAtLabel: string): string {
  if (summary.status === "error") return t("chat.execute.last.failed", { at: executedAtLabel });
  return t(summary.has_more ? "chat.execute.last.truncated" : "chat.execute.last.done", {
    at: executedAtLabel,
    rows: formatCount(summary.row_count),
    columns: formatCount(summary.column_count),
  });
}

/** CSV のファイル名（例: nl2sql-chat-result-20261005-140312.csv）。時刻は利用者の地域の時刻。 */
export function chatResultCsvFilename(executedAt: string): string {
  const date = new Date(executedAt);
  const valid = !Number.isNaN(date.getTime());
  const pad = (value: number) => String(value).padStart(2, "0");
  const stamp = valid
    ? `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
    : "result";
  return `nl2sql-chat-result-${stamp}.csv`;
}

/** 「SELECT SQL を実行」へ SQL を渡す履歴の state（URL には載せない）。 */
export function directSqlPrefillState(sql: string): { prefillSql: string } {
  return { prefillSql: sql };
}

/** 他の画面（チャットの「SELECT SQL を実行で開く」）から渡された SQL（無ければ空文字）。 */
export function directSqlPrefill(state: unknown): string {
  if (!state || typeof state !== "object") return "";
  const sql = (state as { prefillSql?: unknown }).prefillSql;
  return typeof sql === "string" ? sql.trim() : "";
}
