// AI要件確認（#1439 / #1442）を始めるか・使えるかの判定。SQL 生成の画面（ボタンで開く）とチャット（送信の
// ときに自動）が同じ関数で決める（#1715。画面ごとの差は入口だけ）。
//
// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import type { QuerySession } from "./ontology/types";
import { isSqlStatementInput } from "./sqlInput.ts";

/** AI要件確認をしない理由（`sql_statement`: 質問が SQL の文そのもので、確かめることが無い）。 */
export type RequirementCheckSkipReason = "sql_statement";

/**
 * 質問で AI要件確認を飛ばすか（飛ばす理由。確かめるなら null）。チャットは確かめずに SQL を作り、SQL 生成の画面は
 * 「AI要件確認」を押せなくして理由を出す。LLM は呼ばない。
 */
export function requirementCheckSkipReason(question: string): RequirementCheckSkipReason | null {
  return isSqlStatementInput(question) ? "sql_statement" : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 要件の検査を使えない設定（OCI Enterprise AI が無い）。このときは確認せずに SQL を作る。 */
export function isRequirementCheckUnavailable(error: unknown): boolean {
  return (
    isRecord(error) &&
    error.status === 503 &&
    typeof error.message === "string" &&
    error.message.includes("REQUIREMENT_AI_NOT_CONFIGURED")
  );
}

/**
 * session の次の扱い。
 *
 * - `direct`: 確認が要らない（SELECT・FROM が明確で、足りない・あいまいな情報が無く、まだ何も聞いていない）。
 *   聞かずに反映して次へ進む（チャットは SQL を作り、SQL 生成の画面は質問の欄へ反映する）。
 * - `applied`: 反映済み（反映の後、次へ進む前に画面を離れた）。そのまま次へ進む。
 * - `ask`: 確認の質問がある・答えられない・最低の基準を満たさない（1 問ずつ聞く・知らせる）。
 * - `summary`: 全部に答えた。まとめ（再検査・SQL の骨組み）を確かめてから次へ進む。
 * - `ended`: 中止・期限切れ・失敗で終わった。
 */
export type RequirementNextStep = "direct" | "applied" | "ask" | "summary" | "ended";

export function requirementNextStep(session: QuerySession): RequirementNextStep {
  if (session.status === "cancelled" || session.status === "error") return "ended";
  if (session.requirement_applied_at) return "applied";
  if (session.status === "done") return "ended";
  const state = session.requirement_state;
  if (!state) return "ended";
  if (state.status === "unanswerable" || state.current_item || !state.can_apply) return "ask";
  if (
    state.status === "ready" &&
    state.unresolved_items.length === 0 &&
    state.answered_count === 0 &&
    state.review_round === 0
  ) {
    return "direct";
  }
  return "summary";
}
