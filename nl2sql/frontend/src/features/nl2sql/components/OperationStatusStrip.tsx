import { Database } from "lucide-react";

import { Button } from "@/components/ui/button";

import { t } from "@/lib/i18n";
import { formatElapsedDuration as formatElapsed } from "@/lib/operationTiming";
import type { JobData, JobStatus, JobStepData, JobStepStatus } from "../types";
import { GeneratedSqlSummary } from "./GeneratedSqlPanel";
import { QuestionText } from "./QuestionText";
import { WorkflowProgressStrip, type WorkflowProgressStepStatus } from "./WorkflowProgressStrip";

const JOB_STAGES = [
  "prepare_context",
  "generate_sql",
  "safety_check",
  "execute_sql",
  "format_results",
] as const;

function statusLabel(status: JobStatus) {
  if (status === "done") return t("nl2sql.status.done");
  if (status === "error") return t("nl2sql.status.error");
  if (status === "running") return t("nl2sql.status.running");
  return t("nl2sql.status.pending");
}

function stepStatusLabel(status: JobStepStatus) {
  if (status === "done") return t("nl2sql.progress.step.done");
  if (status === "error") return t("nl2sql.progress.step.error");
  if (status === "skipped") return t("nl2sql.progress.step.skipped");
  if (status === "running") return t("nl2sql.progress.step.running");
  return t("nl2sql.progress.step.pending");
}

function stepLabel(stage: string) {
  return t(`nl2sql.progress.${stage}.label`);
}

function stepDescription(stage: string) {
  return t(`nl2sql.progress.${stage}.description`);
}

function normalizeSteps(job: JobData): JobStepData[] {
  const reported = new Map((job.steps ?? []).map((step) => [step.stage, step]));
  return JOB_STAGES.map((stage, index) => {
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
}

function progressMessage(status: JobStatus) {
  if (status === "done") return t("nl2sql.progress.done");
  if (status === "error") return t("nl2sql.progress.error");
  if (status === "running") return t("nl2sql.progress.running");
  return t("nl2sql.progress.pending");
}

function normalizeStepStatus(status: JobStepStatus): WorkflowProgressStepStatus {
  if (status === "done") return "done";
  if (status === "error") return "error";
  if (status === "running") return "running";
  if (status === "skipped") return "skipped";
  return "pending";
}

export function OperationStatusStrip({
  job,
  profileId = "",
  startedAtMs,
  catalogEmpty = false,
  importingSample = false,
  onImportSample,
  sampleImportUnavailableHint = "",
  onPreviewExecute,
  previewExecuteLoading = false,
}: {
  job: JobData | null;
  profileId?: string;
  startedAtMs: number | null;
  /** schema catalog が空（サンプル未投入）か。空 catalog 由来の失敗をアクション化する。 */
  catalogEmpty?: boolean;
  importingSample?: boolean;
  onImportSample?: () => void;
  sampleImportUnavailableHint?: string;
  /** プレビュー(擬似 job)経路で `generate_sql` ステップ内に実行ボタンを出すためのハンドラ。 */
  onPreviewExecute?: () => void;
  previewExecuteLoading?: boolean;
}) {
  const active = job?.status === "pending" || job?.status === "running";
  const finalElapsed =
    job?.elapsed_ms ?? job?.result?.timing.elapsed_ms ?? job?.timing?.elapsed_ms;
  if (!job) return null;

  const variant = job.status === "done" ? "success" : job.status === "error" ? "danger" : "pending";
  const steps = normalizeSteps(job);
  // プレビュー経路: execute/format 未実行(skipped)。「完了」ではなく確認を促す文言に差し替える。
  const isPreview = Boolean(onPreviewExecute);
  const warningMessage = job.warning_message?.trim();
  const errorMessage = job.status === "error" ? job.error_message?.trim() : "";

  return (
    <WorkflowProgressStrip
      active={active}
      operationKey={job.job_id}
      startedAt={startedAtMs}
      elapsedMs={finalElapsed}
      title={t("nl2sql.progress.title")}
      titleId="nl2sql-progress-title"
      message={
        isPreview && job.status === "done"
          ? t("nl2sql.progress.previewDone")
          : progressMessage(job.status)
      }
      statusLabel={statusLabel(job.status)}
      statusVariant={variant}
      tone={job.status === "error" ? "danger" : job.status === "done" ? "success" : "active"}
      stepsAriaLabel={t("nl2sql.progress.stepsLabel")}
      testId="nl2sql-job-progress"
      dataJobStatus={job.status}
      role={job.status === "error" ? "alert" : active ? "status" : undefined}
      meta={
        <span className="font-mono">
          {t("nl2sql.status.jobId", { id: `${job.job_id.slice(0, 8)}...` })}
        </span>
      }
      steps={steps.map((step) => ({
        id: step.stage,
        label: stepLabel(step.stage),
        description: stepDescription(step.stage),
        status: normalizeStepStatus(step.status),
        statusLabel: stepStatusLabel(step.status),
        elapsedLabel: step.elapsed_ms != null ? formatElapsed(step.elapsed_ms) : "",
        open: step.status === "running" || (step.stage === "generate_sql" && Boolean(job.result)),
        testId: `nl2sql-job-step-${step.stage}`,
        dataStatus: step.status,
        content: (
          <>
            {step.stage === "prepare_context" && job.result && (
              <dl className="mt-2 grid gap-2 border-l border-border pl-3 text-xs">
                <div className="grid gap-0.5">
                  <dt className="font-medium text-muted">{t("nl2sql.result.rewritten")}</dt>
                  <dd className="leading-5 text-foreground">
                    <QuestionText
                      value={job.result.rewritten_question || "-"}
                      variant="compact"
                      maxLines={2}
                      className="text-foreground"
                    />
                  </dd>
                </div>
                <div className="grid gap-0.5">
                  <dt className="font-medium text-muted">{t("nl2sql.result.tables")}</dt>
                  <dd className="break-words font-mono leading-5 text-foreground">
                    {job.result.safety.referenced_tables.join(", ") || "-"}
                  </dd>
                </div>
                <div className="grid gap-0.5">
                  <dt className="font-medium text-muted">{t("nl2sql.result.columns")}</dt>
                  <dd className="break-words font-mono leading-5 text-foreground">
                    {job.result.safety.referenced_columns.join(", ") || "-"}
                  </dd>
                </div>
              </dl>
            )}
            {step.stage === "generate_sql" && job.result && (
              <div className="mt-2 border-l border-border pl-3">
                <GeneratedSqlSummary
                  result={job.result}
                  profileId={profileId}
                  onExecute={onPreviewExecute}
                  executeLoading={previewExecuteLoading}
                />
              </div>
            )}
          </>
        ),
      }))}
      footer={
        <>
          {warningMessage && (
            <div
              className="mx-4 mb-4 rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm leading-6 text-warning"
              role="status"
            >
              <p>{warningMessage}</p>
            </div>
          )}

          {errorMessage && (
            <div className="mx-4 mb-4 grid gap-3 rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger">
              <p>{errorMessage}</p>
              {job.status === "error" && catalogEmpty && onImportSample && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    loading={importingSample}
                    onClick={onImportSample}
                  >
                    <Database size={15} aria-hidden="true" />
                    <span>{t("nl2sql.sample.import")}</span>
                  </Button>
                  <span className="text-xs text-muted">{t("nl2sql.sample.importHint")}</span>
                </div>
              )}
              {job.status === "error" && catalogEmpty && !onImportSample && sampleImportUnavailableHint && (
                <p className="text-xs text-muted">{sampleImportUnavailableHint}</p>
              )}
            </div>
          )}
        </>
      }
    />
  );
}
