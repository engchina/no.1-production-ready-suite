import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Banner, toast } from "@engchina/production-ready-ui";
import { StatusBadge } from "@/components/ui/status-badge";
import { Button } from "@/components/ui/button";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { apiGet } from "@/lib/api";
import { useDatabaseStatus } from "@/lib/queries";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/format";
import { useAuth } from "@/features/security/AuthProvider";

export interface SyntheticRun {
  run_id: string;
  status: "pending" | "running" | "verifying" | "completed" | "partial" | "failed" | "no_data" | "unknown";
  targets: { table_name: string; requested_rows: number; loaded_rows: number | null; status: string; error: string }[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  checked_at: string | null;
  message: string;
  operation_ids: number[];
}
export const runFinished = (run: SyntheticRun) => ["completed", "partial", "failed", "no_data"].includes(run.status);
export const runLabel = (run: SyntheticRun) => t(`syntheticRun.status.${run.status}`);

export function useSyntheticRuns() {
  const { user, hasPermission } = useAuth();
  const db = useDatabaseStatus();
  const key = ["synthetic-runs", user?.user_uuid, db.data?.context_id];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => apiGet<SyntheticRun[]>("/api/nl2sql/synthetic-data/runs", { signal, timeoutMs: 10_000 }),
    enabled: Boolean(user) && (hasPermission("menu.data_management") || hasPermission("menu.sample_data")),
    refetchInterval: (q) => q.state.data?.some((r) => !runFinished(r)) ? 2_000 : 15_000,
    refetchIntervalInBackground: true,
    retry: false,
  });
  return { ...query, key };
}

export function SyntheticRunNotifications() {
  const query = useSyntheticRuns();
  const location = useLocation();
  const observed = useRef(new Map<string, string>());
  const [dismissed, setDismissed] = useState(new Set<string>());
  const scope = JSON.stringify(query.key);
  const previousScope = useRef(scope);
  if (previousScope.current !== scope) { observed.current.clear(); previousScope.current = scope; setDismissed(new Set()); }
  useEffect(() => {
    for (const run of query.data ?? []) {
      const previous = observed.current.get(run.run_id);
      if (previous && previous !== run.status && runFinished(run)) {
        const label = runLabel(run);
        if (run.status === "completed") toast.success(label);
        else toast.info(label);
      }
      observed.current.set(run.run_id, run.status);
    }
  }, [query.data]);
  const latest = query.data?.find((run) => runFinished(run) && !dismissed.has(run.run_id) && run.finished_at && Date.now() - Date.parse(run.finished_at) < 24 * 60 * 60_000);
  if (!latest || !runFinished(latest) || dismissed.has(latest.run_id) || location.pathname === "/data-management") return null;
  return <Banner severity={latest.status === "completed" ? "success" : "warning"}
    action={<div className="flex flex-wrap items-center gap-3"><Link to={`/data-management?synthetic_run=${encodeURIComponent(latest.run_id)}`} onClick={() => setDismissed((old) => new Set(old).add(latest.run_id))}>{t("syntheticRun.viewResult")}</Link><Button size="sm" variant="ghost" onClick={() => setDismissed((old) => new Set(old).add(latest.run_id))}>{t("syntheticRun.dismiss")}</Button></div>}>
    {runLabel(latest)} <span className="break-all">{latest.targets.map((target) => target.table_name).join(", ")}</span>
  </Banner>;
}

export function SyntheticRunPanel({ run, runs, onSelect, error, onRefresh, onViewResults, submitting = false }: {
  run: SyntheticRun | null; runs: SyntheticRun[]; onSelect: (id: string) => void;
  error: boolean; submitting?: boolean; onRefresh: () => void; onViewResults?: () => void;
}) {
  return <section aria-label={t("syntheticRun.title")} className="grid min-w-0 gap-3 rounded-md border border-border bg-background p-4" data-testid="synthetic-run-panel">
    <h3 className="font-semibold">{t("syntheticRun.title")}</h3>
    {submitting && <ProcessingIndicator active label={t("syntheticRun.submitting")} activityIcon="none" placement="action" testId="synthetic-submitting" />}
    {error && <Banner severity="warning">{t("syntheticRun.stale")}{run?.checked_at ? ` ${formatDateTime(run.checked_at)}` : ""}</Banner>}
    {submitting ? null : !run ? !error && <p className="text-sm text-muted-foreground">{t("syntheticRun.notStarted")}</p> : <>
      <div role="status" data-testid="synthetic-run-status">
        <StatusBadge className="max-w-full whitespace-normal text-left" variant={run.status === "completed" ? "success" : run.status === "failed" ? "danger" : error || ["unknown", "partial", "no_data"].includes(run.status) ? "warning" : "pending"} label={runLabel(run)} />
      </div>
      {(run.status !== "unknown" && (!runFinished(run) || run.finished_at)) && <ProcessingIndicator
        active={!runFinished(run)}
        operationKey={run.run_id}
        startedAt={run.created_at}
        finishedAt={run.finished_at}
        label={t(error ? "syntheticRun.elapsedUnverified" : runFinished(run) ? "syntheticRun.durationHint" : `syntheticRun.progress.${run.status}`)}
        finalLabel={t("syntheticRun.durationHint")}
        activityIcon={error ? "none" : "spinner"}
        announceActivity={false}
        announceSlow={false}
        showSlowMessage={!error}
        placement="job"
        testId="synthetic-run-processing"
      />}
      {run.status === "unknown" && <p className="text-sm text-muted-foreground">{t("syntheticRun.unknownHint")}</p>}
      {!runFinished(run) && !error && run.status !== "unknown" && <p className="text-sm text-muted-foreground">{t("syntheticRun.continues")}</p>}
      <div className="grid gap-2">
        {run.targets.map((target) => <div key={target.table_name} className="grid min-w-0 gap-1 rounded border border-border bg-card p-3 text-sm">
          <strong className="break-all">{target.table_name}</strong>
          <span>{t("syntheticRun.count", { requested: target.requested_rows, loaded: target.loaded_rows ?? t("syntheticRun.unverified") })}</span>
          <span>{t("syntheticRun.targetStatus", { status: targetStatusLabel(target.status) })}</span>
          {target.error && <p className="break-words text-danger">{target.error}</p>}
        </div>)}
      </div>
      {run.message && <Banner severity="warning">{run.message}</Banner>}
      {run.finished_at && <p className="text-sm text-muted-foreground">{t("syntheticRun.finishedAt", { time: formatDateTime(run.finished_at) })}</p>}
      <details className="text-sm"><summary className="cursor-pointer">{t("syntheticRun.details")}</summary>
        <p className="break-all">{t("syntheticRun.reference", { id: run.run_id })}</p>
        <p>{t("syntheticRun.oracleReference", { id: run.operation_ids.join(", ") || t("syntheticRun.unverified") })}</p>
      </details>
    </>}
    {runs.length > 0 && <label className="grid gap-1 text-sm">{t("syntheticRun.history")}
      <select disabled={submitting} value={run?.run_id ?? ""} onChange={(e) => onSelect(e.target.value)} className="h-11 min-w-0 rounded-md border border-border bg-card px-3">
        {runs.map((r) => <option key={r.run_id} value={r.run_id}>{formatDateTime(r.created_at)} · {runLabel(r)}</option>)}
      </select>
    </label>}
    <div className="flex flex-wrap gap-2">
      {!submitting && run && runFinished(run) && onViewResults && <Button variant="primary" size="sm" onClick={onViewResults}>{t("syntheticRun.goToResults")}</Button>}
      <Button variant="secondary" size="sm" onClick={onRefresh}>{t("syntheticRun.refresh")}</Button>
    </div>
  </section>;
}
function targetStatusLabel(status: string) {
  const known: Record<string, Parameters<typeof t>[0]> = { pending: "syntheticRun.status.pending", running: "syntheticRun.status.running", completed: "syntheticRun.status.completed", failed: "syntheticRun.status.failed", skipped: "syntheticRun.skipped" };
  return t(known[status] ?? "syntheticRun.status.unknown");
}
