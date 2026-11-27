import {
  ApiErrorState as UiApiErrorState,
  LoadingState,
  EmptyState,
  ErrorState as UiErrorState,
} from "@production-ready/ui";

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

/**
 * 取得の失敗（API の失敗）のエラー状態。要約と次の操作を本文に、技術的な詳細（HTTP ステータス・request ID・
 * 通信断のときのブラウザの英語の文など）を閉じた「詳細」に出す（UX 契約 messaging.md §10.3。#906・#5904）。
 */
export function ApiErrorState({
  error,
  fallback,
  detailsDefaultOpen,
  onRetry,
  retrying,
  testId,
}: {
  error: unknown;
  fallback: string;
  detailsDefaultOpen?: boolean;
  onRetry?: () => void;
  /** 押した再試行の間だけ true（再試行のボタンが loading になる）。 */
  retrying?: boolean;
  testId?: string;
}) {
  return (
    <UiApiErrorState
      error={error}
      fallback={fallback}
      detailsDefaultOpen={detailsDefaultOpen}
      onRetry={onRetry}
      retrying={retrying}
      testId={testId}
      retryLabel={t("common.retry")}
    />
  );
}
