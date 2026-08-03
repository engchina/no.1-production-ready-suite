import {
  EmptyState,
  ErrorState as UiErrorState,
} from "@engchina/production-ready-ui";

import {
  TimedLoadingState,
  type ProcessingPlacement,
} from "@/components/ProcessingState";
import { t } from "@/lib/i18n";

export { EmptyState };

/** 単純な結果領域にも共通の経過時間を表示する loading state。 */
export function LoadingState({
  label,
  operationKey,
  onCancel,
  placement = "panel",
}: {
  label: string;
  operationKey?: string | number | null;
  onCancel?: () => void;
  placement?: ProcessingPlacement;
}) {
  return (
    <TimedLoadingState
      label={label}
      operationKey={operationKey}
      onCancel={onCancel}
      placement={placement}
      framed={false}
    />
  );
}

/**
 * エラー状態。共有 UI パッケージの ErrorState に NL2SQL の i18n（再試行ラベル）を注入するラッパ。
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
