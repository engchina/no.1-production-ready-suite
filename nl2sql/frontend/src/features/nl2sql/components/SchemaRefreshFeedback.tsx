import { PageHeaderStatusBadge } from "@/components/PageHeaderStatusBadge";

import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  schemaRefreshHeaderPresentation,
  schemaRefreshProcessingLabel,
} from "../schemaRefreshPresentation";
import {
  ProcessingIndicator,
  type ProcessingActivityIcon,
  type ProcessingPlacement,
} from "@production-ready/ui";

export function SchemaRefreshHeaderStatus({ testId }: { testId?: string }) {
  const { error, isStarting, job } = useSchemaRefreshCoordinator();
  const presentation = schemaRefreshHeaderPresentation(job, {
    starting: isStarting,
    error: Boolean(error),
  });
  if (!presentation) return null;
  return (
    <PageHeaderStatusBadge
      variant={presentation.variant}
      label={presentation.label}
      announcementLabel={presentation.announcementLabel}
      testId={testId}
    />
  );
}

export function SchemaRefreshProcessing({
  placement = "workspace",
  className = "rounded-md border border-border bg-surface-sunken px-3 py-2",
  testId,
  activityIcon,
}: {
  placement?: ProcessingPlacement;
  className?: string;
  testId?: string;
  /**
   * 省略時は、押した「スキーマを更新」が送信中（`loading`）の間だけ "none"、job の間は "spinner"。
   * スキーマの更新は画面をまたいで続く durable job なので、job の間のスピナーはこの進行の表示が 1 つだけ
   * 出し、ボタンは `disabled` にするだけ（UX 契約 messaging §3.7「進捗の表示に残す」、#821）。
   */
  activityIcon?: ProcessingActivityIcon;
}) {
  const { isRefreshing, job, startingOrigin } = useSchemaRefreshCoordinator();
  if (!isRefreshing) return null;
  return (
    <ProcessingIndicator
      active
      label={schemaRefreshProcessingLabel(job)}
      operationKey={job?.job_id || "schema-refresh-starting"}
      startedAt={job?.started_at ?? job?.created_at}
      placement={placement}
      className={className}
      testId={testId}
      activityIcon={activityIcon ?? (startingOrigin ? "none" : "spinner")}
      announceActivity={false}
      announceSlow={false}
    />
  );
}
