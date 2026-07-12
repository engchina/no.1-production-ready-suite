import {
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Loader2,
  Route,
  TriangleAlert,
  X,
} from "lucide-react";

import { StatusBadge } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { formatElapsed } from "../useOperationTimer";
import type { JobData, JobStatus, JobStepData, JobStepStatus } from "../types";

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

function StepIcon({ status, index }: { status: JobStepStatus; index: number }) {
  if (status === "running") {
    return <Loader2 size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />;
  }
  if (status === "done") return <Check size={16} aria-hidden="true" />;
  if (status === "error") return <X size={16} aria-hidden="true" />;
  if (status === "skipped") return <span aria-hidden="true">—</span>;
  return <span aria-hidden="true">{index + 1}</span>;
}

export function OperationStatusStrip({
  job,
  elapsedSeconds,
}: {
  job: JobData | null;
  elapsedSeconds: number;
}) {
  if (!job) return null;

  const active = job.status === "pending" || job.status === "running";
  const variant = job.status === "done" ? "success" : job.status === "error" ? "danger" : "pending";
  const finalElapsed = job.elapsed_ms ?? job.result?.timing.elapsed_ms ?? job.timing?.elapsed_ms;
  const steps = normalizeSteps(job);

  return (
    <section
      className={`overflow-hidden rounded-md border border-border border-l-4 bg-card shadow-sm ${
        job.status === "error"
          ? "border-l-red-600"
          : job.status === "done"
            ? "border-l-emerald-600"
            : "border-l-sky-700"
      }`}
      role={job.status === "error" ? "alert" : active ? "status" : undefined}
      aria-labelledby="nl2sql-progress-title"
      data-testid="nl2sql-job-progress"
      data-job-status={job.status}
    >
      <div className="flex flex-col gap-3 border-b border-border bg-background px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span
            className={`mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${
              job.status === "error"
                ? "bg-danger-bg text-danger"
                : job.status === "done"
                  ? "bg-success-bg text-success"
                  : "bg-primary/10 text-primary"
            }`}
            aria-hidden="true"
          >
            {active ? (
              <Route size={18} />
            ) : job.status === "done" ? (
              <CheckCircle2 size={18} />
            ) : (
              <TriangleAlert size={18} />
            )}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="nl2sql-progress-title" className="font-semibold text-foreground">
                {t("nl2sql.progress.title")}
              </h2>
              <StatusBadge variant={variant} label={statusLabel(job.status)} />
            </div>
            <p className="mt-1 text-sm text-foreground" aria-live="polite">
              {progressMessage(job.status)}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pl-12 text-xs text-muted sm:justify-end sm:pl-0">
          <span className="font-mono">{t("nl2sql.status.jobId", { id: `${job.job_id.slice(0, 8)}...` })}</span>
          <span className="inline-flex items-center gap-1.5 font-mono tabular-nums" aria-label={t("nl2sql.progress.elapsedLabel")}>
            <Clock3 size={14} aria-hidden="true" />
            {active
              ? t("nl2sql.status.elapsed", { seconds: elapsedSeconds })
              : t("nl2sql.status.elapsedFinal", { elapsed: formatElapsed(finalElapsed) })}
          </span>
        </div>
      </div>

      <ol className="grid gap-0 px-4 py-2" aria-label={t("nl2sql.progress.stepsLabel")}>
        {steps.map((step, index) => {
          const running = step.status === "running";
          const done = step.status === "done";
          const failed = step.status === "error";
          const skipped = step.status === "skipped";
          return (
            <li
              key={step.stage}
              className="relative grid min-w-0 grid-cols-[2.25rem_minmax(0,1fr)] gap-3 py-2"
              aria-current={running ? "step" : undefined}
              data-testid={`nl2sql-job-step-${step.stage}`}
              data-step-status={step.status}
            >
              {index < steps.length - 1 && (
                <span
                  className={`absolute bottom-[-0.5rem] left-[1.0625rem] top-10 w-px ${
                    done ? "bg-success" : "bg-border"
                  }`}
                  aria-hidden="true"
                />
              )}
              <span
                className={`relative z-10 inline-flex h-9 w-9 items-center justify-center rounded-full border text-xs font-bold ${
                  running
                    ? "border-primary bg-primary text-white"
                    : done
                      ? "border-success bg-success text-white"
                      : failed
                        ? "border-danger bg-danger text-white"
                        : skipped
                          ? "border-border bg-muted/30 text-muted"
                        : "border-border bg-card text-muted"
                }`}
              >
                <StepIcon status={step.status} index={index} />
              </span>
              <details className="group min-w-0 rounded-md border border-transparent px-2 py-1 open:border-border open:bg-background" open={running}>
                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-sm text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden">
                  <span className="flex min-w-0 items-center gap-2 font-semibold text-foreground">
                    <span className="truncate">{stepLabel(step.stage)}</span>
                    <ChevronDown
                      size={14}
                      className="shrink-0 text-muted transition-transform duration-200 group-open:rotate-180 motion-reduce:transition-none"
                      aria-hidden="true"
                    />
                  </span>
                  <span
                    className={`shrink-0 text-xs font-medium ${
                      running
                        ? "text-primary"
                        : done
                          ? "text-success"
                          : failed
                            ? "text-danger"
                            : skipped
                              ? "text-muted"
                            : "text-muted"
                    }`}
                  >
                    {stepStatusLabel(step.status)}
                    {step.elapsed_ms != null ? ` · ${formatElapsed(step.elapsed_ms)}` : ""}
                  </span>
                </summary>
                <p className="mt-2 border-l border-border pl-3 text-xs leading-5 text-muted">
                  {stepDescription(step.stage)}
                </p>
              </details>
            </li>
          );
        })}
      </ol>

      {job.error_message && (
        <p className="mx-4 mb-4 rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger">
          {job.error_message}
        </p>
      )}
    </section>
  );
}
