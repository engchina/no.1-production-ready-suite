import { t } from "@/lib/i18n";

/** 登録済みQ&A の管理画面の入力の解釈（当てはめない質問。#4788）。 */

// backend の上限と同じ（`MAX_APPROVED_FAQ_NEGATIVE_QUESTIONS`・質問の長さ）。
const MAX_NEGATIVE_QUESTIONS = 20;
const MAX_NEGATIVE_QUESTION_LENGTH = 1000;
/** 欄の文（1 行に 1 つ）を当てはめない質問の一覧にする。空の行は除く。 */
export function parseNegativeQuestions(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** 当てはめない質問の欄の入力の誤り（無ければ null）。 */
export function negativeQuestionsError(questions: readonly string[]): string | null {
  if (questions.length > MAX_NEGATIVE_QUESTIONS) return t("searchAnswerProfiles.faq.negative.tooMany");
  if (questions.some((question) => question.length > MAX_NEGATIVE_QUESTION_LENGTH)) {
    return t("searchAnswerProfiles.faq.negative.tooLong");
  }
  return null;
}
