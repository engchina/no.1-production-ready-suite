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
} from "@engchina/production-ready-ui";

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
  activityIcon = "none",
}: {
  placement?: ProcessingPlacement;
  className?: string;
  testId?: string;
  /**
   * 既定は "none"（更新を始めたボタンの loading がスピナーを出す）。更新を始めたボタンがこの画面で
   * 回っていないとき（別の画面で始めた更新など）は "spinner" を渡す（#819）。
   */
  activityIcon?: ProcessingActivityIcon;
}) {
  const { isRefreshing, job } = useSchemaRefreshCoordinator();
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
      activityIcon={activityIcon}
      announceActivity={false}
      announceSlow={false}
    />
  );
}
