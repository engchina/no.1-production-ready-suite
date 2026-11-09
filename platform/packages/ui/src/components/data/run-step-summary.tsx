import { useOperationTiming } from "../feedback/processing-state";
import { operationTimestampMs } from "../../lib/operation-timing";
import type { ReactNode } from "react";
import { cn } from "../../lib/utils";
import { runStepPresentation } from "../../lib/run-step-presentation";
import { formatRunDuration } from "./run-history";
import { RunStepStatusIcon, RunTimelineProduct, RUN_TIMELINE_CHEVRON_SIZE, runTimelineStepIndentStyle, type RunTimelineStep } from "./run-detail";
import { RUN_TIME_TRACK, RUN_TIME_FILL, RUN_TIME_COLOR, runTimelineNameClass } from "./run-timeline-style";

/** 行の基本情報は両表示で同じ。操作と詳細の置き場所は呼び出し側が持つ。 */
export function RunStepSummary({step, size, statusLabel, bar, showBars = true, spin = true, surface, renderName, durationTestId, formatDuration = formatRunDuration}: {
  step: RunTimelineStep;
  size: "sm" | "md";
  statusLabel: string;
  bar: {offset: number; width: number | null} | null;
  showBars?: boolean;
  spin?: boolean;
  surface: "timeline" | "trace";
  renderName?: (name: ReactNode) => ReactNode;
  durationTestId?: string;
  formatDuration?: (ms: number) => string;
}) {
  const running = step.status === "running" && operationTimestampMs(step.startedAt) != null;
  const timing = useOperationTiming({active: running, operationKey: `step:${step.id}`, startedAt: step.startedAt});
  const view = runStepPresentation(step, statusLabel, formatDuration, running ? Math.floor(timing.elapsedMs / 1000) * 1000 : undefined);
  const name = <span className="min-w-0 break-words [overflow-wrap:anywhere]">
    <span className={step.status === "failed" ? "text-danger-fg" : step.status === "pending" ? "text-fg-muted" : "text-fg"}
      {...(surface === "timeline" ? {"data-run-timeline-label": ""} : {"data-trace-record-label": ""})}>{step.label}</span>
    {step.product ? <RunTimelineProduct product={step.product} /> : null}
  </span>;
  return <span className="@container grid min-w-0 gap-1" data-run-step-summary="">
    <span className={cn("grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3", showBars && "@xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_9rem] @xl:items-center")}>
      <span className={cn("flex min-w-0 items-start gap-2 leading-5", runTimelineNameClass(size))} style={runTimelineStepIndentStyle(step.level)} data-run-step-name="">
        <span className="pr-icon-line"><RunStepStatusIcon status={step.status} retry={step.retry} size={RUN_TIMELINE_CHEVRON_SIZE[size]} spin={spin} /></span>
        {renderName ? renderName(name) : name}
        {step.meta ? <span className="flex shrink-0 flex-wrap items-center gap-1">{step.meta}</span> : null}
      </span>
      {showBars ? <span className={cn(RUN_TIME_TRACK, "hidden overflow-hidden @xl:block")} aria-hidden="true"
        {...(surface === "timeline" ? {"data-run-timeline-bar": ""} : {"data-trace-time-bar": ""})}>
        {bar ? <span className={cn(RUN_TIME_FILL, step.retry ? RUN_TIME_COLOR.skipped : RUN_TIME_COLOR[step.status])} data-run-step-bar="" data-measured-width={bar.width ?? "unknown"}
          style={{left: `${bar.offset}%`, width: `${bar.width ?? 0}%`}} /> : null}
      </span> : null}
      <span className={cn("text-right text-xs leading-5 text-fg-muted tnum", !step.retry && "whitespace-nowrap")} data-duration-kind={view.durationKind}
        {...(surface === "timeline" ? {"data-run-step-status-duration": ""} : {"data-trace-step-status-duration": ""})}>{statusLabel}{view.durationText != null ? <> · <span data-testid={durationTestId}>{view.durationText}</span></> : null}</span>
    </span>
    {view.result ? <span className="ml-6 text-xs leading-5 text-fg-muted" style={runTimelineStepIndentStyle(step.level)}
      {...(surface === "timeline" ? {"data-run-result-summary": ""} : {"data-trace-result-summary": ""})}>{view.result}</span> : null}
  </span>;
}
