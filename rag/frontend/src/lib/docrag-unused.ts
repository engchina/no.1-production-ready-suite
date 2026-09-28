import type { AnswerEngineName } from "@/lib/api";
import type { I18nKey } from "@/lib/i18n";

/**
 * 業務ビューの回答エンジン(上書き値)から、「DocRAG では使われない」注記の文言キーを返す(#300)。
 *
 * - 標準を明示: 注記しない(null)。
 * - DocRAG を明示: 「DocRAG のため使われない」。
 * - 継承(null / 未指定): 全体既定は画面から分からないため、「DocRAG のときは使われない」。
 */
export function docragUnusedNoteKey(
  answerEngine: AnswerEngineName | null | undefined
): I18nKey | null {
  if (answerEngine === "standard") return null;
  return answerEngine === "docrag"
    ? "businessViews.docragUnused.docrag"
    : "businessViews.docragUnused.inherit";
}
