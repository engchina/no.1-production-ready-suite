/**
 * 安全チェック（OCI Guardrails）が入力を止めたときの API の error code（3 製品で同じ値。backend は
 * `pr_system_settings.guardrails.GUARDRAIL_BLOCKED_CODE`。#5191）。
 *
 * 同じ文を送り直しても結果は変わらないので、画面は送信の失敗と分け、再送信・確認の省略を出さない。
 */
export const GUARDRAIL_BLOCKED_CODE = "GUARDRAIL_BLOCKED";

/**
 * 安全チェックが入力を止めた API のエラーか。3 製品の `ApiError` はどれも `errorCode`（problem の `code` か
 * envelope の `error_code`）を持つので、その値だけで判定する（説明の文は解析しない。messaging.md §4.3）。
 */
export function isGuardrailBlockedError(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  return (error as { errorCode?: unknown }).errorCode === GUARDRAIL_BLOCKED_CODE;
}
