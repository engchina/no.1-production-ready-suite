import type { ReactNode } from "react";
import {
  Check,
  CheckCircle2,
  Clock3,
  Loader2,
  Route,
  TriangleAlert,
  X,
} from "lucide-react";

import { useOperationTiming } from "@/components/ProcessingState";
import { Button } from "@/components/ui/button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { StatusBadge } from "@/components/ui/status-badge";
import { t } from "@/lib/i18n";
import type { OperationTimestamp } from "@/lib/operationTiming";

type StatusBadgeVariant = "neutral" | "info" | "success" | "warning" | "danger" | "pending";

export type WorkflowProgressStepStatus = "pending" | "running" | "done" | "error" | "skipped";
export type WorkflowProgressTone = "active" | "success" | "danger" | "neutral";

export interface WorkflowProgressStep {
  id: string;
  label: ReactNode;
  description?: ReactNode;
  content?: ReactNode;
  status: WorkflowProgressStepStatus;
  statusLabel: string;
  elapsedLabel?: string;
  open?: boolean;
  testId?: string;
  dataStatus?: string;
}

export interface WorkflowProgressCollapsible {
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  collapseLabel: string;
  expandLabel: string;
  toggleTestId?: string;
}

export interface WorkflowProgressStripProps {
  active: boolean;
  operationKey: string | number | null;
  startedAt?: OperationTimestamp;
  finishedAt?: OperationTimestamp;
  elapsedMs?: number | null;
  title: ReactNode;
  titleId: string;
  message: ReactNode;
  statusLabel: string;
  statusVariant: StatusBadgeVariant;
  tone: WorkflowProgressTone;
  steps: WorkflowProgressStep[];
  stepsAriaLabel: string;
  testId: string;
  dataJobStatus: string;
  role?: "alert" | "status";
  meta?: ReactNode;
  actions?: ReactNode;
  headerExtra?: ReactNode;
  footer?: ReactNode;
  collapsible?: WorkflowProgressCollapsible;
}

function toneBorderClass(tone: WorkflowProgressTone) {
  if (tone === "danger") return "border-l-danger";
  if (tone === "success") return "border-l-success";
  if (tone === "neutral") return "border-l-muted";
  return "border-l-primary";
}

function toneIconClass(tone: WorkflowProgressTone) {
  if (tone === "danger") return "bg-danger-bg text-danger";
  if (tone === "success") return "bg-success-bg text-success";
  if (tone === "neutral") return "bg-muted/40 text-muted";
  return "bg-primary/10 text-primary";
}

function toneIcon(tone: WorkflowProgressTone) {
  if (tone === "success") return <CheckCircle2 size={18} />;
  if (tone === "danger") return <TriangleAlert size={18} />;
  if (tone === "neutral") return <Clock3 size={18} />;
  return <Route size={18} />;
}

function stepTextClass(status: WorkflowProgressStepStatus) {
  if (status === "running") return "text-primary";
  if (status === "done") return "text-success";
  if (status === "error") return "text-danger";
  return "text-muted";
}

function stepCircleClass(status: WorkflowProgressStepStatus) {
  if (status === "running") {
    return "border-primary-fill bg-primary-fill text-primary-fill-foreground";
  }
  if (status === "done") return "border-success-fill bg-success-fill text-white";
  if (status === "error") return "border-danger-fill bg-danger-fill text-white";
  if (status === "skipped") return "border-border bg-muted/30 text-muted";
  return "border-border bg-card text-muted";
}

function StepIcon({
  status,
  index,
}: {
  status: WorkflowProgressStepStatus;
  index: number;
}) {
  if (status === "running") {
    return <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />;
  }
  if (status === "done") return <Check size={14} aria-hidden="true" />;
  if (status === "error") return <X size={14} aria-hidden="true" />;
  if (status === "skipped") return <span aria-hidden="true">-</span>;
  return <span aria-hidden="true">{index + 1}</span>;
}

export function WorkflowProgressStrip({
  active,
  operationKey,
  startedAt,
  finishedAt,
  elapsedMs,
  title,
  titleId,
  message,
  statusLabel,
  statusVariant,
  tone,
  steps,
  stepsAriaLabel,
  testId,
  dataJobStatus,
  role,
  meta,
  actions,
  headerExtra,
  footer,
  collapsible,
}: WorkflowProgressStripProps) {
  const timing = useOperationTiming({
    active,
    operationKey,
    startedAt,
    finishedAt,
    elapsedMs,
  });
  const timerKind = active ? t("common.processing.elapsed") : t("common.processing.duration");
  const collapsed = collapsible?.collapsed ?? false;
  const bodyId = `${titleId}-body`;
  const toggleLabel = collapsed ? collapsible?.expandLabel : collapsible?.collapseLabel;

  return (
    <section
      className={`overflow-hidden rounded-md border border-border border-l-4 bg-card shadow-sm ${toneBorderClass(tone)}`}
      role={role}
      aria-labelledby={titleId}
      data-testid={testId}
      data-job-status={dataJobStatus}
    >
      <div
        className={`flex flex-col gap-3 bg-background px-4 py-3 sm:flex-row sm:items-center sm:justify-between ${
          collapsed ? "" : "border-b border-border"
        }`}
      >
        <div className="flex min-w-0 items-start gap-3">
          <span
            className={`mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md ${toneIconClass(tone)}`}
            aria-hidden="true"
          >
            {toneIcon(tone)}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id={titleId} className="font-semibold text-foreground">
                {title}
              </h2>
              <StatusBadge variant={statusVariant} label={statusLabel} />
              {headerExtra}
            </div>
            <p className="mt-1 text-sm text-foreground" aria-live="polite">
              {message}
            </p>
          </div>
        </div>
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted sm:justify-end">
          {meta}
          <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-1.5 font-mono tabular-nums">
            <Clock3 size={14} className="shrink-0" aria-hidden="true" />
            <span>{timerKind}</span>
            <span
              className="min-w-0 break-all"
              role="timer"
              aria-live="off"
              aria-label={`${timerKind} ${timing.elapsedClock}`}
            >
              {timing.elapsedClock}
            </span>
          </span>
          {actions}
          {collapsible ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="min-h-11 min-w-11 p-0 text-muted hover:text-foreground"
              aria-label={toggleLabel}
              aria-expanded={!collapsed}
              aria-controls={bodyId}
              title={toggleLabel}
              onClick={() => collapsible.onCollapsedChange(!collapsed)}
              data-testid={collapsible.toggleTestId}
            >
              <DisclosureChevron
                expanded={!collapsed}
                size={16}
              />
            </Button>
          ) : null}
        </div>
      </div>

      <div id={bodyId} hidden={collapsed}>
        <ol className="grid gap-0 px-4 py-2" aria-label={stepsAriaLabel}>
          {steps.map((step, index) => {
            const running = step.status === "running";
            const done = step.status === "done";
            return (
              <li
                key={step.id}
                className="relative grid min-w-0 grid-cols-[1.75rem_minmax(0,1fr)] gap-2 py-2 sm:gap-3"
                aria-current={running ? "step" : undefined}
                data-testid={step.testId}
                data-step-status={step.dataStatus ?? step.status}
              >
                {index < steps.length - 1 && (
                  <span
                    className={`absolute bottom-[-0.5rem] left-[0.84375rem] top-9 w-px ${
                      done ? "bg-success" : "bg-border"
                    }`}
                    aria-hidden="true"
                  />
                )}
                <span
                  className={`relative z-10 inline-flex h-7 w-7 items-center justify-center rounded-full border text-xs font-bold ${stepCircleClass(step.status)}`}
                >
                  <StepIcon status={step.status} index={index} />
                </span>
                <details
                  className="group/disclosure min-w-0 rounded-md border border-transparent px-1 py-1 open:border-border open:bg-background sm:px-2"
                  open={step.open}
                >
                  <summary className="flex min-h-11 min-w-0 max-w-full cursor-pointer list-none flex-wrap items-center justify-between gap-2 overflow-hidden rounded-sm text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 sm:gap-3 [&::-webkit-details-marker]:hidden">
                    <span className="flex min-w-0 max-w-full flex-1 basis-full items-center gap-2 font-semibold text-foreground sm:basis-0">
                      <span className="min-w-0 break-words sm:truncate">{step.label}</span>
                      <DisclosureChevron
                        expanded="group"
                        size={14}
                        className="text-muted"
                      />
                    </span>
                    <span
                      className={`min-w-0 max-w-full basis-full break-all text-right text-xs font-medium leading-5 sm:basis-auto sm:break-normal ${stepTextClass(step.status)}`}
                    >
                      {step.statusLabel}
                      {step.elapsedLabel ? ` · ${step.elapsedLabel}` : ""}
                    </span>
                  </summary>
                  {step.description ? (
                    <p className="mt-2 border-l border-border pl-3 text-xs leading-5 text-muted">
                      {step.description}
                    </p>
                  ) : null}
                  {step.content}
                </details>
              </li>
            );
          })}
        </ol>

        {footer}
      </div>
    </section>
  );
}
