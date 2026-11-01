import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Construction, RefreshCw, Square, Play } from "lucide-react";
import { Button, DataTable, ListToolbar, OffsetPagination, TableSkeleton, TimedLoadingState, INFORMATION_TABLE_VISIBLE_ROWS, FormActionBar, type DataTableColumn } from "@production-ready/ui";
import { ApiErrorState } from "@/components/StateViews";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { queryKeys } from "@/lib/queries";
import { api, type StructureBuildItem, type StructureBuildMode } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { listCountLabels, listToolbarLabels, paginationLabels } from "@/lib/pagination-labels";

const statusLabels: Record<string, I18nKey> = {
  MISSING: "knowledgeBases.structure.build.status.MISSING",
  STALE: "knowledgeBases.structure.build.status.STALE",
  CURRENT: "knowledgeBases.structure.build.status.CURRENT",
  UNAVAILABLE: "knowledgeBases.structure.build.status.UNAVAILABLE",
  QUEUED: "knowledgeBases.structure.build.status.QUEUED",
  RUNNING: "knowledgeBases.structure.build.status.RUNNING",
  SUCCEEDED: "knowledgeBases.structure.build.status.SUCCEEDED",
  PARTIAL: "knowledgeBases.structure.build.status.PARTIAL",
  FAILED: "knowledgeBases.structure.build.status.FAILED",
  SKIPPED: "knowledgeBases.structure.build.status.SKIPPED",
 };
const reasonLabels: Record<string, I18nKey> = {
  no_chunks: "knowledgeBases.structure.build.reason.no_chunks",
  no_structure: "knowledgeBases.structure.build.reason.no_structure",
  source_changed: "knowledgeBases.structure.build.reason.source_changed",
  disabled: "knowledgeBases.structure.build.reason.disabled",
  membership_changed: "knowledgeBases.structure.build.reason.membership_changed",
  authorization_changed: "knowledgeBases.structure.build.reason.authorization_changed",
  invalid_chunk_id: "knowledgeBases.structure.build.reason.invalid_chunk_id",
  interrupted: "knowledgeBases.structure.build.reason.interrupted",
  processing_error: "knowledgeBases.structure.build.reason.processing_error",
  current: "knowledgeBases.structure.build.reason.current",
 };
const jobLabels: Record<string, I18nKey> = {
  RUNNING: "knowledgeBases.structure.build.job.RUNNING",
  STOPPING: "knowledgeBases.structure.build.job.STOPPING",
  STOPPED: "knowledgeBases.structure.build.job.STOPPED",
  INTERRUPTED: "knowledgeBases.structure.build.job.INTERRUPTED",
  COMPLETED: "knowledgeBases.structure.build.job.COMPLETED",
 };

export function KnowledgeBaseStructureBuild({ kbId, enabled, editable }: { kbId: string; enabled: boolean; editable: boolean }) {
  const [preview, setPreview] = useState(false);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(25);
  const confirm = useConfirm();
  const qc = useQueryClient();
  const planKey = ["structure-build-plan", kbId, offset, limit];
  const jobKey = ["structure-build-job", kbId, offset, limit];
  const plan = useQuery({ queryKey: planKey, queryFn: () => api.getStructureBuildPlan(kbId, offset, limit), enabled: preview });
  const job = useQuery({ queryKey: jobKey, queryFn: () => api.getStructureBuildJob(kbId, offset, limit), refetchInterval: (query) => ["RUNNING", "STOPPING"].includes(query.state.data?.status ?? "") ? 1500 : false });
  const active = ["RUNNING", "STOPPING"].includes(job.data?.status ?? "");
  useEffect(() => {
    if (job.data?.status && !active) {
      void qc.invalidateQueries({ queryKey: queryKeys.knowledgeBaseGraph(kbId) });
      void qc.invalidateQueries({ queryKey: ["structure-build-plan", kbId] });
    }
  }, [job.data?.status, active, kbId, qc]);
  const action = useMutation({ mutationFn: (mode: StructureBuildMode | "stop") => mode === "stop" ? api.stopStructureBuild(kbId) : api.startStructureBuild(kbId, mode), onSuccess: () => { setPreview(false); setOffset(0); void qc.invalidateQueries({ queryKey: ["structure-build-job", kbId] }); } });
  const start = async (mode: StructureBuildMode) => {
    const ok = await confirm({ title: t("knowledgeBases.structure.build.confirmTitle"), description: t("knowledgeBases.structure.build.confirmBody"), confirmLabel: t("knowledgeBases.structure.build.start"), tone: "warning" });
    if (ok) action.mutate(mode);
  };
  const view = preview ? plan.data : job.data?.job_id ? job.data : null;
  const columns: DataTableColumn<StructureBuildItem>[] = [
    { key: "name", header: t("knowledgeBases.structure.build.document"), render: (item) => <span className="break-all">{item.name}</span> },
    { key: "status", className: "whitespace-nowrap", header: t("knowledgeBases.structure.build.status"), render: (item) => t(statusLabels[item.status] ?? "knowledgeBases.structure.build.status.FAILED") },
    { key: "reason", header: t("knowledgeBases.structure.build.reason"), render: (item) => item.reason ? t(reasonLabels[item.reason] ?? "knowledgeBases.structure.build.reason.processing_error") : item.quality === "PARTIAL" || item.result?.quality === "PARTIAL" ? t("knowledgeBases.structure.build.reason.partial") : "—" },
  ];
  return <div className="grid gap-3" data-testid="knowledge-base-structure-build">
    <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.build.help")}</p>
    <div className="flex flex-wrap items-center gap-2">
      {editable ? <Button icon={Construction} variant="secondary" disabled={!enabled || active || action.isPending} onClick={() => { setOffset(0); setPreview(true); }}>{t("knowledgeBases.structure.build.preview")}</Button> : null}
      {editable && active ? <Button icon={Square} variant="secondary" loading={action.isPending} disabled={job.data?.status === "STOPPING"} onClick={() => action.mutate("stop")}>{t("knowledgeBases.structure.build.stop")}</Button> : null}
      {editable && ["STOPPED", "INTERRUPTED"].includes(job.data?.status ?? "") ? <Button icon={Play} variant="secondary" disabled={!enabled || action.isPending} onClick={() => void start("continue")}>{t("knowledgeBases.structure.build.continue")}</Button> : null}
      {editable && !active && (job.data?.counts?.FAILED ?? 0) > 0 ? <Button icon={RefreshCw} variant="secondary" disabled={!enabled || action.isPending} onClick={() => void start("retry")}>{t("knowledgeBases.structure.build.retry")}</Button> : null}
    </div>
    {job.data?.partial_visibility ? <p className="text-sm text-fg-muted">{t("knowledgeBases.structure.build.visibleOnly")}</p> : null}
    {action.isError ? <ApiErrorState error={action.error} fallback={t("knowledgeBases.structure.build.error")} onRetry={() => action.reset()} /> : null}
    {job.isError ? <ApiErrorState error={job.error} fallback={t("knowledgeBases.structure.build.loadError")} onRetry={() => void job.refetch()} /> : null}
    {preview && plan.isError ? <ApiErrorState error={plan.error} fallback={t("knowledgeBases.structure.build.loadError")} onRetry={() => void plan.refetch()} /> : null}
    {(preview ? plan.isPending : job.isPending) ? <TimedLoadingState label={t("knowledgeBases.structure.build.loading")} operationKey={`structure-build-${kbId}`}><TableSkeleton columns={3} /></TimedLoadingState> : view ? <>
      {!preview && active && job.data ? <TimedLoadingState label={t(jobLabels[job.data.status] ?? "knowledgeBases.structure.build.job.RUNNING")} operationKey={job.data.job_id} startedAt={job.data.started_at} /> : null}
      {!preview && !active && job.data ? <p className="text-sm" role="status">{t(jobLabels[job.data.status] ?? "knowledgeBases.structure.build.job.INTERRUPTED")}</p> : null}
      <p className="text-sm" aria-live="polite">{preview && plan.data ? t("knowledgeBases.structure.build.planCounts", { missing: plan.data.counts.MISSING ?? 0, stale: plan.data.counts.STALE ?? 0, current: plan.data.counts.CURRENT ?? 0, unavailable: plan.data.counts.UNAVAILABLE ?? 0 }) : t("knowledgeBases.structure.build.jobCounts", { succeeded: job.data?.counts?.SUCCEEDED ?? 0, partial: job.data?.counts?.PARTIAL ?? 0, failed: job.data?.counts?.FAILED ?? 0, skipped: job.data?.counts?.SKIPPED ?? 0, unavailable: job.data?.counts?.UNAVAILABLE ?? 0, pending: (job.data?.counts?.QUEUED ?? 0) + (job.data?.counts?.RUNNING ?? 0) })}</p>
      <ListToolbar count={{ total: view.total, labels: listCountLabels() }} labels={listToolbarLabels()} />
      <DataTable columns={columns} rows={view.items} getRowKey={(item) => item.document_id} stickyHeader visibleRows={INFORMATION_TABLE_VISIBLE_ROWS} tableClassName="min-w-[42rem]" />
      <OffsetPagination total={view.total} offset={offset} limit={limit} count={view.items.length} onPageChange={(_page, nextOffset) => setOffset(nextOffset)} onPageSizeChange={(value) => { setLimit(value); setOffset(0); }} ariaLabel={t("knowledgeBases.structure.build.preview")} labels={paginationLabels()} />
      {preview && editable ? <FormActionBar ariaLabel={t("knowledgeBases.structure.build.preview")}
        primaryActions={[{ id: "build", icon: Construction, label: t("knowledgeBases.structure.build.start"), loading: action.isPending, disabled: !enabled || active || (plan.data?.counts.MISSING ?? 0) + (plan.data?.counts.STALE ?? 0) === 0, onClick: () => start("missing") }]}
        secondaryActions={[{ id: "rebuild", icon: RefreshCw, label: t("knowledgeBases.structure.build.rebuild"), disabled: !enabled || active || action.isPending || (view.total - (plan.data?.counts.UNAVAILABLE ?? 0)) === 0, onClick: () => start("rebuild") }]}
      /> : null}
    </> : null}
  </div>;
}
