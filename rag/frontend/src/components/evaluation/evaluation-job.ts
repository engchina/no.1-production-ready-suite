/**
 * 品質評価の job（#390）の表示のための判定。画面の部品から切り離して単体テストする。
 */

import type { StatusVariant } from "@engchina/production-ready-ui";

import type { EvaluationCaseResult, EvaluationJob, EvaluationJobStatus } from "@/lib/api";
import { answerStageLabel } from "@/lib/answer-progress";
import { t, type I18nKey } from "@/lib/i18n";

/** job の状態 → StatusBadge の variant（色だけに頼らず、バッジのアイコンと文言も付く）。 */
export const EVALUATION_JOB_STATUS_VARIANT: Record<EvaluationJobStatus, StatusVariant> = {
  RUNNING: "info",
  SUCCEEDED: "success",
  FAILED: "danger",
  CANCELLED: "neutral",
};

export function evaluationJobStatusLabel(status: EvaluationJobStatus): string {
  return t(`evaluation.job.status.${status}` as I18nKey);
}

export function isEvaluationJobActive(job: Pick<EvaluationJob, "status"> | null | undefined) {
  return job?.status === "RUNNING";
}

/** 終わったケースの割合（0〜100 の整数）。件数が 0 のときは 0。 */
export function evaluationJobPercent(job: Pick<EvaluationJob, "completed_cases" | "total_cases">) {
  if (job.total_cases <= 0) return 0;
  const ratio = Math.min(1, Math.max(0, job.completed_cases / job.total_cases));
  return Math.floor(ratio * 100);
}

/** 進捗の件数の文言（「3 / 10 件（30%）」）。 */
export function evaluationJobCountLabel(
  job: Pick<EvaluationJob, "completed_cases" | "total_cases">
): string {
  return t("evaluation.job.count", {
    completed: job.completed_cases,
    total: job.total_cases,
    percent: evaluationJobPercent(job),
  });
}

/** 実行中のケースの文言。比較では experiment の id も付ける。ケースが無ければ null。 */
export function evaluationJobCurrentCaseLabel(
  job: Pick<EvaluationJob, "status" | "current_case_id" | "current_experiment_id">
): string | null {
  if (job.status !== "RUNNING" || !job.current_case_id) return null;
  return job.current_experiment_id
    ? t("evaluation.job.currentCaseWithExperiment", {
        case: job.current_case_id,
        experiment: job.current_experiment_id,
      })
    : t("evaluation.job.currentCase", { case: job.current_case_id });
}

/** job 全体の時間の上限の文言（分。1 分未満は切り上げ）。 */
export function evaluationJobTimeLimitLabel(job: Pick<EvaluationJob, "time_limit_seconds">) {
  return t("evaluation.job.timeLimit", {
    minutes: Math.max(1, Math.ceil(job.time_limit_seconds / 60)),
  });
}

/** workspace に保存した job id の検証（空文字は「job なし」）。 */
export function isEvaluationJobId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{0,64}$/.test(value);
}

export interface EvaluationCaseErrorSummary {
  /** 時間切れになった工程の表示名（時間切れ以外は null）。 */
  stageLabel: string | null;
  message: string;
}

/** 失敗したケース（status=error）の理由と工程（#383）。成功したケースは null。 */
export function evaluationCaseErrorSummary(
  result: Pick<EvaluationCaseResult, "status" | "error_stage" | "error_message" | "error_type">
): EvaluationCaseErrorSummary | null {
  if (result.status !== "error") return null;
  return {
    stageLabel: result.error_stage ? answerStageLabel(result.error_stage) : null,
    message: result.error_message || result.error_type || t("evaluation.case.errorUnknown"),
  };
}
