import type { ChatProgressParams, ChatProgressStepStatus, RunStepDetails } from "@production-ready/ui";
import { t, type I18nKey } from "./i18n";

const REASONS: Readonly<Record<string, I18nKey>> = {
  configuration_disabled: "answer.outcome.disabled",
  page_scope: "answer.outcome.pageScope",
  caller_scope: "answer.outcome.callerScope",
  missing_requirement: "answer.outcome.missingRequirement",
  guide_step: "answer.outcome.guideStep",
  caller_queries: "answer.outcome.callerQueries",
  overflow: "answer.outcome.overflow",
  catalog_empty: "answer.outcome.catalogEmpty",
  dependencies_unavailable: "answer.outcome.dependenciesUnavailable",
  document_limit_zero: "answer.outcome.documentLimitZero",
  article_limit_zero: "answer.outcome.articleLimitZero",
  no_match: "answer.outcome.noMatch",
  candidates_found: "answer.outcome.candidatesFound",
  selection_failed: "answer.outcome.failed",
};

/** 実行時の判断記録だけを日本語へ投影する。旧記録に工程や判断を補わない。 */
export function executionStepDetails(status: ChatProgressStepStatus, params: ChatProgressParams, details: RunStepDetails | undefined): RunStepDetails | undefined {
  const reason = params.reason_code;
  if (typeof reason !== "string" || !Object.prototype.hasOwnProperty.call(REASONS, reason)) return details;
  const count = typeof params.result_count === "number" ? params.result_count : undefined;
  const result = (params.reused === true ? t("answer.outcome.reused") : "") + (reason === "candidates_found" && count === undefined ? t("answer.outcome.countUnknown") : t(REASONS[reason], { count: count ?? "" }));
  const resultLabel = t("answer.outcome.result");
  const fields = (details?.fields ?? []).filter(field => field.label !== resultLabel && field.label !== t("answer.outcome.timing"));
  return { ...details, fields: [
    { label: resultLabel, value: result },
    ...fields,
    ...(status === "skipped" ? [{ label: t("answer.outcome.timing"), value: t("answer.outcome.decisionTime") }] : []),
  ] };
}
