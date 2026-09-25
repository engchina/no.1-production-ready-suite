import { AlertCircle, RefreshCw } from "lucide-react";
import {
  Button,
  EmptyState,
  MessageText,
} from "@engchina/production-ready-ui";

import { TimedLoadingState, type ProcessingActivityIcon, type ProcessingPlacement } from "@/components/ProcessingState";
import { t } from "@/lib/i18n";

export { EmptyState };

/** 単純な結果領域にも共通の経過時間を表示する loading state。 */
export function LoadingState({
  label,
  operationKey,
  onCancel,
  placement = "panel",
  activityIcon,
}: {
  label: string;
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
      framed={false}
    />
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
        <Button type="button" variant="secondary" size="sm" onClick={onRetry} icon={RefreshCw}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  );
}
