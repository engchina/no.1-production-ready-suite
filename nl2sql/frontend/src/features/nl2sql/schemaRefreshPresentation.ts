import { t } from "../../lib/i18n";

import type { SchemaRefreshJob } from "./types";

export function schemaRefreshJobIsActive(job: SchemaRefreshJob | null | undefined) {
  return job?.status === "pending" || job?.status === "running";
}

function schemaRefreshPhase(job: SchemaRefreshJob | null, starting: boolean, failed: boolean) {
  if (failed || job?.status === "error") return "error" as const;
  if (starting && !job) return "starting" as const;
  if (!job) return "queued" as const;
  if (job.status === "pending") return job.phase ?? "queued";
  if (job.status === "done") return "done" as const;
  return job.phase ?? "scanning";
}

export interface SchemaRefreshHeaderPresentation {
  label: string;
  announcementLabel: string;
  variant: "info" | "danger";
}

export function schemaRefreshHeaderPresentation(
  job: SchemaRefreshJob | null,
  options: { starting?: boolean; error?: boolean } = {},
): SchemaRefreshHeaderPresentation | null {
  const starting = options.starting ?? false;
  const failed = options.error ?? false;
  if (!starting && !failed && (!job || job.status === "done")) return null;

  const phase = schemaRefreshPhase(job, starting, failed);
  const phaseLabel = t(`common.schemaRefresh.phase.${phase}`);
  const progress =
    !failed && job && (job.total_objects ?? 0) > 0
      ? ` ${job.processed_objects ?? 0}/${job.total_objects}`
      : "";
  const key =
    job?.mode === "targeted"
      ? "common.schemaRefresh.status.targeted"
      : "common.schemaRefresh.status.full";

  return {
    label: t(key, { phase: phaseLabel, progress }),
    announcementLabel: t(key, { phase: phaseLabel, progress: "" }),
    variant: failed || job?.status === "error" ? "danger" : "info",
  };
}

export function schemaRefreshProcessingLabel(job: SchemaRefreshJob | null) {
  return job?.mode === "targeted"
    ? t("common.processing.schemaDeltaSyncing")
    : t("common.processing.schemaRefreshing");
}

export function schemaRefreshJobErrorMessage(job: SchemaRefreshJob | null) {
  if (!job?.error_code) return t("dataMgmt.schemaJob.error");
  return `${t("dataMgmt.schemaJob.error")} (${job.error_code})`;
}
