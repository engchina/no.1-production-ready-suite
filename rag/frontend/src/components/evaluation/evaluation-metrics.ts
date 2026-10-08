import type {
  EvaluationMetricName,
  EvaluationMetrics,
  EvaluationOutcome,
  EvaluationSuiteName,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

/**
 * 品質評価の指標と基準の表示の定義（#591）。
 *
 * 指標は「検索」「根拠」「回答」の 3 つの観点に整理した 9 つ。評価の基準（設定）と品質評価の
 * 画面は、この並び・名前・説明を共有する。保存済みの古い結果には削除した指標・理由・基準の
 * 名前が残るため、未知の名前は原文のまま表示する（表示を壊さない）。
 */
export type EvaluationPerspective = "retrieval" | "grounding" | "answer" | "handling";

export const EVALUATION_PERSPECTIVES: ReadonlyArray<{
  id: EvaluationPerspective;
  metrics: readonly EvaluationMetricName[];
}> = [
  { id: "retrieval", metrics: ["context_recall", "mrr"] },
  {
    id: "grounding",
    // Faithfulness（claim_support_rate。LLM の主張の判定）を先に、語句の一致率（faithfulness。参考値）を
    // 最後に置く（#711）。保存値の key は変えない。
    metrics: ["claim_support_rate", "citation_traceability_coverage", "faithfulness"],
  },
  {
    id: "answer",
    metrics: [
      "answer_keyword_hit_rate",
      "refusal_accuracy",
      "requirement_coverage",
      "answer_pass_rate",
    ],
  },
  // 業務支援の対応（#1231）。期待する対応・手順・禁止の表現・条件のあるケースだけが対象。
  {
    id: "handling",
    metrics: ["handling_accuracy", "step_order_score", "safe_answer_rate", "condition_coverage"],
  },
];

export const EVALUATION_METRIC_NAMES: readonly EvaluationMetricName[] =
  EVALUATION_PERSPECTIVES.flatMap((perspective) => perspective.metrics);

/** 標準回答による LLM の評価で求める指標（標準回答のあるケースだけが対象）。 */
export const STANDARD_ANSWER_METRICS: ReadonlySet<EvaluationMetricName> = new Set([
  "claim_support_rate",
  "requirement_coverage",
  "answer_pass_rate",
]);

export const EVALUATION_SUITE_NAMES: readonly EvaluationSuiteName[] = ["standard", "strict"];

const METRIC_KEYS: Record<EvaluationMetricName, { label: I18nKey; description: I18nKey }> = {
  context_recall: {
    label: "evaluation.metric.context_recall",
    description: "evaluation.metric.context_recall.description",
  },
  mrr: { label: "evaluation.metric.mrr", description: "evaluation.metric.mrr.description" },
  faithfulness: {
    label: "evaluation.metric.faithfulness",
    description: "evaluation.metric.faithfulness.description",
  },
  citation_traceability_coverage: {
    label: "evaluation.metric.citation_traceability_coverage",
    description: "evaluation.metric.citation_traceability_coverage.description",
  },
  claim_support_rate: {
    label: "evaluation.metric.claim_support_rate",
    description: "evaluation.metric.claim_support_rate.description",
  },
  answer_keyword_hit_rate: {
    label: "evaluation.metric.answer_keyword_hit_rate",
    description: "evaluation.metric.answer_keyword_hit_rate.description",
  },
  refusal_accuracy: {
    label: "evaluation.metric.refusal_accuracy",
    description: "evaluation.metric.refusal_accuracy.description",
  },
  requirement_coverage: {
    label: "evaluation.metric.requirement_coverage",
    description: "evaluation.metric.requirement_coverage.description",
  },
  answer_pass_rate: {
    label: "evaluation.metric.answer_pass_rate",
    description: "evaluation.metric.answer_pass_rate.description",
  },
  handling_accuracy: {
    label: "evaluation.metric.handling_accuracy",
    description: "evaluation.metric.handling_accuracy.description",
  },
  step_order_score: {
    label: "evaluation.metric.step_order_score",
    description: "evaluation.metric.step_order_score.description",
  },
  safe_answer_rate: {
    label: "evaluation.metric.safe_answer_rate",
    description: "evaluation.metric.safe_answer_rate.description",
  },
  condition_coverage: {
    label: "evaluation.metric.condition_coverage",
    description: "evaluation.metric.condition_coverage.description",
  },
};

const PERSPECTIVE_KEYS: Record<EvaluationPerspective, { label: I18nKey; description: I18nKey }> = {
  retrieval: {
    label: "evaluation.perspective.retrieval",
    description: "evaluation.perspective.retrieval.description",
  },
  grounding: {
    label: "evaluation.perspective.grounding",
    description: "evaluation.perspective.grounding.description",
  },
  answer: {
    label: "evaluation.perspective.answer",
    description: "evaluation.perspective.answer.description",
  },
  handling: {
    label: "evaluation.perspective.handling",
    description: "evaluation.perspective.handling.description",
  },
};

const FAILURE_REASON_KEYS: Record<string, I18nKey> = {
  retrieval_miss: "evaluation.failureReason.retrieval_miss",
  partial_recall: "evaluation.failureReason.partial_recall",
  unexpected_answer: "evaluation.failureReason.unexpected_answer",
  unexpected_refusal: "evaluation.failureReason.unexpected_refusal",
  answer_keyword_miss: "evaluation.failureReason.answer_keyword_miss",
  low_groundedness: "evaluation.failureReason.low_groundedness",
  unsupported_claim: "evaluation.failureReason.unsupported_claim",
  missing_content: "evaluation.failureReason.missing_content",
  answer_failed: "evaluation.failureReason.answer_failed",
  answer_evaluation_error: "evaluation.failureReason.answer_evaluation_error",
  guardrail_warning: "evaluation.failureReason.guardrail_warning",
  unexpected_handling: "evaluation.failureReason.unexpected_handling",
  step_missing: "evaluation.failureReason.step_missing",
  forbidden_action: "evaluation.failureReason.forbidden_action",
  condition_missing: "evaluation.failureReason.condition_missing",
  evidence_miss: "evaluation.failureReason.evidence_miss",
  known_condition_reasked: "evaluation.failureReason.known_condition_reasked",
  case_error: "evaluation.failureReason.case_error",
};

const SUITE_KEYS: Record<EvaluationSuiteName, { label: I18nKey; description: I18nKey }> = {
  standard: {
    label: "settings.evaluation.suite.standard",
    description: "settings.evaluation.suite.standard.description",
  },
  strict: {
    label: "settings.evaluation.suite.strict",
    description: "settings.evaluation.suite.strict.description",
  },
};

export function isEvaluationMetric(name: string): name is EvaluationMetricName {
  return Object.hasOwn(METRIC_KEYS, name);
}

export function isEvaluationSuite(name: string): name is EvaluationSuiteName {
  return Object.hasOwn(SUITE_KEYS, name);
}

/** 指標の表示名。削除した指標（保存済みの結果）は原文の名前を返す。 */
export function metricLabel(name: string): string {
  return isEvaluationMetric(name) ? t(METRIC_KEYS[name].label) : name;
}

export function metricDescription(name: EvaluationMetricName): string {
  return t(METRIC_KEYS[name].description);
}

export function perspectiveLabel(perspective: EvaluationPerspective): string {
  return t(PERSPECTIVE_KEYS[perspective].label);
}

export function perspectiveDescription(perspective: EvaluationPerspective): string {
  return t(PERSPECTIVE_KEYS[perspective].description);
}

/** 失敗理由の表示名。削除した理由（保存済みの結果）は原文のコードを返す。 */
export function failureReasonLabel(reason: string): string {
  const key = FAILURE_REASON_KEYS[reason];
  return key ? t(key) : reason;
}

/** 基準の表示名。削除した基準（保存済みの結果）は原文の名前を返す。 */
export function suiteLabel(name: string): string {
  return isEvaluationSuite(name) ? t(SUITE_KEYS[name].label) : name;
}

export function suiteDescription(name: EvaluationSuiteName): string {
  return t(SUITE_KEYS[name].description);
}

/** 0..1 の値を百分率で表示する。測れなかった値（null・欄なし）は「—」。 */
export function formatMetricValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value * 1000) / 10}%`;
}

/** 集計結果の指標の値（保存済みの古い結果で欄が無いときは null）。 */
export function metricValue(metrics: EvaluationMetrics, name: EvaluationMetricName): number | null {
  const value = metrics[name];
  return typeof value === "number" ? value : null;
}

/** 指標の対象のケース数。古い結果で件数が無いときは null（件数を表示しない）。 */
export function metricCaseCount(
  metrics: EvaluationMetrics,
  name: EvaluationMetricName
): number | null {
  const count = metrics.metric_case_counts?.[name];
  return typeof count === "number" ? count : null;
}

/** 閾値の dict を、指標の並び順（観点の順）で返す。未知の指標は後ろに原文の名前で並べる。 */
export function orderedThresholdEntries(
  thresholds: Partial<Record<string, number | null>> | null | undefined
): Array<[string, number]> {
  const entries = Object.entries(thresholds ?? {}).filter(
    (entry): entry is [string, number] => typeof entry[1] === "number"
  );
  const order = new Map(EVALUATION_METRIC_NAMES.map((name, index) => [name as string, index]));
  return entries.sort(
    ([left], [right]) =>
      (order.get(left) ?? Number.MAX_SAFE_INTEGER) - (order.get(right) ?? Number.MAX_SAFE_INTEGER)
  );
}

/**
 * 結果に出す観点か。業務支援の対応（handling）は、対象のケースが 1 件も無い評価では出さない
 * （期待する対応・手順を持たない評価セットに「対象のケースなし」の欄を並べない。#1231）。
 */
export function isPerspectiveShown(
  metrics: EvaluationMetrics,
  perspective: (typeof EVALUATION_PERSPECTIVES)[number]
): boolean {
  if (perspective.id !== "handling") return true;
  return perspective.metrics.some((metric) => (metricCaseCount(metrics, metric) ?? 0) > 0);
}

const OUTCOME_KEYS: Record<EvaluationOutcome, I18nKey> = {
  answered: "evaluation.outcome.answered",
  conditional: "evaluation.outcome.conditional",
  needs_clarification: "evaluation.outcome.needs_clarification",
  needs_environment_data: "evaluation.outcome.needs_environment_data",
  needs_human: "evaluation.outcome.needs_human",
  insufficient_evidence: "evaluation.outcome.insufficient_evidence",
};

/** 回答の対応の表示名（未知の値は原文のまま）。 */
export function outcomeLabel(outcome: string): string {
  return outcome in OUTCOME_KEYS ? t(OUTCOME_KEYS[outcome as EvaluationOutcome]) : outcome;
}

