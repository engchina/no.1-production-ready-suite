// AI要件確認（#1439 / #1442）を使えるか・次にどうするかの判定。SQL 生成の画面（ボタンで開く）とチャット（送信の
// ときに自動）が同じ関数で決める（#1715。画面ごとの差は入口だけ）。入力が SQL の文かどうかでは分けない
// （判断は SQL を 1 つに決める最低限の情報があるかだけ。#1727）。
//
// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import type { QuerySession } from "./ontology/types";

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
 *   聞かずに反映して次へ進む（チャットは SQL を作り、SQL 生成の画面は次の SQL の生成に渡す。どちらも利用者の質問は書き換えない。#1727）。
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
