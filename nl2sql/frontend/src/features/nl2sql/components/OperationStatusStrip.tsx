import { CheckCircle2, Loader2, TriangleAlert } from "lucide-react";

import { StatusBadge } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { formatElapsed } from "../useOperationTimer";
import type { JobData, JobStatus } from "../types";

function statusLabel(status: JobStatus) {
  if (status === "done") return t("nl2sql.status.done");
  if (status === "error") return t("nl2sql.status.error");
  if (status === "running") return t("nl2sql.status.running");
  return t("nl2sql.status.pending");
}

export function OperationStatusStrip({
  job,
  elapsedSeconds,
}: {
  job: JobData | null;
  elapsedSeconds: number;
}) {
  if (!job) return null;

  const active = job.status === "pending" || job.status === "running";
  const variant = job.status === "done" ? "success" : job.status === "error" ? "danger" : "pending";
  const finalElapsed = job.elapsed_ms ?? job.result?.timing.elapsed_ms ?? job.timing?.elapsed_ms;

  return (
    <div
      className="flex flex-wrap items-center gap-3 rounded-md border border-slate-200 bg-white px-4 py-3 text-sm shadow-sm"
      role={active ? "status" : undefined}
      aria-live={active ? "polite" : undefined}
    >
      {active ? (
        <Loader2 size={16} className="animate-spin text-sky-700" aria-hidden="true" />
      ) : job.status === "done" ? (
        <CheckCircle2 size={16} className="text-emerald-700" aria-hidden="true" />
      ) : (
        <TriangleAlert size={16} className="text-red-700" aria-hidden="true" />
      )}
      <StatusBadge variant={variant} label={statusLabel(job.status)} />
      <span className="font-mono text-xs text-slate-600">
        {t("nl2sql.status.jobId", { id: `${job.job_id.slice(0, 8)}...` })}
      </span>
      {active ? (
        <span className="font-mono text-slate-800">
          {t("nl2sql.status.elapsed", { seconds: elapsedSeconds })}
        </span>
      ) : (
        <span className="font-mono text-slate-800">
          {t("nl2sql.status.elapsedFinal", { elapsed: formatElapsed(finalElapsed) })}
        </span>
      )}
      {job.error_message && <span className="text-red-700">{job.error_message}</span>}
    </div>
  );
}
