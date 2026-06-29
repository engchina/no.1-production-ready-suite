import { AlertCircle, Check, ChevronRight, Minus } from "lucide-react";

import { cn } from "@/lib/utils";
import { t, type I18nKey } from "@/lib/i18n";
import type { FileStatus } from "@/lib/api";

export type FlowStepStatus =
  | "UPLOADED"
  | "PREPROCESSING"
  | "PREPROCESSED"
  | "INGESTING"
  | "REVIEW"
  | "CHUNKING"
  | "CHUNKED"
  | "INDEXING"
  | "INDEXED";
const ORDER: FlowStepStatus[] = [
  "UPLOADED",
  "PREPROCESSING",
  "PREPROCESSED",
  "INGESTING",
  "REVIEW",
  "CHUNKING",
  "CHUNKED",
  "INDEXING",
  "INDEXED",
];
export const STEP_LABEL_KEY: Record<FlowStepStatus, I18nKey> = {
  UPLOADED: "flow.step.upload",
  PREPROCESSING: "flow.step.preprocess",
  PREPROCESSED: "flow.step.preprocessReview",
  INGESTING: "flow.step.extract",
  REVIEW: "flow.step.review",
  CHUNKING: "flow.step.chunk",
  CHUNKED: "flow.step.chunkReview",
  INDEXING: "flow.step.indexing",
  INDEXED: "flow.step.indexed",
};

/**
 * ドキュメントの処理段階を可視化する。
 * エラー時も工程列は維持し、失敗ステップ(failedStep)を danger で強調する（messaging-spec §9 P5）。
 */
export function FlowStepper({
  status,
  skippedSteps = [],
  failedStep = null,
}: {
  status: FileStatus;
  skippedSteps?: readonly FlowStepStatus[];
  /** ERROR 時に danger で強調する工程。不明なら null（その場合は工程を中立表示)。 */
  failedStep?: FlowStepStatus | null;
}) {
  const errored = status === "ERROR";
  const currentIndex = ORDER.indexOf(status as FlowStepStatus);
  const failedIndex = failedStep ? ORDER.indexOf(failedStep) : -1;
  const skipped = new Set(skippedSteps);

  return (
    <ol className="flex flex-wrap items-center gap-y-2">
      {ORDER.map((step, i) => {
        const skip = skipped.has(step);
        const failed = errored && i === failedIndex;
        const done =
          !skip &&
          !failed &&
          (errored ? failedIndex >= 0 && i < failedIndex : i < currentIndex || status === "INDEXED");
        const active = !errored && !skip && i === currentIndex;
        return (
          <li key={step} className="flex items-center">
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  "flex size-7 items-center justify-center rounded-full text-xs font-semibold",
                  skip
                    ? "border border-border bg-card text-muted"
                    : failed
                    ? "bg-danger text-white"
                    : done
                    ? "bg-success text-white"
                    : active
                      ? "bg-primary text-white"
                      : "bg-border/60 text-muted"
                )}
              >
                {skip ? (
                  <Minus size={14} aria-hidden />
                ) : failed ? (
                  <AlertCircle size={14} aria-hidden />
                ) : done ? (
                  <Check size={14} aria-hidden />
                ) : (
                  i + 1
                )}
              </span>
              <span
                className={cn(
                  "flex items-center gap-1.5 text-sm",
                  failed
                    ? "font-medium text-danger"
                    : skip || done || active
                    ? "font-medium text-foreground"
                    : "text-muted"
                )}
              >
                {t(STEP_LABEL_KEY[step])}
                {skip ? (
                  <span className="rounded bg-border/60 px-1.5 py-0.5 text-[11px] font-semibold uppercase text-muted">
                    {t("flow.step.skipped")}
                  </span>
                ) : null}
                {failed ? (
                  <span className="rounded bg-danger-bg px-1.5 py-0.5 text-[11px] font-semibold text-danger">
                    {t("status.ERROR")}
                  </span>
                ) : null}
              </span>
            </span>
            {i < ORDER.length - 1 ? (
              <ChevronRight size={15} className="mx-2 text-muted" aria-hidden />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
