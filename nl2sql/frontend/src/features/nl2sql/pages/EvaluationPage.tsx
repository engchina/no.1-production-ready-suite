import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  Play,
  Upload,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  Banner,
  Button,
  EmptyState,
  PageHeader,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { ErrorState, LoadingState } from "@/components/StateViews";
import { usePageNotice, PageNotice } from "@/components/page-notice";
import { FieldError } from "@/components/ui/field-error";
import { ApiError, apiFetch, apiGet, apiPostForm } from "@/lib/api";
import { t } from "@/lib/i18n";
import { engineLabel } from "../labels";
import {
  qualityEvaluationPollingInterval,
  toggleQualityEvaluationEngine,
  validateQualityEvaluationInput,
  type QualityEvaluationValidationCode,
} from "../qualityEvaluationLogic";
import type {
  Nl2SqlProfile,
  QualityEvaluationCapabilities,
  QualityEvaluationEngine,
  QualityEvaluationEngineSummary,
  QualityEvaluationJobPage,
  QualityEvaluationJobSummary,
  QualityEvaluationResult,
  QualityEvaluationResultPage,
  QualityEvaluationStatus,
  QualityEvaluationVerdict,
} from "../types";

const TERMINAL_STATUSES = new Set<QualityEvaluationStatus>([
  "completed",
  "completed_with_errors",
  "failed",
]);
const ACTIVE_STATUSES = new Set<QualityEvaluationStatus>(["pending", "running"]);
const sectionClass = "min-w-0 rounded-xl border border-border bg-card p-4 shadow-sm lg:p-6";
const controlClass =
  "min-h-11 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60";

type FormErrors = Partial<Record<"profile" | "file" | "engines" | "repeat", string>>;

export function EvaluationPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const currentJobId = searchParams.get("job") ?? "";
  const { notice, showNotice, clearNotice } = usePageNotice();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [profileId, setProfileId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [engines, setEngines] = useState<QualityEvaluationEngine[]>([]);
  const [repeatCount, setRepeatCount] = useState(1);
  const [formErrors, setFormErrors] = useState<FormErrors>({});
  const [jobCursor, setJobCursor] = useState<string | null>(null);
  const [jobCursorHistory, setJobCursorHistory] = useState<Array<string | null>>([]);
  const [resultCursor, setResultCursor] = useState<string | null>(null);
  const [resultCursorHistory, setResultCursorHistory] = useState<Array<string | null>>([]);
  const [downloading, setDownloading] = useState(false);

  const capabilitiesQuery = useQuery({
    queryKey: ["quality-evaluations", "capabilities"],
    queryFn: () =>
      apiGet<QualityEvaluationCapabilities>(
        "/api/nl2sql/quality-evaluations/capabilities"
      ),
  });
  const profilesQuery = useQuery({
    queryKey: ["nl2sql", "profiles", "quality-evaluation"],
    queryFn: () => apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
  });
  const recentJobsQuery = useQuery({
    queryKey: ["quality-evaluations", "jobs", jobCursor],
    queryFn: () =>
      apiGet<QualityEvaluationJobPage>(
        `/api/nl2sql/quality-evaluations?limit=10${
          jobCursor ? `&cursor=${encodeURIComponent(jobCursor)}` : ""
        }`
      ),
  });
  const currentJobQuery = useQuery({
    queryKey: ["quality-evaluations", "job", currentJobId],
    queryFn: () =>
      apiGet<QualityEvaluationJobSummary>(
        `/api/nl2sql/quality-evaluations/${encodeURIComponent(currentJobId)}`
      ),
    enabled: Boolean(currentJobId),
    refetchInterval: (query) => qualityEvaluationPollingInterval(query.state.data?.status),
  });
  const currentJob = currentJobQuery.data ?? null;
  const resultsQuery = useQuery({
    queryKey: ["quality-evaluations", "results", currentJobId, resultCursor],
    queryFn: () =>
      apiGet<QualityEvaluationResultPage>(
        `/api/nl2sql/quality-evaluations/${encodeURIComponent(
          currentJobId
        )}/results?limit=25${
          resultCursor ? `&cursor=${encodeURIComponent(resultCursor)}` : ""
        }`
      ),
    enabled: Boolean(currentJob && TERMINAL_STATUSES.has(currentJob.status)),
  });

  useEffect(() => {
    const profiles = profilesQuery.data ?? [];
    if (!profileId && profiles.length > 0) setProfileId(profiles[0].id);
  }, [profileId, profilesQuery.data]);

  useEffect(() => {
    setResultCursor(null);
    setResultCursorHistory([]);
  }, [currentJobId]);

  useEffect(() => {
    if (currentJob && TERMINAL_STATUSES.has(currentJob.status)) {
      void queryClient.invalidateQueries({ queryKey: ["quality-evaluations", "jobs"] });
    }
  }, [currentJob?.status, currentJob?.job_id, queryClient]);

  const startMutation = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error(t("qualityEvaluation.file.required"));
      const form = new FormData();
      form.append("profile_id", profileId);
      for (const engine of engines) form.append("engines", engine);
      form.append("repeat_count", String(repeatCount));
      form.append("file", file);
      return apiPostForm<QualityEvaluationJobSummary>(
        "/api/nl2sql/quality-evaluations",
        form
      );
    },
    onSuccess: (job) => {
      queryClient.setQueryData(["quality-evaluations", "job", job.job_id], job);
      void queryClient.invalidateQueries({ queryKey: ["quality-evaluations", "jobs"] });
      const next = new URLSearchParams(searchParams);
      next.set("job", job.job_id);
      setSearchParams(next, { replace: true });
      showNotice("success", t("qualityEvaluation.notice.started"));
      toast.success(t("qualityEvaluation.notice.started"));
    },
    onError: (cause) => {
      const message =
        cause instanceof ApiError
          ? cause.messages.join("\n")
          : cause instanceof Error
            ? cause.message
            : t("qualityEvaluation.error.start");
      showNotice("danger", message);
    },
  });

  const capabilities = capabilitiesQuery.data;
  const selectedCapabilities = useMemo(
    () =>
      capabilities?.engines.filter((item) => engines.includes(item.engine)) ?? [],
    [capabilities?.engines, engines]
  );
  const selectedUnavailable = selectedCapabilities.some((item) => !item.available);
  const running = Boolean(currentJob && ACTIVE_STATUSES.has(currentJob.status));
  const pageLoading = capabilitiesQuery.isLoading || profilesQuery.isLoading;
  const pageError = capabilitiesQuery.error || profilesQuery.error;

  const validate = () => {
    const codes = validateQualityEvaluationInput({
      profileId,
      file,
      engines,
      repeatCount,
      maxFileBytes: capabilities?.limits.max_file_bytes ?? 10 * 1024 * 1024,
      capabilities: capabilities?.engines ?? [],
    });
    const next: FormErrors = Object.fromEntries(
      Object.entries(codes).map(([field, code]) => [
        field,
        validationMessage(code as QualityEvaluationValidationCode, capabilities),
      ])
    );
    setFormErrors(next);
    return Object.keys(next).length === 0;
  };

  const startEvaluation = () => {
    clearNotice();
    if (validate()) startMutation.mutate();
  };

  const toggleEngine = (engine: QualityEvaluationEngine) => {
    setEngines((current) => toggleQualityEvaluationEngine(current, engine));
    setFormErrors((current) => ({ ...current, engines: undefined }));
  };

  const openJob = (jobId: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("job", jobId);
    setSearchParams(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const downloadFile = async (path: string, fallbackName: string) => {
    setDownloading(true);
    try {
      const response = await apiFetch(path);
      if (!response.ok) throw new Error(t("qualityEvaluation.error.download"));
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? fallbackName;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success(t("qualityEvaluation.notice.downloaded"));
    } catch (cause) {
      showNotice(
        "danger",
        cause instanceof Error ? cause.message : t("qualityEvaluation.error.download")
      );
    } finally {
      setDownloading(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.evaluation")} subtitle={t("qualityEvaluation.subtitle")} />
      <main className="grid min-w-0 gap-4 p-4 lg:gap-6 lg:p-8">
        <PageNotice notice={notice} onDismiss={clearNotice} />
        {pageError ? (
          <ErrorState
            message={t("qualityEvaluation.error.load")}
            onRetry={() => {
              void capabilitiesQuery.refetch();
              void profilesQuery.refetch();
            }}
          />
        ) : null}

        <section className={sectionClass} aria-labelledby="quality-evaluation-conditions">
          <SectionHeader
            icon={FileSpreadsheet}
            id="quality-evaluation-conditions"
            title={t("qualityEvaluation.conditions.title")}
            description={t("qualityEvaluation.conditions.description")}
          />
          {pageLoading ? (
            <LoadingState label={t("common.loading")} />
          ) : pageError ? null : (
            <div className="mt-5 grid min-w-0 gap-5">
              {capabilities && !capabilities.judge.available ? (
                <Banner
                  severity="warning"
                  title={t("qualityEvaluation.judge.unavailableTitle")}
                >
                  {capabilities?.judge.reason}
                </Banner>
              ) : null}

              <div className="grid min-w-0 gap-4 lg:grid-cols-2">
                <label className="grid min-w-0 gap-1.5 text-sm font-medium text-foreground">
                  <span>{t("qualityEvaluation.profile.label")}</span>
                  <select
                    className={controlClass}
                    value={profileId}
                    onChange={(event) => {
                      setProfileId(event.currentTarget.value);
                      setFormErrors((current) => ({ ...current, profile: undefined }));
                    }}
                    aria-invalid={Boolean(formErrors.profile)}
                    aria-describedby={formErrors.profile ? "quality-profile-error" : undefined}
                    disabled={running}
                  >
                    <option value="">{t("qualityEvaluation.profile.placeholder")}</option>
                    {(profilesQuery.data ?? [])
                      .filter((profile) => !profile.archived)
                      .map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.name}
                        </option>
                      ))}
                  </select>
                  <FieldError id="quality-profile-error" message={formErrors.profile} />
                </label>

                <div className="grid min-w-0 gap-1.5">
                  <span className="text-sm font-medium text-foreground">
                    {t("qualityEvaluation.file.label")}
                  </span>
                  <label
                    className="flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/20 px-4 py-4 text-center outline-none transition hover:border-primary hover:bg-primary/5 focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/40"
                  >
                    <Upload className="size-5 text-primary" aria-hidden="true" />
                    <span className="text-sm font-medium text-foreground">
                      {file
                        ? t("qualityEvaluation.file.selected", { name: file.name })
                        : t("qualityEvaluation.file.drop")}
                    </span>
                    <span className="text-xs text-muted">{t("qualityEvaluation.file.hint")}</span>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                      className="sr-only"
                      disabled={running}
                      aria-invalid={Boolean(formErrors.file)}
                      aria-describedby="quality-file-hint quality-file-error"
                      onChange={(event) => {
                        setFile(event.currentTarget.files?.[0] ?? null);
                        setFormErrors((current) => ({ ...current, file: undefined }));
                      }}
                    />
                  </label>
                  <span id="quality-file-hint" className="sr-only">
                    {t("qualityEvaluation.file.hint")}
                  </span>
                  <FieldError id="quality-file-error" message={formErrors.file} />
                  <div>
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      disabled={downloading}
                      onClick={() =>
                        void downloadFile(
                          "/api/nl2sql/quality-evaluations/template.xlsx",
                          "nl2sql_quality_evaluation_template.xlsx"
                        )
                      }
                    >
                      <Download className="size-4" aria-hidden="true" />
                      {t("qualityEvaluation.template.download")}
                    </Button>
                  </div>
                </div>
              </div>

              <fieldset
                className="grid min-w-0 gap-2"
                disabled={running}
                aria-describedby="quality-engines-hint quality-engines-error"
              >
                <legend className="text-sm font-semibold text-foreground">
                  {t("qualityEvaluation.engines.label")}
                </legend>
                <p id="quality-engines-hint" className="text-xs text-muted">
                  {t("qualityEvaluation.engines.hint")}
                </p>
                <div className="grid min-w-0 gap-3 md:grid-cols-3">
                  {(capabilities?.engines ?? []).map((capability) => {
                    const selected = engines.includes(capability.engine);
                    return (
                      <label
                        key={capability.engine}
                        className={`grid min-w-0 gap-2 rounded-lg border p-4 outline-none transition focus-within:ring-2 focus-within:ring-ring/40 ${
                          capability.available
                            ? selected
                              ? "border-primary bg-primary/5"
                              : "border-border bg-background hover:border-primary/60"
                            : "cursor-not-allowed border-border bg-muted/30 opacity-70"
                        }`}
                      >
                        <span className="flex min-w-0 items-start gap-3">
                          <input
                            type="checkbox"
                            className="mt-0.5 size-4 accent-primary"
                            checked={selected}
                            disabled={!capability.available || running}
                            onChange={() => toggleEngine(capability.engine)}
                          />
                          <span className="min-w-0">
                            <span className="block font-semibold text-foreground">
                              {capability.label}
                            </span>
                            {!capability.available ? (
                              <span className="mt-1 block text-xs leading-5 text-muted">
                                {capability.reason}
                              </span>
                            ) : (
                              <span className="mt-1 block text-xs text-muted">
                                {t("qualityEvaluation.engines.strict")}
                              </span>
                            )}
                          </span>
                        </span>
                        {!capability.available ? (
                          <StatusBadge
                            variant="warning"
                            label={t("qualityEvaluation.engines.unavailable")}
                          />
                        ) : null}
                      </label>
                    );
                  })}
                </div>
                <FieldError id="quality-engines-error" message={formErrors.engines} />
              </fieldset>

              <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(12rem,18rem)_1fr]">
                <label className="grid gap-1.5 text-sm font-medium text-foreground">
                  <span>{t("qualityEvaluation.repeat.label")}</span>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    step={1}
                    inputMode="numeric"
                    className={controlClass}
                    value={repeatCount}
                    disabled={running}
                    aria-invalid={Boolean(formErrors.repeat)}
                    aria-describedby="quality-repeat-hint quality-repeat-error"
                    onChange={(event) => {
                      setRepeatCount(Number(event.currentTarget.value));
                      setFormErrors((current) => ({ ...current, repeat: undefined }));
                    }}
                  />
                  <span id="quality-repeat-hint" className="text-xs font-normal text-muted">
                    {t("qualityEvaluation.repeat.hint")}
                  </span>
                  <FieldError id="quality-repeat-error" message={formErrors.repeat} />
                </label>
                <div className="rounded-lg border border-border bg-muted/20 p-4">
                  <div className="text-sm font-semibold text-foreground">
                    {t("qualityEvaluation.estimate.title")}
                  </div>
                  <p className="mt-1 text-sm text-muted">
                    {currentJob
                      ? t("qualityEvaluation.estimate.confirmed", {
                          generations: currentJob.total_attempts,
                          analyses: currentJob.total_attempts,
                        })
                      : t("qualityEvaluation.estimate.formula", {
                          engines: engines.length,
                          repeats: repeatCount || 0,
                        })}
                  </p>
                </div>
              </div>

              <Banner severity="info">{t("qualityEvaluation.judge.note")}</Banner>
              <div className="flex justify-end">
                <Button
                  type="button"
                  size="md"
                  variant="primary"
                  loading={startMutation.isPending}
                  disabled={
                    running ||
                    startMutation.isPending ||
                    !capabilities?.judge.available ||
                    selectedUnavailable
                  }
                  onClick={startEvaluation}
                >
                  <Play className="size-4" aria-hidden="true" />
                  {t("qualityEvaluation.action.start")}
                </Button>
              </div>
            </div>
          )}
        </section>

        <section className={sectionClass} aria-labelledby="quality-evaluation-progress">
          <SectionHeader
            icon={Play}
            id="quality-evaluation-progress"
            title={t("qualityEvaluation.progress.title")}
            description={t("qualityEvaluation.progress.description")}
          />
          <div className="mt-5">
            {!currentJobId ? (
              <EmptyState
                title={t("qualityEvaluation.progress.emptyTitle")}
                hint={t("qualityEvaluation.progress.emptyHint")}
              />
            ) : currentJobQuery.isLoading ? (
              <LoadingState label={t("common.loading")} />
            ) : currentJobQuery.isError || !currentJob ? (
              <ErrorState
                message={t("qualityEvaluation.error.load")}
                onRetry={() => void currentJobQuery.refetch()}
              />
            ) : (
              <JobProgress job={currentJob} />
            )}
          </div>
        </section>

        <section className={sectionClass} aria-labelledby="quality-evaluation-summary">
          <SectionHeader
            icon={BarChart3}
            id="quality-evaluation-summary"
            title={t("qualityEvaluation.summary.title")}
            description={t("qualityEvaluation.summary.description")}
            action={
              currentJob && TERMINAL_STATUSES.has(currentJob.status) ? (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  loading={downloading}
                  onClick={() =>
                    void downloadFile(
                      `/api/nl2sql/quality-evaluations/${encodeURIComponent(
                        currentJob.job_id
                      )}/results.xlsx`,
                      "nl2sql_quality_evaluation.xlsx"
                    )
                  }
                >
                  <Download className="size-4" aria-hidden="true" />
                  {t("qualityEvaluation.action.download")}
                </Button>
              ) : null
            }
          />
          <div className="mt-5">
            {!currentJob || !TERMINAL_STATUSES.has(currentJob.status) ? (
              <EmptyState
                title={t("qualityEvaluation.summary.waitingTitle")}
                hint={t("qualityEvaluation.summary.waitingHint")}
              />
            ) : currentJob.engine_summaries.length === 0 ? (
              <EmptyState
                title={t("qualityEvaluation.details.emptyTitle")}
                hint={currentJob.error_message || t("qualityEvaluation.details.emptyHint")}
              />
            ) : (
              <div className="grid min-w-0 gap-3 xl:grid-cols-3">
                {currentJob.engine_summaries.map((summary) => (
                  <EngineSummaryCard key={summary.engine} summary={summary} />
                ))}
              </div>
            )}
          </div>
        </section>

        <section className={sectionClass} aria-labelledby="quality-evaluation-details">
          <SectionHeader
            icon={CheckCircle2}
            id="quality-evaluation-details"
            title={t("qualityEvaluation.details.title")}
            description={t("qualityEvaluation.details.description")}
          />
          <div className="mt-5">
            {!currentJob || !TERMINAL_STATUSES.has(currentJob.status) ? (
              <EmptyState
                title={t("qualityEvaluation.details.emptyTitle")}
                hint={t("qualityEvaluation.details.emptyHint")}
              />
            ) : resultsQuery.isLoading ? (
              <LoadingState label={t("common.loading")} />
            ) : resultsQuery.isError ? (
              <ErrorState
                message={t("qualityEvaluation.error.load")}
                onRetry={() => void resultsQuery.refetch()}
              />
            ) : !resultsQuery.data?.items.length ? (
              <EmptyState
                title={t("qualityEvaluation.details.emptyTitle")}
                hint={currentJob.error_message || t("qualityEvaluation.details.emptyHint")}
              />
            ) : (
              <>
                <ResultTable results={resultsQuery.data.items} />
                <Pagination
                  canGoPrevious={resultCursorHistory.length > 0}
                  canGoNext={Boolean(resultsQuery.data.next_cursor)}
                  onPrevious={() => {
                    const history = [...resultCursorHistory];
                    setResultCursor(history.pop() ?? null);
                    setResultCursorHistory(history);
                  }}
                  onNext={() => {
                    setResultCursorHistory((history) => [...history, resultCursor]);
                    setResultCursor(resultsQuery.data?.next_cursor ?? null);
                  }}
                />
              </>
            )}
          </div>
        </section>

        <section className={sectionClass} aria-labelledby="quality-evaluation-recent">
          <SectionHeader
            icon={FileSpreadsheet}
            id="quality-evaluation-recent"
            title={t("qualityEvaluation.recent.title")}
            description={t("qualityEvaluation.recent.description")}
          />
          <div className="mt-5">
            {recentJobsQuery.isLoading ? (
              <LoadingState label={t("common.loading")} />
            ) : recentJobsQuery.isError ? (
              <ErrorState
                message={t("qualityEvaluation.error.load")}
                onRetry={() => void recentJobsQuery.refetch()}
              />
            ) : !recentJobsQuery.data?.items.length ? (
              <EmptyState
                title={t("qualityEvaluation.recent.emptyTitle")}
                hint={t("qualityEvaluation.recent.emptyHint")}
              />
            ) : (
              <>
                <div className="grid min-w-0 gap-2">
                  {recentJobsQuery.data.items.map((job) => (
                    <article
                      key={job.job_id}
                      className={`grid min-w-0 gap-3 rounded-lg border p-4 md:grid-cols-[1fr_auto] md:items-center ${
                        job.job_id === currentJobId
                          ? "border-primary bg-primary/5"
                          : "border-border bg-background"
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold text-foreground">{job.profile_name}</span>
                          <StatusBadge
                            variant={statusVariant(job.status)}
                            label={statusLabel(job.status)}
                          />
                        </div>
                        <p className="mt-1 break-words text-xs text-muted">
                          {formatDate(job.created_at)} ·{" "}
                          {t("qualityEvaluation.recent.meta", {
                            cases: job.case_count,
                            attempts: job.total_attempts,
                          })}
                        </p>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        onClick={() => openJob(job.job_id)}
                      >
                        {t("qualityEvaluation.action.view")}
                      </Button>
                    </article>
                  ))}
                </div>
                <Pagination
                  canGoPrevious={jobCursorHistory.length > 0}
                  canGoNext={Boolean(recentJobsQuery.data.next_cursor)}
                  onPrevious={() => {
                    const history = [...jobCursorHistory];
                    setJobCursor(history.pop() ?? null);
                    setJobCursorHistory(history);
                  }}
                  onNext={() => {
                    setJobCursorHistory((history) => [...history, jobCursor]);
                    setJobCursor(recentJobsQuery.data?.next_cursor ?? null);
                  }}
                />
              </>
            )}
          </div>
        </section>
      </main>
    </>
  );
}

function SectionHeader({
  icon: Icon,
  id,
  title,
  description,
  action,
}: {
  icon: typeof FileSpreadsheet;
  id: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
          <Icon className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <h2 id={id} className="text-base font-semibold text-foreground">
            {title}
          </h2>
          <p className="mt-1 text-sm leading-6 text-muted">{description}</p>
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

function JobProgress({ job }: { job: QualityEvaluationJobSummary }) {
  const percentage = job.total_attempts
    ? Math.round((job.completed_attempts / job.total_attempts) * 100)
    : 0;
  return (
    <div className="grid min-w-0 gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusBadge variant={statusVariant(job.status)} label={statusLabel(job.status)} />
        <span className="text-sm font-semibold tabular-nums text-foreground">
          {t("qualityEvaluation.progress.count", {
            completed: job.completed_attempts,
            total: job.total_attempts,
          })}
        </span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-muted/40"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={job.total_attempts}
        aria-valuenow={job.completed_attempts}
        aria-label={t("qualityEvaluation.progress.title")}
      >
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-300"
          style={{ width: `${percentage}%` }}
        />
      </div>
      <dl className="grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-5">
        <Metric
          label={t("qualityEvaluation.progress.currentCase")}
          value={job.current_case_id || "-"}
        />
        <Metric
          label={t("qualityEvaluation.progress.currentEngine")}
          value={job.current_engine ? engineLabel(job.current_engine) : "-"}
        />
        <Metric
          label={t("qualityEvaluation.progress.currentRepeat")}
          value={job.current_repetition ? `${job.current_repetition} / ${job.repeat_count}` : "-"}
        />
        <Metric
          label={t("qualityEvaluation.progress.success")}
          value={String(job.success_count)}
        />
        <Metric
          label={t("qualityEvaluation.progress.errors")}
          value={String(job.error_count)}
          danger={job.error_count > 0}
        />
      </dl>
      {job.error_message ? (
        <Banner severity="danger" title={statusLabel(job.status)}>
          {job.error_message}
        </Banner>
      ) : null}
    </div>
  );
}

function Metric({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-muted/20 p-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`mt-1 break-words text-sm font-semibold ${danger ? "text-danger" : "text-foreground"}`}>
        {value}
      </dd>
    </div>
  );
}

function EngineSummaryCard({ summary }: { summary: QualityEvaluationEngineSummary }) {
  return (
    <article className="grid min-w-0 gap-4 rounded-lg border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-foreground">{engineLabel(summary.engine)}</h3>
        <StatusBadge
          variant={summary.error_count ? "warning" : "success"}
          label={t("qualityEvaluation.summary.errors", { count: summary.error_count })}
        />
      </div>
      <dl className="grid grid-cols-2 gap-2">
        <Metric
          label={t("qualityEvaluation.summary.successRate")}
          value={formatPercent(summary.generation_success_rate)}
        />
        <Metric
          label={t("qualityEvaluation.summary.consistency")}
          value={formatPercent(summary.normalized_sql_consistency)}
        />
      </dl>
      <div>
        <div className="text-xs font-medium text-muted">
          {t("qualityEvaluation.summary.verdicts")}
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          <VerdictBadge verdict="correct" count={summary.correct} />
          <VerdictBadge verdict="incorrect" count={summary.incorrect} />
          <VerdictBadge verdict="uncertain" count={summary.uncertain} />
          <VerdictBadge verdict="not_analyzed" count={summary.not_analyzed} />
        </div>
      </div>
    </article>
  );
}

function ResultTable({ results }: { results: QualityEvaluationResult[] }) {
  return (
    <>
      <div className="hidden overflow-x-auto rounded-lg border border-border md:block">
        <table className="w-full min-w-[74rem] border-collapse text-left text-sm">
          <thead className="bg-muted/30 text-xs text-muted">
            <tr>
              <th className="px-3 py-3 font-medium">{t("qualityEvaluation.details.case")}</th>
              <th className="px-3 py-3 font-medium">{t("qualityEvaluation.details.engine")}</th>
              <th className="px-3 py-3 font-medium">{t("qualityEvaluation.details.expectedSql")}</th>
              <th className="px-3 py-3 font-medium">{t("qualityEvaluation.details.generatedSql")}</th>
              <th className="px-3 py-3 font-medium">{t("qualityEvaluation.details.judgement")}</th>
              <th className="px-3 py-3 font-medium">{t("qualityEvaluation.details.elapsed")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {results.map((result) => (
              <tr key={result.result_id} className="align-top">
                <td className="max-w-56 px-3 py-3">
                  <div className="font-semibold text-foreground">{result.case_id}</div>
                  <div className="mt-1 break-words text-xs leading-5 text-muted">
                    {result.question}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <div className="font-medium text-foreground">{engineLabel(result.engine)}</div>
                  <div className="mt-1 text-xs text-muted">#{result.repetition_no}</div>
                </td>
                <td className="max-w-72 px-3 py-3">
                  <SqlBlock sql={result.expected_sql} />
                </td>
                <td className="max-w-72 px-3 py-3">
                  <SqlBlock sql={result.generated_sql} error={result.generation_error} />
                </td>
                <td className="max-w-64 px-3 py-3">
                  <ResultJudgement result={result} />
                </td>
                <td className="whitespace-nowrap px-3 py-3 text-muted">
                  {result.total_elapsed_ms} ms
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid min-w-0 gap-3 md:hidden">
        {results.map((result) => (
          <article key={result.result_id} className="grid min-w-0 gap-3 rounded-lg border border-border p-4">
            <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 className="break-words font-semibold text-foreground">{result.case_id}</h3>
                <p className="mt-1 break-words text-xs leading-5 text-muted">{result.question}</p>
              </div>
              <VerdictBadge verdict={result.verdict} />
            </div>
            <div className="text-xs font-medium text-muted">
              {engineLabel(result.engine)} / #{result.repetition_no} / {result.total_elapsed_ms} ms
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-muted">
                {t("qualityEvaluation.details.expectedSql")}
              </div>
              <SqlBlock sql={result.expected_sql} />
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-muted">
                {t("qualityEvaluation.details.generatedSql")}
              </div>
              <SqlBlock sql={result.generated_sql} error={result.generation_error} />
            </div>
            <ResultJudgement result={result} />
          </article>
        ))}
      </div>
    </>
  );
}

function ResultJudgement({ result }: { result: QualityEvaluationResult }) {
  const errors = [result.generation_error, result.judge_error].filter(Boolean);
  return (
    <div className="grid min-w-0 gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge verdict={result.verdict} />
        {result.judge ? (
          <span className="text-xs tabular-nums text-muted">
            {Math.round(result.judge.confidence * 100)}%
          </span>
        ) : null}
        <StatusBadge
          variant={result.deterministic_analysis.is_safe ? "success" : "danger"}
          label={
            result.deterministic_analysis.is_safe
              ? t("qualityEvaluation.details.safe")
              : t("qualityEvaluation.details.unsafe")
          }
        />
      </div>
      {result.judge?.summary ? (
        <p className="break-words text-xs leading-5 text-foreground">{result.judge.summary}</p>
      ) : null}
      {result.judge || errors.length ? (
        <details className="min-w-0 text-xs">
          <summary className="cursor-pointer font-medium text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
            {t("qualityEvaluation.details.analysis")}
          </summary>
          <div className="mt-2 grid min-w-0 gap-2 rounded-md bg-muted/20 p-3 text-muted">
            <AnalysisList
              label={t("qualityEvaluation.details.differences")}
              items={result.judge?.differences ?? []}
            />
            <AnalysisList
              label={t("qualityEvaluation.details.risks")}
              items={[...(result.judge?.risks ?? []), ...result.deterministic_analysis.risk_findings]}
            />
            {result.judge?.correction_suggestion ? (
              <div>
                <div className="font-medium text-foreground">
                  {t("qualityEvaluation.details.suggestion")}
                </div>
                <p className="mt-1 break-words">{result.judge.correction_suggestion}</p>
              </div>
            ) : null}
            <AnalysisList label={t("qualityEvaluation.details.error")} items={errors} danger />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function AnalysisList({
  label,
  items,
  danger = false,
}: {
  label: string;
  items: string[];
  danger?: boolean;
}) {
  if (!items.length) return null;
  return (
    <div>
      <div className={`font-medium ${danger ? "text-danger" : "text-foreground"}`}>{label}</div>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        {items.map((item, index) => (
          <li key={`${index}-${item}`} className="break-words">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SqlBlock({ sql, error }: { sql: string; error?: string }) {
  if (!sql) {
    return (
      <span className={`break-words text-xs ${error ? "text-danger" : "text-muted"}`}>
        {error || "-"}
      </span>
    );
  }
  return (
    <pre className="max-h-32 min-w-0 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted/30 p-2 font-mono text-xs leading-5 text-foreground">
      {sql}
    </pre>
  );
}

function VerdictBadge({
  verdict,
  count,
}: {
  verdict: QualityEvaluationVerdict;
  count?: number;
}) {
  return (
    <StatusBadge
      variant={verdictVariant(verdict)}
      label={`${verdictLabel(verdict)}${count === undefined ? "" : ` ${count}`}`}
    />
  );
}

function Pagination({
  canGoPrevious,
  canGoNext,
  onPrevious,
  onNext,
}: {
  canGoPrevious: boolean;
  canGoNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  if (!canGoPrevious && !canGoNext) return null;
  return (
    <nav
      className="mt-4 flex justify-end gap-2"
      aria-label={t("qualityEvaluation.pagination.label")}
    >
      <Button
        type="button"
        size="sm"
        variant="secondary"
        disabled={!canGoPrevious}
        onClick={onPrevious}
      >
        {t("qualityEvaluation.action.previous")}
      </Button>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        disabled={!canGoNext}
        onClick={onNext}
      >
        {t("qualityEvaluation.action.next")}
      </Button>
    </nav>
  );
}

function statusLabel(status: QualityEvaluationStatus) {
  return t(`qualityEvaluation.status.${status}`);
}

function statusVariant(status: QualityEvaluationStatus) {
  if (status === "completed") return "success" as const;
  if (status === "completed_with_errors") return "warning" as const;
  if (status === "failed") return "danger" as const;
  if (status === "running") return "info" as const;
  return "neutral" as const;
}

function verdictLabel(verdict: QualityEvaluationVerdict) {
  return t(`qualityEvaluation.verdict.${verdict}`);
}

function verdictVariant(verdict: QualityEvaluationVerdict) {
  if (verdict === "correct") return "success" as const;
  if (verdict === "incorrect") return "danger" as const;
  if (verdict === "uncertain") return "warning" as const;
  return "neutral" as const;
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function validationMessage(
  code: QualityEvaluationValidationCode,
  capabilities?: QualityEvaluationCapabilities
) {
  if (code === "profile_required") return t("qualityEvaluation.profile.required");
  if (code === "file_required") return t("qualityEvaluation.file.required");
  if (code === "file_extension") return t("qualityEvaluation.file.invalidExtension");
  if (code === "file_size") {
    return t("qualityEvaluation.file.tooLarge", {
      size: Math.floor((capabilities?.limits.max_file_bytes ?? 10 * 1024 * 1024) / 1024 / 1024),
    });
  }
  if (code === "engine_required") return t("qualityEvaluation.engines.required");
  return t("qualityEvaluation.repeat.invalid");
}
