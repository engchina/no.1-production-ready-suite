import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, PlayCircle, RefreshCw, X } from "lucide-react";
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
  FormSkeleton,
  ListToolbar,
  TableSkeleton,
  PageHeader,
  RowActionMenu,
  StatusBadge,
  Switch,
  ToggleChip,
  toast,
  useConfirm,
  type DataTableColumn,
  type EntityAction,
  PageBody,
  RowTitleButton,
  isSubmitEnter,
  SelectField,
  TextareaField,
  useActionPending,
} from "@engchina/production-ready-ui";
import { agentApi, type ApprovalRequest, type RunState } from "@/lib/api";
import { AgentSplitPane } from "@/components/EntityLayout";
import { PagedDataTable, QueryState } from "@/components/ListViews";
import {
  FilterChipGroup,
  ListSearchField,
  listCountLabel,
  matchesSearch,
  NoMatchState,
  useListSearch,
} from "@/components/ListFilters";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { runStatusView } from "@/lib/status-labels";
import { useEditorLeaveGuard } from "@/lib/leave-guard";
import {
  isNullableString,
  isOneOf,
  isString,
  useRestoredSelectionCheck,
  useWorkspaceState,
} from "@/lib/workspace-state";
import { RunDetail, runCapabilities } from "@/pages/runs/RunDetail";
import {
  type RunCommandKind,
  type RunStreamMode,
  useRunEventSource,
  useRunEventWebSocket,
} from "@/pages/runs/run-event-stream";
import { focusField, formatDate } from "@/pages/shared/page-helpers";

const DEFAULT_RUN_GOAL = t("run.form.goalDefault");

/** 実行履歴の状態の絞り込み（「実行中・待機中」は queued と running をまとめる）。 */
const RUN_STATUS_FILTERS = ["all", "active", "waiting_approval", "completed", "failed", "cancelled"] as const;

type RunStatusFilter = (typeof RUN_STATUS_FILTERS)[number];

const isRunStatusFilter = isOneOf<RunStatusFilter>(RUN_STATUS_FILTERS);

function runMatchesStatusFilter(run: RunState, filter: RunStatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "active") return run.status === "queued" || run.status === "running";
  return run.status === filter;
}

function runStatusFilterLabel(filter: RunStatusFilter): string {
  if (filter === "all") return t("run.filter.all");
  if (filter === "active") return t("run.filter.active");
  return runStatusView(filter).label;
}

export function RunsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const capabilities = useCapabilities();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: agentApi.listRuns,
    refetchInterval: 5000,
  });
  // 「表示を更新」は押した取り直しの間だけ回す（定期の取り直し・他の操作の後の invalidate・条件の切り替えでは回さない。#819）。
  const manualRefresh = useActionPending();
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  // 組み込み Runtime が実行できるか（モデル未設定なら Run は失敗するため、作成の前に知らせる。#754）。
  const runtimeStatus = useQuery({ queryKey: ["runtime-status"], queryFn: agentApi.getRuntimeStatus });
  const createRun = useMutation({
    mutationFn: agentApi.createRun,
    onSuccess: (run) => {
      toast.success(t("run.createdToast"));
      setSelectedRunId(run.id);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const refreshRunQueries = () => {
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
  };
  // 行メニュー・詳細の操作で、結果を出す固定の面が無いため、失敗は danger の Toast で返す（messaging.md §1）。
  const cancelRun = useMutation({
    mutationFn: agentApi.cancelRun,
    onSuccess: refreshRunQueries,
    onError: (error) => toast.error(t("run.cancelFailed"), { description: error.message }),
  });
  const resumeRun = useMutation({
    mutationFn: agentApi.resumeRun,
    onSuccess: refreshRunQueries,
    onError: (error) => toast.error(t("run.resumeFailed"), { description: error.message }),
  });
  const replayRun = useMutation({
    mutationFn: agentApi.replayRun,
    onSuccess: () => {
      toast.success(t("run.createdToast"));
      refreshRunQueries();
    },
    onError: (error) => toast.error(t("run.replayFailed"), { description: error.message }),
  });
  const decideApproval = useMutation({
    mutationFn: ({ approval, approved }: { approval: ApprovalRequest; approved: boolean }) =>
      // 決定者はログイン中の利用者から server が決める（#215）。
      agentApi.decideApproval(approval.id, { approved }),
    onSuccess: (_data, { approved }) => {
      toast.success(approved ? t("approval.decided") : t("approval.rejected"));
      refreshRunQueries();
    },
    onError: (error) => toast.error(t("run.decideFailed"), { description: error.message }),
  });
  // 作業状態（目標の下書き・選択中の Run・購読方式）はこのタブの sessionStorage に残す（#87）。
  // Agent は実行条件なので残さず、戻るたびに選び直す（実行の意思は確認し直す）。
  const [goal, setGoal, goalSaved] = useWorkspaceState("runs", "goal", DEFAULT_RUN_GOAL, isString);
  const [agentId, setAgentId] = useState("default");
  const [goalError, setGoalError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useWorkspaceState(
    "runs",
    "selectedRunId",
    null as string | null,
    isNullableString
  );
  const [streamMode, setStreamMode] = useWorkspaceState(
    "runs",
    "streamMode",
    "sse" as RunStreamMode,
    isOneOf<RunStreamMode>(["sse", "websocket"])
  );
  // 一時保存に失敗した下書きは、離脱の前に破棄を確認する。
  useEditorLeaveGuard(!goalSaved && goal !== DEFAULT_RUN_GOAL);

  const runItems = useMemo(() => runs.data?.runs ?? [], [runs.data?.runs]);
  const agentNames = useMemo(
    () => new Map((agents.data?.agents ?? []).map((agent) => [agent.id, agent.name])),
    [agents.data?.agents]
  );
  const agentNameOf = (agentId: string) => agentNames.get(agentId) ?? agentId;
  // 実行履歴の絞り込み（目標・実行 ID・業務 Agent の検索と、状態のチップ。#808）。どちらも作業状態に残す。
  const [runQuery, setRunQuery] = useListSearch("runs");
  const [runFilter, setRunFilter] = useWorkspaceState("listFilter", "runs", "all" as RunStatusFilter, isRunStatusFilter);
  const visibleRuns = runItems.filter(
    (run) =>
      runMatchesStatusFilter(run, runFilter) &&
      matchesSearch(runQuery, [run.goal, run.id, run.agent_id, agentNames.get(run.agent_id)])
  );
  const runIds = useMemo(() => runs.data?.runs.map((run) => run.id), [runs.data?.runs]);
  const restoredSelection = useRestoredSelectionCheck(selectedRunId, runIds);
  // 詳細は絞り込んだ一覧の中から選ぶ（絞り込みで隠れた実行を詳細に出したままにしない）。
  const selectedRun = visibleRuns.find((run) => run.id === selectedRunId) ?? visibleRuns[0];
  // 利用できるエージェントは backend が絞り込む。既定の Agent を使えない利用者は、使える最初の Agent を選ぶ（#215）。
  // 下書きで実行するのは Agent 管理（admin）だけ（#770）。下書きでなければ公開した版のある Agent だけ選べる。
  const [runDraft, setRunDraft] = useState(false);
  const draftRun = capabilities.admin && runDraft;
  const runnableAgents = (agents.data?.agents ?? []).filter(
    (agent) => agent.enabled && (draftRun || agent.published_version !== null)
  );
  const selectedAgentId =
    runnableAgents.some((agent) => agent.id === agentId) || !runnableAgents.length ? agentId : runnableAgents[0].id;

  useEffect(() => {
    if (!selectedRunId && runItems.length) {
      setSelectedRunId(runItems[0].id);
    }
    if (selectedRunId && runItems.length && !runItems.some((run) => run.id === selectedRunId)) {
      setSelectedRunId(runItems[0].id);
    }
  }, [runItems, selectedRunId, setSelectedRunId]);

  const refreshRuntimeEvents = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
  }, [queryClient]);
  // WebSocket のコマンドの拒否も、REST の操作の失敗と同じく danger の Toast で返す（messaging.md §1）。
  const onWebSocketCommandError = useCallback(
    (reason: string) => toast.error(t("run.stream.commandFailed"), { description: reason }),
    []
  );
  const websocketState = useRunEventWebSocket(
    selectedRun,
    streamMode === "websocket",
    refreshRuntimeEvents,
    onWebSocketCommandError
  );

  const sseState = useRunEventSource(selectedRun, streamMode === "sse", refreshRuntimeEvents);

  function onAgentChange(value: string) {
    setAgentId(value);
  }

  function submitRun() {
    // ゴールは backend（RunCreateRequest）でも必須（#540）。未入力は欄の下に出し、画面の並び順で最初の欄へ移す。
    const nextGoalError = goal.trim() ? null : t("run.goalRequired");
    setGoalError(nextGoalError);
    if (nextGoalError) {
      focusField("run-goal");
      return;
    }
    createRun.mutate({ goal, agent_id: selectedAgentId, ...(draftRun ? { draft: true } : {}) });
  }

  /**
   * 送る経路は handler の中で選ぶ（buttons.md §5.1。操作のボタンは ObjectActionBar / RowActionMenu の 1 か所）。
   * 詳細で WebSocket の購読が接続済みのときはその接続でコマンドを送り、それ以外は REST を呼ぶ。
   */
  const viaWebSocket = (run: RunState) =>
    streamMode === "websocket" && websocketState.status === "open" && run.id === selectedRun?.id;

  async function cancelLatestRun(run: RunState) {
    const confirmed = await confirm({
      title: t("run.cancelTitle"),
      description: run.id,
      confirmLabel: t("run.cancelConfirm"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!confirmed) return;
    if (viaWebSocket(run)) {
      websocketState.sendCancel();
    } else {
      cancelRun.mutate(run.id);
    }
  }

  function resumeLatestRun(run: RunState) {
    if (viaWebSocket(run)) {
      websocketState.sendResume();
    } else {
      resumeRun.mutate(run.id);
    }
  }

  async function decideRunApproval(run: RunState, approval: ApprovalRequest, approved: boolean) {
    const ok = await confirm({
      title: approved ? t("run.approveTitle") : t("run.rejectTitle"),
      description: approval.tool_call.name,
      confirmLabel: approved ? t("common.approve") : t("common.reject"),
      cancelLabel: t("common.cancel"),
      tone: approved ? "info" : "danger",
    });
    if (!ok) return;
    if (viaWebSocket(run)) {
      websocketState.sendApprovalDecision(approval.id, approved);
    } else {
      decideApproval.mutate({ approval, approved });
    }
  }

  // 操作ごとに pending を分け、押した操作だけを loading、他は disabled にする（messaging.md §3.7）。
  const wsCommand = streamMode === "websocket" ? websocketState.pendingCommand : null;
  const pendingAction: RunCommandKind | "replay" | null = cancelRun.isPending
    ? "cancel"
    : resumeRun.isPending
      ? "resume"
      : replayRun.isPending
        ? "replay"
        : decideApproval.isPending
          ? decideApproval.variables?.approved
            ? "approve"
            : "reject"
          : wsCommand;
  // 一覧の行と詳細で同じ定義を使う（UX 契約 buttons.md §5.1）。取消・却下は確認してから送る。
  const runActions = (run: RunState): EntityAction[] => {
    const { canCancel, canResume } = runCapabilities(run);
    const pendingApproval = run.approvals.find((approval) => approval.status === "pending");
    // 実行中の操作が対象の Run のものか（別の Run の行のボタンを回さない）。variables は完了後も前の値が
    // 残るため、処理中の mutation の variables だけを読む（#819）。
    const pendingRunId = cancelRun.isPending
      ? cancelRun.variables
      : resumeRun.isPending
        ? resumeRun.variables
        : replayRun.isPending
          ? replayRun.variables
          : decideApproval.isPending
            ? decideApproval.variables?.approval.run_id
            : undefined;
    const isPendingFor = (kind: RunCommandKind | "replay") =>
      pendingAction === kind && (wsCommand === kind ? run.id === selectedRun?.id : pendingRunId === run.id);
    const busy = pendingAction !== null;
    return [
      {
        id: "approve",
        label: t("common.approve"),
        icon: Check,
        // 承認・却下は承認の判断の権限（approver）が必要（#215）。
        visible: capabilities.decideApprovals && pendingApproval !== undefined,
        disabled: busy,
        loading: isPendingFor("approve"),
        onSelect: () => (pendingApproval ? decideRunApproval(run, pendingApproval, true) : undefined),
      },
      {
        id: "resume",
        label: t("run.resume"),
        icon: PlayCircle,
        // 取消・再開・再実行は Run の実行・操作の権限（operator）が必要（#215）。
        visible: capabilities.operateRuns && canResume,
        disabled: busy,
        loading: isPendingFor("resume"),
        onSelect: () => resumeLatestRun(run),
      },
      {
        id: "replay",
        label: t("run.replay"),
        icon: RefreshCw,
        visible: capabilities.operateRuns,
        disabled: busy,
        loading: isPendingFor("replay"),
        onSelect: () => replayRun.mutate(run.id),
      },
      {
        id: "reject",
        label: t("common.reject"),
        icon: X,
        tone: "danger",
        visible: capabilities.decideApprovals && pendingApproval !== undefined,
        disabled: busy,
        loading: isPendingFor("reject"),
        onSelect: () => (pendingApproval ? decideRunApproval(run, pendingApproval, false) : undefined),
      },
      {
        id: "cancel",
        label: t("run.cancel"),
        icon: X,
        tone: "danger",
        visible: capabilities.operateRuns && canCancel,
        disabled: busy,
        loading: isPendingFor("cancel"),
        onSelect: () => cancelLatestRun(run),
      },
    ];
  };

  return (
    <>
      <PageHeader
        wide
        title={t("nav.runs")}
        subtitle={t("page.runs.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () => void manualRefresh.track(() => runs.refetch()),
          },
        ]}
      />
      <PageBody wide>
        <AgentSplitPane
          splitId="runs-list"
          left={
            <div className="min-w-0 space-y-5">
              {/* Run の作成は Run の実行・操作の権限（operator）がある利用者だけに出す（#215）。 */}
              {capabilities.operateRuns ? (
                <Card className="min-w-0">
                  <CardHeader>
                    <CardTitle>{t("run.form.submit")}</CardTitle>
                    <CardDescription>{t("run.form.runtimeHint")}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {/* grid の外に単独で置く選択欄は値の長さの幅（業務 Agent の名前は lg。#613）。 */}
                    <SelectField
                      id="run-agent"
                      label={t("run.form.agent")}
                      width="lg"
                      value={selectedAgentId}
                      options={runnableAgents.map((agent) => ({ value: agent.id, label: agent.name }))}
                      onValueChange={onAgentChange}
                    />
                    {capabilities.admin ? (
                      <label className="flex items-center gap-2 text-sm text-fg">
                        <Switch
                          checked={runDraft}
                          aria-label={t("run.form.draft")}
                          onCheckedChange={setRunDraft}
                          data-testid="run-draft"
                        />
                        {t("run.form.draft")}
                        <span className="text-xs text-fg-muted">{t("run.form.draftHint")}</span>
                      </label>
                    ) : null}
                    <TextareaField
                      id="run-goal"
                      label={t("run.form.goal")}
                      required
                      error={goalError ?? undefined}
                      value={goal}
                      onValueChange={(value) => {
                        setGoal(value);
                        setGoalError(null);
                      }}
                      rows={4}
                      helper={t("run.form.submitShortcut")}
                      // 目標は重い問い合わせ（page-archetypes.md #535）。複数行なので Ctrl/⌘+Enter で明示的に実行する。
                      onKeyDown={(event) => {
                        if ((event.ctrlKey || event.metaKey) && isSubmitEnter(event)) {
                          event.preventDefault();
                          if (!createRun.isPending) submitRun();
                        }
                      }}
                    />
                    {/* 組み込み Runtime が実行できないとき（モデル未設定など）は、理由と直す場所を知らせる。 */}
                    {runtimeStatus.data && !runtimeStatus.data.ready ? (
                      <Banner severity="warning" title={t("runtime.builtin.notReadyTitle")}>
                        <p>{runtimeStatus.data.message ?? t("runtime.builtin.notReadyDefault")}</p>
                      </Banner>
                    ) : null}
                    {!goalSaved ? <Banner severity="warning">{t("workspace.draftNotSaved")}</Banner> : null}
                    {/* 工程を進める主操作は区切り線の下の lg、失敗はその操作の行に出す（buttons.md §5.2.1、messaging.md §3.3）。 */}
                    <FormActionBar
                      ariaLabel={t("run.form.actions")}
                      primaryActions={[
                        {
                          id: "create-run",
                          label: t("run.form.submit"),
                          icon: PlayCircle,
                          loading: createRun.isPending,
                          onClick: submitRun,
                          testId: "run-create-submit",
                        },
                      ]}
                      status={
                        createRun.error ? (
                          <FormStatus
                            tone="danger"
                            message={t("run.form.failed", { reason: createRun.error.message })}
                          />
                        ) : null
                      }
                    />
                  </CardContent>
                </Card>
              ) : null}

              <QueryState
                query={runs}
                loadingLabel={t("loading.runs")}
                skeleton={<RunHistorySkeleton />}
              >
                {restoredSelection.missing ? (
                  <Banner severity="warning">{t("workspace.selectionMissing")}</Banner>
                ) : null}
                <RunHistoryList
                  runs={visibleRuns}
                  resetKey={`${runQuery}\u0000${runFilter}`}
                  toolbar={
                    <ListToolbar
                      search={
                        <ListSearchField
                          id="run-search"
                          label={t("run.search")}
                          value={runQuery}
                          onSearch={setRunQuery}
                          count={visibleRuns.length}
                        />
                      }
                      filters={
                        <FilterChipGroup label={t("run.filter.label")}>
                          {RUN_STATUS_FILTERS.map((filter) => (
                            <ToggleChip key={filter} selected={runFilter === filter} onClick={() => setRunFilter(filter)}>
                              {runStatusFilterLabel(filter)}
                            </ToggleChip>
                          ))}
                        </FilterChipGroup>
                      }
                      summary={listCountLabel(visibleRuns.length, runItems.length)}
                      testId="run-list-toolbar"
                    />
                  }
                  empty={
                    runItems.length ? (
                      <NoMatchState
                        title={t("run.noMatch")}
                        clearLabel={runFilter === "all" ? t("common.clearSearch") : t("common.clearFilters")}
                        onClear={() => {
                          setRunQuery("");
                          setRunFilter("all");
                        }}
                      />
                    ) : (
                      <EmptyState
                        title={t("run.empty.title")}
                        hint={capabilities.operateRuns ? t("run.empty.hint") : undefined}
                      />
                    )
                  }
                  agentNameOf={agentNameOf}
                  selectedRunId={selectedRun?.id ?? null}
                  actionsFor={runActions}
                  onSelect={(runId) => {
                    restoredSelection.dismiss();
                    setSelectedRunId(runId);
                  }}
                />
              </QueryState>
            </div>
          }
          right={
            // 同じ取得の経過時間は実行履歴の側に出し、詳細は形だけにする（messaging.md §3.7）。
            <QueryState
              query={runs}
              loadingLabel={t("loading.runs")}
              skeleton={<FormSkeleton fields={4} />}
              skeletonOnly
              testId="run-detail-loading"
            >
              {selectedRun ? (
                <RunDetail
                  run={selectedRun}
                  agentName={agentNameOf(selectedRun.agent_id)}
                  actions={runActions(selectedRun)}
                  streamMode={streamMode}
                  onStreamModeChange={setStreamMode}
                  websocketState={websocketState}
                  sseState={sseState}
                  capabilities={capabilities}
                />
              ) : (
                <EmptyState title={t("common.empty.title")} hint={t("run.selectHint")} />
              )}
            </QueryState>
          }
        />
      </PageBody>
    </>
  );
}

function RunHistoryList({
  runs,
  resetKey,
  toolbar,
  empty,
  selectedRunId,
  onSelect,
  actionsFor,
  agentNameOf,
}: {
  runs: RunState[];
  /** 検索語・絞り込みが変わったら 1 ページ目へ戻す契機。 */
  resetKey: unknown;
  /** 表の上の検索・絞り込み（ListToolbar）。 */
  toolbar: ReactNode;
  empty: ReactNode;
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  actionsFor: (run: RunState) => EntityAction[];
  /** 業務 Agent の ID を名前にする（一覧に無い・読めないときは ID のまま）。 */
  agentNameOf: (agentId: string) => string;
}) {
  const columns: DataTableColumn<RunState>[] = [
    {
      key: "goal",
      header: t("run.form.goal"),
      rowHeader: true,
      render: (run) => (
        <RowTitleButton
          title={run.goal}
          // 長いゴールは 2 行で切り詰め、全文は Tooltip と右の詳細で読む。
          maxLines={2}
          subtitle={`${agentNameOf(run.agent_id)} / ${formatDate(run.created_at)}`}
          current={run.id === selectedRunId}
          onClick={() => onSelect(run.id)}
        />
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      render: (run) => <StatusBadge {...runStatusView(run.status)} />,
    },
    {
      key: "actions",
      header: t("run.actions"),
      align: "right",
      render: (run) => (
        <RowActionMenu
          actions={actionsFor(run)}
          ariaLabel={t("common.entityActions", { name: run.id })}
          testId={`run-row-actions-${run.id}`}
        />
      ),
    },
  ];

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.history")}</CardTitle>
        <CardDescription>{t("run.historyDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {toolbar}
        {/* 5 秒ごとの再取得で行が変わっても、ページは作業状態に残して戻さない（戻すのは絞り込みを変えたときだけ）。 */}
        <PagedDataTable
          pageKey="runs"
          resetKey={resetKey}
          rows={runs}
          columns={columns}
          getRowKey={(run) => run.id}
          selectedRowKey={selectedRunId}
          onRowClick={(run) => onSelect(run.id)}
          rowProps={(run) => ({ className: "align-top", "data-testid": `run-row-${run.id}` })}
          ariaLabel={t("run.history")}
          paginationTestId="run-history-pagination"
          empty={empty}
        />
      </CardContent>
    </Card>
  );
}

/** 実行履歴のカード（見出し + 表）の形。 */
function RunHistorySkeleton() {
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.history")}</CardTitle>
        <CardDescription>{t("run.historyDescription")}</CardDescription>
      </CardHeader>
      <CardContent>
        <TableSkeleton columns={3} />
      </CardContent>
    </Card>
  );
}
