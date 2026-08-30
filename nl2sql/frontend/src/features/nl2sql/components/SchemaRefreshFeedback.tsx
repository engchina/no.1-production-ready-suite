import { PageHeaderStatusBadge } from "@/components/PageHeader";
import { ProcessingIndicator, type ProcessingPlacement } from "@/components/ProcessingState";

import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import {
  schemaRefreshHeaderPresentation,
  schemaRefreshProcessingLabel,
} from "../schemaRefreshPresentation";

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
  className = "rounded-md border border-border bg-background px-3 py-2",
  testId,
}: {
  placement?: ProcessingPlacement;
  className?: string;
  testId?: string;
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
      activityIcon="none"
      announceActivity={false}
      announceSlow={false}
    />
  );
}
