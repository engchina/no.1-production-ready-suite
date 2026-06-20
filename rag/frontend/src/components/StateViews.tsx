import { AlertCircle, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/lib/i18n";

/**
 * 読込状態（汎用 Skeleton）。1 秒超の取得はブロッキングスピナーでなくこれを使う
 * （docs/frontend-messaging-spec.md §3.6 / progressive-loading）。
 * 領域寸法を予約して CLS を防ぐため、行数を rows で指定する。
 */
export function LoadingState({ rows = 3, label }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="flex flex-col gap-2 py-2">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-5 w-full" />
      ))}
    </div>
  );
}

/** エラー状態（再試行ボタン付き）。 */
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-danger/30 bg-danger-bg/40 p-8 text-center"
    >
      <AlertCircle size={24} className="text-danger" aria-hidden />
      <p className="text-sm text-foreground">{message}</p>
      {onRetry ? (
        <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
          <RefreshCw size={14} aria-hidden />
          {t("common.retry")}
        </Button>
      ) : null}
    </div>
  );
}

/** 空状態。任意で操作（作成導線など）を添える。 */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-1 py-10 text-center">
      <Inbox size={22} className="text-muted" aria-hidden />
      <p className="mt-1 text-sm text-foreground">{title}</p>
      {hint ? <p className="max-w-md text-xs leading-relaxed text-muted">{hint}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
