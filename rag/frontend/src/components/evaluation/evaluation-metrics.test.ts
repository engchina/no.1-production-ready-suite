import { describe, expect, it } from "vitest";

import type { EvaluationMetrics } from "@/lib/api";

import {
  EVALUATION_METRIC_NAMES,
  EVALUATION_PERSPECTIVES,
  STANDARD_ANSWER_METRICS,
  failureReasonLabel,
  formatMetricValue,
  isPerspectiveShown,
  outcomeLabel,
  metricCaseCount,
  metricLabel,
  metricValue,
  orderedThresholdEntries,
  suiteLabel,
} from "./evaluation-metrics";

describe("evaluation metrics (issue 591)", () => {
  it("groups the metrics into retrieval / grounding / answer / handling", () => {
    expect(EVALUATION_PERSPECTIVES.map((perspective) => perspective.id)).toEqual([
      "retrieval",
      "grounding",
      "answer",
      "handling",
    ]);
    expect(EVALUATION_METRIC_NAMES).toHaveLength(13);
    expect(new Set(EVALUATION_METRIC_NAMES).size).toBe(13);
    for (const name of STANDARD_ANSWER_METRICS) {
      expect(EVALUATION_METRIC_NAMES).toContain(name);
    }
  });

  it("uses Japanese labels and keeps removed names of saved results as-is", () => {
    expect(metricLabel("context_recall")).toBe("正解文書の再現率");
    expect(metricLabel("precision_at_k")).toBe("precision_at_k");
    expect(failureReasonLabel("unexpected_refusal")).toBe("答えるべき質問に答えなかった");
    expect(failureReasonLabel("section_miss")).toBe("section_miss");
    expect(suiteLabel("strict")).toBe("厳格");
    expect(suiteLabel("ragas_like")).toBe("ragas_like");
  });

  it("formats unmeasured values as a dash, not as 0%", () => {
    expect(formatMetricValue(0.8333)).toBe("83.3%");
    expect(formatMetricValue(0)).toBe("0%");
    expect(formatMetricValue(null)).toBe("—");
    expect(formatMetricValue(undefined)).toBe("—");
  });

  it("reads values and case counts from legacy results without the new fields", () => {
    const legacy = {
      case_count: 1,
      error_count: 0,
      evaluation_suite: "balanced",
      mrr: 1,
      passed: true,
      threshold_failures: [],
      failure_reason_counts: {},
      case_results: [],
    } satisfies EvaluationMetrics;
    expect(metricValue(legacy, "mrr")).toBe(1);
    expect(metricValue(legacy, "refusal_accuracy")).toBeNull();
    expect(metricCaseCount(legacy, "mrr")).toBeNull();
  });

  it("orders thresholds by perspective and drops unset values", () => {
    expect(
      orderedThresholdEntries({ answer_pass_rate: 0.7, legacy_metric: 0.5, mrr: 0.6, faithfulness: null })
    ).toEqual([
      ["mrr", 0.6],
      ["answer_pass_rate", 0.7],
      ["legacy_metric", 0.5],
    ]);
  });
});

describe("business support handling (issue 1231)", () => {
  const handling = EVALUATION_PERSPECTIVES.find((perspective) => perspective.id === "handling")!;
  const answer = EVALUATION_PERSPECTIVES.find((perspective) => perspective.id === "answer")!;
  const base = {
    case_count: 1,
    error_count: 0,
    evaluation_suite: "standard",
    passed: true,
    threshold_failures: [],
    failure_reason_counts: {},
    case_results: [],
  } satisfies EvaluationMetrics;

  it("shows the handling perspective only when some case measured it", () => {
    expect(isPerspectiveShown(base, handling)).toBe(false);
    expect(isPerspectiveShown({ ...base, metric_case_counts: { handling_accuracy: 0 } }, handling)).toBe(false);
    expect(isPerspectiveShown({ ...base, metric_case_counts: { safe_answer_rate: 2 } }, handling)).toBe(true);
    expect(isPerspectiveShown(base, answer)).toBe(true);
  });

  it("labels outcomes and failure reasons in Japanese", () => {
    expect(outcomeLabel("needs_environment_data")).toBe("現場のデータが必要");
    expect(outcomeLabel("future_outcome")).toBe("future_outcome");
    expect(failureReasonLabel("forbidden_action")).toBe("危険な操作を提示");
    // 必要な根拠の取りこぼしと、分かっている条件の聞き直し（#1284）。
    expect(failureReasonLabel("evidence_miss")).toBe("必要な根拠を取れなかった");
    expect(failureReasonLabel("known_condition_reasked")).toBe("分かっている条件を聞き直した");
    expect(metricLabel("safe_answer_rate")).toBe("危険な回答の無さ");
  });
});

