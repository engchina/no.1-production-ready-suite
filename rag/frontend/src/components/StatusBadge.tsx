import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import type { FileStatus } from "@/lib/api";

export type { FileStatus };

const STATUS_STYLES: Record<FileStatus, string> = {
  UPLOADED: "bg-slate-100 text-slate-700",
  ANALYZING: "bg-amber-100 text-amber-700",
  ANALYZED: "bg-sky-100 text-sky-700",
  REGISTERED: "bg-emerald-100 text-emerald-700",
  ERROR: "bg-red-100 text-red-700",
};

/** ステータスバッジ（ファイル処理状態を色＋日本語で表示）。 */
export function StatusBadge({ status }: { status: FileStatus }) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium",
        STATUS_STYLES[status]
      )}
    >
      {t(`status.${status}`)}
    </span>
  );
}
