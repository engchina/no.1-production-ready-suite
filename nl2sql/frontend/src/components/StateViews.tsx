import {
  LoadingState,
  EmptyState,
  ErrorState as UiErrorState,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

// Loading / Empty はそのまま再公開。
export { LoadingState, EmptyState };

/**
 * エラー状態。共有 UI パッケージの ErrorState に RAG の i18n（再試行ラベル）を注入するラッパ。
 */
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return <UiErrorState message={message} onRetry={onRetry} retryLabel={t("common.retry")} />;
}
