import type { ReactNode } from "react";
import {
  ErrorState as UiErrorState,
  EmptyState,
  TimedLoadingState,
  type ProcessingActivityIcon,
  type ProcessingPlacement,
} from "@production-ready/ui";

import { t } from "@/lib/i18n";

export { EmptyState };

/** 単純な結果領域にも共通の経過時間を表示する loading state。 */
export function LoadingState({
  label,
  operationKey,
  onCancel,
  placement = "panel",
  activityIcon,
  children,
}: {
  label: string;
  /** 行の下に置く内容の形の Skeleton（#2510）。 */
  children?: ReactNode;
  operationKey?: string | number | null;
  onCancel?: () => void;
  placement?: ProcessingPlacement;
  activityIcon?: ProcessingActivityIcon;
}) {
  return (
    <TimedLoadingState
      label={label}
      operationKey={operationKey}
      onCancel={onCancel}
      placement={placement}
      activityIcon={activityIcon}
    >
      {children}
    </TimedLoadingState>
  );
}

/**
 * エラー状態。共通 Button と NL2SQL の i18n で再試行操作を統一する。
 */
export function ErrorState({
  message,
  onRetry,
  retryLabel = t("common.retry"),
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return <UiErrorState message={message} onRetry={onRetry} retryLabel={retryLabel} />;
}
