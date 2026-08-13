import { useId } from "react";
import { CheckCircle2, CircleAlert, Clock3 } from "lucide-react";

import { useOperationTiming, type UseOperationTimingOptions } from "@/components/ProcessingState";
import { t } from "@/lib/i18n";

export type ExecutionActivityStatus = "running" | "success" | "error";
type ExecutionActivityTone = "info" | "success" | "danger";

export interface ExecutionActivityPanelProps
  extends Pick<
    UseOperationTimingOptions,
    "operationKey" | "startedAt" | "finishedAt" | "elapsedMs"
  > {
  status: ExecutionActivityStatus;
  label: string;
  testId?: string;
}

const statusLabelKey: Record<ExecutionActivityStatus, string> = {
  running: "executionActivity.status.running",
  success: "executionActivity.status.success",
  error: "executionActivity.status.error",
};

const toneClass: Record<ExecutionActivityTone, string> = {
  info: "border-info/30 bg-info-bg text-info",
  success: "border-success/30 bg-success-bg text-success",
  danger: "border-danger/30 bg-danger-bg text-danger",
};

function statusTone(status: ExecutionActivityStatus): ExecutionActivityTone {
  if (status === "success") return "success";
  if (status === "error") return "danger";
  return "info";
}

function ActivityIcon({ status }: { status: ExecutionActivityStatus }) {
  if (status === "success") return <CheckCircle2 size={17} aria-hidden="true" />;
  if (status === "error") return <CircleAlert size={17} aria-hidden="true" />;
  return <Clock3 size={17} aria-hidden="true" />;
}

export function ExecutionActivityPanel({
  status,
  label,
  operationKey,
  startedAt,
  finishedAt,
  elapsedMs,
  testId,
}: ExecutionActivityPanelProps) {
  const titleId = useId();
  const active = status === "running";
  const timing = useOperationTiming({
    active,
    operationKey,
    startedAt,
    finishedAt,
    elapsedMs,
  });
  const statusClass = toneClass[statusTone(status)];
  const timerLabel = active ? t("common.processing.elapsed") : t("common.processing.duration");

  return (
    <section
      role="status"
      aria-atomic="true"
      aria-busy={active ? "true" : undefined}
      aria-labelledby={titleId}
      className="grid min-w-0 gap-3 rounded-md border border-border bg-background px-3 py-3 text-sm shadow-sm"
      data-testid={testId}
      data-execution-activity-status={status}
    >
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-2">
          <span
            className={`mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border ${statusClass}`}
            aria-hidden="true"
          >
            <ActivityIcon status={status} />
          </span>
          <div className="min-w-0">
            <h3 id={titleId} className="text-sm font-semibold text-foreground">
              {t("executionActivity.title")}
            </h3>
            <p className="mt-0.5 break-words text-sm text-foreground">{label}</p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
          <span
            className={`inline-flex min-h-7 items-center rounded-md border px-2 py-1 text-xs font-semibold ${statusClass}`}
          >
            {t(statusLabelKey[status])}
          </span>
          <span
            className="inline-flex min-h-7 items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-card px-2 py-1 text-xs font-medium text-muted"
            role="timer"
            aria-live="off"
            aria-label={`${timerLabel} ${timing.elapsedClock}`}
            data-testid={testId ? `${testId}-timer` : undefined}
          >
            <Clock3 size={14} aria-hidden="true" />
            <span>{timerLabel}</span>
            <span className="min-w-[3.25rem] text-right font-mono tabular-nums text-foreground">
              {timing.elapsedClock}
            </span>
          </span>
        </div>
      </div>
    </section>
  );
}
