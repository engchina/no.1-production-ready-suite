/**
 * 品質評価の入力（Golden set JSON）の検証。画面の部品から切り離して単体テストする。
 */

import { t } from "@/lib/i18n";

/** ケースの id の長さの上限（backend の `EvaluationCase.id` と job の `current_case_id` の列）。 */
export const EVALUATION_CASE_ID_MAX_CHARS = 200;

/**
 * cases の id の検証（#977）。結果の表は id で行を区別するため、空・長すぎる・重複する id を実行前に
 * 止める（backend も 422 で拒否する）。問題が無ければ null。
 */
export function evaluationCaseIdError(cases: readonly unknown[]): string | null {
  const ids: string[] = [];
  for (const [index, item] of cases.entries()) {
    const raw =
      typeof item === "object" && item !== null ? (item as { id?: unknown }).id : undefined;
    const id = typeof raw === "string" ? raw.trim() : "";
    if (!id || id.length > EVALUATION_CASE_ID_MAX_CHARS) {
      return t("evaluation.input.caseIdRequired", { index: index + 1 });
    }
    ids.push(id);
  }
  const duplicates = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
  return duplicates.length
    ? t("evaluation.input.duplicateCaseIds", { ids: duplicates.join("、") })
    : null;
}
