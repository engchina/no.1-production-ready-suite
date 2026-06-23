import { AlertCircle, Check, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import type { FileStatus } from "@/lib/api";

type StepStatus =
  | "UPLOADED"
  | "INGESTING"
  | "REVIEW"
  | "CHUNKING"
  | "CHUNKED"
  | "INDEXING"
  | "INDEXED";
const ORDER: StepStatus[] = [
  "UPLOADED",
  "INGESTING",
  "REVIEW",
  "CHUNKING",
  "CHUNKED",
  "INDEXING",
  "INDEXED",
];
const STEP_LABEL: Record<StepStatus, string> = {
  UPLOADED: "アップロード",
  INGESTING: "抽出",
  REVIEW: "抽出確認",
  CHUNKING: "Chunk",
  CHUNKED: "Chunk 確認",
  INDEXING: "索引中",
  INDEXED: "索引済み",
};

/** ドキュメントの処理段階を可視化する。 */
export function FlowStepper({ status }: { status: FileStatus }) {
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

  const currentIndex = ORDER.indexOf(status as StepStatus);

  return (
    <ol className="flex flex-wrap items-center gap-y-2">
      {ORDER.map((step, i) => {
        const done = i < currentIndex || status === "INDEXED";
        const active = i === currentIndex;
        return (
          <li key={step} className="flex items-center">
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  "flex size-7 items-center justify-center rounded-full text-xs font-semibold",
                  done
                    ? "bg-success text-white"
                    : active
                      ? "bg-primary text-white"
                      : "bg-border/60 text-muted"
                )}
              >
                {done ? <Check size={14} aria-hidden /> : i + 1}
              </span>
              <span
                className={cn(
                  "text-sm",
                  done || active ? "font-medium text-foreground" : "text-muted"
                )}
              >
                {STEP_LABEL[step]}
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
