import type { JobData, JobStepData, JobStepStatus } from "./types";

export const NL2SQL_JOB_STAGES = [
  "prepare_context",
  "generate_sql",
  "safety_check",
  "execute_sql",
  "format_results",
] as const;

function isUnfinishedStatus(status: JobStepStatus): boolean {
  return status === "pending" || status === "running";
}

function firstErrorIndex(steps: JobStepData[]): number {
  return steps.findIndex((step) => step.status === "error");
}

/**
 * API snapshot が一時的に「後続完了・前段 running」の形になっても、
 * UI では工程順に矛盾しない進捗へ正規化する。error 境界は越えない。
 */
export function normalizeNl2SqlJobSteps(job: JobData): JobStepData[] {
  const reported = new Map((job.steps ?? []).map((step) => [step.stage, step]));
  const normalized = NL2SQL_JOB_STAGES.map((stage, index): JobStepData => {
    const step = reported.get(stage);
    if (step) return step;
    if (job.status === "done") return { stage, status: "done" };
    if (job.status === "running" && reported.size === 0 && index === 0) {
      return { stage, status: "running" };
    }
    if (job.status === "error" && reported.size === 0 && index === 0) {
      return { stage, status: "error" };
    }
    return { stage, status: "pending" };
  });

  const lastDoneIndex = normalized.reduce(
    (lastIndex, step, index) => (step.status === "done" ? index : lastIndex),
    -1
  );
  if (lastDoneIndex <= 0) return normalized;
  const errorIndex = firstErrorIndex(normalized);
  const fillUntilIndex =
    errorIndex >= 0 && errorIndex < lastDoneIndex ? errorIndex : lastDoneIndex;

  return normalized.map((step, index) => {
    if (index >= fillUntilIndex || !isUnfinishedStatus(step.status)) return step;
    return { ...step, status: "done" };
  });
}
