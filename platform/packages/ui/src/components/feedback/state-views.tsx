import { AlertCircle, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "../ui/button";
import { MessageText } from "../ui/message-text";
import { Skeleton } from "../ui/skeleton";

/**
 * 読込状態（汎用 Skeleton）。1 秒超の取得はブロッキングスピナーでなくこれを使う
 * （progressive-loading）。領域寸法を予約して CLS を防ぐため、行数を rows で指定する。
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

/**
 * エラー状態（再試行ボタン付き）。
 * 再試行ラベルは i18n をパッケージに持ち込まないため `retryLabel` で注入（既定「再試行」）。
 */
export function ErrorState({
  message,
  onRetry,
  retryLabel = "再試行",
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-lg border border-danger-border bg-danger-subtle p-8 text-center"
    >
      <AlertCircle size={24} className="text-danger-fg" aria-hidden />
      <p className="text-sm leading-relaxed text-fg">
        <MessageText text={message} />
      </p>
      {onRetry ? (
        <Button type="button" variant="secondary" size="sm" icon={RefreshCw} onClick={onRetry}>
          <span>{retryLabel}</span>
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
      <Inbox size={24} className="text-fg-muted" aria-hidden />
      <p className="mt-1 text-sm leading-relaxed text-fg">
        <MessageText text={title} />
      </p>
      {hint ? (
        <p className="max-w-md text-xs leading-relaxed text-fg-muted">
          <MessageText text={hint} />
        </p>
      ) : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
