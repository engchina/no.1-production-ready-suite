import { Bot, Database, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";

import { Banner } from "@engchina/production-ready-ui";

import { Button, buttonVariants } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

import { engineLabel } from "../labels";
import {
  profileSaveProgressPresentation,
  type ProfileSaveProgressStatus,
  type ProfileSaveProgressStepStatus,
} from "../profileSyncPresentation";
import type { AssetRefreshData, ProfileSyncJobData } from "../types";
import { WorkflowProgressStrip, type WorkflowProgressTone } from "./WorkflowProgressStrip";

type StatusBadgeVariant = "neutral" | "success" | "danger" | "warning" | "pending";

function statusVariant(status: ProfileSaveProgressStatus): StatusBadgeVariant {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "submission_failed") return "danger";
  if (status === "cancelled") return "warning";
  return "pending";
}

function progressTone(status: ProfileSaveProgressStatus): WorkflowProgressTone {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "submission_failed") return "danger";
  if (status === "cancelled") return "neutral";
  return "active";
}

function stepStatusLabel(status: ProfileSaveProgressStepStatus) {
  return t(`nl2sql.progress.step.${status}`);
}

function progressMessage(job: ProfileSyncJobData | null, status: ProfileSaveProgressStatus) {
  if (status === "submission_failed") return t("profiles.oracle.progress.message.submissionFailed");
  if (status === "succeeded") return t("profiles.oracle.progress.message.succeeded");
  if (status === "failed") return t("profiles.oracle.progress.message.failed");
  if (status === "cancelled") return t("profiles.oracle.progress.message.cancelled");
  if (status === "queued") return t("profiles.oracle.progress.message.queued");
  return t(`profiles.oracle.sync.phase.${job?.phase ?? "syncing_oracle_profile"}`);
}

function failureMessage(job: ProfileSyncJobData | null, submissionError: string) {
  if (job?.error_code === "SELECT_AI_CREDENTIAL_MISSING") {
    return t("profiles.oracle.sync.credentialMissing");
  }
  const detail = submissionError || job?.error_message_ja || "";
  const summary = submissionError
    ? t("profiles.oracle.sync.savedButFailed")
    : t("profiles.oracle.sync.failed");
  return detail ? `${summary} ${detail}` : summary;
}

function credentialSettingsHref(job: ProfileSyncJobData | null) {
  const returnParams = new URLSearchParams();
  if (job?.profile_id) returnParams.set("profile", job.profile_id);
  if (job?.job_id) returnParams.set("syncJobId", job.job_id);
  const returnTo = `${APP_ROUTES.profiles}?${returnParams.toString()}`;
  const settingsParams = new URLSearchParams({ returnTo });
  return `${APP_ROUTES.settingsDatabase}?${settingsParams.toString()}#select-ai-credential`;
}

function AgentAssetDetails({ result }: { result: AssetRefreshData }) {
  return (
    <section
      className="mt-3 grid min-w-0 gap-3 border-l border-border pl-3"
      aria-label={t("profiles.oracle.assets.lastRefresh")}
      data-testid={`profile-asset-status-${result.engine}`}
    >
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <Bot size={17} className="mt-0.5 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0">
            <p className="break-words font-semibold text-foreground">{engineLabel(result.engine)}</p>
            <p className="mt-1 break-words text-xs text-muted">
              {result.refreshed_at ? new Date(result.refreshed_at).toLocaleString("ja-JP") : "-"}
            </p>
          </div>
        </div>
        <StatusBadge variant={result.refreshed ? "success" : "warning"} label={result.status} />
      </div>
      <dl className="grid min-w-0 gap-2 sm:grid-cols-2">
        {Object.entries(result.asset_names).map(([name, value]) => (
          <div key={name} className="grid min-w-0 gap-1 rounded-md bg-card p-2 text-sm">
            <dt className="break-words text-xs font-medium uppercase text-muted">{name}</dt>
            <dd className="min-w-0 break-all font-mono text-xs leading-5 text-foreground">
              {value}
            </dd>
          </div>
        ))}
      </dl>
      {result.warning ? <Banner severity="warning">{result.warning}</Banner> : null}
    </section>
  );
}

export function ProfileSaveProgress({
  job,
  submissionError,
  rebuildAgentAssets,
  retrying,
  onRetry,
}: {
  job: ProfileSyncJobData | null;
  submissionError: string;
  rebuildAgentAssets: boolean;
  retrying: boolean;
  onRetry: () => void;
}) {
  const presentation = profileSaveProgressPresentation(job, {
    rebuildAgentAssets,
    submissionError,
  });
  if (!presentation) return null;

  const failed = presentation.status === "failed" || presentation.status === "submission_failed";
  const credentialMissing = job?.error_code === "SELECT_AI_CREDENTIAL_MISSING";
  const shortJobId = job
    ? `${job.job_id.slice(0, 12)}${job.job_id.length > 12 ? "…" : ""}`
    : "";

  return (
    <WorkflowProgressStrip
      active={presentation.active}
      operationKey={job?.job_id ?? (submissionError ? "profile-sync-submission-failed" : null)}
      startedAt={job?.started_at || job?.created_at}
      finishedAt={job?.finished_at}
      title={t("profiles.oracle.progress.title")}
      titleId="profile-save-progress-title"
      message={progressMessage(job, presentation.status)}
      statusLabel={t(`profiles.oracle.progress.status.${presentation.status}`)}
      statusVariant={statusVariant(presentation.status)}
      tone={progressTone(presentation.status)}
      stepsAriaLabel={t("profiles.oracle.progress.stepsLabel")}
      testId="profile-save-progress"
      dataJobStatus={presentation.status}
      role={presentation.active ? "status" : undefined}
      meta={
        shortJobId ? (
          <span className="min-w-0 max-w-full break-all font-mono">
            {t("profiles.oracle.progress.jobId", { id: shortJobId })}
          </span>
        ) : undefined
      }
      steps={presentation.steps.map((step) => ({
        id: step.id,
        label: t(`profiles.oracle.progress.${step.id}.label`),
        description: t(`profiles.oracle.progress.${step.id}.description`),
        status: step.status,
        statusLabel: stepStatusLabel(step.status),
        open:
          step.status === "running" ||
          step.status === "error" ||
          (step.id === "rebuild_agent_assets" && Boolean(job?.agent_result)),
        testId: `profile-save-step-${step.id}`,
        dataStatus: step.status,
        content:
          step.id === "rebuild_agent_assets" && job?.agent_result ? (
            <AgentAssetDetails result={job.agent_result} />
          ) : undefined,
      }))}
      footer={
        failed ? (
          <Banner
            severity="danger"
            className="mx-4 mb-4"
            action={
              <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
                {credentialMissing ? (
                  <Link
                    to={credentialSettingsHref(job)}
                    className={`${buttonVariants({ variant: "secondary", size: "sm" })} w-full sm:w-auto`}
                  >
                    <Database size={15} aria-hidden="true" />
                    <span>{t("profiles.oracle.sync.openDatabaseSettings")}</span>
                  </Link>
                ) : null}
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="w-full sm:w-auto"
                  loading={retrying}
                  onClick={onRetry}
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>{t("profiles.oracle.sync.retry")}</span>
                </Button>
              </div>
            }
          >
            {failureMessage(job, submissionError)}
          </Banner>
        ) : undefined
      }
    />
  );
}
