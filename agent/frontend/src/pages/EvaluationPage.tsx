import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, Download, Eye, FlaskConical, Pencil, Plus, Trash2 } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  Disclosure,
  EmptyState,
  ErrorState,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ListSkeleton,
  MessageText,
  offsetForPage,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  RowActionMenu,
  RowTitleButton,
  SelectField,
  SideSheet,
  StatusBadge,
  TableSkeleton,
  TimedLoadingState,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import { MissingEditorTarget } from "@/components/EntityLayout";
import { listScrollLabel, PagedDataTable, ServerPagination, usePersistedPage } from "@/components/ListViews";
import { EvaluationSetEditor, downloadBlob } from "@/components/evaluation/EvaluationSetEditor";
import {
  EvaluationVersionField,
  evaluatedVersionLabel,
  usableEvaluationTarget,
} from "@/components/evaluation/EvaluationVersionField";
import {
  agentApi,
  type AgentProfile,
  type EvaluationCaseResult,
  type EvaluationJob,
  type EvaluationJobItem,
  type EvaluationJobStatus,
  type EvaluationSetItem,
  type EvaluationSummary,
  type EvaluationTarget,
} from "@/lib/api";
import { useEditorRoute } from "@/lib/editor-route";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { isNullableString, isString, useWorkspaceState } from "@/lib/workspace-state";
import { NonPersistentStorageNotice } from "@/components/system/StorageNotice";

// 品質評価（#776）。業務 Agent ごとの評価セット（評価ケースの集まり）で業務 Agent を実行し、
// 期待する回答の要点と比べて合否を判定する。評価の Run は承認が要るツールを実行しない（dry-run）で、
// 期待するツールを指定したケースはツールの選択も判定する。画面の形は RAG の品質評価・NL2SQL の
// SQL生成評価にそろえる（評価セット → 実行状況 → 評価概要（前回との比較）→ ケース別結果 → 最近の評価）。

const POLL_INTERVAL_MS = 1500;
// 最近の評価の 1 ページ（サーバー側のページング。終わった評価は 365 日残る。#794）。
const JOBS_PAGE_SIZE = 10;
const ACTIVE: ReadonlySet<EvaluationJobStatus> = new Set(["queued", "running"]);

export function EvaluationPage() {
  const queryClient = useQueryClient();
  const editor = useEditorRoute();
  const [agentId, setAgentId] = useWorkspaceState("evaluation", "agentId", "", isString);
  const [jobId, setJobId] = useWorkspaceState<"evaluation", string | null>("evaluation", "jobId", null, isNullableString);

  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const usableAgents = useMemo(
    () => (agents.data?.agents ?? []).filter((agent) => agent.enabled && !agent.migration_required),
    [agents.data]
  );
  const selectedAgentId = usableAgents.some((agent) => agent.id === agentId) ? agentId : (usableAgents[0]?.id ?? "");
  const selectedAgent = usableAgents.find((agent) => agent.id === selectedAgentId);
  // 評価する版（#810）。選んだ業務 Agent ごとの選択で、業務 Agent を変えたら既定に戻す。
  // 実行の意思なので作業状態には残さない（workspace-state.md）。
  const [versionChoice, setVersionChoice] = useState<{ agentId: string; value: EvaluationTarget } | null>(null);
  const target = usableEvaluationTarget(
    selectedAgent,
    versionChoice?.agentId === selectedAgentId ? versionChoice.value : null
  );

  const start = useMutation({
    mutationFn: ({ setId, version }: { setId: string; version: EvaluationTarget }) =>
      agentApi.createEvaluation({ set_id: setId, agent_version: version }),
    onSuccess: (created) => {
      setJobId(created.id);
      queryClient.setQueryData(["evaluation", created.id], created);
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      void queryClient.invalidateQueries({ queryKey: ["evaluation-sets"] });
      toast.success(t("evaluation.form.started"));
      editor.backToList();
    },
    onError: (error) => toast.error(error.message),
  });

  const editorTarget = editor.target;
  const editingId = editorTarget.kind === "edit" ? editorTarget.id : null;
  const editingSet = useQuery({
    queryKey: ["evaluation-set", editingId],
    queryFn: () => agentApi.getEvaluationSet(editingId ?? ""),
    enabled: Boolean(editingId),
    retry: false,
  });

  if (editorTarget.kind === "new") {
    return (
      <EvaluationSetEditor
        agents={usableAgents}
        defaultAgentId={selectedAgentId}
        onBack={() => editor.backToList()}
        onSaved={(saved) => {
          setAgentId(saved.agent_id);
          editor.openItem(saved.id, { replace: true });
        }}
        onStart={(set, version) => start.mutate({ setId: set.id, version })}
        onDeleted={() => editor.backToList({ replace: true })}
        starting={start.isPending}
      />
    );
  }
  if (editorTarget.kind === "edit") {
    if (editingSet.isLoading) {
      return (
        <PageBody wide>
          <TimedLoadingState label={t("loading.evaluationSet")} testId="evaluation-set-loading">
            <ListSkeleton rows={4} />
          </TimedLoadingState>
        </PageBody>
      );
    }
    if (!editingSet.data) {
      return <MissingEditorTarget id={editorTarget.id} onBack={() => editor.backToList({ replace: true })} />;
    }
    return (
      <EvaluationSetEditor
        key={editingSet.data.id}
        evaluationSet={editingSet.data}
        agents={usableAgents}
        defaultAgentId={selectedAgentId}
        onBack={() => editor.backToList()}
        onSaved={(saved) => queryClient.setQueryData(["evaluation-set", saved.id], saved)}
        onStart={(set, version) => start.mutate({ setId: set.id, version })}
        onDeleted={() => editor.backToList({ replace: true })}
        starting={start.isPending}
      />
    );
  }
  return (
    <EvaluationOverview
      agentId={selectedAgentId}
      agent={selectedAgent}
      version={target}
      onVersionChange={(value) => setVersionChoice({ agentId: selectedAgentId, value })}
      onAgentChange={setAgentId}
      agentOptions={usableAgents.map((agent) => ({ value: agent.id, label: agent.name }))}
      jobId={jobId}
      onJobChange={setJobId}
      onCreate={() => editor.openNew()}
      onOpen={(set) => editor.openItem(set.id)}
      itemHref={editor.itemHref}
      onStart={(set) => start.mutate({ setId: set.id, version: target })}
      starting={start.isPending}
    />
  );
}

function EvaluationOverview({
  agentId,
  agent,
  version,
  onVersionChange,
  onAgentChange,
  agentOptions,
  jobId,
  onJobChange,
  onCreate,
  onOpen,
  itemHref,
  onStart,
  starting,
}: {
  agentId: string;
  agent: AgentProfile | undefined;
  version: EvaluationTarget;
  onVersionChange: (version: EvaluationTarget) => void;
  onAgentChange: (agentId: string) => void;
  agentOptions: { value: string; label: string }[];
  jobId: string | null;
  onJobChange: (jobId: string | null) => void;
  onCreate: () => void;
  onOpen: (set: EvaluationSetItem) => void;
  itemHref: (id: string) => string;
  onStart: (set: EvaluationSetItem) => void;
  starting: boolean;
}) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [selectedCase, setSelectedCase] = useState<EvaluationCaseResult | null>(null);

  const sets = useQuery({
    queryKey: ["evaluation-sets", agentId],
    queryFn: () => agentApi.listEvaluationSets(agentId),
    enabled: Boolean(agentId),
  });
  const [jobsPage, setJobsPage] = usePersistedPage("evaluationJobs");
  // 最新の評価（1 ページ目）。表示する評価の既定と、実行中の評価の有無に使う。
  // 一覧が 1 ページ目のときは同じ query key なので 1 回だけ取得する。
  const latest = useQuery({
    queryKey: ["evaluations", 1],
    queryFn: () => agentApi.listEvaluations({ offset: 0, limit: JOBS_PAGE_SIZE }),
    // 表示していない実行中の評価（前の評価を表示している間の最新の評価）は、ここで終わりを待つ。
    // 表示中の評価は `job` が取り直す（#965）。
    refetchInterval: (query) => {
      const items = query.state.data?.jobs ?? [];
      const shown = jobId ?? items[0]?.id;
      return items.some((item) => ACTIVE.has(item.status) && item.id !== shown) ? POLL_INTERVAL_MS : false;
    },
  });
  const jobs = useQuery({
    queryKey: ["evaluations", jobsPage],
    queryFn: () => agentApi.listEvaluations({ offset: offsetForPage(jobsPage, JOBS_PAGE_SIZE), limit: JOBS_PAGE_SIZE }),
    // ページを送っている間は今のページを出したまま取り直す。
    placeholderData: keepPreviousData,
  });
  // 表示する評価は、選んだ評価か、無ければ最新の評価。
  const shownJobId = jobId ?? latest.data?.jobs[0]?.id ?? null;
  const job = useQuery({
    queryKey: ["evaluation", shownJobId],
    queryFn: () => agentApi.getEvaluation(shownJobId ?? ""),
    enabled: Boolean(shownJobId),
    retry: false,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data && ACTIVE.has(query.state.data.status) ? POLL_INTERVAL_MS : false),
  });
  useEffect(() => {
    if (jobId && job.isError) onJobChange(null);
  }, [jobId, job.isError, onJobChange]);
  // 実行中の評価が終わったら、評価セットと最近の評価を取り直す。
  const shownStatus = job.data?.status;
  useEffect(() => {
    if (shownStatus && !ACTIVE.has(shownStatus)) {
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      void queryClient.invalidateQueries({ queryKey: ["evaluation-sets"] });
    }
  }, [shownStatus, queryClient]);
  // 最新の一覧の実行中の評価が終わったら、評価セット（前回の結果）と最近の評価の他のページも取り直す（#965）。
  const latestActive = (latest.data?.jobs ?? []).some((item) => ACTIVE.has(item.status));
  const wasLatestActive = useRef(latestActive);
  useEffect(() => {
    if (wasLatestActive.current && !latestActive) {
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      void queryClient.invalidateQueries({ queryKey: ["evaluation-sets"] });
    }
    wasLatestActive.current = latestActive;
  }, [latestActive, queryClient]);
  const running = latestActive || (job.data ? ACTIVE.has(job.data.status) : false);

  const cancel = useMutation({
    mutationFn: (id: string) => agentApi.cancelEvaluation(id),
    onSuccess: (cancelled) => {
      queryClient.setQueryData(["evaluation", cancelled.id], cancelled);
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      toast.success(t("evaluation.progress.cancelled"));
    },
    onError: (error) => toast.error(error.message),
  });
  const removeJob = useMutation({
    mutationFn: (id: string) => agentApi.deleteEvaluation(id),
    onSuccess: (_data, id) => {
      if (jobId === id) onJobChange(null);
      queryClient.removeQueries({ queryKey: ["evaluation", id] });
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      toast.success(t("evaluation.jobs.deleted"));
    },
    onError: (error) => toast.error(error.message),
  });
  // 業種テンプレートの評価ケースで評価セットを作る（評価セットの無い、テンプレートから作った業務 Agent。#810）。
  const fromTemplate = useMutation({
    mutationFn: (id: string) => agentApi.createEvaluationSetFromTemplate(id),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["evaluation-sets"] });
      toast.success(t("evaluation.sets.fromTemplateCreated", { name: created.name }));
    },
    onError: (error) => toast.error(error.message),
  });
  const removeSet = useMutation({
    mutationFn: (id: string) => agentApi.deleteEvaluationSet(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["evaluation-sets"] });
      toast.success(t("evaluation.set.deleted"));
    },
    onError: (error) => toast.error(error.message),
  });

  async function confirmDeleteJob(item: EvaluationJobItem) {
    const ok = await confirm({
      title: t("evaluation.jobs.deleteTitle"),
      description: t("evaluation.jobs.deleteDescription", {
        agent: item.set_name || item.agent_name || item.agent_id,
        started: formatDateTime(item.created_at),
      }),
      confirmLabel: t("evaluation.jobs.delete"),
      tone: "danger",
    });
    if (ok) removeJob.mutate(item.id);
  }

  async function confirmDeleteSet(item: EvaluationSetItem) {
    const ok = await confirm({
      title: t("evaluation.set.deleteTitle", { name: item.name }),
      description: t("evaluation.set.deleteDescription"),
      confirmLabel: t("evaluation.set.delete"),
      tone: "danger",
    });
    if (ok) removeSet.mutate(item.id);
  }

  async function exportSet(item: EvaluationSetItem) {
    try {
      downloadBlob(await agentApi.downloadEvaluationSetXlsx(item.id), `${item.name}.xlsx`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <>
      <PageHeader
        wide
        title={t("nav.evaluation")}
        subtitle={t("page.evaluation.subtitle")}
        actions={[
          {
            id: "create",
            kind: "primary",
            label: t("evaluation.set.create"),
            icon: Plus,
            disabled: !agentId,
            onClick: onCreate,
          },
        ]}
      />
      <PageBody wide>
        <NonPersistentStorageNotice />
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("evaluation.sets.title")}</CardTitle>
            <CardDescription>{t("evaluation.sets.description")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
              <SelectField<string>
                id="evaluation-agent"
                label={t("evaluation.form.agent")}
                width="md"
                value={agentId}
                options={agentOptions}
                onValueChange={onAgentChange}
                disabled={agentOptions.length === 0}
              />
              <EvaluationVersionField id="evaluation-version" agent={agent} value={version} onChange={onVersionChange} />
            </div>
            {sets.isLoading ? (
              <TimedLoadingState label={t("loading.evaluationSets")} testId="evaluation-sets-loading">
                <TableSkeleton columns={4} />
              </TimedLoadingState>
            ) : sets.error ? (
              // 取得の失敗を「評価セットがありません」と出さない（messaging.md §3.6。#818）。
              <ErrorState message={sets.error.message} retryLabel={t("common.retry")} onRetry={() => void sets.refetch()} />
            ) : (sets.data?.sets ?? []).length === 0 ? (
              <EmptyState
                title={t("evaluation.sets.empty")}
                hint={agent?.template_id ? t("evaluation.sets.fromTemplateHint") : t("evaluation.sets.emptyHint")}
                action={
                  <div className="flex flex-wrap justify-center gap-2">
                    {agent?.template_id ? (
                      <Button
                        icon={ClipboardList}
                        loading={fromTemplate.isPending}
                        onClick={() => fromTemplate.mutate(agent.id)}
                        data-testid="evaluation-sets-from-template"
                      >
                        {t("evaluation.sets.fromTemplate")}
                      </Button>
                    ) : null}
                    <Button variant="secondary" icon={Plus} onClick={onCreate} disabled={!agentId}>
                      {t("evaluation.set.create")}
                    </Button>
                  </div>
                }
              />
            ) : (
              <SetsTable
                sets={sets.data?.sets ?? []}
                running={running || starting}
                itemHref={itemHref}
                onOpen={onOpen}
                onStart={onStart}
                onExport={(item) => void exportSet(item)}
                onDelete={(item) => void confirmDeleteSet(item)}
              />
            )}
            {running ? <p className="text-xs text-fg-muted">{t("evaluation.form.busy")}</p> : null}
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
            ) : jobs.error && !jobs.data ? (
              <ErrorState message={jobs.error.message} retryLabel={t("common.retry")} onRetry={() => void jobs.refetch()} />
            ) : !jobs.data || jobs.data.total === 0 ? (
              <EmptyState title={t("evaluation.jobs.empty")} hint={t("evaluation.jobs.emptyHint")} />
            ) : (
              <div className="grid min-w-0 gap-2">
                <JobsTable
                  jobs={jobs.data.jobs}
                  shownJobId={shownJobId}
                  onShow={(item) => onJobChange(item.id)}
                  onDelete={(item) => void confirmDeleteJob(item)}
                />
                <ServerPagination
                  offset={jobs.data.offset}
                  limit={jobs.data.limit}
                  total={jobs.data.total}
                  count={jobs.data.jobs.length}
                  page={jobsPage}
                  onPageChange={setJobsPage}
                  ariaLabel={t("evaluation.jobs.pagerLabel")}
                  testId="evaluation-jobs-pagination"
                />
              </div>
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

function SetsTable({
  sets,
  running,
  itemHref,
  onOpen,
  onStart,
  onExport,
  onDelete,
}: {
  sets: EvaluationSetItem[];
  running: boolean;
  itemHref: (id: string) => string;
  onOpen: (item: EvaluationSetItem) => void;
  onStart: (item: EvaluationSetItem) => void;
  onExport: (item: EvaluationSetItem) => void;
  onDelete: (item: EvaluationSetItem) => void;
}) {
  const columns: DataTableColumn<EvaluationSetItem>[] = [
    {
      key: "name",
      header: t("evaluation.set.name"),
      rowHeader: true,
      className: "min-w-56",
      render: (item) => (
        <RowTitleButton
          title={item.name}
          subtitle={item.description || undefined}
          href={itemHref(item.id)}
          onClick={() => onOpen(item)}
        />
      ),
    },
    {
      key: "cases",
      header: t("evaluation.jobs.cases"),
      align: "right",
      className: "tabular-nums text-fg",
      render: (item) => formatNumber(item.case_count),
    },
    {
      key: "last",
      header: t("evaluation.sets.last"),
      render: (item) =>
        item.last_job_status ? (
          <span className="inline-flex flex-wrap items-center gap-2">
            <StatusBadge
              variant={jobStatusVariant(item.last_job_status)}
              label={t(`evaluation.jobStatus.${item.last_job_status}` as I18nKey)}
            />
            <span className="text-xs tabular-nums text-fg">{formatRate(item.last_pass_rate)}</span>
          </span>
        ) : (
          <span className="text-xs text-fg-muted">{t("evaluation.sets.never")}</span>
        ),
    },
    {
      key: "updated",
      header: t("common.updatedAt"),
      className: "whitespace-nowrap text-xs tabular-nums text-fg-muted",
      render: (item) => formatDateTime(item.updated_at),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (item) => {
        const actions: EntityAction[] = [
          {
            id: "start",
            label: t("evaluation.form.start"),
            icon: FlaskConical,
            disabled: running,
            onSelect: () => onStart(item),
            testId: `evaluation-set-start-${item.id}`,
          },
          { id: "edit", label: t("evaluation.set.edit"), icon: Pencil, onSelect: () => onOpen(item) },
          { id: "export", label: t("evaluation.set.export"), icon: Download, onSelect: () => onExport(item) },
          { id: "delete", label: t("evaluation.set.delete"), icon: Trash2, tone: "danger", onSelect: () => onDelete(item) },
        ];
        return (
          <RowActionMenu
            actions={actions}
            ariaLabel={t("common.entityActions", { name: item.name })}
            testId={`evaluation-set-row-actions-${item.id}`}
          />
        );
      },
    },
  ];
  return (
    <PagedDataTable<EvaluationSetItem>
      pageKey="evaluationSets"
      rows={sets}
      columns={columns}
      getRowKey={(item) => item.id}
      ariaLabel={t("evaluation.sets.label")}
      tableClassName="w-full min-w-[51rem]"
    />
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
                      agent: job.set_name || job.agent_name || job.agent_id,
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
              set: job.set_name || "—",
              agent: job.agent_name || job.agent_id,
              version: evaluatedVersionLabel(job.agent_version),
              started: formatDateTime(job.created_at),
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {job.status === "failed" ? (
            // 1 文目に何が起きたかと対処、サーバーのエラーの本文は「詳細」に畳む（失敗なので開いて出す。messaging.md §10.3）。
            <Banner severity="danger" title={t("evaluation.summary.failed")}>
              <div className="min-w-0 space-y-2">
                <p>{t("evaluation.summary.failedHint")}</p>
                {job.error ? (
                  <Disclosure variant="plain" size="sm" summary={t("evaluation.summary.failedDetails")} defaultOpen>
                    <p className="break-words text-xs text-fg-muted [overflow-wrap:anywhere]">{job.error}</p>
                  </Disclosure>
                ) : null}
              </div>
            </Banner>
          ) : null}
          <SummaryMetrics summary={summary} previous={job.previous_summary} />
          <p className="text-xs text-fg-muted" data-testid="evaluation-previous">
            {/* どの版どうしを比べたかを出す（同じ評価ケースの前回とだけ比べる。#810）。 */}
            {job.previous_summary
              ? t("evaluation.summary.comparedWithPrevious", {
                  version: evaluatedVersionLabel(job.previous_agent_version),
                  started: job.previous_created_at ? formatDateTime(job.previous_created_at) : "—",
                })
              : t("evaluation.summary.noPrevious")}
          </p>
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

function SummaryMetrics({ summary, previous }: { summary: EvaluationSummary; previous: EvaluationSummary | null }) {
  const ofTotal = t("evaluation.summary.ofTotal", { total: formatNumber(summary.total) });
  return (
    // 統計のタイルはカードの幅を等分する（6 個なので 2 / 3 列。design-system README §4「wide 画面の 100% 充填」）。
    <div className="grid grid-cols-2 gap-x-5 gap-y-4 xl:grid-cols-3" data-testid="evaluation-summary">
      <Metric
        label={t("evaluation.summary.passRate")}
        value={formatRate(summary.pass_rate)}
        detail={previous ? rateDelta(summary.pass_rate, previous.pass_rate) : ofTotal}
      />
      <Metric
        label={t("evaluation.summary.averageScore")}
        value={summary.average_score === null ? "—" : summary.average_score.toFixed(2)}
        detail={previous ? scoreDelta(summary.average_score, previous.average_score) : t("evaluation.summary.scoreRange")}
      />
      <Metric
        label={t("evaluation.summary.toolAccuracy")}
        value={formatRate(summary.tool_accuracy)}
        detail={
          summary.tool_cases
            ? t("evaluation.summary.toolCases", { correct: summary.tool_correct, total: summary.tool_cases })
            : t("evaluation.summary.noToolCases")
        }
      />
      <Metric label={t("evaluation.summary.correct")} value={formatNumber(summary.correct)} detail={ofTotal} />
      <Metric
        label={t("evaluation.summary.incorrect")}
        value={formatNumber(summary.incorrect + summary.uncertain)}
        detail={t("evaluation.summary.uncertainIncluded", { count: summary.uncertain })}
      />
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
    { key: "verdict", header: t("evaluation.column.verdict"), render: (result) => <CaseBadge result={result} /> },
    {
      key: "score",
      header: t("evaluation.column.score"),
      align: "right",
      className: "tabular-nums text-fg",
      render: (result) => (result.judgement ? result.judgement.score.toFixed(2) : "—"),
    },
    {
      key: "tools",
      header: t("evaluation.column.tools"),
      className: "max-w-64 text-xs",
      render: (result) => <ToolSelection result={result} />,
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
      tableClassName="w-full min-w-[54rem]"
      resetKey={jobId}
    />
  );
}

function ToolSelection({ result }: { result: EvaluationCaseResult }) {
  return (
    <span className="flex flex-col items-start gap-1">
      {result.tool_selection_correct === null ? null : (
        <StatusBadge
          variant={result.tool_selection_correct ? "success" : "danger"}
          label={result.tool_selection_correct ? t("evaluation.tools.correct") : t("evaluation.tools.incorrect")}
        />
      )}
      <span className="break-all font-mono text-fg-muted">
        {result.tool_calls.length ? result.tool_calls.join(", ") : t("evaluation.tools.none")}
      </span>
    </span>
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
      <DetailSection title={t("evaluation.detail.tools")}>
        <div className="space-y-1 text-xs">
          <p className="text-fg">
            <span className="text-fg-muted">{`${t("evaluation.detail.expectedTools")}: `}</span>
            <span className="font-mono">{result.case.expected_tools?.length ? result.case.expected_tools.join(", ") : "—"}</span>
          </p>
          <p className="text-fg">
            <span className="text-fg-muted">{`${t("evaluation.detail.calledTools")}: `}</span>
            <span className="font-mono">{result.tool_calls.length ? result.tool_calls.join(", ") : "—"}</span>
          </p>
          <p className="text-fg-muted">{t("evaluation.detail.dryRunNote")}</p>
        </div>
      </DetailSection>
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
    { key: "set", header: t("evaluation.jobs.set"), className: "text-xs text-fg", render: (item) => item.set_name || "—" },
    {
      key: "version",
      header: t("evaluation.version.column"),
      className: "whitespace-nowrap text-xs tabular-nums text-fg",
      render: (item) => evaluatedVersionLabel(item.agent_version),
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
    <DataTable<EvaluationJobItem>
      rows={jobs}
      columns={columns}
      getRowKey={(item) => item.id}
      rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
      ariaLabel={t("evaluation.jobs.label")}
      scrollAriaLabel={listScrollLabel(t("evaluation.jobs.label"))}
      tableClassName="w-full min-w-[51rem]"
      stickyHeader
      visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
    />
  );
}

function formatRate(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function rateDelta(current: number | null, previous: number | null): string {
  if (current === null || previous === null) return t("evaluation.summary.previousUnknown");
  const delta = Math.round((current - previous) * 100);
  if (delta === 0) return t("evaluation.summary.previousSame");
  return t("evaluation.summary.previousPoints", { value: `${delta > 0 ? "+" : "−"}${Math.abs(delta)}` });
}

function scoreDelta(current: number | null, previous: number | null): string {
  if (current === null || previous === null) return t("evaluation.summary.previousUnknown");
  const delta = current - previous;
  if (Math.abs(delta) < 0.005) return t("evaluation.summary.previousSame");
  return t("evaluation.summary.previousScore", { value: `${delta > 0 ? "+" : "−"}${Math.abs(delta).toFixed(2)}` });
}
