/**
 * モデルのカスケード（#4581）の表示。規則は共通の `modelCascadeView`（3 製品で同じ）で、文言は RAG の i18n。
 */
import { modelCascadeView, type ModelCascadeView } from "@production-ready/ui";

import type { ModelCascadeRecord } from "@/lib/answer-diagnostics";
import { t, type I18nKey } from "@/lib/i18n";

const REASONS = [
  "structured_output_invalid",
] as const;

/** 回答の記録のカスケードの表示。カスケードしない実行・古い記録は null。 */
export function answerModelCascadeView(record: ModelCascadeRecord | null | undefined): ModelCascadeView | null {
  return modelCascadeView(record, {
    escalated: t("search.answerDetails.cascade.escalated"),
    notEscalated: t("search.answerDetails.cascade.notEscalated"),
    reasons: Object.fromEntries(
      REASONS.map((reason) => [reason, t(`search.answerDetails.cascade.reason.${reason}` as I18nKey)])
    ),
  });
}
