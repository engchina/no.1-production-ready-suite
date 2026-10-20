import {
  parseAnswerPassageRecords,
  type AnswerPassageLabels,
  type AnswerPassageRecords,
} from "@production-ready/ui";

import { t } from "@/lib/i18n";

/**
 * 回答の段落の印・外した段落・回答全体の注意（#2738。契約は platform/contracts/answer-passages/passage-marks.json）。
 * 回答の記録の診断（`diagnostics.answer`。保存した回答は `answer_diagnostics`）の `passage_marks`・
 * `removed_passages`・`answer_warnings` を読む（回答の構造 `envelope` に置かれた記録も読む）。記録の無い過去の回答は
 * null（今までの表示のまま）。画面は Agent と同じ共有の `AnswerSections`・`AnswerPassageBadges` で描く。
 */
export function answerPassages(diagnostics: unknown): AnswerPassageRecords | null {
  if (!diagnostics || typeof diagnostics !== "object" || Array.isArray(diagnostics)) return null;
  const raw = diagnostics as Record<string, unknown>;
  return parseAnswerPassageRecords(raw) ?? parseAnswerPassageRecords(raw.envelope);
}

/** 段落の印の文言（RAG のチャット・RAG 検索・実行履歴で同じ。Agent の同じ表示と同じ文言）。 */
export function answerPassageLabels(): AnswerPassageLabels {
  return {
    state: { unverified: t("search.answerText.passage.unverified"), warning: t("search.answerText.passage.warning") },
    stateDescription: {
      unverified: t("search.answerText.passage.unverifiedDescription"),
      warning: t("search.answerText.passage.warningDescription"),
    },
    toggleHint: t("search.answerText.passage.toggleHint"),
    removedTitle: t("search.answerText.passage.removedTitle"),
    removedNote: t("search.answerText.passage.removedNote"),
    marksTitle: t("search.answerText.passage.marksTitle"),
    marksNote: t("search.answerText.passage.marksNote"),
    jumpLabel: (quote) => t("search.answerText.passage.jump", { quote }),
    warningsTitle: t("search.answerText.passage.warningsTitle"),
    badge: {
      unverified: (count) => t("search.answerText.passage.badgeUnverified", { count }),
      warning: (count) => t("search.answerText.passage.badgeWarning", { count }),
      removed: (count) => t("search.answerText.passage.badgeRemoved", { count }),
    },
  };
}
