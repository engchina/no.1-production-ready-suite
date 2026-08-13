import { useEffect, useRef, useState, type ReactNode } from "react";
import { Clock3, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

import {
  elapsedMsBetween,
  elapsedMsSince,
  formatElapsedClock,
  type OperationTimestamp,
} from "@/lib/operationTiming";
import { t } from "@/lib/i18n";

const DEFAULT_SLOW_AFTER_MS = 10_000;

/**
 * 処理が影響する最小領域。
 * 表示位置を座標ではなく情報構造で固定し、ページごとの漂流を防ぐ。
 */
export type ProcessingPlacement =
  | "page"
  | "workspace"
  | "panel"
  | "tab"
  | "result"
  | "action"
  | "job";

export type ProcessingActivityIcon = "spinner" | "none";

export interface UseOperationTimingOptions {
  active: boolean;
  /** 同じ component instance で別処理へ切り替わるとき timer をリセットする識別子。 */
  operationKey?: string | number | null;
  /** durable job 等、server が返す開始/終了時刻。未指定時は component 側で開始時刻を保持する。 */
  startedAt?: OperationTimestamp;
  finishedAt?: OperationTimestamp;
  /** server が確定済みの所要時間を返す場合に使用する。 */
  elapsedMs?: number | null;
  slowAfterMs?: number;
}

export interface OperationTiming {
  active: boolean;
  elapsedMs: number;
  elapsedClock: string;
  slow: boolean;
}

/**
 * client request と durable job の両方で使う共通 timer。
 * interval が background tab で間引かれても毎回 Date.now() から再計算する。
 */
export function useOperationTiming({
  active,
  operationKey = null,
  startedAt,
  finishedAt,
  elapsedMs: reportedElapsedMs,
  slowAfterMs = DEFAULT_SLOW_AFTER_MS,
}: UseOperationTimingOptions): OperationTiming {
  const localStartRef = useRef<{ key: string | number | null; at: number | null }>({
    key: operationKey,
    at: active ? Date.now() : null,
  });
  const wasActiveRef = useRef(active);
  const [nowMs, setNowMs] = useState(() => Date.now());

  if (
    active &&
    (!wasActiveRef.current ||
      localStartRef.current.at === null ||
      localStartRef.current.key !== operationKey)
  ) {
    localStartRef.current = { key: operationKey, at: Date.now() };
  } else if (!active && (wasActiveRef.current || localStartRef.current.key !== operationKey)) {
    localStartRef.current = { key: operationKey, at: null };
  }
  wasActiveRef.current = active;

  useEffect(() => {
    if (!active) return undefined;
    const update = () => setNowMs(Date.now());
    update();
    const timer = window.setInterval(update, 1000);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, [active, operationKey]);

  const externalElapsed = elapsedMsBetween(startedAt, finishedAt, nowMs);
  const localStart = localStartRef.current.at;
  const elapsedMs =
    !active && reportedElapsedMs != null
      ? Math.max(0, reportedElapsedMs)
      : externalElapsed ?? (localStart === null ? 0 : elapsedMsSince(localStart, nowMs));

  return {
    active,
    elapsedMs,
    elapsedClock: formatElapsedClock(elapsedMs),
    slow: active && elapsedMs >= slowAfterMs,
  };
}

export interface ProcessingIndicatorProps extends UseOperationTimingOptions {
  label: string;
  finalLabel?: string;
  onCancel?: () => void;
  placement?: ProcessingPlacement;
  className?: string;
  testId?: string;
  showSlowMessage?: boolean;
  /** 同じ operation の主ボタンが loading の場合は "none" にして動的 icon を 1 つへ絞る。 */
  activityIcon?: ProcessingActivityIcon;
}

/** 処理ラベル・spinner・経過時間・取消を一列にまとめた全画面共通表示。 */
export function ProcessingIndicator({
  label,
  finalLabel,
  onCancel,
  placement = "panel",
  className = "",
  testId,
  showSlowMessage = true,
  activityIcon = "spinner",
  ...timingOptions
}: ProcessingIndicatorProps) {
  const timing = useOperationTiming(timingOptions);
  const displayLabel = timing.active ? label : finalLabel ?? t("common.processing.completed");
  const showActivityIcon = activityIcon === "spinner";

  return (
    <div
      className={`grid min-w-0 gap-2 ${className}`}
      aria-busy={timing.active}
      data-processing-placement={placement}
      data-processing-activity-icon={activityIcon}
      data-testid={testId}
    >
      {timing.active ? (
        <span className="sr-only" role="status">
          {label}
        </span>
      ) : null}
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
          {timing.active && showActivityIcon ? (
            <Loader2
              size={16}
              className="shrink-0 animate-spin text-primary motion-reduce:animate-none"
              aria-hidden="true"
            />
          ) : !timing.active && showActivityIcon ? (
            <Clock3 size={16} className="shrink-0 text-muted" aria-hidden="true" />
          ) : null}
          <span className="min-w-0 break-words">{displayLabel}</span>
        </span>
        <span className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
          <span
            className="inline-flex min-h-7 items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-card px-2 py-1 text-xs font-medium text-muted"
            role="timer"
            aria-live="off"
            aria-label={`${timing.active ? t("common.processing.elapsed") : t("common.processing.duration")} ${timing.elapsedClock}`}
            data-testid={testId ? `${testId}-timer` : undefined}
          >
            <Clock3 size={14} aria-hidden="true" />
            <span>{timing.active ? t("common.processing.elapsed") : t("common.processing.duration")}</span>
            <span className="min-w-[3.25rem] text-right font-mono tabular-nums text-foreground">
              {timing.elapsedClock}
            </span>
          </span>
          {timing.active && onCancel ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="min-h-11"
              onClick={onCancel}
            >
              {t("common.cancel")}
            </Button>
          ) : null}
        </span>
      </div>
      {showSlowMessage && timing.slow ? (
        <p className="text-xs leading-5 text-muted" role="status" data-testid={testId ? `${testId}-slow` : undefined}>
          {t("common.processing.slow")}
        </p>
      ) : null}
    </div>
  );
}

export interface TimedLoadingStateProps {
  label: string;
  children?: ReactNode;
  operationKey?: string | number | null;
  startedAt?: OperationTimestamp;
  onCancel?: () => void;
  placement?: ProcessingPlacement;
  className?: string;
  testId?: string;
  framed?: boolean;
  activityIcon?: ProcessingActivityIcon;
}

/** Skeleton/結果領域の寸法を保ったまま共通 timer を付ける loading container。 */
export function TimedLoadingState({
  label,
  children,
  operationKey,
  startedAt,
  onCancel,
  placement = "panel",
  className = "",
  testId,
  framed = true,
  activityIcon,
}: TimedLoadingStateProps) {
  const effectiveActivityIcon = activityIcon ?? (placement === "result" ? "none" : "spinner");

  return (
    <section
      className={`grid min-w-0 gap-3 ${
        framed ? "rounded-md border border-border bg-background p-3" : ""
      } ${className}`}
      aria-busy="true"
      aria-label={label}
      data-processing-placement={placement}
      data-testid={testId}
    >
      <ProcessingIndicator
        active
        label={label}
        operationKey={operationKey}
        startedAt={startedAt}
        onCancel={onCancel}
        placement={placement}
        testId={testId ? `${testId}-processing` : undefined}
        activityIcon={effectiveActivityIcon}
      />
      {children}
    </section>
  );
}
