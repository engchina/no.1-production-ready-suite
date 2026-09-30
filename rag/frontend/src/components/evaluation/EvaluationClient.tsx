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
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ProcessingIndicator,
  SelectField,
  StatusBadge,
  TextareaField,
  useConfirm,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";
import {
  BarChart3,
  CheckCircle2,
  ClipboardCheck,
  FlaskConical,
  GitCompare,
  Settings2,
  XCircle,
} from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { PagedDataTable } from "@/components/PagedDataTable";
import { EmptyState } from "@/components/StateViews";
import { KnowledgeBaseScopePicker } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import {
  ApiError,
  type EvaluationAnswerJudgement,
  type EvaluationJob,
  type EvaluationJobKind,
  type EvaluationJobStatus,
  type EvaluationCaseResult,
  type EvaluationCompareResponse,
  type EvaluationExperimentResult,
  type EvaluationExperiment,
  type EvaluationMetricName,
  type EvaluationMetrics,
  type EvaluationRunRequestBody,
  type EvaluationSuiteName,
  type EvaluationSuiteStatusData,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { isOneOf, useWorkspaceState } from "@/lib/workspace-state";
import {
  useCancelEvaluationJob,
  useEvaluationJob,
  useEvaluationSettings,
  useSubmitCompareEvaluationJob,
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
  EVALUATION_METRIC_NAMES,
  EVALUATION_PERSPECTIVES,
  EVALUATION_SUITE_NAMES,
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

/** suite セレクタの「設定の既定に従う」を表す擬似値(suite を送らない)。 */
const DEFAULT_SUITE_VALUE = "__default__" as const;
const REQUEST_JSON_ID = "evaluation-request-json";
const EXPERIMENTS_JSON_ID = "evaluation-experiments-json";
type SuiteSelection = EvaluationSuiteName | typeof DEFAULT_SUITE_VALUE;

/**
 * サンプルは、答えるべき質問（正解の文書・期待する語・標準回答）と答えるべきでない質問を含む。
 * 検索の方式（mode）と rerank の件数は回答エンジンが使わないため書かない（#591）。
 */
const SAMPLE_REQUEST = JSON.stringify(
  {
    cases: [
      {
        id: "policy-approval-flow-basic",
        query: "経費申請の承認フローを教えてください。",
        relevant_document_ids: ["doc-expense-policy"],
        expected_answer_keywords: ["部門長", "承認"],
        standard_answer:
          "経費申請は申請者が申請書を提出し、部門長が内容を確認して承認します。",
      },
      {
        id: "out-of-scope-refusal",
        query: "社員食堂の来月の献立を教えてください。",
        answerable: false,
      },
    ],
    top_k: 20,
    filters: { status: "INDEXED" },
  },
  null,
  2
);

const SAMPLE_EXPERIMENTS = JSON.stringify(
  [
    {
      id: "default",
      top_k: 20,
      filters: { status: "INDEXED" },
    },
    {
      id: "rag-fusion",
      top_k: 20,
      filters: { status: "INDEXED" },
      rag_overrides: { query_strategy: "rag_fusion" },
    },
  ],
  null,
  2
);

const RANKING_METRIC_OPTIONS = EVALUATION_METRIC_NAMES.map((metric) => ({
  value: metric,
  label: metricLabel(metric),
})) satisfies SelectFieldOption<EvaluationMetricName>[];

/** RAG golden set 評価画面。 */
export function EvaluationClient() {
  const runMutation = useSubmitRunEvaluationJob();
  const compareMutation = useSubmitCompareEvaluationJob();
  const settingsQuery = useEvaluationSettings();
  // 評価と比較は job で動く（#390）。job id だけを作業状態に残し、画面に戻ったらサーバーの状態
  // （進捗・結果）を確かめる（workspace-state.md）。戻っただけで評価を送り直さない。
  const runJob = useEvaluationJobState("run");
  const compareJob = useEvaluationJobState("compare");
  // 評価の入力（JSON・指標・KB スコープ・基準）は、ページを行き来しても再読込しても残す
  // （workspace-state.md）。評価結果は保存せず、戻っただけで評価を送り直さない。
  const [requestJson, setRequestJson] = useWorkspaceState("evaluation.requestJson", SAMPLE_REQUEST);
  const [experimentsJson, setExperimentsJson] = useWorkspaceState(
    "evaluation.experimentsJson",
    SAMPLE_EXPERIMENTS
  );
  const [rankingMetric, setRankingMetric] = useWorkspaceState<EvaluationMetricName>(
    "evaluation.rankingMetric",
    "context_recall",
    isOneOf(EVALUATION_METRIC_NAMES)
  );
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useWorkspaceState<string[]>(
    "evaluation.knowledgeBaseIds",
    []
  );
  const [suite, setSuite] = useWorkspaceState<SuiteSelection>(
    "evaluation.suite",
    DEFAULT_SUITE_VALUE,
    isOneOf<SuiteSelection>([DEFAULT_SUITE_VALUE, ...EVALUATION_SUITE_NAMES])
  );
  const [runError, setRunError] = useState("");
  const [compareError, setCompareError] = useState("");
  // JSON の未入力・形式のエラーは、実行を押したときに欄の直下へ出す（押せないボタンだけにしない。#541）。
  const [requestJsonError, setRequestJsonError] = useState<string | null>(null);
  const [experimentsJsonError, setExperimentsJsonError] = useState<string | null>(null);

  const parsedRequest = useMemo(() => parseEvaluationRequest(requestJson), [requestJson]);
  const parsedExperiments = useMemo(() => parseExperiments(experimentsJson), [experimentsJson]);
  const runActive = runMutation.isPending || isEvaluationJobActive(runJob.job);
  const compareActive = compareMutation.isPending || isEvaluationJobActive(compareJob.job);

  const globalSuite = settingsQuery.data?.suite ?? null;
  const suiteStatuses = settingsQuery.data?.suites ?? [];
  const effectiveSuiteName: EvaluationSuiteName | null =
    suite === DEFAULT_SUITE_VALUE ? globalSuite : suite;
  // thresholds を書いた（空の {} を含む）ときは、backend が基準より優先して使う。
  const requestThresholds =
    parsedRequest.ok && isRecord(parsedRequest.value.thresholds)
      ? parsedRequest.value.thresholds
      : null;

  const runEvaluation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (runActive) return;
    setRequestJsonError(parsedRequest.ok ? null : parsedRequest.error);
    if (!parsedRequest.ok) {
      document.getElementById(REQUEST_JSON_ID)?.focus();
      return;
    }
    setRunError("");
    runMutation.reset();
    try {
      const body = applyRequestKnowledgeBaseScope(parsedRequest.value, knowledgeBaseIds);
      const job = await runMutation.mutateAsync(
        suite === DEFAULT_SUITE_VALUE ? body : { ...body, suite }
      );
      runJob.start(job);
      toast.info(t("evaluation.toast.started"));
    } catch (error) {
      setRunError(error instanceof ApiError ? error.message : t("evaluation.error.run"));
    }
  };

  const compareEvaluation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (compareActive) return;
    // 比較は Golden set JSON の cases も使うので、両方の欄を検証し、画面の並び順で最初のエラーの欄へ移す。
    setRequestJsonError(parsedRequest.ok ? null : parsedRequest.error);
    setExperimentsJsonError(parsedExperiments.ok ? null : parsedExperiments.error);
    if (!parsedRequest.ok || !parsedExperiments.ok) {
      document.getElementById(parsedRequest.ok ? EXPERIMENTS_JSON_ID : REQUEST_JSON_ID)?.focus();
      return;
    }
    setCompareError("");
    compareMutation.reset();
    try {
      const job = await compareMutation.mutateAsync({
        cases: parsedRequest.value.cases,
        thresholds: parsedRequest.value.thresholds ?? null,
        experiments: applyExperimentKnowledgeBaseScope(parsedExperiments.value, knowledgeBaseIds),
        ranking_metric: rankingMetric,
        ...(suite === DEFAULT_SUITE_VALUE ? {} : { suite }),
      });
      compareJob.start(job);
      toast.info(t("evaluation.toast.startedCompare"));
    } catch (error) {
      setCompareError(error instanceof ApiError ? error.message : t("evaluation.error.compare"));
    }
  };

  return (
    <div>
      <PageHeader wide title={t("nav.evaluation")} subtitle={t("evaluation.subtitle")} />
      <PageBody wide>
        <Card className="min-w-0">
          <CardContent className="pt-5">
            <KnowledgeBaseScopePicker
              selectedIds={knowledgeBaseIds}
              onChange={setKnowledgeBaseIds}
              disabled={runMutation.isPending || compareMutation.isPending}
              helper={t("evaluation.knowledgeBaseScope.helper")}
            />
          </CardContent>
        </Card>

        <SuiteSelector
          suite={suite}
          onChange={setSuite}
          globalSuite={globalSuite}
          effectiveSuiteName={effectiveSuiteName}
          suiteStatuses={suiteStatuses}
          requestThresholds={requestThresholds}
        />

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FlaskConical size={16} className="text-accent-fg" aria-hidden />
                {t("evaluation.input.title")}
              </CardTitle>
              <CardDescription>{t("evaluation.input.description")}</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-4" onSubmit={(event) => void runEvaluation(event)}>
                <JsonField
                  id={REQUEST_JSON_ID}
                  label={t("evaluation.input.label")}
                  value={requestJson}
                  rows={18}
                  placeholder={t("evaluation.input.placeholder")}
                  error={requestJsonError}
                  onChange={(value) => {
                    setRequestJson(value);
                    setRequestJsonError(null);
                  }}
                />
                {runError ? <ErrorNotice message={runError} /> : null}
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="submit" loading={runMutation.isPending} disabled={runActive} icon={BarChart3}>
                    {t("evaluation.actions.run")}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => {
                      setRequestJson(SAMPLE_REQUEST);
                      setRequestJsonError(null);
                      setRunError("");
                    }}
                  >
                    {t("evaluation.actions.loadSample")}
                  </Button>
                </div>
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
              </form>
            </CardContent>
          </Card>

          <Card className="min-w-0">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <GitCompare size={16} className="text-accent-fg" aria-hidden />
                {t("evaluation.compare.title")}
              </CardTitle>
              <CardDescription>{t("evaluation.compare.description")}</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-4" onSubmit={(event) => void compareEvaluation(event)}>
                <SelectField
                  id="evaluation-ranking-metric"
                  label={t("evaluation.compare.metric")}
                  value={rankingMetric}
                  options={RANKING_METRIC_OPTIONS}
                  onValueChange={setRankingMetric}
                  width="md"
                />
                <JsonField
                  id={EXPERIMENTS_JSON_ID}
                  label={t("evaluation.compare.experiments")}
                  value={experimentsJson}
                  rows={13}
                  placeholder={t("evaluation.compare.placeholder")}
                  error={experimentsJsonError}
                  onChange={(value) => {
                    setExperimentsJson(value);
                    setExperimentsJsonError(null);
                  }}
                />
                {compareError ? <ErrorNotice message={compareError} /> : null}
                <Button
                  type="submit"
                  className="w-full"
                  loading={compareMutation.isPending}
                  disabled={compareActive} icon={GitCompare}>
                  {t("evaluation.actions.compare")}
                </Button>
                {compareMutation.isPending ? (
                  <ProcessingIndicator
                    active
                    label={t("evaluation.actions.startingCompare")}
                    operationKey="evaluation-compare"
                    placement="action"
                    activityIcon="none"
                    testId="evaluation-compare-processing"
                  />
                ) : null}
              </form>
            </CardContent>
          </Card>
        </div>

        {runJob.jobId ? (
          <EvaluationJobSection
            kind="run"
            state={runJob}
            renderResult={(job) =>
              job.run_result ? <EvaluationResult metrics={job.run_result} /> : null
            }
          />
        ) : runMutation.isPending ? (
          <EvaluationResultSkeleton kind="run" />
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

        {compareJob.jobId ? (
          <EvaluationJobSection
            kind="compare"
            state={compareJob}
            renderResult={(job) =>
              job.compare_result ? <CompareResult comparison={job.compare_result} /> : null
            }
          />
        ) : null}
      </PageBody>
    </div>
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
 * 評価（run）・比較（compare）の job の状態。job id を作業状態に残し、実行中は状態を取得し続ける。
 * 実行中から終わりへ移ったときだけ、完了・失敗を 1 回通知する（messaging.md §4.2）。
 */
function useEvaluationJobState(kind: EvaluationJobKind): EvaluationJobState {
  const [jobId, setJobId] = useWorkspaceState<string>(
    kind === "compare" ? "evaluation.compareJobId" : "evaluation.runJobId",
    "",
    isEvaluationJobId
  );
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
      toast.success(
        t(kind === "compare" ? "evaluation.toast.succeededCompare" : "evaluation.toast.succeeded")
      );
    } else if (job.status === "FAILED") {
      toast.error(t("evaluation.toast.failed"));
    }
  }, [job, kind]);

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
  kind,
  state,
  renderResult,
}: {
  kind: EvaluationJobKind;
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
    return <EvaluationJobLoading kind={kind} />;
  }
  return (
    <>
      <EvaluationJobPanel
        kind={kind}
        job={job}
        onCancel={(target) => void state.cancel(target)}
        cancelling={state.cancelling}
      />
      {job.status === "RUNNING" ? <EvaluationResultSkeleton kind={kind} /> : null}
      {job.status === "SUCCEEDED" ? renderResult(job) : null}
    </>
  );
}

function SuiteSelector({
  suite,
  onChange,
  globalSuite,
  effectiveSuiteName,
  suiteStatuses,
  requestThresholds,
}: {
  suite: SuiteSelection;
  onChange: (value: SuiteSelection) => void;
  globalSuite: EvaluationSuiteName | null;
  effectiveSuiteName: EvaluationSuiteName | null;
  suiteStatuses: EvaluationSuiteStatusData[];
  requestThresholds: Record<string, unknown> | null;
}) {
  const defaultLabel = globalSuite
    ? t("evaluation.suite.followDefaultWith", { suite: suiteLabel(globalSuite) })
    : t("evaluation.suite.followDefault");
  const options: SelectFieldOption<SuiteSelection>[] = [
    { value: DEFAULT_SUITE_VALUE, label: defaultLabel },
    ...EVALUATION_SUITE_NAMES.map((name) => ({ value: name, label: suiteLabel(name) })),
  ];
  const effectiveStatus = effectiveSuiteName
    ? (suiteStatuses.find((item) => item.name === effectiveSuiteName) ?? null)
    : null;
  // Golden set JSON に thresholds があるときは backend が基準より優先するため、
  // プレビューも実際に適用される JSON の値を表示する（基準の値ではなく）。
  const requestHasThresholds = requestThresholds !== null;
  const thresholdEntries = orderedThresholdEntries(
    requestHasThresholds
      ? (requestThresholds as Partial<Record<string, number | null>>)
      : effectiveStatus?.thresholds
  );

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ClipboardCheck size={16} className="text-accent-fg" aria-hidden />
          {t("evaluation.suite.title")}
        </CardTitle>
        <CardDescription>{t("evaluation.suite.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <SelectField
          id="evaluation-suite"
          label={t("evaluation.suite.label")}
          value={suite}
          options={options}
          onValueChange={onChange}
          width="lg"
        />
        <div className="rounded-md border border-border bg-surface-hover p-3">
          <p className="text-xs font-medium text-fg-muted">
            {requestHasThresholds
              ? t("evaluation.suite.thresholdsPreviewOverride")
              : t("evaluation.suite.thresholdsPreview")}
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
        {requestHasThresholds ? (
          <Banner severity="info">{t("evaluation.suite.manualOverrideNote")}</Banner>
        ) : null}
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

function EvaluationResult({ metrics }: { metrics: EvaluationMetrics }) {
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

      {EVALUATION_PERSPECTIVES.map((perspective) => (
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

      <CaseTable metrics={metrics} />
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

function CaseTable({ metrics }: { metrics: EvaluationMetrics }) {
  return (
    <section aria-labelledby="evaluation-cases-title">
      <h3 id="evaluation-cases-title" className="mb-3 text-sm font-semibold text-fg">
        {t("evaluation.cases")}
      </h3>
      <PagedDataTable<EvaluationCaseResult>
        columns={[
          {
            key: "case_id",
            header: t("evaluation.case.id"),
            rowHeader: true,
            className: "break-words font-medium text-fg",
            render: (result) => <CaseIdCell result={result} />,
          },
          {
            key: "context_recall",
            header: metricLabel("context_recall"),
            headerClassName: "hidden md:table-cell",
            className: "tnum hidden whitespace-nowrap md:table-cell",
            render: (result) => formatMetricValue(result.context_recall),
          },
          {
            key: "mrr",
            header: metricLabel("mrr"),
            headerClassName: "hidden md:table-cell",
            className: "tnum hidden whitespace-nowrap md:table-cell",
            render: (result) => formatMetricValue(result.reciprocal_rank),
          },
          {
            key: "faithfulness",
            header: metricLabel("faithfulness"),
            headerClassName: "hidden lg:table-cell",
            className: "tnum hidden whitespace-nowrap lg:table-cell",
            render: (result) => formatMetricValue(result.faithfulness),
          },
          {
            key: "answer",
            header: t("evaluation.case.answer"),
            className: "whitespace-nowrap",
            render: (result) => <AnswerCell result={result} />,
          },
          {
            key: "judgement",
            header: t("evaluation.case.judgement"),
            render: (result) => <JudgementCell judgement={result.answer_evaluation ?? null} />,
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
        rows={metrics.case_results}
        getRowKey={(result) => result.case_id}
        resetKey={metrics}
        scrollAriaLabel={t("evaluation.case.scrollLabel")}
        scrollTestId="evaluation-case-scroll-region"
        paginationTestId="evaluation-case-pagination"
        tableClassName="w-full min-w-[22.86rem] text-sm md:min-w-[48.57rem]"
      />
    </section>
  );
}

/**
 * ケースの id と、失敗したケース（status=error）の理由・時間切れになった工程（#383）。
 * 375px でも見えるよう、狭い幅で隠す「理由」の列ではなく id の列に出す。
 */
function CaseIdCell({ result }: { result: EvaluationCaseResult }) {
  const error = evaluationCaseErrorSummary(result);
  if (!error) return <>{result.case_id}</>;
  return (
    <div className="grid min-w-0 gap-1.5" data-testid="evaluation-case-error">
      <span>{result.case_id}</span>
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
  );
}

/** 回答したか拒答したかと、それが期待どおりか（アイコンと読み上げの文言で示す）。 */
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
  const params = {
    score: judgement.total_score ?? "—",
    max: judgement.max_score,
  };
  return (
    <span data-testid="evaluation-case-judgement">
      <StatusBadge
        variant={judgement.passed ? "success" : "danger"}
        label={t(
          judgement.passed ? "evaluation.case.judgement.passed" : "evaluation.case.judgement.failed",
          params
        )}
      />
    </span>
  );
}

function CompareResult({ comparison }: { comparison: EvaluationCompareResponse }) {
  return (
    <section className="min-w-0 space-y-3" aria-labelledby="evaluation-compare-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="evaluation-compare-title" className="text-base font-semibold text-fg">
          {t("evaluation.compare.title")}
        </h2>
        {comparison.best_experiment_id ? (
          <span className="rounded-full bg-success-subtle px-3 py-1 text-xs font-medium text-success-fg">
            {t("evaluation.compare.best")}: {comparison.best_experiment_id}
          </span>
        ) : null}
      </div>
      {/* contain:paint で横スクロール領域を確実に封じ込める。main の [contain:layout] 配下では
          縦スクロールが発生しない scroll container が min-width をもつ表を祖先へ伝播させ、
          ページが横スクロール(崩れ)するため(決定論的に再現・検証済み)。 */}
      <DataTable<EvaluationExperimentResult>
        columns={[
          {
            key: "rank",
            header: t("evaluation.compare.rank"),
            className: "tnum whitespace-nowrap",
            render: (result) => result.rank,
          },
          {
            key: "experiment",
            header: t("evaluation.compare.experiment"),
            rowHeader: true,
            className: "break-words font-medium text-fg",
            render: (result) => result.experiment.id,
          },
          {
            key: "score",
            header: metricLabel(comparison.ranking_metric),
            className: "tnum whitespace-nowrap",
            render: (result) => formatMetricValue(result.ranking_score),
          },
          {
            key: "context_recall",
            header: metricLabel("context_recall"),
            headerClassName: "hidden md:table-cell",
            className: "tnum hidden whitespace-nowrap md:table-cell",
            render: (result) => formatMetricValue(metricValue(result.metrics, "context_recall")),
          },
          {
            key: "refusal_accuracy",
            header: metricLabel("refusal_accuracy"),
            headerClassName: "hidden md:table-cell",
            className: "tnum hidden whitespace-nowrap md:table-cell",
            render: (result) =>
              formatMetricValue(metricValue(result.metrics, "refusal_accuracy")),
          },
          {
            key: "answer_pass_rate",
            header: metricLabel("answer_pass_rate"),
            headerClassName: "hidden lg:table-cell",
            className: "tnum hidden whitespace-nowrap lg:table-cell",
            render: (result) =>
              formatMetricValue(metricValue(result.metrics, "answer_pass_rate")),
          },
          {
            key: "passed",
            header: t("evaluation.compare.passed"),
            render: (result) => <PassedBadge passed={result.metrics.passed} />,
          },
        ]}
        rows={comparison.results}
        getRowKey={(result) => result.experiment.id}
        rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
        stickyHeader
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
        scrollAriaLabel={t("evaluation.compare.scrollLabel")}
        className="[contain:paint]"
        tableClassName="w-full min-w-[45.71rem] text-sm"
      />
    </section>
  );
}

function JsonField({
  id,
  label,
  value,
  rows,
  placeholder,
  error,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  rows: number;
  placeholder: string;
  /** 欄の直下に出すエラー（未入力・JSON の形式・件数）。 */
  error: string | null;
  onChange: (value: string) => void;
}) {
  // どちらの JSON も空・不正のままでは実行できず、backend も cases / experiments を 1 件以上必須にする（#531）。
  return (
    <TextareaField
      id={id}
      label={label}
      required
      error={error ?? undefined}
      value={value}
      rows={rows}
      placeholder={placeholder}
      monospace
      onValueChange={onChange}
    />
  );
}

function ErrorNotice({ message }: { message: string }) {
  return <Banner severity="danger">{message}</Banner>;
}

function applyRequestKnowledgeBaseScope(
  request: EvaluationRunRequestBody,
  knowledgeBaseIds: string[]
): EvaluationRunRequestBody {
  if (knowledgeBaseIds.length === 0) return request;
  return {
    ...request,
    filters: stripKnowledgeBaseFilter(request.filters) ?? {},
    knowledge_base_ids: knowledgeBaseIds,
  };
}

function applyExperimentKnowledgeBaseScope(
  experiments: EvaluationExperiment[],
  knowledgeBaseIds: string[]
): EvaluationExperiment[] {
  if (knowledgeBaseIds.length === 0) return experiments;
  return experiments.map((experiment) => ({
    ...experiment,
    filters: stripKnowledgeBaseFilter(experiment.filters) ?? {},
    knowledge_base_ids: knowledgeBaseIds,
  }));
}

function stripKnowledgeBaseFilter(
  filters: Record<string, string> | undefined
): Record<string, string> | undefined {
  if (!filters) return undefined;
  const next = { ...filters };
  delete next.knowledge_base_id;
  return Object.keys(next).length ? next : undefined;
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

function parseEvaluationRequest(raw: string): ParseResult<EvaluationRunRequestBody> {
  const parsed = parseJson(raw, "evaluation.input.required", "evaluation.input.invalidJson");
  if (!parsed.ok) return parsed;
  if (!isRecord(parsed.value) || !Array.isArray(parsed.value.cases) || parsed.value.cases.length < 1) {
    return { ok: false, error: t("evaluation.input.noCases") };
  }
  return { ok: true, value: parsed.value as unknown as EvaluationRunRequestBody };
}

function parseExperiments(raw: string): ParseResult<EvaluationExperiment[]> {
  const parsed = parseJson(raw, "evaluation.compare.required", "evaluation.compare.invalidJson");
  if (!parsed.ok) return parsed;
  if (!Array.isArray(parsed.value) || parsed.value.length < 1) {
    return { ok: false, error: t("evaluation.compare.noExperiments") };
  }
  return { ok: true, value: parsed.value as unknown as EvaluationExperiment[] };
}

function parseJson(
  raw: string,
  requiredKey: "evaluation.input.required" | "evaluation.compare.required",
  invalidKey: "evaluation.input.invalidJson" | "evaluation.compare.invalidJson",
): ParseResult<unknown> {
  if (!raw.trim()) return { ok: false, error: t(requiredKey) };
  try {
    return { ok: true, value: JSON.parse(raw) };
  } catch {
    return { ok: false, error: t(invalidKey) };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

type ParseResult<T> = { ok: true; value: T } | { ok: false; error: string };
