import { describe, expect, it } from "vitest";

import type { EvaluationJob } from "@/lib/api";

import {
  EVALUATION_JOB_STATUS_VARIANT,
  evaluationCaseErrorSummary,
  evaluationJobCountLabel,
  evaluationJobCurrentCaseLabel,
  evaluationJobPercent,
  evaluationJobStatusLabel,
  evaluationJobTimeLimitLabel,
  isEvaluationJobActive,
  isEvaluationJobId,
} from "./evaluation-job";

function job(overrides: Partial<EvaluationJob> = {}): EvaluationJob {
  return {
    job_id: "job-1",
    kind: "run",
    status: "RUNNING",
    total_cases: 10,
    completed_cases: 3,
    current_case_id: "case-4",
    current_experiment_id: null,
    current_case_started_at: "2026-09-28T00:00:00Z",
    time_limit_seconds: 3600,
    error_message: null,
    created_at: "2026-09-28T00:00:00Z",
    started_at: "2026-09-28T00:00:00Z",
    finished_at: null,
    heartbeat_at: "2026-09-28T00:00:10Z",
    run_result: null,
    compare_result: null,
    ...overrides,
  };
}

describe("評価 job の表示（Issue 390）", () => {
  it("進捗は件数と割合で示す", () => {
    expect(evaluationJobPercent(job())).toBe(30);
    expect(evaluationJobCountLabel(job())).toBe("3 / 10 件（30%）");
    expect(evaluationJobPercent(job({ total_cases: 0, completed_cases: 0 }))).toBe(0);
    expect(evaluationJobPercent(job({ completed_cases: 12 }))).toBe(100);
  });

  it("実行中のケースを示し、比較では experiment も付ける", () => {
    expect(evaluationJobCurrentCaseLabel(job())).toBe("実行中のケース: case-4");
    expect(evaluationJobCurrentCaseLabel(job({ current_experiment_id: "hybrid" }))).toBe(
      "実行中のケース: case-4（hybrid）"
    );
    expect(evaluationJobCurrentCaseLabel(job({ status: "SUCCEEDED" }))).toBeNull();
    expect(evaluationJobCurrentCaseLabel(job({ current_case_id: null }))).toBeNull();
  });

  it("状態は StatusBadge の variant と文言に対応する", () => {
    expect(EVALUATION_JOB_STATUS_VARIANT.RUNNING).toBe("info");
    expect(EVALUATION_JOB_STATUS_VARIANT.FAILED).toBe("danger");
    expect(evaluationJobStatusLabel("CANCELLED")).toBe("取り消し済み");
    expect(isEvaluationJobActive(job())).toBe(true);
    expect(isEvaluationJobActive(job({ status: "FAILED" }))).toBe(false);
    expect(isEvaluationJobActive(null)).toBe(false);
  });

  it("全体の上限は分で示す", () => {
    expect(evaluationJobTimeLimitLabel(job())).toBe("全体の上限 60 分");
    expect(evaluationJobTimeLimitLabel(job({ time_limit_seconds: 90 }))).toBe("全体の上限 2 分");
  });

  it("保存した job id は英数字だけを受け付ける", () => {
    expect(isEvaluationJobId("")).toBe(true);
    expect(isEvaluationJobId("a1b2c3")).toBe(true);
    expect(isEvaluationJobId("../x")).toBe(false);
    expect(isEvaluationJobId(1)).toBe(false);
  });

  it("失敗したケースは理由と時間切れになった工程を返す", () => {
    expect(
      evaluationCaseErrorSummary({
        status: "error",
        error_type: "TimeoutError",
        error_stage: "answer",
        error_message: "評価ケースの回答生成が上限の 5 分以内に終わりませんでした",
      })
    ).toEqual({
      stageLabel: "根拠の検索と回答の生成",
      message: "評価ケースの回答生成が上限の 5 分以内に終わりませんでした",
    });
    expect(
      evaluationCaseErrorSummary({
        status: "error",
        error_type: "EvaluationTimeBudgetExceeded",
        error_stage: null,
        error_message: null,
      })
    ).toEqual({ stageLabel: null, message: "EvaluationTimeBudgetExceeded" });
    expect(
      evaluationCaseErrorSummary({
        status: "success",
        error_type: null,
        error_stage: null,
        error_message: null,
      })
    ).toBeNull();
  });
});
