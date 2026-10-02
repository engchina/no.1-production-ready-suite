import { useEffect, useMemo, useState, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, FileText, FlaskConical, Trash2 } from "lucide-react";
import {
  Banner,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  FormActionBar,
  FormStatus,
  ListSkeleton,
  MessageText,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  RowActionMenu,
  RowTitleButton,
  SelectField,
  SideSheet,
  StatusBadge,
  TableSkeleton,
  TextareaField,
  TimedLoadingState,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import { PagedDataTable } from "@/components/ListViews";
import {
  agentApi,
  type EvaluationCase,
  type EvaluationCaseResult,
  type EvaluationJob,
  type EvaluationJobItem,
  type EvaluationJobStatus,
  type EvaluationSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { isNullableString, isString, useWorkspaceState } from "@/lib/workspace-state";

// 品質評価（#776）。決まった質問（評価ケース）で業務 Agent を実行し、期待する回答の要点と比べて
// 合否を判定する。画面の形は RAG の品質評価・NL2SQL の SQL生成評価にそろえる
// （評価の条件 → 実行状況 → 評価概要 → ケース別結果 → 最近の評価）。

const MAX_CASES = 50;
const POLL_INTERVAL_MS = 1500;
const ACTIVE: ReadonlySet<EvaluationJobStatus> = new Set(["queued", "running"]);

const SAMPLE_CASES: EvaluationCase[] = [
  {
    id: "expense-deadline",
    question: "経費精算の締め日はいつですか？",
    expected: "毎月 25 日が締め日で、過ぎた分は翌月の精算になること",
  },
  {
    id: "sales-total",
    question: "今月の売上の合計はいくらですか？",
    expected: "今月の売上の合計金額を、単位（円）付きの数値で答えていること",
  },
];

type ParsedCases = { cases: EvaluationCase[]; error: null } | { cases: null; error: string };

/** 評価ケースの JSON を読む（backend も同じ規則で検証する）。 */
export function parseEvaluationCases(text: string): ParsedCases {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return { cases: null, error: t("evaluation.form.invalidJson") };
  }
  if (!Array.isArray(value)) return { cases: null, error: t("evaluation.form.notArray") };
  if (value.length === 0) return { cases: null, error: t("evaluation.form.empty") };
  if (value.length > MAX_CASES) return { cases: null, error: t("evaluation.form.tooMany") };
  const cases: EvaluationCase[] = [];
  for (const [index, item] of value.entries()) {
    const record = item as Record<string, unknown> | null;
    const question = record?.question;
    const expected = record?.expected;
    if (typeof question !== "string" || !question.trim() || typeof expected !== "string" || !expected.trim()) {
      return { cases: null, error: t("evaluation.form.caseInvalid", { index: index + 1 }) };
    }
    const id = typeof record?.id === "string" ? record.id : undefined;
    cases.push({ ...(id ? { id } : {}), question, expected });
  }
  return { cases, error: null };
}

export function EvaluationPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [agentId, setAgentId] = useWorkspaceState("evaluation", "agentId", "", isString);
  const [casesJson, setCasesJson] = useWorkspaceState("evaluation", "casesJson", "", isString);
  const [jobId, setJobId] = useWorkspaceState<"evaluation", string | null>(
    "evaluation",
    "jobId",
    null,
    isNullableString
  );
  const [touched, setTouched] = useState(false);
  const [selectedCase, setSelectedCase] = useState<EvaluationCaseResult | null>(null);

  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const usableAgents = useMemo(
    () => (agents.data?.agents ?? []).filter((agent) => agent.enabled && !agent.migration_required),
    [agents.data]
  );
  const selectedAgentId = usableAgents.some((agent) => agent.id === agentId) ? agentId : (usableAgents[0]?.id ?? "");

  const jobs = useQuery({ queryKey: ["evaluations"], queryFn: agentApi.listEvaluations });
  // 表示する評価は、選んだ評価か、無ければ最新の評価。
  const shownJobId = jobId ?? jobs.data?.jobs[0]?.id ?? null;
  const job = useQuery({
    queryKey: ["evaluation", shownJobId],
    queryFn: () => agentApi.getEvaluation(shownJobId ?? ""),
    enabled: Boolean(shownJobId),
    retry: false,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data && ACTIVE.has(query.state.data.status) ? POLL_INTERVAL_MS : false),
  });
  // 表示していた評価が消えていたら、最新の評価に戻す。
  useEffect(() => {
    if (jobId && job.isError) setJobId(null);
  }, [jobId, job.isError, setJobId]);
  // 実行中の評価が終わったら、最近の評価の一覧を取り直す。
  const shownStatus = job.data?.status;
  useEffect(() => {
    if (shownStatus && !ACTIVE.has(shownStatus)) {
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
    }
  }, [shownStatus, queryClient]);

  const parsed = useMemo(() => parseEvaluationCases(casesJson), [casesJson]);
  const running = (jobs.data?.jobs ?? []).some((item) => ACTIVE.has(item.status)) || (job.data ? ACTIVE.has(job.data.status) : false);

  const start = useMutation({
    mutationFn: (cases: EvaluationCase[]) => agentApi.createEvaluation({ agent_id: selectedAgentId, cases }),
    onSuccess: (created) => {
      setJobId(created.id);
      queryClient.setQueryData(["evaluation", created.id], created);
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      toast.success(t("evaluation.form.started"));
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => agentApi.cancelEvaluation(id),
    onSuccess: (cancelled) => {
      queryClient.setQueryData(["evaluation", cancelled.id], cancelled);
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      toast.success(t("evaluation.progress.cancelled"));
    },
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => agentApi.deleteEvaluation(id),
    onSuccess: (_data, id) => {
      if (jobId === id) setJobId(null);
      queryClient.removeQueries({ queryKey: ["evaluation", id] });
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      toast.success(t("evaluation.jobs.deleted"));
    },
    onError: (error) => toast.error(error.message),
  });

  function submit() {
    setTouched(true);
    if (parsed.cases && selectedAgentId) start.mutate(parsed.cases);
  }

  async function confirmDelete(item: EvaluationJobItem) {
    const ok = await confirm({
      title: t("evaluation.jobs.deleteTitle"),
      description: t("evaluation.jobs.deleteDescription", {
        agent: item.agent_name || item.agent_id,
        started: formatDateTime(item.created_at),
      }),
      confirmLabel: t("evaluation.jobs.delete"),
      tone: "danger",
    });
    if (ok) remove.mutate(item.id);
  }

  const casesError = touched || casesJson.trim() ? parsed.error : null;
  return (
    <>
      <PageHeader wide title={t("nav.evaluation")} subtitle={t("page.evaluation.subtitle")} />
      <PageBody wide>
        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.form.title")}</CardTitle>
            <CardDescription>{t("evaluation.form.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <SelectField<string>
              id="evaluation-agent"
              label={t("evaluation.form.agent")}
              width="md"
              value={selectedAgentId}
              options={usableAgents.map((agent) => ({ value: agent.id, label: agent.name }))}
              onValueChange={setAgentId}
              disabled={usableAgents.length === 0}
            />
            <TextareaField
              id="evaluation-cases"
              label={t("evaluation.form.cases")}
              value={casesJson}
              rows={10}
              textareaClassName="font-mono"
              helper={
                parsed.cases
                  ? `${t("evaluation.form.casesHelper")} ${t("evaluation.form.caseCount", { count: parsed.cases.length })}`
                  : t("evaluation.form.casesHelper")
              }
              error={casesError ?? undefined}
              onChange={(event) => setCasesJson(event.target.value)}
            />
            <FormActionBar
              ariaLabel={t("evaluation.form.title")}
              primaryActions={[
                {
                  id: "start",
                  label: t("evaluation.form.start"),
                  icon: FlaskConical,
                  loading: start.isPending,
                  disabled: !selectedAgentId || running,
                  onClick: submit,
                  testId: "evaluation-start",
                },
              ]}
              secondaryActions={[
                {
                  id: "sample",
                  label: t("evaluation.form.loadSample"),
                  icon: FileText,
                  onClick: () => setCasesJson(JSON.stringify(SAMPLE_CASES, null, 2)),
                },
              ]}
              status={
                start.error ? (
                  <FormStatus tone="danger" message={start.error.message} />
                ) : running && !start.isPending ? (
                  <FormStatus tone="info" message={t("evaluation.form.busy")} />
                ) : null
              }
            />
          </CardContent>
        </Card>

        {shownJobId && job.isLoading ? (
          <TimedLoadingState label={t("loading.evaluation")} testId="evaluation-loading">
            <TableSkeleton columns={4} />
          </TimedLoadingState>
        ) : job.data ? (
          <JobView
            job={job.data}
            cancelling={cancel.isPending}
            onCancel={() => cancel.mutate(job.data.id)}
            onOpenCase={setSelectedCase}
          />
        ) : null}

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("evaluation.jobs.title")}</CardTitle>
            <CardDescription>{t("evaluation.jobs.description")}</CardDescription>
          </CardHeader>
          <CardContent>
            {jobs.isLoading ? (
              <TimedLoadingState label={t("loading.evaluations")} testId="evaluations-loading">
                <ListSkeleton rows={3} />
              </TimedLoadingState>
            ) : (jobs.data?.jobs ?? []).length === 0 ? (
              <EmptyState title={t("evaluation.jobs.empty")} hint={t("evaluation.jobs.emptyHint")} />
            ) : (
              <JobsTable
                jobs={jobs.data?.jobs ?? []}
                shownJobId={shownJobId}
                onShow={(item) => setJobId(item.id)}
                onDelete={(item) => void confirmDelete(item)}
              />
            )}
          </CardContent>
        </Card>

        <SideSheet
          open={selectedCase !== null}
          onClose={() => setSelectedCase(null)}
          title={t("evaluation.detail.title")}
          closeLabel={t("evaluation.detail.close")}
          id="evaluation-case-detail"
          side="right"
        >
          {selectedCase ? <CaseDetail result={selectedCase} /> : null}
        </SideSheet>
      </PageBody>
    </>
  );
}

function JobView({
  job,
  cancelling,
  onCancel,
  onOpenCase,
}: {
  job: EvaluationJob;
  cancelling: boolean;
  onCancel: () => void;
  onOpenCase: (result: EvaluationCaseResult) => void;
}) {
  const active = ACTIVE.has(job.status);
  const { summary } = job;
  return (
    <div className="space-y-4">
      {active ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.progress.title")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ProcessingIndicator
              active
              operationKey={job.id}
              startedAt={job.started_at ?? job.created_at}
              placement="job"
              label={
                job.status === "queued"
                  ? t("evaluation.progress.queued")
                  : t("evaluation.progress.running", {
                      agent: job.agent_name || job.agent_id,
                      done: summary.completed,
                      total: summary.total,
                    })
              }
              onCancel={cancelling ? undefined : onCancel}
              labels={{ cancel: t("evaluation.progress.cancel") }}
              testId="evaluation-progress"
            />
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>{t("evaluation.summary.title")}</CardTitle>
            <StatusBadge variant={jobStatusVariant(job.status)} label={t(`evaluation.jobStatus.${job.status}` as I18nKey)} />
          </div>
          <CardDescription>
            {t("evaluation.summary.description", {
              agent: job.agent_name || job.agent_id,
              started: formatDateTime(job.created_at),
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {job.status === "failed" ? (
            <Banner severity="danger" title={t("evaluation.summary.failed")}>
              {job.error}
            </Banner>
          ) : null}
          <SummaryMetrics summary={summary} />
        </CardContent>
      </Card>

      <Card className="min-w-0">
        <CardHeader>
          <CardTitle>{t("evaluation.results.title")}</CardTitle>
          <CardDescription>{t("evaluation.results.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ResultsTable results={job.results} jobId={job.id} onOpen={onOpenCase} />
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryMetrics({ summary }: { summary: EvaluationSummary }) {
  const ofTotal = t("evaluation.summary.ofTotal", { total: formatNumber(summary.total) });
  return (
    <div className="grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3 xl:grid-cols-6" data-testid="evaluation-summary">
      <Metric label={t("evaluation.summary.passRate")} value={formatRate(summary.pass_rate)} detail={ofTotal} />
      <Metric
        label={t("evaluation.summary.averageScore")}
        value={summary.average_score === null ? "—" : summary.average_score.toFixed(2)}
        detail="0〜1"
      />
      <Metric label={t("evaluation.summary.correct")} value={formatNumber(summary.correct)} detail={ofTotal} />
      <Metric label={t("evaluation.summary.incorrect")} value={formatNumber(summary.incorrect)} detail={ofTotal} />
      <Metric label={t("evaluation.summary.uncertain")} value={formatNumber(summary.uncertain)} detail={ofTotal} />
      <Metric label={t("evaluation.summary.errors")} value={formatNumber(summary.errors)} detail={ofTotal} />
    </div>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="min-w-0 border-l-2 border-accent-emphasis pl-3">
      <p className="truncate text-xs text-fg-muted" title={label}>
        {label}
      </p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-fg">{value}</p>
      <p className="mt-0.5 text-xs tabular-nums text-fg-muted">{detail}</p>
    </div>
  );
}

function ResultsTable({
  results,
  jobId,
  onOpen,
}: {
  results: EvaluationCaseResult[];
  jobId: string;
  onOpen: (result: EvaluationCaseResult) => void;
}) {
  const columns: DataTableColumn<EvaluationCaseResult>[] = [
    {
      key: "case",
      header: t("evaluation.column.case"),
      rowHeader: true,
      className: "min-w-56 max-w-96",
      render: (result) => (
        <RowTitleButton title={result.case.question} subtitle={result.case.id} onClick={() => onOpen(result)} />
      ),
    },
    {
      key: "verdict",
      header: t("evaluation.column.verdict"),
      render: (result) => <CaseBadge result={result} />,
    },
    {
      key: "score",
      header: t("evaluation.column.score"),
      align: "right",
      className: "tabular-nums text-fg",
      render: (result) => (result.judgement ? result.judgement.score.toFixed(2) : "—"),
    },
    {
      key: "duration",
      header: t("evaluation.column.duration"),
      align: "right",
      className: "whitespace-nowrap tabular-nums text-xs text-fg-muted",
      render: (result) => (result.duration_ms === null ? "—" : `${(result.duration_ms / 1000).toFixed(1)} 秒`),
    },
  ];
  return (
    <PagedDataTable<EvaluationCaseResult>
      pageKey="evaluationResults"
      rows={results}
      columns={columns}
      getRowKey={(result) => result.case.id}
      ariaLabel={t("evaluation.results.label")}
      tableClassName="w-full min-w-[640px]"
      resetKey={jobId}
    />
  );
}

function caseStatusKey(result: EvaluationCaseResult): string {
  return result.status === "judged" && result.judgement ? result.judgement.verdict : result.status;
}

function CaseBadge({ result }: { result: EvaluationCaseResult }) {
  const key = caseStatusKey(result);
  return <StatusBadge variant={caseVariant(key)} label={t(`evaluation.status.${key}` as I18nKey)} />;
}

function caseVariant(key: string): StatusVariant {
  if (key === "correct") return "success";
  if (key === "incorrect") return "danger";
  if (key === "uncertain" || key === "needs_approval" || key === "timed_out") return "warning";
  if (key === "running" || key === "pending") return "pending";
  if (key === "run_failed" || key === "judge_failed") return "danger";
  return "neutral";
}

function jobStatusVariant(status: EvaluationJobStatus): StatusVariant {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "cancelled") return "neutral";
  return "pending";
}

function CaseDetail({ result }: { result: EvaluationCaseResult }) {
  const judgement = result.judgement;
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <CaseBadge result={result} />
        {judgement ? (
          <span className="tabular-nums text-fg">{`${t("evaluation.column.score")} ${judgement.score.toFixed(2)}`}</span>
        ) : null}
      </div>
      <DetailSection title={t("evaluation.detail.question")}>
        <p className="whitespace-pre-wrap break-words rounded-md bg-accent-subtle px-3 py-2 text-fg">{result.case.question}</p>
      </DetailSection>
      <DetailSection title={t("evaluation.detail.expected")}>
        <p className="whitespace-pre-wrap break-words text-fg">{result.case.expected}</p>
      </DetailSection>
      <DetailSection title={t("evaluation.detail.answer")}>
        {result.answer ? (
          <div className="rounded-md border border-border p-3">
            <MessageText text={result.answer} className="text-sm text-fg" />
          </div>
        ) : (
          <p className="text-fg-muted">{t("evaluation.detail.noAnswer")}</p>
        )}
      </DetailSection>
      {judgement ? (
        <>
          <DetailSection title={t("evaluation.detail.reason")}>
            <p className="whitespace-pre-wrap break-words text-fg">{judgement.summary}</p>
          </DetailSection>
          {judgement.missing_points.length ? (
            <DetailSection title={t("evaluation.detail.missing")}>
              <ul className="list-disc space-y-1 pl-5 text-fg">
                {judgement.missing_points.map((point) => (
                  <li key={point} className="break-words">
                    {point}
                  </li>
                ))}
              </ul>
            </DetailSection>
          ) : null}
        </>
      ) : null}
      {result.error ? (
        <DetailSection title={t("evaluation.detail.error")}>
          <p className="break-words text-fg">{result.error}</p>
        </DetailSection>
      ) : null}
      {result.run_id ? (
        <DetailSection title={t("evaluation.detail.run")}>
          <p className="break-all font-mono text-xs text-fg">{result.run_id}</p>
        </DetailSection>
      ) : null}
    </div>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1">
      <h3 className="text-xs font-semibold text-fg-muted">{title}</h3>
      {children}
    </section>
  );
}

function JobsTable({
  jobs,
  shownJobId,
  onShow,
  onDelete,
}: {
  jobs: EvaluationJobItem[];
  shownJobId: string | null;
  onShow: (item: EvaluationJobItem) => void;
  onDelete: (item: EvaluationJobItem) => void;
}) {
  const columns: DataTableColumn<EvaluationJobItem>[] = [
    {
      key: "started",
      header: t("evaluation.jobs.startedAt"),
      rowHeader: true,
      className: "whitespace-nowrap",
      render: (item) => (
        <RowTitleButton
          title={formatDateTime(item.created_at)}
          subtitle={item.id === shownJobId ? t("evaluation.jobs.showing") : undefined}
          onClick={() => onShow(item)}
        />
      ),
    },
    { key: "agent", header: t("evaluation.form.agent"), className: "text-xs text-fg", render: (item) => item.agent_name || item.agent_id },
    {
      key: "status",
      header: t("evaluation.jobs.status"),
      render: (item) => (
        <StatusBadge variant={jobStatusVariant(item.status)} label={t(`evaluation.jobStatus.${item.status}` as I18nKey)} />
      ),
    },
    {
      key: "passRate",
      header: t("evaluation.summary.passRate"),
      align: "right",
      className: "tabular-nums text-fg",
      render: (item) => formatRate(item.summary.pass_rate),
    },
    {
      key: "cases",
      header: t("evaluation.jobs.cases"),
      align: "right",
      className: "tabular-nums text-fg",
      render: (item) => formatNumber(item.summary.total),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (item) => {
        const actions: EntityAction[] = [
          { id: "show", label: t("evaluation.jobs.show"), icon: Eye, onSelect: () => onShow(item) },
          {
            id: "delete",
            label: t("evaluation.jobs.delete"),
            icon: Trash2,
            tone: "danger",
            disabled: ACTIVE.has(item.status),
            onSelect: () => onDelete(item),
          },
        ];
        return (
          <RowActionMenu
            actions={actions}
            ariaLabel={t("common.entityActions", { name: formatDateTime(item.created_at) })}
            testId={`evaluation-row-actions-${item.id}`}
          />
        );
      },
    },
  ];
  return (
    <PagedDataTable<EvaluationJobItem>
      pageKey="evaluationJobs"
      rows={jobs}
      columns={columns}
      getRowKey={(item) => item.id}
      ariaLabel={t("evaluation.jobs.label")}
      tableClassName="w-full min-w-[640px]"
    />
  );
}

function formatRate(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}
