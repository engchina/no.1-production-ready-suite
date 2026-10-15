import {
  PageBody,
  PageHeader,
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  FieldError,
  FormGrid,
  ProcessingIndicator,
  Skeleton,
  StatusBadge,
  TimedLoadingState,
  useConfirm,
  ListToolbar,
  PagedDataTable,
  PAGE_SIZE_OPTIONS,
} from "@production-ready/ui";
import { BarChart3, CheckCircle2, ClipboardCheck, Settings2, XCircle } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { EmptyState } from "@/components/StateViews";
import { KnowledgeBaseScopePicker } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import {
  api,
  ApiError,
  type EvaluationAnswerJudgement,
  type EvaluationJob,
  type EvaluationJobStatus,
  type EvaluationCaseResult,
  type EvaluationCategorySummary,
  type EvaluationImportResult,
  type EvaluationMetrics,
} from "@/lib/api";
import { saveDownloadedFile } from "@/lib/download";
import { t } from "@/lib/i18n";
import { useWorkspaceState } from "@/lib/workspace-state";
import {
  useCancelEvaluationJob,
  useEvaluationJob,
  useEvaluationSettings,
  useImportEvaluationCases,
  useSubmitRunEvaluationJob,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";

import {
  evaluationCaseErrorSummary,
  isEvaluationJobActive,
  isEvaluationJobId,
} from "./evaluation-job";
import {
  buildEvaluationCaseLabels,
  evaluationCaseRowLabel,
  evaluationEvidenceSummary,
  sameIdSet,
  type EvaluationCaseLabel,
  type EvaluationCaseLabels,
} from "./evaluation-input";
import {
  EVALUATION_PERSPECTIVES,
  isPerspectiveShown,
  outcomeLabel,
  STANDARD_ANSWER_METRICS,
  failureReasonLabel,
  formatMetricValue,
  metricCaseCount,
  metricDescription,
  metricLabel,
  metricValue,
  orderedThresholdEntries,
  perspectiveDescription,
  perspectiveLabel,
  suiteLabel,
} from "./evaluation-metrics";
import {
  EvaluationJobError,
  EvaluationJobLoading,
  EvaluationJobPanel,
  EvaluationResultSkeleton,
} from "./EvaluationJobPanel";
import {
  EVALUATION_UPLOAD_ID,
  EvaluationRequestUpload,
  evaluationUploadSelectionError,
} from "./EvaluationRequestUpload";

const KNOWLEDGE_BASE_ERROR_ID = "evaluation-knowledge-base-error";
/** アップロードの上限（backend の `EVALUATION_EXCEL_MAX_ROWS` / `_BYTES`。検証の前の案内に出す）。 */
const EVALUATION_EXCEL_MAX_ROWS = 200;
const EVALUATION_EXCEL_MAX_MEGABYTES = 10;

/**
 * 品質評価（改善・運用 › 品質評価。#2065）。評価するナレッジベース（必須）と評価リクエストの Excel
 * （テンプレートのダウンロード・アップロード・検証）を条件に評価を実行し、評価の基準は設定の既定を
 * 表示だけする。評価は job で動き（#390）、結果は Excel の行（質問・期待する根拠）に合わせて出す。
 */
export function EvaluationClient() {
  const runMutation = useSubmitRunEvaluationJob();
  const importMutation = useImportEvaluationCases();
  const settingsQuery = useEvaluationSettings();
  // 評価は job で動く（#390）。job id だけを作業状態に残し、画面に戻ったらサーバーの状態
  // （進捗・結果）を確かめる（workspace-state.md）。戻っただけで評価を送り直さない。
  const runJob = useEvaluationJobState();
  // 評価の条件（KB スコープ・検証した Excel）は、ページを行き来しても再読込しても残す
  // （workspace-state.md）。選んだファイルそのものはメモリだけに持つ（KB を変えたら検証し直す）。
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useWorkspaceState<string[]>(
    "evaluation.knowledgeBaseIds",
    [],
    isStringArray
  );
  const [importResult, setImportResult] = useWorkspaceState<EvaluationImportResult | null>(
    "evaluation.import",
    null,
    isImportResult
  );
  // 実行した評価のケースの見出し（結果の表の質問・期待する根拠）。Excel を差し替えても結果と合う。
  const [runCaseLabels, setRunCaseLabels] = useWorkspaceState<EvaluationCaseLabels>(
    "evaluation.runCaseLabels",
    {},
    isCaseLabels
  );
  // 選んだファイル（KB を変えたときに検証し直す）。再読込の後は無い（検証の結果だけが残る）。
  const [file, setFile] = useState<File | null>(null);
  const importSequence = useRef(0);
  const [fileName, setFileName] = useState<string | null>(importResult?.file_name ?? null);
  const [knowledgeBaseError, setKnowledgeBaseError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [runError, setRunError] = useState("");
  const [templateDownloading, setTemplateDownloading] = useState(false);
  const [templateError, setTemplateError] = useState<string | null>(null);

  const runActive = runMutation.isPending || isEvaluationJobActive(runJob.job);
  const validating = importMutation.isPending;
  // KB を変えた後の検証の結果は、期待する根拠のファイル名の照合が変わるため使えない。
  const importStale =
    importResult !== null && !sameIdSet(importResult.knowledge_base_ids, knowledgeBaseIds);
  const shownResult = importStale ? null : importResult;
  const uploadNotice = validating
    ? null
    : fileName && knowledgeBaseIds.length === 0
      ? t("evaluation.import.needsKnowledgeBase")
      : importStale && !file
        ? t("evaluation.import.staleKnowledgeBase")
        : null;

  const validate = (target: File, ids: string[]) => {
    importSequence.current += 1;
    const sequence = importSequence.current;
    setUploadError(null);
    setRunError("");
    importMutation.mutate(
      { file: target, knowledgeBaseIds: ids },
      {
        // 検証中に別のファイル・KB に変えたときは、前の結果を捨てる。
        onSuccess: (result) => {
          if (sequence === importSequence.current) setImportResult(result);
        },
        onError: (error) => {
          if (sequence !== importSequence.current) return;
          setImportResult(null);
          setUploadError(error instanceof ApiError ? error.message : t("evaluation.import.error"));
        },
      }
    );
  };

  const selectFiles = (files: File[]) => {
    const selectionError = evaluationUploadSelectionError(files);
    if (selectionError) {
      setUploadError(selectionError);
      return;
    }
    const [selected] = files;
    if (!selected) return;
    setFile(selected);
    setFileName(selected.name);
    setImportResult(null);
    if (knowledgeBaseIds.length > 0) validate(selected, knowledgeBaseIds);
    else setUploadError(null);
  };

  const changeKnowledgeBases = (ids: string[]) => {
    setKnowledgeBaseIds(ids);
    setKnowledgeBaseError(null);
    if (file && ids.length > 0) validate(file, ids);
  };

  const downloadTemplate = async () => {
    setTemplateError(null);
    setTemplateDownloading(true);
    try {
      saveDownloadedFile(await api.downloadEvaluationTemplate());
      toast.success(t("evaluation.import.templateDone"));
    } catch (error) {
      setTemplateError(
        error instanceof ApiError ? error.message : t("evaluation.import.templateError")
      );
    } finally {
      setTemplateDownloading(false);
    }
  };

  const runEvaluation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (runActive || validating) return;
    // 未選択・未検証・誤りは、押したときに欄の直下へ出し、画面の並び順で最初の欄へ移す（#541）。
    const nextKnowledgeBaseError = knowledgeBaseIds.length
      ? null
      : t("evaluation.knowledgeBaseScope.required");
    const nextUploadError = !fileName
      ? t("evaluation.import.required")
      : !shownResult
        ? importStale && !file
          ? t("evaluation.import.staleKnowledgeBase")
          : knowledgeBaseIds.length
            ? (uploadError ?? t("evaluation.import.required"))
            : null
        : shownResult.error_count > 0
          ? t("evaluation.import.fixErrors")
          : null;
    setKnowledgeBaseError(nextKnowledgeBaseError);
    setUploadError(nextUploadError);
    if (nextKnowledgeBaseError) {
      document
        .querySelector<HTMLElement>('[data-testid="evaluation-knowledge-base"] input')
        ?.focus();
      return;
    }
    if (nextUploadError || !shownResult) {
      document.getElementById(EVALUATION_UPLOAD_ID)?.focus();
      return;
    }
    setRunError("");
    runMutation.reset();
    try {
      const job = await runMutation.mutateAsync({
        cases: shownResult.cases,
        knowledge_base_ids: knowledgeBaseIds,
      });
      setRunCaseLabels(buildEvaluationCaseLabels(shownResult));
      runJob.start(job);
      toast.info(t("evaluation.toast.started"));
    } catch (error) {
      setRunError(error instanceof ApiError ? error.message : t("evaluation.error.run"));
    }
  };

  return (
    <div>
      <PageHeader wide title={t("nav.evaluation")} subtitle={t("evaluation.subtitle")} />
      <PageBody wide>
        <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_26rem]">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ClipboardCheck size={16} className="text-accent-fg" aria-hidden />
                {t("evaluation.conditions.title")}
              </CardTitle>
              <CardDescription>{t("evaluation.conditions.description")}</CardDescription>
            </CardHeader>
            <CardContent>
              <form
                className="grid min-w-0 gap-5"
                aria-label={t("evaluation.conditions.title")}
                noValidate
                onSubmit={(event) => void runEvaluation(event)}
              >
                {/* ナレッジベースの選択は FormGrid の外に置く（FormGrid は container の箱で、開いた候補の一覧が
                    後ろのカードの下に隠れるため）。幅は部品の width（lg）で決まる。 */}
                <div className="min-w-0" data-testid="evaluation-knowledge-base">
                  <KnowledgeBaseScopePicker
                    selectedIds={knowledgeBaseIds}
                    onChange={changeKnowledgeBases}
                    disabled={runMutation.isPending}
                    helper={t("evaluation.knowledgeBaseScope.helper")}
                    emptySelectionText={t("evaluation.knowledgeBaseScope.emptySelection")}
                    required
                    errorId={knowledgeBaseError ? KNOWLEDGE_BASE_ERROR_ID : undefined}
                    // 対象の選択（ナレッジベース）は lg（#1891）。
                    width="lg"
                  />
                  <FieldError
                    id={KNOWLEDGE_BASE_ERROR_ID}
                    message={knowledgeBaseError}
                    className="mt-1"
                  />
                </div>
                <FormGrid columns={1}>
                  <EvaluationRequestUpload
                    fileName={fileName}
                    result={shownResult}
                    validating={validating}
                    disabled={runMutation.isPending}
                    error={uploadError}
                    notice={uploadNotice}
                    maxRows={EVALUATION_EXCEL_MAX_ROWS}
                    maxMegabytes={EVALUATION_EXCEL_MAX_MEGABYTES}
                    templateDownloading={templateDownloading}
                    templateError={templateError}
                    onFiles={selectFiles}
                    onDownloadTemplate={() => void downloadTemplate()}
                  />
                </FormGrid>
                <div className="grid min-w-0 gap-3">
                  <div>
                    <Button
                      type="submit"
                      loading={runMutation.isPending}
                      disabled={runActive || validating}
                      icon={BarChart3}
                    >
                      {t("evaluation.actions.run")}
                    </Button>
                  </div>
                  {/* 投入の失敗は操作の行の直下に出す（messaging.md §10）。 */}
                  {runError ? <Banner severity="danger">{runError}</Banner> : null}
                  {runMutation.isPending ? (
                    // job の投入（検証と作成）の間。ボタンの loading がスピナーを担うので、ここは文言と
                    // 経過時間だけを出す（messaging.md §3.7）。作成後は実行状況（placement="job"）が引き継ぐ。
                    <ProcessingIndicator
                      active
                      label={t("evaluation.actions.starting")}
                      operationKey="evaluation-run"
                      placement="action"
                      activityIcon="none"
                      testId="evaluation-run-processing"
                    />
                  ) : null}
                </div>
              </form>
            </CardContent>
          </Card>

          <SuiteSummary query={settingsQuery} />
        </div>

        {runJob.jobId ? (
          <EvaluationJobSection
            state={runJob}
            renderResult={(job) =>
              job.run_result ? (
                <EvaluationResult metrics={job.run_result} caseLabels={runCaseLabels} />
              ) : null
            }
          />
        ) : runMutation.isPending ? (
          <EvaluationResultSkeleton />
        ) : (
          <Card>
            <CardContent className="pt-5">
              <EmptyState
                title={t("evaluation.result.empty")}
                hint={t("evaluation.result.emptyHint")}
              />
            </CardContent>
          </Card>
        )}
      </PageBody>
    </div>
  );
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 作業状態に残した検証の結果の形（古い版・壊れた値は捨てる）。 */
function isImportResult(value: unknown): value is EvaluationImportResult | null {
  if (value === null) return true;
  return (
    isRecord(value) &&
    typeof value.file_name === "string" &&
    typeof value.error_count === "number" &&
    Array.isArray(value.cases) &&
    Array.isArray(value.rows) &&
    Array.isArray(value.errors) &&
    isStringArray(value.knowledge_base_ids) &&
    isRecord(value.document_names)
  );
}

function isCaseLabels(value: unknown): value is EvaluationCaseLabels {
  return (
    isRecord(value) &&
    Object.values(value).every(
      (label) => isRecord(label) && typeof label.question === "string" && Array.isArray(label.evidence)
    )
  );
}

interface EvaluationJobState {
  jobId: string;
  job: EvaluationJob | null;
  query: ReturnType<typeof useEvaluationJob>;
  start: (job: EvaluationJob) => void;
  cancel: (job: EvaluationJob) => Promise<void>;
  cancelling: boolean;
}

/**
 * 評価の job の状態。job id を作業状態に残し、実行中は状態を取得し続ける。
 * 実行中から終わりへ移ったときだけ、完了・失敗を 1 回通知する（messaging.md §4.2）。
 */
function useEvaluationJobState(): EvaluationJobState {
  const [jobId, setJobId] = useWorkspaceState<string>("evaluation.runJobId", "", isEvaluationJobId);
  const query = useEvaluationJob(jobId || null);
  const cancelMutation = useCancelEvaluationJob();
  const confirm = useConfirm();
  const job = query.data ?? null;
  const observed = useRef<{ jobId: string; status: EvaluationJobStatus } | null>(null);

  useEffect(() => {
    if (!job) return;
    const previous = observed.current;
    observed.current = { jobId: job.job_id, status: job.status };
    if (previous?.jobId !== job.job_id || previous.status !== "RUNNING") return;
    if (job.status === "SUCCEEDED") {
      toast.success(t("evaluation.toast.succeeded"));
    } else if (job.status === "FAILED") {
      toast.error(t("evaluation.toast.failed"));
    }
  }, [job]);

  const cancel = async (target: EvaluationJob) => {
    const confirmed = await confirm({
      title: t("evaluation.job.cancelConfirm.title"),
      description: t("evaluation.job.cancelConfirm.description"),
      confirmLabel: t("evaluation.job.cancelConfirm.confirm"),
      tone: "danger",
    });
    if (!confirmed) return;
    cancelMutation.mutate(target.job_id, {
      onSuccess: () => toast.success(t("evaluation.toast.cancelled")),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t("evaluation.error.cancel")),
    });
  };

  return {
    jobId,
    job,
    query,
    start: (started) => {
      observed.current = { jobId: started.job_id, status: started.status };
      setJobId(started.job_id);
    },
    cancel,
    cancelling: cancelMutation.isPending,
  };
}

/** job の実行状況と、実行中は結果の Skeleton、成功したら結果を出す。 */
function EvaluationJobSection({
  state,
  renderResult,
}: {
  state: EvaluationJobState;
  renderResult: (job: EvaluationJob) => ReactNode;
}) {
  const { job, query } = state;
  if (!job) {
    if (query.isError) {
      const error = query.error;
      return (
        <EvaluationJobError
          notFound={error instanceof ApiError && error.status === 404}
          message={error instanceof ApiError ? error.message : null}
          onRetry={() => void query.refetch()}
        />
      );
    }
    return <EvaluationJobLoading />;
  }
  // 実行中に状態の取得が失敗したとき（再起動・通信断など）。取得は自動で続けるので、最後に分かっている
  // 状態のまま、パネルの直下に取得できていないことを出す（取得できたら消える。#977）。
  const pollError = query.isError && job.status === "RUNNING" ? query.error : null;
  return (
    <>
      <EvaluationJobPanel
        job={job}
        onCancel={(target) => void state.cancel(target)}
        cancelling={state.cancelling}
      />
      {pollError ? (
        <Banner severity="warning">
          {pollError instanceof ApiError && pollError.status === 404
            ? t("evaluation.job.notFound")
            : t("evaluation.job.pollError")}
        </Banner>
      ) : null}
      {job.status === "RUNNING" ? <EvaluationResultSkeleton /> : null}
      {job.status === "SUCCEEDED" ? renderResult(job) : null}
    </>
  );
}

/**
 * 評価の基準（表示だけ。#2065）。評価は常に設定の既定の基準を使うので、画面では選ばせず、適用される
 * 閾値と設定の画面（取込・回答設定 › 評価の基準）へのリンクだけを出す。
 */
function SuiteSummary({ query }: { query: ReturnType<typeof useEvaluationSettings> }) {
  const suite = query.data?.suite ?? null;
  const status = suite ? (query.data?.suites.find((item) => item.name === suite) ?? null) : null;
  const thresholdEntries = orderedThresholdEntries(status?.thresholds ?? query.data?.thresholds);
  return (
    <Card className="min-w-0" data-testid="evaluation-suite">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ClipboardCheck size={16} className="text-accent-fg" aria-hidden />
          {t("evaluation.suite.title")}
        </CardTitle>
        <CardDescription>{t("evaluation.suite.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {query.isPending ? (
          <TimedLoadingState
            label={t("settings.loading")}
            operationKey="evaluation-suite-load"
            framed={false}
            testId="evaluation-suite-loading"
          >
            <Skeleton className="h-5 w-32" aria-hidden="true" />
            <Skeleton className="h-16 w-full" aria-hidden="true" />
          </TimedLoadingState>
        ) : query.isError ? (
          <Banner severity="warning">
            {query.error instanceof ApiError ? query.error.message : t("evaluation.error.run")}
          </Banner>
        ) : (
          <>
            {suite ? (
              <p className="text-sm font-medium text-fg" data-testid="evaluation-suite-current">
                {t("evaluation.suite.current", { suite: suiteLabel(suite) })}
              </p>
            ) : null}
            <div
              data-surface-tint="neutral"
              className="rounded-md border border-border bg-surface-hover p-3"
            >
              <p className="text-xs font-medium text-fg-muted">
                {t("evaluation.suite.thresholdsPreview")}
              </p>
              {thresholdEntries.length ? (
                <ul className="mt-2 flex flex-wrap gap-1.5" data-testid="evaluation-suite-thresholds">
                  {thresholdEntries.map(([metric, value]) => (
                    <li
                      key={metric}
                      className="inline-flex min-h-6 items-center rounded-md bg-surface px-2 text-xs font-medium text-fg ring-1 ring-border"
                    >
                      {metricLabel(metric)}
                      <span className="tnum ml-1 font-semibold text-accent-fg">
                        {formatMetricValue(value)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-fg">{t("evaluation.suite.noThresholds")}</p>
              )}
            </div>
          </>
        )}
        <Link
          to={APP_ROUTES.settingsEvaluation}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-fg hover:underline"
        >
          <Settings2 size={14} aria-hidden />
          {t("evaluation.suite.settingsLink")}
        </Link>
      </CardContent>
    </Card>
  );
}

function EvaluationResult({
  metrics,
  caseLabels,
}: {
  metrics: EvaluationMetrics;
  caseLabels: EvaluationCaseLabels;
}) {
  const failedMetrics = new Set(metrics.threshold_failures.map((failure) => failure.metric));
  const failureReasons = Object.entries(metrics.failure_reason_counts).filter(
    (entry): entry is [string, number] => typeof entry[1] === "number" && entry[1] > 0
  );
  return (
    <section className="min-w-0 space-y-4" aria-labelledby="evaluation-result-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="evaluation-result-title" className="text-base font-semibold text-fg">
          {t("evaluation.result.title")}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {metrics.evaluation_suite ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-sunken px-2.5 py-1 text-xs font-medium text-fg-muted">
              <ClipboardCheck size={14} aria-hidden />
              {t("evaluation.suite.applied")}: {suiteLabel(metrics.evaluation_suite)}
            </span>
          ) : null}
          {metrics.error_count > 0 ? (
            <StatusBadge
              variant="danger"
              label={t("evaluation.errors", {
                count: metrics.error_count,
                total: metrics.case_count,
              })}
            />
          ) : null}
          <PassedBadge passed={metrics.passed} />
        </div>
      </div>

      {EVALUATION_PERSPECTIVES.filter((perspective) => isPerspectiveShown(metrics, perspective)).map((perspective) => (
        <MetricGroup
          key={perspective.id}
          perspective={perspective}
          metrics={metrics}
          failedMetrics={failedMetrics}
        />
      ))}

      {metrics.threshold_failures.length ? (
        <Banner severity="warning" title={t("evaluation.thresholdFailures")}>
          <ul className="space-y-1">
            {metrics.threshold_failures.map((failure) => (
              <li key={failure.metric}>
                {metricLabel(failure.metric)}: {formatMetricValue(failure.actual)} /{" "}
                {formatMetricValue(failure.threshold)}
              </li>
            ))}
          </ul>
        </Banner>
      ) : null}

      <CategoryBreakdown metrics={metrics} />

      {failureReasons.length ? (
        <div className="rounded-md border border-border bg-surface p-4 text-sm">
          <p className="font-medium text-fg">{t("evaluation.failureReasons")}</p>
          <ul className="mt-3 flex flex-wrap gap-2" data-testid="evaluation-failure-reasons">
            {failureReasons.map(([reason, count]) => (
              <li
                key={reason}
                className="rounded-full border border-border bg-surface-sunken px-2.5 py-1 text-xs text-fg-muted"
              >
                {failureReasonLabel(reason)}: <span className="tnum">{count}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <CaseTable metrics={metrics} caseLabels={caseLabels} />
    </section>
  );
}

/** 1 つの観点（検索・根拠・回答）の指標。測れなかった指標は「—」と「対象のケースなし」。 */
function MetricGroup({
  perspective,
  metrics,
  failedMetrics,
}: {
  perspective: (typeof EVALUATION_PERSPECTIVES)[number];
  metrics: EvaluationMetrics;
  failedMetrics: ReadonlySet<string>;
}) {
  const titleId = `evaluation-perspective-${perspective.id}`;
  return (
    <section aria-labelledby={titleId} className="space-y-2" data-testid={titleId}>
      <div>
        <h3 id={titleId} className="text-sm font-semibold text-fg">
          {perspectiveLabel(perspective.id)}
        </h3>
        <p className="text-xs text-fg-muted">{perspectiveDescription(perspective.id)}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {perspective.metrics.map((metric) => {
          const value = metricValue(metrics, metric);
          const count = metricCaseCount(metrics, metric);
          return (
            <Card key={metric} data-testid={`evaluation-metric-${metric}`}>
              <CardContent className="space-y-1.5 pt-5">
                <p className="text-xs font-medium text-fg-muted" title={metricDescription(metric)}>
                  {metricLabel(metric)}
                </p>
                <p className="tnum text-2xl font-semibold text-fg">{formatMetricValue(value)}</p>
                <div className="flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
                  {value === null ? (
                    <span>
                      {STANDARD_ANSWER_METRICS.has(metric)
                        ? t("evaluation.metric.needsStandardAnswer")
                        : t("evaluation.metric.notMeasured")}
                    </span>
                  ) : count !== null ? (
                    <span className="tnum">{t("evaluation.metric.caseCount", { count })}</span>
                  ) : null}
                  {failedMetrics.has(metric) ? (
                    <StatusBadge variant="warning" label={t("evaluation.metric.belowThreshold")} />
                  ) : null}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

function CaseTable({
  metrics,
  caseLabels,
}: {
  metrics: EvaluationMetrics;
  caseLabels: EvaluationCaseLabels;
}) {
  const results = metrics.case_results;
  // 評価の結果ごとに 1 ページ目から。結果は trace の ID（ケースごとに一意）で見分ける（#1891）。
  const paging = useClientListPaging(
    "evaluation.casesPaging",
    `${results.length}:${results[0]?.trace_id ?? ""}:${results.at(-1)?.trace_id ?? ""}`
  );
  const hasEvidence = results.some((result) => (caseLabels[result.case_id]?.evidence.length ?? 0) > 0);
  return (
    <section aria-labelledby="evaluation-cases-title">
      {/* 見出しの右に件数（ListCount。#1891）。 */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 id="evaluation-cases-title" className="text-sm font-semibold text-fg">
          {t("evaluation.cases")}
        </h3>
        <ListToolbar
          count={listCountProps({ matchedTotal: results.length, filtered: false, live: true })}
          // 見出しの残りの幅を持たせる（ListToolbar は container query の箱なので、幅を内容から決めない）。
          className="min-w-0 flex-1"
          testId="evaluation-case-toolbar"
        />
      </div>
      <PagedDataTable<EvaluationCaseResult>
        paginationLabels={paginationLabels()}
        {...paging}
        pageSizeOptions={PAGE_SIZE_OPTIONS}
        paginationAriaLabel={t("evaluation.case.paginationLabel")}
        columns={[
          {
            key: "question",
            header: t("evaluation.case.question"),
            rowHeader: true,
            // 375px でも質問が 1 文字ずつ折り返さない幅（表は横にスクロールする）。
            className: "min-w-[10rem] break-words font-medium text-fg",
            render: (result) => (
              <CaseQuestionCell result={result} label={caseLabels[result.case_id]} />
            ),
          },
          ...(hasEvidence
            ? [
                {
                  key: "evidence",
                  header: t("evaluation.case.evidence"),
                  className: "min-w-[10rem]",
                  render: (result: EvaluationCaseResult) => (
                    <EvidenceCell result={result} label={caseLabels[result.case_id]} />
                  ),
                },
              ]
            : []),
          {
            key: "answer",
            header: t("evaluation.case.answer"),
            className: "whitespace-nowrap",
            render: (result) => <AnswerCell result={result} />,
          },
          ...(metrics.case_results.some((result) => result.handling_correct != null || result.missing_steps?.length || result.forbidden_hits?.length)
            ? [
                {
                  key: "handling",
                  header: t("evaluation.case.handling"),
                  render: (result: EvaluationCaseResult) => <HandlingCell result={result} />,
                },
              ]
            : []),
          {
            key: "judgement",
            header: t("evaluation.case.judgement"),
            render: (result) => <JudgementCell judgement={result.answer_evaluation ?? null} />,
          },
          {
            key: "faithfulness",
            header: metricLabel("faithfulness"),
            headerClassName: "hidden lg:table-cell",
            className: "tnum hidden whitespace-nowrap lg:table-cell",
            render: (result) => formatMetricValue(result.faithfulness),
          },
          {
            key: "failures",
            header: t("evaluation.case.failures"),
            headerClassName: "hidden md:table-cell",
            className: "hidden break-words text-xs text-fg-muted md:table-cell",
            render: (result) =>
              result.failure_reasons.length
                ? result.failure_reasons.map(failureReasonLabel).join("、")
                : "-",
          },
          {
            key: "trace",
            header: t("evaluation.case.trace"),
            headerClassName: "hidden xl:table-cell",
            className: "tnum hidden whitespace-nowrap text-xs text-fg-muted xl:table-cell",
            render: (result) => result.trace_id.slice(0, 12),
          },
        ]}
        rows={results}
        getRowKey={(result) => result.case_id}
        scrollAriaLabel={t("evaluation.case.scrollLabel")}
        scrollTestId="evaluation-case-scroll-region"
        paginationTestId="evaluation-case-pagination"
        tableClassName="w-full min-w-[22.86rem] text-sm md:min-w-[48.57rem]"
      />
    </section>
  );
}

/**
 * ケースの質問（Excel の行と質問）と、失敗したケース（status=error）の理由・時間切れになった工程（#383）。
 * 375px でも見えるよう、狭い幅で隠す「理由」の列ではなく質問の列に出す。見出しの無い古い結果は id。
 */
function CaseQuestionCell({
  result,
  label,
}: {
  result: EvaluationCaseResult;
  label: EvaluationCaseLabel | undefined;
}) {
  const error = evaluationCaseErrorSummary(result);
  return (
    <div className="grid min-w-0 gap-1" data-testid="evaluation-case-question">
      {label ? (
        <>
          <span className="tnum text-xs font-normal text-fg-muted">
            {evaluationCaseRowLabel(result.case_id)}
          </span>
          <span>{label.question}</span>
        </>
      ) : (
        <span>{result.case_id}</span>
      )}
      {error ? (
        <div className="grid min-w-0 gap-1.5" data-testid="evaluation-case-error">
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge variant="danger" label={t("evaluation.case.error")} />
            {error.stageLabel ? (
              <span className="inline-flex items-center rounded-md bg-surface-sunken px-2 py-0.5 text-xs font-medium text-fg-muted ring-1 ring-border">
                {t("evaluation.case.errorStage", { stage: error.stageLabel })}
              </span>
            ) : null}
          </div>
          <p className="break-words text-xs font-normal text-fg-muted">{error.message}</p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 期待する根拠の照合の結果（#2065）。見つかった数と、根拠ごとの見つかった / 見つからない（アイコンと
 * 読み上げの文言で示し、色だけに頼らない）。測れないケース（検索をしない回答・失敗）は「測れず」。
 */
function EvidenceCell({
  result,
  label,
}: {
  result: EvaluationCaseResult;
  label: EvaluationCaseLabel | undefined;
}) {
  const summary = evaluationEvidenceSummary(result, label);
  if (!summary) return <span className="text-fg-muted">—</span>;
  if (!summary.measured) {
    return <span className="text-xs text-fg-muted">{t("evaluation.case.evidenceNotMeasured")}</span>;
  }
  return (
    <div className="grid min-w-0 gap-1" data-testid="evaluation-case-evidence">
      <span className="tnum text-sm font-medium text-fg">
        {t("evaluation.case.evidenceFound", { found: summary.found, total: summary.total })}
      </span>
      <ul className="grid gap-0.5">
        {summary.items.map((item, index) => (
          <li
            key={`${index}:${item.label}`}
            className="flex min-w-0 items-start gap-1 text-xs text-fg-muted"
          >
            {item.found ? (
              <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-success-fg" aria-hidden />
            ) : (
              <XCircle size={14} className="mt-0.5 shrink-0 text-danger-fg" aria-hidden />
            )}
            <span className="min-w-0 break-all">
              <span className="sr-only">
                {t(item.found ? "evaluation.case.evidenceItemFound" : "evaluation.case.evidenceItemMissing")}
                :{" "}
              </span>
              {item.label}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 業務支援の採点（#1231）。回答の対応と期待どおりか（アイコンと文言）、不足の手順・危険な表現・
 * 触れていない条件。推定した対応は「（推定）」を付ける。
 */
function HandlingCell({ result }: { result: EvaluationCaseResult }) {
  const notes = [
    result.missing_steps?.length ? t("evaluation.case.missingSteps", { steps: result.missing_steps.join("、") }) : null,
    result.forbidden_hits?.length
      ? t("evaluation.case.forbiddenHits", { phrases: result.forbidden_hits.join("、") })
      : null,
    result.missing_conditions?.length
      ? t("evaluation.case.missingConditions", { conditions: result.missing_conditions.join("、") })
      : null,
  ].filter((note): note is string => Boolean(note));
  if (!result.observed_outcome && !notes.length) return <>—</>;
  const outcome = result.observed_outcome ? outcomeLabel(result.observed_outcome) : null;
  const expected = result.handling_correct;
  return (
    <div className="grid min-w-0 gap-1" data-testid="evaluation-case-handling">
      {outcome ? (
        <span className="inline-flex items-center gap-1.5">
          {expected === true ? (
            <CheckCircle2 size={16} className="shrink-0 text-success-fg" aria-hidden />
          ) : expected === false ? (
            <XCircle size={16} className="shrink-0 text-danger-fg" aria-hidden />
          ) : null}
          <span>
            {result.outcome_source === "inferred" ? t("evaluation.case.outcomeInferred", { outcome }) : outcome}
          </span>
          {expected != null ? (
            <span className="sr-only">
              {t(expected ? "evaluation.case.expected" : "evaluation.case.unexpected")}
            </span>
          ) : null}
        </span>
      ) : null}
      {notes.map((note) => (
        <p key={note} className="break-words text-xs text-fg-muted">
          {note}
        </p>
      ))}
    </div>
  );
}

/** 回答したか回答を控えたかと、それが期待どおりか（アイコンと読み上げの文言で示す）。 */
function AnswerCell({ result }: { result: EvaluationCaseResult }) {
  if (result.abstained === null || result.abstained === undefined) return <>—</>;
  const expected = result.refusal_correct === true;
  const label = t(result.abstained ? "evaluation.case.refused" : "evaluation.case.answered");
  const verdict = t(expected ? "evaluation.case.expected" : "evaluation.case.unexpected");
  return (
    <span className="inline-flex items-center gap-1.5" data-testid="evaluation-case-answer">
      {expected ? (
        <CheckCircle2 size={16} className="shrink-0 text-success-fg" aria-hidden />
      ) : (
        <XCircle size={16} className="shrink-0 text-danger-fg" aria-hidden />
      )}
      <span>{label}</span>
      <span className="sr-only">（{verdict}）</span>
    </span>
  );
}

/** 標準回答による評価の結果（合格・不合格と点数、評価できなかったとき）。 */
function JudgementCell({ judgement }: { judgement: EvaluationAnswerJudgement | null }) {
  if (!judgement) return <span className="text-fg-muted">—</span>;
  if (judgement.status !== "completed") {
    return (
      <span className="inline-grid gap-1" data-testid="evaluation-case-judgement">
        <StatusBadge variant="warning" label={t("evaluation.case.judgement.incomplete")} />
        {judgement.message ? (
          <span className="text-xs text-fg-muted">{judgement.message}</span>
        ) : null}
      </span>
    );
  }
  return (
    <span data-testid="evaluation-case-judgement">
      <StatusBadge
        variant={judgement.passed ? "success" : "danger"}
        label={t(
          judgement.passed ? "evaluation.case.judgement.passed" : "evaluation.case.judgement.failed"
        )}
      />
    </span>
  );
}

/** 評価全体の合否（共有の StatusBadge。色だけでなくアイコンと文言でも示す）。 */
function PassedBadge({ passed }: { passed: boolean }) {
  return (
    <StatusBadge
      variant={passed ? "success" : "danger"}
      label={passed ? t("evaluation.status.passed") : t("evaluation.status.failed")}
    />
  );
}

/** 分類ごとの結果（#1226）。ケースに分類があるときだけ出す。 */
function CategoryBreakdown({ metrics }: { metrics: EvaluationMetrics }) {
  const rows = Object.entries(metrics.category_breakdown ?? {})
    .filter((entry): entry is [string, EvaluationCategorySummary] => Boolean(entry[1]))
    .map(([category, summary]) => ({ category, ...summary }));
  if (!rows.length) return null;
  return (
    <div className="space-y-2" data-testid="evaluation-category-breakdown">
      <p className="text-sm font-medium text-fg">{t("evaluation.category.title")}</p>
      <DataTable
        ariaLabel={t("evaluation.category.title")}
        rows={rows}
        getRowKey={(row) => row.category}
        columns={[
          {
            key: "category",
            header: t("evaluation.category.column.category"),
            render: (row) => categoryLabel(row.category),
          },
          {
            key: "count",
            header: t("evaluation.category.column.count"),
            className: "tnum",
            render: (row) =>
              row.error_count
                ? t("evaluation.category.countWithErrors", { count: row.case_count, errors: row.error_count })
                : String(row.case_count),
          },
          {
            key: "pass",
            header: t("evaluation.category.column.pass"),
            className: "tnum",
            render: (row) => formatMetricValue(row.answer_pass_rate),
          },
          {
            key: "keyword",
            header: t("evaluation.category.column.keyword"),
            className: "tnum",
            render: (row) => formatMetricValue(row.answer_keyword_hit_rate),
          },
          {
            key: "abstain",
            header: t("evaluation.category.column.abstain"),
            className: "tnum",
            render: (row) => formatMetricValue(row.abstain_rate),
          },
          {
            key: "refusal",
            header: t("evaluation.category.column.refusal"),
            className: "tnum",
            render: (row) => formatMetricValue(row.refusal_correct_rate),
          },
          ...(rows.some((row) => row.handling_correct_rate != null || row.safe_answer_rate != null)
            ? [
                {
                  key: "handling",
                  header: t("evaluation.category.column.handling"),
                  className: "tnum",
                  render: (row: (typeof rows)[number]) => formatMetricValue(row.handling_correct_rate),
                },
                {
                  key: "safe",
                  header: t("evaluation.category.column.safe"),
                  className: "tnum",
                  render: (row: (typeof rows)[number]) => formatMetricValue(row.safe_answer_rate),
                },
              ]
            : []),
        ]}
      />
    </div>
  );
}

const CATEGORY_KEYS = {
  document_answerable: "evaluation.category.document_answerable",
  clarification_required: "evaluation.category.clarification_required",
  environment_data_required: "evaluation.category.environment_data_required",
  knowledge_missing: "evaluation.category.knowledge_missing",
  conflicting_sources: "evaluation.category.conflicting_sources",
  uncategorized: "evaluation.category.uncategorized",
} as const;

function categoryLabel(category: string): string {
  return category in CATEGORY_KEYS ? t(CATEGORY_KEYS[category as keyof typeof CATEGORY_KEYS]) : category;
}
