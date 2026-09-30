import { describe, expect, it } from "vitest";

import type { EvaluationMetrics } from "@/lib/api";

import {
  EVALUATION_METRIC_NAMES,
  EVALUATION_PERSPECTIVES,
  STANDARD_ANSWER_METRICS,
  failureReasonLabel,
  formatMetricValue,
  metricCaseCount,
  metricLabel,
  metricValue,
  orderedThresholdEntries,
  suiteLabel,
} from "./evaluation-metrics";

describe("evaluation metrics (issue 591)", () => {
  it("groups the nine metrics into retrieval / grounding / answer", () => {
    expect(EVALUATION_PERSPECTIVES.map((perspective) => perspective.id)).toEqual([
      "retrieval",
      "grounding",
      "answer",
    ]);
    expect(EVALUATION_METRIC_NAMES).toHaveLength(9);
    expect(new Set(EVALUATION_METRIC_NAMES).size).toBe(9);
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
