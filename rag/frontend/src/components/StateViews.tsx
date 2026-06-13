import { AlertCircle, Inbox, RefreshCw } from "lucide-react";

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
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-background"
        >
          <RefreshCw size={14} aria-hidden />
          再試行
        </button>
      ) : null}
    </div>
  );
}

/** 空状態。 */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-1 py-10 text-center">
      <Inbox size={22} className="text-muted" aria-hidden />
      <p className="mt-1 text-sm text-foreground">{title}</p>
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}
