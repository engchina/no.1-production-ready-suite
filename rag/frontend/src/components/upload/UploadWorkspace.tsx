"use client";

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Cloud,
  Clock3,
  Database,
  FileText,
  HardDrive,
  ListChecks,
  Loader2,
  PlayCircle,
  RefreshCw,
  RotateCcw,
  Settings,
  Sparkles,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Dropzone } from "./Dropzone";
import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { KnowledgeBasePickerGrid } from "@/components/knowledge-bases/KnowledgeBasePickerGrid";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/StateViews";
import {
  ApiError,
  type BatchUploadFailedItem,
  type IngestionJob,
  type UploadIngestionMode,
  type UploadResult,
  type UploadStorageSettingsData,
} from "@/lib/api";
import {
  useBatchUploadDocuments,
  useCancelIngestionJob,
  useDrainIngestionJobs,
  useIngestionJobs,
  useKnowledgeBases,
  useRetryIngestionJob,
  useUploadDocument,
  useUploadStorageSettings,
} from "@/lib/queries";
import { t, type I18nKey } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import {
  parserProfileKey,
  sourceModalityKey,
  sourcePreviewKey,
  sourceWarningKey,
  unsupportedReasonLabel,
} from "@/lib/source-profile-labels";
import { cn } from "@/lib/utils";

/** アップロード → 取込 → RAG 索引化を1画面で進めるワークスペース。 */
export function UploadWorkspace() {
  const [uploaded, setUploaded] = useState<UploadResult | null>(null);
  const [batchItems, setBatchItems] = useState<UploadResult[]>([]);
  const [batchFailedItems, setBatchFailedItems] = useState<BatchUploadFailedItem[]>([]);
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState<string[]>([]);
  const [ingestionMode, setIngestionMode] = useState<UploadIngestionMode>("manual");
  const upload = useUploadDocument();
  const batchUpload = useBatchUploadDocuments();
  const isBusy = upload.isPending || batchUpload.isPending;
  const mutationError = upload.error ?? batchUpload.error;

  const reset = () => {
    setUploaded(null);
    setBatchItems([]);
    setBatchFailedItems([]);
    upload.reset();
    batchUpload.reset();
  };

  const handleFiles = (files: File[]) => {
    if (files.length === 0) return;
    setUploaded(null);
    setBatchItems([]);
    setBatchFailedItems([]);
    upload.reset();
    batchUpload.reset();
    if (files.length === 1) {
      upload.mutate(
        { file: files[0], knowledgeBaseIds, ingestionMode },
        {
          onSuccess: (result) => {
            setBatchItems([result]);
            setBatchFailedItems([]);
            setUploaded(result);
          },
        }
      );
      return;
    }
    batchUpload.mutate(
      { files, knowledgeBaseIds, ingestionMode },
      {
        onSuccess: (result) => {
          setBatchItems(result.items);
          setBatchFailedItems(result.failed_items);
          setUploaded(result.items[0] ?? null);
        },
      }
    );
  };

  return (
    <div>
      <PageHeader title={t("nav.upload")} subtitle={t("upload.subtitle")} />
      <div className="space-y-6 p-8">
        {!uploaded ? (
          <>
            <UploadStorageNotice />
            <UploadKnowledgeBasePicker
              selectedIds={knowledgeBaseIds}
              onChange={setKnowledgeBaseIds}
              disabled={isBusy}
            />
            <UploadIngestionOptions
              ingestionMode={ingestionMode}
              onChange={setIngestionMode}
              disabled={isBusy}
            />
            <Dropzone onFiles={handleFiles} disabled={isBusy} />
            {isBusy ? (
              <p className="text-sm text-muted" role="status">
                {t("upload.uploading")}
              </p>
            ) : null}
            {mutationError ? (
              <ErrorState
                message={
                  mutationError instanceof ApiError
                    ? mutationError.message
                    : "アップロードに失敗しました。"
                }
              />
            ) : null}
            {batchFailedItems.length > 0 ? (
              <BatchUploadFailureList failedItems={batchFailedItems} />
            ) : null}
            <RecentIngestionJobsPanel />
          </>
        ) : (
          <>
            {batchItems.length > 1 ? (
              <BatchUploadSummary
                items={batchItems}
                failedItems={batchFailedItems}
                selectedId={uploaded.id}
                onSelect={setUploaded}
              />
            ) : null}
            {batchItems.length <= 1 ? (
              <UploadIngestionJobNotice job={uploaded.ingestion_job} />
            ) : null}
            <DocumentWorkspace
              documentId={uploaded.id}
              watchProcessing={shouldWatchProcessing(uploaded)}
              initialSourceProfile={uploaded.source_profile}
            />
            <Button variant="ghost" onClick={reset}>
              {t("upload.uploadAnother")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function UploadIngestionJobNotice({ job }: { job: IngestionJob | null | undefined }) {
  if (!job) return null;
  return (
    <Banner severity={uploadJobNoticeSeverity(job.status)} title={t("upload.jobs.title")}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <IngestionJobBadge job={job} />
        {job.skip_reason ? (
          <span className="text-muted">{uploadSkipReasonLabel(job.skip_reason)}</span>
        ) : null}
        {job.error_message ? <span className="text-danger">{job.error_message}</span> : null}
      </div>
    </Banner>
  );
}

function uploadJobNoticeSeverity(status: IngestionJob["status"]) {
  switch (status) {
    case "SUCCEEDED":
      return "success";
    case "FAILED":
      return "danger";
    case "SKIPPED":
      return "warning";
    default:
      return "info";
  }
}

function uploadSkipReasonLabel(reason: string): string {
  switch (reason) {
    case "duplicate_content":
      return t("sourceProfile.warning.duplicate");
    default:
      return unsupportedReasonLabel(reason) || t("flow.ingestionSkipped");
  }
}

function shouldWatchProcessing(uploaded: UploadResult): boolean {
  return (
    uploaded.ingestion_started ||
    uploaded.ingestion_job?.status === "QUEUED" ||
    uploaded.ingestion_job?.status === "RUNNING"
  );
}

function BatchUploadSummary({
  items,
  failedItems,
  selectedId,
  onSelect,
}: {
  items: UploadResult[];
  failedItems: BatchUploadFailedItem[];
  selectedId: string;
  onSelect: (item: UploadResult) => void;
}) {
  const queuedCount = items.filter((item) => item.ingestion_job?.status === "QUEUED").length;
  const skippedCount = items.filter((item) => item.ingestion_job?.status === "SKIPPED").length;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ListChecks size={18} className="text-primary" aria-hidden />
          {t("upload.batch.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-4">
          <BatchMetric label={t("upload.batch.total")} value={items.length + failedItems.length} />
          <BatchMetric label={t("upload.batch.queued")} value={queuedCount} />
          <BatchMetric label={t("upload.batch.skipped")} value={skippedCount} />
          <BatchMetric label={t("upload.batch.failed")} value={failedItems.length} />
        </div>
        <div className="bounded-scroll-area divide-y divide-border rounded-md border border-border bg-background">
          {items.map((item) => {
            const selected = item.id === selectedId;
            return (
              <div
                key={item.id}
                className={cn(
                  "flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center sm:justify-between",
                  selected && "bg-info-bg/40"
                )}
              >
                <div className="flex min-w-0 items-start gap-2">
                  <FileText size={16} className="mt-0.5 shrink-0 text-primary" aria-hidden />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground" title={item.file_name}>
                      {item.file_name}
                    </p>
                    <p className="mt-1 text-xs text-muted">
                      {t("sourceProfile.parser")}: {t(parserProfileKey(item.source_profile.parser_profile))}
                    </p>
                  </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  <IngestionJobBadge job={item.ingestion_job} />
                  <Button
                    type="button"
                    variant={selected ? "secondary" : "ghost"}
                    size="sm"
                    onClick={() => onSelect(item)}
                    aria-label={t("upload.batch.open", { name: item.file_name })}
                  >
                    {selected ? t("upload.batch.current") : t("upload.batch.openShort")}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
        {failedItems.length > 0 ? <BatchUploadFailureList failedItems={failedItems} /> : null}
      </CardContent>
    </Card>
  );
}

function BatchUploadFailureList({
  failedItems,
}: {
  failedItems: BatchUploadFailedItem[];
}) {
  return (
    <Banner severity="warning" title={t("upload.batch.failedTitle")}>
      <ul className="bounded-scroll-area space-y-2 pr-1 text-sm">
        {failedItems.map((item) => (
          <li key={`${item.file_name}-${item.status_code}`} className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium">{item.file_name}</span>
              <span className="tnum text-muted">{item.status_code}</span>
              <span>{item.message}</span>
            </div>
            {item.source_profile ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
                <span className="rounded-full border border-border bg-card px-2 py-0.5 text-muted">
                  {t(sourceModalityKey(item.source_profile.modality))}
                </span>
                <span className="rounded-full border border-border bg-card px-2 py-0.5 text-muted">
                  {t("sourceProfile.parser")}:{" "}
                  {t(parserProfileKey(item.source_profile.parser_profile))}
                </span>
                <span className="rounded-full border border-border bg-card px-2 py-0.5 text-muted">
                  {t("sourceProfile.previewKind")}:{" "}
                  {t(sourcePreviewKey(item.source_profile.preview_kind))}
                </span>
                {item.source_profile.quality_warnings.slice(0, 2).map((warning) => (
                  <span
                    key={warning}
                    className="rounded-full border border-warning/30 bg-warning-bg px-2 py-0.5 text-warning"
                  >
                    {t(sourceWarningKey(warning))}
                  </span>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Banner>
  );
}

function BatchMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      <p className="text-xs text-muted">{label}</p>
      <p className="tnum mt-1 text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

function RecentIngestionJobsPanel() {
  const query = useIngestionJobs({ limit: 5, offset: 0 });
  const drain = useDrainIngestionJobs();
  const retry = useRetryIngestionJob();
  const cancel = useCancelIngestionJob();
  const [manualRefreshing, setManualRefreshing] = useState(false);
  const jobs = query.data?.items ?? [];
  if (query.isPending || query.isError || jobs.length === 0) return null;

  const refreshJobs = async () => {
    setManualRefreshing(true);
    try {
      await query.refetch();
    } finally {
      setManualRefreshing(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle className="flex items-center gap-2">
            <Clock3 size={18} className="text-primary" aria-hidden />
            {t("upload.jobs.title")}
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => void refreshJobs()}
              loading={manualRefreshing}
            >
              {!manualRefreshing ? <RefreshCw size={14} aria-hidden /> : null}
              {t("upload.jobs.refresh")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => drain.mutate({ limit: 50 })}
              loading={drain.isPending}
            >
              {!drain.isPending ? <PlayCircle size={14} aria-hidden /> : null}
              {t("upload.jobs.drain")}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {drain.isError ? (
          <Banner severity="danger">
            {drain.error instanceof ApiError
              ? drain.error.message
              : t("upload.jobs.drainFailed")}
          </Banner>
        ) : null}
        {retry.isError ? (
          <Banner severity="danger">
            {retry.error instanceof ApiError
              ? retry.error.message
              : t("upload.jobs.retryFailed")}
          </Banner>
        ) : null}
        {cancel.isError ? (
          <Banner severity="danger">
            {cancel.error instanceof ApiError
              ? cancel.error.message
              : t("upload.jobs.cancelFailed")}
          </Banner>
        ) : null}
        <div className="divide-y divide-border rounded-md border border-border bg-background">
          {jobs.map((job) => (
            <div
              key={job.id}
              className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">
                  {t("upload.jobs.documentId", { id: job.document_id })}
                </p>
                <p className="mt-1 text-xs text-muted">
                  {t("sourceProfile.parser")}: {t(parserProfileKey(job.parser_profile))}
                </p>
                {job.error_message ? (
                  <p className="mt-1 text-xs text-danger">{job.error_message}</p>
                ) : null}
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <IngestionJobBadge job={job} />
                {job.status === "QUEUED" || job.status === "RUNNING" ? (
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() => cancel.mutate({ id: job.id })}
                    loading={cancel.isPending && cancel.variables?.id === job.id}
                  >
                    {!(cancel.isPending && cancel.variables?.id === job.id) ? (
                      <Ban size={14} aria-hidden />
                    ) : null}
                    {t("upload.jobs.cancel")}
                  </Button>
                ) : null}
                {job.status === "FAILED" ? (
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => retry.mutate({ id: job.id })}
                    loading={retry.isPending && retry.variables?.id === job.id}
                  >
                    {!(retry.isPending && retry.variables?.id === job.id) ? (
                      <RotateCcw size={14} aria-hidden />
                    ) : null}
                    {t("upload.jobs.retry")}
                  </Button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function IngestionJobBadge({ job }: { job: IngestionJob | null | undefined }) {
  if (!job) return null;
  const status = job.status;
  const Icon = jobIcon(status);
  return (
    <span
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium",
        jobBadgeClass(status)
      )}
    >
      <Icon size={13} aria-hidden className={status === "RUNNING" ? "animate-spin" : ""} />
      {t(jobStatusKey(status))}
    </span>
  );
}

function jobIcon(status: IngestionJob["status"]) {
  switch (status) {
    case "QUEUED":
      return Clock3;
    case "RUNNING":
      return Loader2;
    case "SUCCEEDED":
      return CheckCircle2;
    case "FAILED":
      return XCircle;
    case "SKIPPED":
      return AlertTriangle;
    case "CANCELLED":
      return Ban;
    default:
      return Clock3;
  }
}

function jobBadgeClass(status: IngestionJob["status"]) {
  switch (status) {
    case "QUEUED":
    case "RUNNING":
      return "border-info/30 bg-info-bg text-info";
    case "SUCCEEDED":
      return "border-success/30 bg-success-bg text-success";
    case "FAILED":
      return "border-danger/30 bg-danger-bg text-danger";
    case "SKIPPED":
      return "border-warning/30 bg-warning-bg text-warning";
    case "CANCELLED":
      return "border-border bg-card text-muted";
    default:
      return "border-border bg-card text-foreground";
  }
}

function jobStatusKey(status: IngestionJob["status"]): I18nKey {
  switch (status) {
    case "QUEUED":
      return "upload.job.status.QUEUED";
    case "RUNNING":
      return "upload.job.status.RUNNING";
    case "SUCCEEDED":
      return "upload.job.status.SUCCEEDED";
    case "FAILED":
      return "upload.job.status.FAILED";
    case "SKIPPED":
      return "upload.job.status.SKIPPED";
    case "CANCELLED":
      return "upload.job.status.CANCELLED";
    default:
      return "upload.job.status.QUEUED";
  }
}

function UploadIngestionOptions({
  ingestionMode,
  onChange,
  disabled,
}: {
  ingestionMode: UploadIngestionMode;
  onChange: (mode: UploadIngestionMode) => void;
  disabled: boolean;
}) {
  const auto = ingestionMode === "auto";
  return (
    <Card>
      <CardContent className="p-4">
        <label className="flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={auto}
            onChange={(event) => onChange(event.target.checked ? "auto" : "manual")}
            disabled={disabled}
            className="mt-1 cursor-pointer accent-[var(--primary)] disabled:cursor-not-allowed"
          />
          <span className="min-w-0">
            <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <Sparkles size={15} className="text-primary" aria-hidden />
              {t("upload.autoIngest.label")}
            </span>
            <span className="mt-1 block text-xs text-muted">
              {auto ? t("upload.autoIngest.enabled") : t("upload.autoIngest.disabled")}
            </span>
          </span>
        </label>
      </CardContent>
    </Card>
  );
}

function UploadKnowledgeBasePicker({
  selectedIds,
  onChange,
  disabled,
}: {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled: boolean;
}) {
  const query = useKnowledgeBases({ status: "ACTIVE", limit: 50, offset: 0 });
  const items = query.data?.items ?? [];

  if (query.isError) {
    return (
      <Banner severity="warning" title={t("upload.knowledgeBases.loadWarning")}>
        <p>{t("upload.knowledgeBases.loadWarningHint")}</p>
      </Banner>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("upload.knowledgeBases.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        {query.isPending ? (
          <p className="text-sm text-muted" role="status">
            {t("upload.knowledgeBases.loading")}
          </p>
        ) : items.length > 0 ? (
          <KnowledgeBasePickerGrid
            items={items}
            selectedIds={selectedIds}
            onChange={onChange}
            disabled={disabled}
            ariaLabel={t("upload.knowledgeBases.aria")}
          />
        ) : (
          <div className="flex flex-col gap-3 rounded-md border border-border bg-background p-4 text-sm text-muted sm:flex-row sm:items-center sm:justify-between">
            <span>{t("upload.knowledgeBases.emptyHint")}</span>
            <Link
              to={APP_ROUTES.knowledgeBases}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-info-bg"
            >
              <Database size={14} aria-hidden />
              {t("upload.knowledgeBases.manage")}
            </Link>
          </div>
        )}
        {items.length > 0 ? (
          <p className="mt-3 text-xs text-muted">
            {selectedIds.length > 0
              ? t("upload.knowledgeBases.selected", { count: selectedIds.length })
              : t("upload.knowledgeBases.defaultHint")}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function UploadStorageNotice() {
  const query = useUploadStorageSettings();

  if (query.isPending || query.isError || !query.data) return null;

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-card px-4 py-3 text-sm text-foreground md:flex-row md:items-center md:justify-between">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
          {query.data.backend === "oci" ? (
            <Cloud size={18} aria-hidden />
          ) : (
            <HardDrive size={18} aria-hidden />
          )}
        </div>
        <div>
          <p className="font-medium">
            {t("upload.storageNotice.title")}: {storageBackendLabel(query.data.backend)}
          </p>
          <p className="mt-1 break-all text-xs text-muted">
            {storageTarget(query.data)}
          </p>
        </div>
      </div>
      <Link
        to={APP_ROUTES.settingsUploadStorage}
        className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-info-bg"
      >
        <Settings size={14} aria-hidden />
        {t("upload.storageNotice.settings")}
      </Link>
    </div>
  );
}

function storageBackendLabel(backend: UploadStorageSettingsData["backend"]): string {
  return backend === "oci"
    ? t("settings.uploadStorage.backend.oci")
    : t("settings.uploadStorage.backend.local");
}

function storageTarget(settings: UploadStorageSettingsData): string {
  if (settings.backend === "oci") {
    return settings.object_storage_namespace && settings.object_storage_bucket
      ? `${settings.object_storage_namespace}/${settings.object_storage_bucket}`
      : t("upload.storageNotice.unset");
  }
  return settings.local_storage_dir || t("upload.storageNotice.unset");
}
