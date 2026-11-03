import { describe, expect, it } from "vitest";

import type { EvaluationJob, EvaluationMetrics } from "@/lib/api";

import {
  EVALUATION_JOB_STATUS_VARIANT,
  evaluationCaseErrorSummary,
  evaluationJobCountLabel,
  evaluationJobCurrentCaseLabel,
  evaluationJobPartialLabel,
  evaluationJobPercent,
  evaluationJobShownResult,
  evaluationJobStatusLabel,
  evaluationJobTimeLimitLabel,
  isEvaluationJobActive,
  isEvaluationJobId,
  splitEvaluationJobFailure,
} from "./evaluation-job";

function job(overrides: Partial<EvaluationJob> = {}): EvaluationJob {
  return {
    job_id: "job-1",
    kind: "run",
    status: "RUNNING",
    total_cases: 10,
    completed_cases: 3,
    current_case_id: "case-4",
    current_case_started_at: "2026-09-28T00:00:00Z",
    time_limit_seconds: 3600,
    error_message: null,
    created_at: "2026-09-28T00:00:00Z",
    started_at: "2026-09-28T00:00:00Z",
    finished_at: null,
    heartbeat_at: "2026-09-28T00:00:10Z",
    run_result: null,
    partial_result: null,
    source_workbook_id: null,
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

  it("実行中のケースを示し、Excel から作ったケースは行で示す", () => {
    expect(evaluationJobCurrentCaseLabel(job())).toBe("実行中のケース: case-4");
    expect(evaluationJobCurrentCaseLabel(job({ current_case_id: "row-12" }))).toBe(
      "実行中のケース: 12 行目"
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
        error_message: "評価ケースの回答生成が上限の 10 分以内に終わりませんでした",
      })
    ).toEqual({
      stageLabel: "質問の条件を整理して回答を作る",
      message: "評価ケースの回答生成が上限の 10 分以内に終わりませんでした",
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

describe("評価 job の失敗の文", () => {
  it("末尾のエラー種別は本文から分ける（「詳細」に出す）", () => {
    expect(
      splitEvaluationJobFailure(
        "品質評価の実行中に予期しないエラーが発生したため、評価を中断しました。もう一度実行してください。 エラー種別: TimeoutError"
      )
    ).toEqual({
      message: "品質評価の実行中に予期しないエラーが発生したため、評価を中断しました。もう一度実行してください。",
      errorType: "TimeoutError",
    });
  });

  it("エラー種別の無い文（中断・取り消しなど）はそのまま出す", () => {
    expect(splitEvaluationJobFailure("サービスの停止・再起動で品質評価を中断しました。")).toEqual({
      message: "サービスの停止・再起動で品質評価を中断しました。",
      errorType: null,
    });
    expect(splitEvaluationJobFailure(null)).toEqual({ message: "", errorType: null });
  });
});

describe("途中までの結果（Issue 4208）", () => {
  const metrics = (caseCount: number, comparison?: EvaluationMetrics["comparison"]) =>
    ({ case_count: caseCount, comparison: comparison ?? null }) as EvaluationMetrics;

  it("結果の領域には成功の結果を優先し、無ければ途中までの結果を出す", () => {
    const partial = metrics(2);
    expect(evaluationJobShownResult(job({ partial_result: partial }))).toBe(partial);
    const done = metrics(3);
    expect(evaluationJobShownResult(job({ run_result: done }))).toBe(done);
    expect(evaluationJobShownResult(job())).toBeNull();
  });

  it("範囲の文言は比べるプロファイルの分も数え、途中までの結果が無ければ出さない", () => {
    const comparison = { search_answer_profile_id: "p", metrics: metrics(1) };
    const label = evaluationJobPartialLabel(
      job({ total_cases: 10, partial_result: metrics(2, comparison as EvaluationMetrics["comparison"]) })
    );
    expect(label).toContain("3 件");
    expect(label).toContain("全 10 件");
    expect(evaluationJobPartialLabel(job())).toBeNull();
  });
});
