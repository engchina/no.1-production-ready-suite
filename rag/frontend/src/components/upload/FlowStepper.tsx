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
const STEP_LABEL_KEY: Record<FlowStepStatus, I18nKey> = {
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

/** ドキュメントの処理段階を可視化する。 */
export function FlowStepper({
  status,
  skippedSteps = [],
}: {
  status: FileStatus;
  skippedSteps?: readonly FlowStepStatus[];
}) {
  if (status === "ERROR") {
    return (
      <div
        role="status"
        className="flex items-center gap-2 rounded-md bg-danger-bg/50 px-3 py-2 text-sm font-medium text-danger"
      >
        <AlertCircle size={16} aria-hidden />
        {t("status.ERROR")}
      </div>
    );
  }

  const currentIndex = ORDER.indexOf(status as FlowStepStatus);
  const skipped = new Set(skippedSteps);

  return (
    <ol className="flex flex-wrap items-center gap-y-2">
      {ORDER.map((step, i) => {
        const skip = skipped.has(step);
        const done = !skip && (i < currentIndex || status === "INDEXED");
        const active = !skip && i === currentIndex;
        return (
          <li key={step} className="flex items-center">
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  "flex size-7 items-center justify-center rounded-full text-xs font-semibold",
                  skip
                    ? "border border-border bg-card text-muted"
                    : done
                    ? "bg-success text-white"
                    : active
                      ? "bg-primary text-white"
                      : "bg-border/60 text-muted"
                )}
              >
                {skip ? (
                  <Minus size={14} aria-hidden />
                ) : done ? (
                  <Check size={14} aria-hidden />
                ) : (
                  i + 1
                )}
              </span>
              <span
                className={cn(
                  "flex items-center gap-1.5 text-sm",
                  skip || done || active ? "font-medium text-foreground" : "text-muted"
                )}
              >
                {t(STEP_LABEL_KEY[step])}
                {skip ? (
                  <span className="rounded bg-border/60 px-1.5 py-0.5 text-[11px] font-semibold uppercase text-muted">
                    {t("flow.step.skipped")}
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
