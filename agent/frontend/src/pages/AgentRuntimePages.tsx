import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Brain,
  Check,
  Download,
  FileText,
  GitBranch,
  ListChecks,
  Minus,
  PlayCircle,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  Save,
  Server,
  ShieldAlert,
  Star,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  ObjectActionBar,
  PageHeader,
  RowActionMenu,
  Section,
  StatusBadge,
  Switch,
  toast,
  useConfirm,
  type DataTableColumn,
  visibleEntityActions,
  type EntityAction,
  type PageHeaderAction,
  type StatusVariant,
  PageBody,
} from "@engchina/production-ready-ui";

import {
  agentApi,
  type AgentProfile,
  type AgentProfilePatchPayload,
  type AgentSkill,
  type Artifact,
  type ApprovalRequest,
  type ExternalMcpServerSettings,
  type ExternalMcpToolInfo,
  type ExternalServiceSettings,
  type MarketplaceSource,
  type PluginManifest,
  type PluginSummary,
  type MemoryEntry,
  type MemoryKind,
  type RuntimeSnapshot,
  type RuntimeSnapshotImportResult,
  type RuntimeSnapshotSummary,
  type RunAuditData,
  type RunEvent,
  type RunState,
  type RuntimeBinding,
  type RuntimeDefinition,
  type ToolCallAuditFilters,
  type ToolCallAuditRecord,
  type ToolAuditRecord,
  type ToolDefinition,
} from "@/lib/api";
import {
  AgentSplitPane,
  EditorBreadcrumbs,
  MissingEditorTarget,
  RowTitleButton,
} from "@/components/EntityLayout";
import { useEditorRoute } from "@/lib/editor-route";
import { t } from "@/lib/i18n";
import { useValuesChanged } from "@/lib/render-sync";
import { APP_ROUTES } from "@/lib/routes";
import { sameDraft, useDirtySources, useEditorLeaveGuard, useSettingsLeaveGuard } from "@/lib/leave-guard";
import {
  isNullableString,
  isOneOf,
  isString,
  useRestoredSelectionCheck,
  useWorkspaceState,
  type WorkspaceValidator,
} from "@/lib/workspace-state";

type ToolPolicyChoice = "default" | "allow" | "ask" | "deny";
type RunStreamMode = "sse" | "websocket";
type WebSocketStreamStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error";

interface RunWebSocketState {
  status: WebSocketStreamStatus;
  lastHeartbeat: string | null;
  lastAck: string | null;
  lastError: string | null;
  lastEventId: string | null;
  reconnectAttempts: number;
  sendCancel: () => void;
  sendResume: () => void;
  sendApprovalDecision: (approvalId: string, approved: boolean) => void;
}

interface WebSocketMessage {
  type?: string;
  event?: RunEvent;
  run_status?: string;
  server_time?: string;
  command?: string;
  command_id?: string | null;
  ok?: boolean;
  duplicate?: boolean;
  error_code?: string;
  message?: string;
}

const statusVariant: Record<RunState["status"], StatusVariant> = {
  queued: "neutral",
  running: "info",
  waiting_approval: "warning",
  completed: "success",
  failed: "danger",
  cancelled: "warning",
};

const stepStatusVariant: Record<string, StatusVariant> = {
  pending: "neutral",
  running: "info",
  waiting_approval: "warning",
  completed: "success",
  failed: "danger",
  cancelled: "warning",
};

const websocketStatusVariant: Record<WebSocketStreamStatus, StatusVariant> = {
  idle: "neutral",
  connecting: "info",
  open: "success",
  reconnecting: "info",
  closed: "neutral",
  error: "danger",
};

function useRunEventWebSocket(
  run: RunState | undefined,
  enabled: boolean,
  onRuntimeEvent: () => void
): RunWebSocketState {
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptRef = useRef(0);
  const lastEventIdRef = useRef<string | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const runId = run?.id;
  const runStatus = run?.status;
  const [status, setStatus] = useState<WebSocketStreamStatus>("idle");
  const [lastHeartbeat, setLastHeartbeat] = useState<string | null>(null);
  const [lastAck, setLastAck] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastEventId, setLastEventId] = useState<string | null>(null);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const inactive = !enabled || !runId || !runStatus || isRunTerminal(runStatus);

  // Run が変わったレンダーで、前の Run の接続情報を消す（effect で setState しない）。
  const runChanged = useValuesChanged([runId ?? null]);
  if (runChanged) {
    setReconnectAttempts(0);
    setLastEventId(null);
    setLastHeartbeat(null);
    setLastAck(null);
    setLastError(null);
  }
  // 接続し直す条件（下の effect の deps）が変わったレンダーで、接続状態を初期化する。
  // 接続しない間は idle、接続する場合は新しい接続を張る前の connecting にする。
  const connectionChanged = useValuesChanged([enabled, onRuntimeEvent, runId, runStatus]);
  if (connectionChanged) {
    if (inactive) {
      setStatus("idle");
    } else {
      setStatus("connecting");
      setLastError(null);
    }
  }

  useEffect(() => {
    if (activeRunIdRef.current !== runId) {
      activeRunIdRef.current = runId ?? null;
      reconnectAttemptRef.current = 0;
      lastEventIdRef.current = null;
    }

    if (!enabled || !runId || !runStatus || isRunTerminal(runStatus)) {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      socketRef.current?.close();
      socketRef.current = null;
      return;
    }

    const activeRunId = runId;
    let disposed = false;

    function scheduleReconnect() {
      if (disposed) {
        return;
      }
      const nextAttempt = reconnectAttemptRef.current + 1;
      reconnectAttemptRef.current = nextAttempt;
      setReconnectAttempts(nextAttempt);
      setStatus("reconnecting");
      const delayMs = Math.min(5000, 500 * 2 ** Math.min(nextAttempt - 1, 3));
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        connect(true);
      }, delayMs);
    }

    function connect(isReconnect: boolean) {
      if (disposed) {
        return;
      }
      const socket = new WebSocket(runEventWebSocketUrl(activeRunId, lastEventIdRef.current));
      socketRef.current = socket;
      // 初回の接続（connecting）は render 中に設定済み。再接続だけここで状態を変える。
      if (isReconnect) {
        setStatus("reconnecting");
        setLastError(null);
      }

      socket.onopen = () => {
        if (socketRef.current === socket && !disposed) {
          setStatus("open");
        }
      };
      socket.onclose = () => {
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
        scheduleReconnect();
      };
      socket.onerror = () => {
        if (socketRef.current === socket && !disposed) {
          setStatus("error");
          setLastError("websocket.error");
        }
      };
      socket.onmessage = (event) => {
        const message = parseWebSocketMessage(event.data);
        if (!message) {
          setLastError("websocket.invalid_message");
          return;
        }
        if (message.type === "heartbeat") {
          const heartbeatTime = message.server_time ? formatDate(message.server_time) : "-";
          setLastHeartbeat(`${message.run_status ?? "-"} / ${heartbeatTime}`);
          return;
        }
        if (message.type === "command.accepted") {
          const duplicateLabel = message.duplicate ? ` / ${t("run.stream.duplicate")}` : "";
          setLastAck(`${message.command ?? "-"} / ${message.command_id ?? "-"}${duplicateLabel}`);
          onRuntimeEvent();
          return;
        }
        if (message.type === "error") {
          setLastError(message.error_code ?? message.message ?? "websocket.error");
          return;
        }
        if (message.event) {
          lastEventIdRef.current = message.event.id;
          setLastEventId(message.event.id);
          onRuntimeEvent();
        }
      };
    }

    connect(false);

    return () => {
      disposed = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      const socket = socketRef.current;
      if (socket) {
        socket.close();
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
      }
    };
  }, [enabled, onRuntimeEvent, runId, runStatus]);

  const sendCancel = useCallback(() => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setLastError("websocket.not_open");
      return;
    }
    socket.send(
      JSON.stringify({
        type: "cancel",
        command_id: `cancel-${Date.now()}`,
      })
    );
  }, []);

  const sendResume = useCallback(() => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setLastError("websocket.not_open");
      return;
    }
    socket.send(
      JSON.stringify({
        type: "resume",
        command_id: `resume-${Date.now()}`,
      })
    );
  }, []);

  const sendApprovalDecision = useCallback((approvalId: string, approved: boolean) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setLastError("websocket.not_open");
      return;
    }
    socket.send(
      JSON.stringify({
        type: "approval_decision",
        approval_id: approvalId,
        approved,
        decided_by: "operator",
        command_id: `${approved ? "approve" : "reject"}-${Date.now()}`,
      })
    );
  }, []);

  return {
    status,
    lastHeartbeat,
    lastAck,
    lastError,
    lastEventId,
    reconnectAttempts,
    sendCancel,
    sendResume,
    sendApprovalDecision,
  };
}

function runEventWebSocketUrl(runId: string, afterEventId: string | null = null): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams({ heartbeat_interval_seconds: "1" });
  if (afterEventId) {
    params.set("after_event_id", afterEventId);
  }
  return `${protocol}//${window.location.host}/api/runs/${runId}/events/ws?${params.toString()}`;
}

function parseWebSocketMessage(value: string): WebSocketMessage | null {
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as WebSocketMessage) : null;
  } catch {
    return null;
  }
}

function isRunTerminal(status: RunState["status"]): boolean {
  return ["completed", "failed", "cancelled"].includes(status);
}

export function AgentsPage() {
  const queryClient = useQueryClient();
  const editor = useEditorRoute();
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const skills = useQuery({ queryKey: ["skills"], queryFn: agentApi.listSkills });
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: agentApi.listRuntimes });
  const bindings = useQuery({
    queryKey: ["runtime-bindings"],
    queryFn: () => agentApi.listRuntimeBindings(),
  });
  const toggleAgent = useMutation({
    mutationFn: (agent: AgentProfile) => agentApi.patchAgent(agent.id, { enabled: !agent.enabled }),
    onSuccess: () => {
      toast.success(t("agent.enabledUpdated"));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error) => toast.error(error.message),
  });

  // 一覧の行と詳細（エディタの概要）で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const agentActions = (agent: AgentProfile): EntityAction[] => [
    {
      id: "toggle-enabled",
      label: agent.enabled ? t("agent.disable") : t("agent.enable"),
      icon: agent.enabled ? PowerOff : Power,
      disabled: toggleAgent.isPending,
      onSelect: () => toggleAgent.mutate(agent),
    },
  ];

  const agentList = agents.data?.agents ?? [];
  const bindingList = bindings.data?.bindings ?? [];
  const { target } = editor;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("nav.agents")}
          subtitle={t("page.agents.subtitle")}
          actions={[
            { id: "create", kind: "primary", label: t("agent.create"), icon: Plus, onClick: editor.openNew },
          ]}
        />
        <PageBody wide>
          <QueryState query={agents}>
            {skills.error ? <Banner severity="danger">{skills.error.message}</Banner> : null}
            <Section title={t("agent.list")}>
              <AgentTable
                agents={agentList}
                bindings={bindingList}
                onOpen={(agent) => editor.openItem(agent.id)}
                actionsFor={agentActions}
              />
            </Section>
          </QueryState>
        </PageBody>
      </>
    );
  }

  const agent = target.kind === "edit" ? agentList.find((candidate) => candidate.id === target.id) : undefined;
  if (target.kind === "edit" && !agent) {
    return (
      <>
        <PageHeader
          wide
          title={t("nav.agents")}
          breadcrumbs={
            <EditorBreadcrumbs listLabel={t("nav.agents")} listHref={APP_ROUTES.agents} current={target.id} />
          }
        />
        <PageBody wide>
          <QueryState query={agents}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <AgentEditorView
      key={agent?.id ?? "new"}
      agent={agent}
      availableSkills={skills.data?.skills ?? []}
      skillsError={skills.error}
      bindings={agent ? bindingList.filter((binding) => binding.agent_id === agent.id) : []}
      runtimes={runtimes.data?.runtimes ?? []}
      actions={agent ? agentActions(agent) : []}
      onBack={() => editor.backToList()}
      onCreated={(created) => editor.openItem(created.id, { replace: true })}
    />
  );
}

function AgentTable({
  agents,
  bindings,
  onOpen,
  actionsFor,
}: {
  agents: AgentProfile[];
  bindings: RuntimeBinding[];
  onOpen: (agent: AgentProfile) => void;
  actionsFor: (agent: AgentProfile) => EntityAction[];
}) {
  const columns: DataTableColumn<AgentProfile>[] = [
    {
      key: "name",
      header: t("agent.name"),
      rowHeader: true,
      render: (agent) => <RowTitleButton title={agent.name} subtitle={agent.id} onClick={() => onOpen(agent)} />,
    },
    {
      key: "description",
      header: t("agent.description"),
      className: "max-w-xs text-fg-muted",
      render: (agent) => agent.description || "-",
    },
    {
      key: "skills",
      header: t("agent.skillCount"),
      align: "right",
      className: "tabular-nums",
      render: (agent) => agent.skill_ids.length,
    },
    {
      key: "binding",
      header: t("agent.defaultBinding"),
      render: (agent) => {
        const binding = bindings.find((candidate) => candidate.agent_id === agent.id && candidate.is_default);
        return binding ? (
          <span className="break-all text-xs text-fg">{binding.native_agent_ref}</span>
        ) : (
          <StatusBadge variant="warning" label={t("agent.unbound")} />
        );
      },
    },
    {
      key: "enabled",
      header: t("common.status"),
      render: (agent) => (
        <StatusBadge
          variant={agent.enabled ? "success" : "neutral"}
          label={agent.enabled ? t("agent.enabled") : t("agent.disabled")}
        />
      ),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (agent) => (
        <RowActionMenu
          actions={actionsFor(agent)}
          ariaLabel={t("common.entityActions", { name: agent.name })}
          testId={`agent-row-actions-${agent.id}`}
        />
      ),
    },
  ];

  return (
    <DataTable
      rows={agents}
      columns={columns}
      getRowKey={(agent) => agent.id}
      onRowClick={onOpen}
      rowProps={(agent) => ({ className: "align-top", "data-testid": `agent-row-${agent.id}` })}
      tableClassName="w-full min-w-[46rem]"
      ariaLabel={t("agent.list")}
      empty={<EmptyState title={t("common.empty.title")} />}
    />
  );
}
export function RuntimesPage() {
  const queryClient = useQueryClient();
  const runtimes = useQuery({ queryKey: ["runtimes"], queryFn: agentApi.listRuntimes });
  const [logs, setLogs] = useState<Record<string, string>>({});
  const patchRuntime = useMutation({
    mutationFn: ({ runtime, enabled }: { runtime: RuntimeDefinition; enabled: boolean }) =>
      agentApi.patchRuntime(runtime.id, { enabled }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["runtimes"] }),
  });
  const probe = useMutation({
    mutationFn: agentApi.probeRuntime,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["runtimes"] }),
  });
  const serviceAction = useMutation({
    mutationFn: ({
      serviceId,
      action,
    }: {
      serviceId: string;
      action: "pull" | "start" | "stop" | "restart" | "remove";
    }) => agentApi.runtimeServiceAction(serviceId, action),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["runtimes"] }),
  });

  async function loadLogs(runtime: RuntimeDefinition) {
    if (!runtime.managed_service_id) return;
    try {
      const result = await agentApi.runtimeServiceLogs(runtime.managed_service_id);
      setLogs((current) => ({ ...current, [runtime.id]: result.content }));
    } catch (error) {
      setLogs((current) => ({
        ...current,
        [runtime.id]: error instanceof Error ? error.message : t("common.error"),
      }));
    }
  }

  const error = patchRuntime.error ?? probe.error ?? serviceAction.error;
  return (
    <>
      <PageHeader
        wide
        title={t("nav.runtimes")}
        subtitle={t("page.runtimes.subtitle")}
        actions={
          <Button variant="secondary" onClick={() => void runtimes.refetch()} icon={RefreshCw}>
            {t("common.retry")}
          </Button>
        }
      />
      <PageBody wide>
        {error ? <Banner severity="danger">{error.message}</Banner> : null}
        <QueryState query={runtimes}>
          <div className="grid gap-4 xl:grid-cols-2">
            {(runtimes.data?.runtimes ?? []).map((runtime) => (
              <Card key={runtime.id} className="min-w-0">
                <CardHeader className="flex-row items-start justify-between gap-4">
                  <div className="min-w-0">
                    <CardTitle className="flex items-center gap-2">
                      <Server size={20} aria-hidden />
                      {runtime.name}
                    </CardTitle>
                    <CardDescription className="break-all">
                      {runtime.kind === "legacy_native" ? t("runtime.legacyReadOnly") : runtime.base_url}
                    </CardDescription>
                  </div>
                  <StatusBadge
                    variant={
                      runtime.status === "running"
                        ? "success"
                        : runtime.status === "degraded"
                          ? "warning"
                          : runtime.status === "stopped"
                            ? "danger"
                            : "neutral"
                    }
                    label={runtime.status}
                  />
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap gap-2" aria-label={t("runtime.capabilities")}>
                    {Object.entries(runtime.capabilities).map(([name, supported]) => (
                      <StatusBadge
                        key={name}
                        variant={supported ? "info" : "neutral"}
                        label={`${name}: ${supported ? "on" : "off"}`}
                        // 対応の有無は Runtime の probe で変わる「状態」なのでアイコンで冗長に符号化する。
                        // 既定の Info（注意喚起）は「対応」の意味にならないため、形で on / off を区別する。
                        icon={supported ? Check : Minus}
                      />
                    ))}
                  </div>
                  {runtime.kind !== "legacy_native" ? (
                    <>
                      <div className="flex flex-wrap items-center gap-3">
                        <Switch
                          checked={runtime.enabled}
                          onCheckedChange={(enabled) => patchRuntime.mutate({ runtime, enabled })}
                          aria-label={`${runtime.name} ${t("agent.enabled")}`}
                        />
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => probe.mutate(runtime.id)} icon={RefreshCw}>
                          {t("runtime.probe")}
                        </Button>
                      </div>
                      {runtime.managed_service_id ? (
                        <div className="flex flex-wrap gap-2">
                          {(["pull", "start", "stop", "restart", "remove"] as const).map(
                            (action) => (
                              <Button
                                key={action}
                                size="sm"
                                variant={action === "remove" ? "danger" : "secondary"}
                                onClick={() =>
                                  serviceAction.mutate({
                                    serviceId: runtime.managed_service_id as string,
                                    action,
                                  })
                                }
                              >
                                {t(`runtime.action.${action}` as Parameters<typeof t>[0])}
                              </Button>
                            )
                          )}
                          <Button size="sm" variant="ghost" onClick={() => void loadLogs(runtime)}>
                            {t("runtime.logs")}
                          </Button>
                        </div>
                      ) : null}
                    </>
                  ) : null}
                  {logs[runtime.id] ? (
                    <pre className="max-h-56 overflow-auto rounded-md bg-surface-hover p-3 text-xs leading-5">
                      {logs[runtime.id]}
                    </pre>
                  ) : null}
                </CardContent>
              </Card>
            ))}
          </div>
        </QueryState>
      </PageBody>
    </>
  );
}

const DEFAULT_RUN_GOAL = "外部データを確認して要点を整理する";

export function RunsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: agentApi.listRuns,
    refetchInterval: 5000,
  });
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const bindings = useQuery({
    queryKey: ["runtime-bindings"],
    queryFn: () => agentApi.listRuntimeBindings(),
  });
  const createRun = useMutation({
    mutationFn: agentApi.createRun,
    onSuccess: (run) => {
      toast.success(t("run.createdToast"));
      setSelectedRunId(run.id);
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });
  const refreshRunQueries = () => {
    void queryClient.invalidateQueries({ queryKey: ["runs"] });
    void queryClient.invalidateQueries({ queryKey: ["memory"] });
  };
  const cancelRun = useMutation({
    mutationFn: agentApi.cancelRun,
    onSuccess: refreshRunQueries,
  });
  const resumeRun = useMutation({
    mutationFn: agentApi.resumeRun,
    onSuccess: refreshRunQueries,
  });
  const replayRun = useMutation({
    mutationFn: agentApi.replayRun,
    onSuccess: () => {
      toast.success(t("run.createdToast"));
      refreshRunQueries();
    },
  });
  // 作業状態（目標の下書き・選択中の Run・購読方式）はこのタブの sessionStorage に残す（#87）。
  // Agent / Binding は実行条件なので残さず、戻るたびに選び直す（実行の意思は確認し直す）。
  const [goal, setGoal, goalSaved] = useWorkspaceState("runs", "goal", DEFAULT_RUN_GOAL, isString);
  const [agentId, setAgentId] = useState("default");
  const [bindingId, setBindingId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
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
  const runIds = useMemo(() => runs.data?.runs.map((run) => run.id), [runs.data?.runs]);
  const restoredSelection = useRestoredSelectionCheck(selectedRunId, runIds);
  const selectedRun = runItems.find((run) => run.id === selectedRunId) ?? runItems[0];
  const agentBindings = (bindings.data?.bindings ?? []).filter(
    (binding) => binding.agent_id === agentId && binding.enabled
  );
  const defaultBinding = agentBindings.find((binding) => binding.is_default);
  const resolvedBindingId = bindingId || defaultBinding?.id || "";

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
    void queryClient.invalidateQueries({ queryKey: ["memory"] });
  }, [queryClient]);
  const websocketState = useRunEventWebSocket(
    selectedRun,
    streamMode === "websocket",
    refreshRuntimeEvents
  );

  useEffect(() => {
    if (streamMode !== "sse" || !selectedRun || isRunTerminal(selectedRun.status)) {
      return;
    }
    const source = new EventSource(`/api/runs/${selectedRun.id}/events?follow=true`);
    const refresh = () => {
      refreshRuntimeEvents();
    };
    const eventTypes = [
      "run.status_changed",
      "planner.completed",
      "skill.planned",
      "step.started",
      "tool.approval_required",
      "approval.decided",
      "artifact.created",
      "tool.completed",
      "tool.failed",
      "tool.guardrail_warning",
      "run.completed",
      "run.cancelled",
      "runtime.dispatch_claimed",
      "runtime.submitted",
      "runtime.failed",
      "memory.written",
    ];
    eventTypes.forEach((type) => source.addEventListener(type, refresh));
    source.onerror = () => source.close();
    return () => source.close();
  }, [selectedRun, refreshRuntimeEvents, streamMode]);

  function onAgentChange(value: string) {
    setAgentId(value);
    setBindingId("");
    setFormError(null);
  }

  function submitRun() {
    setFormError(null);
    if (!resolvedBindingId) {
      setFormError(t("run.bindingRequired"));
      return;
    }
    createRun.mutate({
      goal,
      agent_id: agentId,
      runtime_binding_id: resolvedBindingId,
    });
  }

  async function cancelLatestRun(run: RunState, viaWebSocket = false) {
    const confirmed = await confirm({
      title: t("run.cancelTitle"),
      description: run.id,
      confirmLabel: t("run.cancelConfirm"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (confirmed) {
      if (viaWebSocket) {
        websocketState.sendCancel();
      } else {
        cancelRun.mutate(run.id);
      }
    }
  }

  const actionPending = cancelRun.isPending || resumeRun.isPending || replayRun.isPending;
  // 一覧の行と詳細で同じ定義を使う（UX 契約 buttons.md §5.1）。取消は確認してから送る。
  const runActions = (run: RunState): EntityAction[] => {
    const { isExternal, canCancel, canResume } = runCapabilities(run);
    return [
      {
        id: "resume",
        label: t("run.resume"),
        icon: PlayCircle,
        visible: canResume,
        disabled: actionPending,
        onSelect: () => resumeRun.mutate(run.id),
      },
      {
        id: "replay",
        label: t("run.replay"),
        icon: RefreshCw,
        visible: !isExternal,
        disabled: actionPending,
        onSelect: () => replayRun.mutate(run.id),
      },
      {
        id: "cancel",
        label: t("run.cancel"),
        icon: X,
        tone: "danger",
        visible: canCancel,
        disabled: actionPending,
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
        actions={
          <Button variant="secondary" onClick={() => void runs.refetch()} aria-label="実行一覧を再読み込み" icon={RefreshCw}>
            {t("common.retry")}
          </Button>
        }
      />
      <PageBody wide>
        <AgentSplitPane
          splitId="runs-list"
          left={
            <div className="min-w-0 space-y-5">
              <Card className="min-w-0">
                <CardHeader>
                  <CardTitle>{t("run.form.submit")}</CardTitle>
                  <CardDescription>{t("run.runtime")}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <Field label={t("run.form.agent")} htmlFor="run-agent">
                    <select
                      id="run-agent"
                      value={agentId}
                      onChange={(event) => onAgentChange(event.target.value)}
                      className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    >
                      {(agents.data?.agents ?? []).filter((agent) => agent.enabled).map((agent) => (
                        <option key={agent.id} value={agent.id}>
                          {agent.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={t("run.form.goal")} htmlFor="run-goal">
                    <textarea
                      id="run-goal"
                      value={goal}
                      onChange={(event) => setGoal(event.target.value)}
                      className="min-h-24 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    />
                  </Field>
                  <Field label={t("run.form.binding")} htmlFor="run-binding">
                    <select
                      id="run-binding"
                      value={bindingId}
                      onChange={(event) => setBindingId(event.target.value)}
                      className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    >
                      <option value="">
                        {defaultBinding
                          ? `${t("run.form.defaultBinding")}: ${defaultBinding.native_agent_ref}`
                          : t("run.form.selectBinding")}
                      </option>
                      {agentBindings.map((binding) => (
                        <option key={binding.id} value={binding.id}>
                          {binding.native_agent_ref} / {binding.runtime_id}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {!agentBindings.length ? <Banner severity="warning">{t("run.unbound")}</Banner> : null}
                  {formError ? <Banner severity="danger">{formError}</Banner> : null}
                  {!goalSaved ? <Banner severity="warning">{t("workspace.draftNotSaved")}</Banner> : null}
                  {createRun.error ? <Banner severity="danger">{createRun.error.message}</Banner> : null}
                  <Button onClick={submitRun} loading={createRun.isPending} className="w-full" icon={PlayCircle}>
                    {t("run.form.submit")}
                  </Button>
                </CardContent>
              </Card>

              <QueryState query={runs}>
                {restoredSelection.missing ? (
                  <Banner severity="warning">{t("workspace.selectionMissing")}</Banner>
                ) : null}
                <RunHistoryList
                  runs={runItems}
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
            <QueryState query={runs}>
              {selectedRun ? (
                <RunDetail
                  run={selectedRun}
                  actions={runActions(selectedRun)}
                  actionPending={actionPending}
                  onWebSocketCancel={() => void cancelLatestRun(selectedRun, true)}
                  onWebSocketResume={() => websocketState.sendResume()}
                  onWebSocketApprovalDecision={(approvalId, approved) =>
                    websocketState.sendApprovalDecision(approvalId, approved)
                  }
                  streamMode={streamMode}
                  onStreamModeChange={setStreamMode}
                  websocketState={websocketState}
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

type ApprovalRow = { run: RunState; approval: ApprovalRequest };

export function ApprovalsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: agentApi.listRuns,
    refetchInterval: 5000,
  });
  const decide = useMutation({
    mutationFn: ({ approval, approved }: { approval: ApprovalRequest; approved: boolean }) =>
      agentApi.decideApproval(approval.id, { approved, decided_by: "operator" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const approvals = useMemo<ApprovalRow[]>(
    () => (runs.data?.runs ?? []).flatMap((run) => run.approvals.map((approval) => ({ run, approval }))),
    [runs.data?.runs]
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = approvals.find((row) => row.approval.id === selectedId) ?? approvals[0];

  async function decideApproval(approval: ApprovalRequest, approved: boolean) {
    const ok = await confirm({
      title: approved ? t("run.approveTitle") : t("run.rejectTitle"),
      description: approval.tool_call.name,
      confirmLabel: approved ? t("common.approve") : t("common.reject"),
      cancelLabel: t("common.cancel"),
      tone: approved ? "info" : "danger",
    });
    if (ok) {
      decide.mutate({ approval, approved });
    }
  }

  // 一覧の行と詳細で同じ定義を使う。判断は保留中の承認だけに出す。
  const approvalActions = (approval: ApprovalRequest): EntityAction[] => [
    {
      id: "approve",
      label: t("common.approve"),
      icon: Check,
      visible: approval.status === "pending",
      disabled: decide.isPending,
      onSelect: () => decideApproval(approval, true),
    },
    {
      id: "reject",
      label: t("common.reject"),
      icon: X,
      tone: "danger",
      visible: approval.status === "pending",
      disabled: decide.isPending,
      onSelect: () => decideApproval(approval, false),
    },
  ];

  const columns: DataTableColumn<ApprovalRow>[] = [
    {
      key: "tool",
      header: t("common.tool"),
      rowHeader: true,
      render: ({ run, approval }) => (
        <RowTitleButton
          title={approval.tool_call.name}
          subtitle={run.goal}
          current={approval.id === selected?.approval.id}
          onClick={() => setSelectedId(approval.id)}
        />
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      render: ({ approval }) => (
        <StatusBadge variant={approvalStatusVariant(approval.status)} label={approval.status} />
      ),
    },
    {
      key: "actions",
      header: t("run.actions"),
      align: "right",
      render: ({ approval }) => (
        <RowActionMenu
          actions={approvalActions(approval)}
          ariaLabel={t("common.entityActions", { name: approval.tool_call.name })}
          testId={`approval-row-actions-${approval.id}`}
        />
      ),
    },
  ];

  return (
    <>
      <PageHeader wide title={t("nav.approvals")} subtitle={t("page.approvals.subtitle")} />
      <PageBody wide>
        <QueryState query={runs}>
          <AgentSplitPane
            splitId="approvals-list"
            left={
              <Section title={t("approval.list")}>
                <DataTable
                  rows={approvals}
                  columns={columns}
                  getRowKey={({ approval }) => approval.id}
                  selectedRowKey={selected?.approval.id ?? null}
                  onRowClick={({ approval }) => setSelectedId(approval.id)}
                  rowProps={() => ({ className: "align-top" })}
                  ariaLabel={t("approval.list")}
                  empty={<EmptyState title={t("common.empty.title")} />}
                />
              </Section>
            }
            right={
              selected ? (
                <Section
                  title={t("approval.detail")}
                  aria-label={t("approval.detail")}
                  actions={
                    <ObjectActionBar
                      actions={approvalActions(selected.approval)}
                      ariaLabel={t("common.entityActions", { name: selected.approval.tool_call.name })}
                      moreLabel={t("common.moreActions")}
                      testId="approval-object-actions"
                    />
                  }
                >
                  <Card className="min-w-0">
                    <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0">
                        <CardTitle>{selected.approval.tool_call.name}</CardTitle>
                        <CardDescription className="break-words [overflow-wrap:anywhere]">
                          {selected.run.goal}
                        </CardDescription>
                      </div>
                      <StatusBadge
                        variant={approvalStatusVariant(selected.approval.status)}
                        label={selected.approval.status}
                      />
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="grid gap-2 text-xs text-fg-muted sm:grid-cols-2">
                        <span className="break-all">{`${t("audit.runId")}: ${selected.run.id}`}</span>
                        <span>{`${t("audit.runStatus")}: ${selected.run.status}`}</span>
                      </div>
                      <JsonPanel title={t("approval.arguments")} value={selected.approval.tool_call.arguments} />
                    </CardContent>
                  </Card>
                </Section>
              ) : (
                <EmptyState title={t("common.empty.title")} hint={t("approval.selectHint")} />
              )
            }
          />
        </QueryState>
      </PageBody>
    </>
  );
}

type AuditWarningsFilter = "any" | "true" | "false";

/** 監査の絞り込みフォーム。入力中の条件と適用済みの条件をこの形で sessionStorage に残す（#87）。 */
interface AuditFilterForm {
  runId: string;
  toolName: string;
  stepStatus: string;
  approvalStatus: string;
  errorCode: string;
  warnings: AuditWarningsFilter;
  limit: string;
}

const DEFAULT_AUDIT_FILTER_FORM: AuditFilterForm = {
  runId: "",
  toolName: "",
  stepStatus: "",
  approvalStatus: "",
  errorCode: "",
  warnings: "any",
  limit: "100",
};

const isAuditFilterForm: WorkspaceValidator<AuditFilterForm> = (value): value is AuditFilterForm => {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    Object.keys(DEFAULT_AUDIT_FILTER_FORM).every((key) => typeof record[key] === "string") &&
    ["any", "true", "false"].includes(record.warnings as string)
  );
};

function auditFiltersOf(form: AuditFilterForm): ToolCallAuditFilters {
  const parsedLimit = Number(form.limit);
  return {
    run_id: form.runId.trim() || undefined,
    tool_name: form.toolName || undefined,
    status: form.stepStatus || undefined,
    approval_status: form.approvalStatus || undefined,
    error_code: form.errorCode.trim() || undefined,
    has_guardrail_warnings: form.warnings === "any" ? undefined : form.warnings === "true",
    limit: Number.isInteger(parsedLimit) && parsedLimit > 0 ? parsedLimit : 100,
    offset: 0,
  };
}

export function AuditPage() {
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const [filterForm, setFilterForm] = useWorkspaceState(
    "audit",
    "filterForm",
    DEFAULT_AUDIT_FILTER_FORM,
    isAuditFilterForm
  );
  const [appliedForm, setAppliedForm] = useWorkspaceState(
    "audit",
    "appliedForm",
    DEFAULT_AUDIT_FILTER_FORM,
    isAuditFilterForm
  );
  const appliedFilters = useMemo(() => auditFiltersOf(appliedForm), [appliedForm]);
  const audit = useQuery({
    queryKey: ["audit", "tool-calls", appliedFilters],
    queryFn: () => agentApi.listToolCallAudit(appliedFilters),
  });
  const { runId, toolName, stepStatus, approvalStatus, errorCode, warnings, limit } = filterForm;

  function setFilter<K extends keyof AuditFilterForm>(key: K, value: AuditFilterForm[K]) {
    setFilterForm((current) => ({ ...current, [key]: value }));
  }

  function applyFilters() {
    setAppliedForm(filterForm);
  }

  function downloadCsv() {
    const link = document.createElement("a");
    link.href = agentApi.toolCallAuditCsvUrl(auditFiltersOf(filterForm));
    link.download = "agent-tool-call-audit.csv";
    link.click();
    toast.success(t("audit.csvDownloaded"));
  }

  return (
    <>
      <PageHeader
        wide
        title={t("nav.audit")}
        subtitle={t("page.audit.subtitle")}
        actions={
          <Button variant="secondary" onClick={() => void audit.refetch()} aria-label={t("common.retry")} icon={RefreshCw}>
            {t("common.retry")}
          </Button>
        }
      />
      <PageBody wide>
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("audit.filters")}</CardTitle>
            <CardDescription>{t("page.audit.subtitle")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Field label={t("audit.runId")} htmlFor="audit-run-id">
                <input
                  id="audit-run-id"
                  value={runId}
                  onChange={(event) => setFilter("runId", event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              <Field label={t("audit.toolName")} htmlFor="audit-tool-name">
                <select
                  id="audit-tool-name"
                  value={toolName}
                  onChange={(event) => setFilter("toolName", event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  <option value="">{t("common.all")}</option>
                  {(tools.data?.tools ?? []).map((tool) => (
                    <option key={tool.name} value={tool.name}>
                      {tool.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("audit.stepStatus")} htmlFor="audit-step-status">
                <select
                  id="audit-step-status"
                  value={stepStatus}
                  onChange={(event) => setFilter("stepStatus", event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  <option value="">{t("common.all")}</option>
                  {["pending", "running", "waiting_approval", "completed", "failed", "cancelled"].map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("audit.approvalStatus")} htmlFor="audit-approval-status">
                <select
                  id="audit-approval-status"
                  value={approvalStatus}
                  onChange={(event) => setFilter("approvalStatus", event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  <option value="">{t("common.all")}</option>
                  {["pending", "approved", "rejected", "cancelled"].map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("audit.errorCode")} htmlFor="audit-error-code">
                <input
                  id="audit-error-code"
                  value={errorCode}
                  onChange={(event) => setFilter("errorCode", event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              <Field label={t("audit.guardrailWarnings")} htmlFor="audit-warning-filter">
                <select
                  id="audit-warning-filter"
                  value={warnings}
                  onChange={(event) => setFilter("warnings", event.target.value as AuditWarningsFilter)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  <option value="any">{t("common.all")}</option>
                  <option value="true">{t("audit.hasWarnings")}</option>
                  <option value="false">{t("audit.noWarnings")}</option>
                </select>
              </Field>
              <Field label={t("audit.limit")} htmlFor="audit-limit">
                <input
                  id="audit-limit"
                  type="number"
                  min="1"
                  max="1000"
                  value={limit}
                  onChange={(event) => setFilter("limit", event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={applyFilters} loading={audit.isFetching} icon={RefreshCw}>
                {t("audit.apply")}
              </Button>
              <Button variant="secondary" onClick={downloadCsv} icon={Download}>
                {t("audit.downloadCsv")}
              </Button>
            </div>
            {tools.error ? <Banner severity="warning">{tools.error.message}</Banner> : null}
          </CardContent>
        </Card>

        <Card className="min-w-0">
          <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>{t("audit.records")}</CardTitle>
              <CardDescription>{t("page.audit.subtitle")}</CardDescription>
            </div>
            {audit.data ? <StatusBadge variant="info" label={`${t("audit.total")}: ${audit.data.total}`} icon={false} /> : null}
          </CardHeader>
          <CardContent>
            <QueryState query={audit}>
              {audit.data?.records.length ? (
                <AuditRecordsTable records={audit.data.records} />
              ) : (
                <EmptyState title={t("audit.noRecords")} />
              )}
            </QueryState>
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}

function AuditRecordsTable({ records }: { records: ToolCallAuditRecord[] }) {
  const columns: DataTableColumn<ToolCallAuditRecord>[] = [
    {
      key: "run_goal",
      header: t("audit.runGoal"),
      className: "max-w-72",
      render: (record) => (
        <>
          <p className="break-words text-sm font-medium text-fg [overflow-wrap:anywhere]">{record.run_goal}</p>
          <p className="mt-1 break-all text-xs text-fg-muted">{record.run_id}</p>
          <p className="mt-1 text-xs text-fg-muted">{formatDate(record.run_created_at)}</p>
        </>
      ),
    },
    {
      key: "tool_name",
      header: t("audit.toolName"),
      render: (record) => (
        <>
          <p className="break-all font-medium text-fg">{record.tool_name}</p>
          {record.error_code ? (
            <p className="mt-1 break-words text-xs text-danger-fg [overflow-wrap:anywhere]">{record.error_code}</p>
          ) : null}
        </>
      ),
    },
    {
      key: "status",
      header: t("audit.stepStatus"),
      render: (record) => (
        <StatusBadge variant={stepStatusVariant[record.status] ?? "neutral"} label={record.status} />
      ),
    },
    {
      key: "approval_status",
      header: t("audit.approvalStatus"),
      render: (record) =>
        record.approval_status ? (
          <StatusBadge variant={approvalStatusVariant(record.approval_status)} label={record.approval_status} />
        ) : (
          <span className="text-xs text-fg-muted">-</span>
        ),
    },
    {
      key: "policy_decision",
      header: t("run.auditPolicy"),
      className: "text-xs text-fg",
      render: (record) => record.policy_decision ?? "-",
    },
    {
      key: "permission_level",
      header: t("common.permission"),
      render: (record) => (
        <StatusBadge
          variant={permissionStatusVariant(record.permission_level)}
          label={record.permission_level ?? "-"}
          icon={false}
        />
      ),
    },
    {
      key: "guardrail_warnings",
      header: t("audit.guardrailWarnings"),
      className: "max-w-64",
      render: (record) =>
        record.guardrail_warnings.length ? (
          <div className="space-y-1">
            {record.guardrail_warnings.map((warning) => (
              <p key={warning} className="break-words text-xs text-warning-fg [overflow-wrap:anywhere]">
                {warning}
              </p>
            ))}
          </div>
        ) : (
          <span className="text-xs text-fg-muted">-</span>
        ),
    },
    {
      key: "duration_ms",
      header: t("run.auditDuration"),
      className: "text-xs text-fg",
      render: (record) =>
        record.duration_ms === null || record.duration_ms === undefined ? "-" : `${record.duration_ms}ms`,
    },
    {
      key: "trace_id",
      header: t("run.auditTrace"),
      className: "max-w-48",
      render: (record) => (
        <>
          <p className="break-all text-xs text-fg-muted">{record.trace_id ?? "-"}</p>
          {record.artifact_ids.length ? (
            <p className="mt-1 text-xs text-fg-muted">{`${t("run.auditArtifacts")}: ${record.artifact_ids.length}`}</p>
          ) : null}
        </>
      ),
    },
  ];

  return (
    <DataTable
      rows={records}
      columns={columns}
      getRowKey={(record) => `${record.run_id}:${record.step_id}`}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[980px]"
      ariaLabel={t("audit.records")}
    />
  );
}

function approvalStatusVariant(status: string): StatusVariant {
  if (status === "approved") {
    return "success";
  }
  if (status === "rejected") {
    return "danger";
  }
  if (status === "pending") {
    return "pending";
  }
  return "neutral";
}

function permissionStatusVariant(permission?: string | null): StatusVariant {
  if (permission === "read") {
    return "success";
  }
  if (permission === "write") {
    return "warning";
  }
  if (permission === "sensitive") {
    return "danger";
  }
  return "neutral";
}

export function ToolsPage() {
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const list = tools.data?.tools ?? [];
  const selected = list.find((tool) => tool.name === selectedName) ?? list[0];

  const columns: DataTableColumn<ToolDefinition>[] = [
    {
      key: "name",
      header: t("common.tool"),
      rowHeader: true,
      render: (tool) => (
        <RowTitleButton
          title={tool.name}
          current={tool.name === selected?.name}
          onClick={() => setSelectedName(tool.name)}
        />
      ),
    },
    {
      key: "permission",
      header: t("common.permission"),
      render: (tool) => (
        <StatusBadge variant={permissionStatusVariant(tool.permission_level)} label={tool.permission_level} icon={false} />
      ),
    },
  ];

  return (
    <>
      <PageHeader wide title={t("nav.tools")} subtitle={t("page.tools.subtitle")} />
      <PageBody wide>
        <QueryState query={tools}>
          <AgentSplitPane
            splitId="tools-list"
            left={
              <Section title={t("tool.list")}>
                <DataTable
                  rows={list}
                  columns={columns}
                  getRowKey={(tool) => tool.name}
                  selectedRowKey={selected?.name ?? null}
                  onRowClick={(tool) => setSelectedName(tool.name)}
                  ariaLabel={t("tool.list")}
                  empty={<EmptyState title={t("common.empty.title")} />}
                />
              </Section>
            }
            right={
              selected ? (
                <ToolCard tool={selected} />
              ) : (
                <EmptyState title={t("common.empty.title")} hint={t("tool.selectHint")} />
              )
            }
          />
        </QueryState>
      </PageBody>
    </>
  );
}

export function MemoryPage() {
  const queryClient = useQueryClient();
  // 検索語は作業状態として残し、登録フォームの未保存の入力は離脱ガードで守る（#87）。
  const [query, setQuery] = useWorkspaceState("memory", "query", "", isString);
  const [kind, setKind] = useState<MemoryKind>("user_preference");
  const [content, setContent] = useState("");
  const [metadataText, setMetadataText] = useState("{}");
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const memory = useQuery({
    queryKey: ["memory", query],
    queryFn: () => agentApi.searchMemory(query),
  });
  const addMemory = useMutation({
    mutationFn: agentApi.addMemory,
    onSuccess: () => {
      toast.success(t("memory.added"));
      setContent("");
      setMetadataText("{}");
      setFormError(null);
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });

  useEditorLeaveGuard(content.trim() !== "" || metadataText.trim() !== "{}", addMemory.isPending);

  function submitMemory() {
    setFormError(null);
    if (!content.trim()) {
      setFormError(t("memory.contentRequired"));
      return;
    }
    try {
      const metadata = JSON.parse(metadataText || "{}") as Record<string, unknown>;
      addMemory.mutate({ kind, content, metadata });
    } catch {
      setFormError(t("memory.metadataInvalid"));
    }
  }

  const entries = memory.data?.entries ?? [];
  const selectedEntry = entries.find((entry) => entry.id === selectedEntryId) ?? entries[0];
  const memoryColumns: DataTableColumn<MemoryEntry>[] = [
    {
      key: "content",
      header: t("memory.content"),
      rowHeader: true,
      render: (entry) => (
        <RowTitleButton
          title={entry.content.length > 80 ? `${entry.content.slice(0, 80)}…` : entry.content}
          subtitle={formatDate(entry.created_at)}
          current={entry.id === selectedEntry?.id}
          onClick={() => setSelectedEntryId(entry.id)}
        />
      ),
    },
    {
      key: "kind",
      header: t("memory.kind"),
      render: (entry) => <StatusBadge variant="info" label={entry.kind} icon={false} />,
    },
  ];

  return (
    <>
      <PageHeader wide title={t("nav.memory")} subtitle={t("page.memory.subtitle")} />
      <PageBody wide className="space-y-6">
        <Section title={t("memory.create")} description={t("page.memory.subtitle")}>
          <Card className="min-w-0">
            <CardContent className="grid min-w-0 gap-4 pt-5 lg:grid-cols-2">
              <div className="min-w-0 space-y-4">
                <Field label={t("memory.kind")} htmlFor="memory-kind">
                  <select
                    id="memory-kind"
                    value={kind}
                    onChange={(event) => setKind(event.target.value as MemoryKind)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  >
                    <option value="user_preference">{t("memory.kind.userPreference")}</option>
                    <option value="tool_learning">{t("memory.kind.toolLearning")}</option>
                    <option value="note">{t("memory.kind.note")}</option>
                    <option value="run_summary">{t("memory.kind.runSummary")}</option>
                  </select>
                </Field>
                <Field label={t("memory.content")} htmlFor="memory-content">
                  <textarea
                    id="memory-content"
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    className="min-h-28 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
              </div>
              <div className="min-w-0 space-y-4">
                <Field label={t("memory.metadata")} htmlFor="memory-metadata">
                  <textarea
                    id="memory-metadata"
                    value={metadataText}
                    onChange={(event) => setMetadataText(event.target.value)}
                    className="min-h-28 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    spellCheck={false}
                  />
                </Field>
                {formError ? <Banner severity="danger">{formError}</Banner> : null}
                {addMemory.error ? <Banner severity="danger">{addMemory.error.message}</Banner> : null}
                <Button onClick={submitMemory} loading={addMemory.isPending} icon={Save}>
                  {t("memory.create")}
                </Button>
              </div>
            </CardContent>
          </Card>
        </Section>
        <AgentSplitPane
          splitId="memory-list"
          left={
            <Section title={t("memory.list")}>
              <Field label={t("common.search")} htmlFor="memory-search">
                <input
                  id="memory-search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              <QueryState query={memory}>
                <DataTable
                  rows={entries}
                  columns={memoryColumns}
                  getRowKey={(entry) => entry.id}
                  selectedRowKey={selectedEntry?.id ?? null}
                  onRowClick={(entry) => setSelectedEntryId(entry.id)}
                  rowProps={() => ({ className: "align-top" })}
                  ariaLabel={t("memory.list")}
                  empty={<EmptyState title={t("common.empty.title")} />}
                />
              </QueryState>
            </Section>
          }
          right={
            selectedEntry ? (
              <Section title={t("memory.detail")} aria-label={t("memory.detail")}>
                <Card className="min-w-0">
                  <CardContent className="min-w-0 space-y-3 pt-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge variant="info" label={selectedEntry.kind} icon={false} />
                      <span className="text-xs text-fg-muted">{formatDate(selectedEntry.created_at)}</span>
                    </div>
                    <p className="whitespace-pre-wrap break-words text-sm leading-6 text-fg [overflow-wrap:anywhere]">
                      {selectedEntry.content}
                    </p>
                    <JsonPanel title={t("memory.metadata")} value={selectedEntry.metadata} />
                  </CardContent>
                </Card>
              </Section>
            ) : (
              <EmptyState title={t("common.empty.title")} hint={t("memory.selectHint")} />
            )
          }
        />
      </PageBody>
    </>
  );
}

interface ExternalSettingsDraft {
  baseUrl: string;
  timeoutSeconds: string;
  defaultLimit: string;
}

export function ExternalSettingsPage({ kind }: { kind: "rag" | "nl2sql" }) {
  const queryClient = useQueryClient();
  const isRag = kind === "rag";
  const isNl2Sql = kind === "nl2sql";
  const title = isRag ? t("nav.settingsExternalRag") : t("nav.settingsExternalNl2Sql");
  const subtitle = isRag ? t("page.settings.rag.subtitle") : t("page.settings.nl2sql.subtitle");
  const settings = useQuery({
    queryKey: ["settings", kind],
    queryFn: isRag ? agentApi.getExternalRagSettings : agentApi.getExternalNl2SqlSettings,
  });
  const mutation = useMutation({
    mutationFn: (payload: {
      base_url?: string | null;
      timeout_seconds?: number;
      default_limit?: number;
    }) => {
      if (isRag) {
        return agentApi.patchExternalRagSettings(payload);
      }
      return agentApi.patchExternalNl2SqlSettings(payload);
    },
    onSuccess: () => {
      toast.success(t("common.saved"));
      void queryClient.invalidateQueries({ queryKey: ["settings", kind] });
    },
  });
  const [baseUrl, setBaseUrl] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("10");
  const [defaultLimit, setDefaultLimit] = useState("100");
  const [baseline, setBaseline] = useState<ExternalSettingsDraft | null>(null);

  // server 値が変わったレンダーで、フォームと比較の基準を server 値に戻す。
  const serverChanged = useValuesChanged([settings.data]);
  if (serverChanged) {
    const current = settings.data;
    if (current) {
      const saved = {
        baseUrl: current.base_url ?? "",
        timeoutSeconds: String(current.timeout_seconds),
        defaultLimit: String(current.default_limit ?? 100),
      };
      setBaseUrl(saved.baseUrl);
      setTimeoutSeconds(saved.timeoutSeconds);
      setDefaultLimit(saved.defaultLimit);
      setBaseline(saved);
    }
  }

  const draft: ExternalSettingsDraft = { baseUrl, timeoutSeconds, defaultLimit };
  // RAG では既定件数を扱わないので比較から外す。
  const comparable = (value: ExternalSettingsDraft) => (isNl2Sql ? value : { ...value, defaultLimit: "" });
  const isDirty = baseline !== null && !sameDraft(comparable(draft), comparable(baseline));
  useSettingsLeaveGuard(isDirty, mutation.isPending);

  function save() {
    const submitted = draft;
    mutation.mutate(
      {
        base_url: baseUrl,
        timeout_seconds: Number(timeoutSeconds),
        default_limit: isNl2Sql ? Number(defaultLimit) : undefined,
      },
      { onSuccess: () => setBaseline(submitted) }
    );
  }

  return (
    <>
      <PageHeader wide title={title} subtitle={subtitle} />
      <PageBody wide>
<div className="space-y-5">
        <QueryState query={settings}>
          <ConnectionBanner settings={settings.data} />
          <Card>
            <CardHeader>
              <CardTitle>{title}</CardTitle>
              <CardDescription>{t("settings.apiKeyManaged")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* URL は全幅、タイムアウト・既定件数は 2 列に並べる。 */}
              <div className="grid gap-x-6 gap-y-4 lg:grid-cols-2">
                <Field label={t("settings.baseUrl")} htmlFor={`${kind}-base-url`} className="lg:col-span-2">
                  <input
                    id={`${kind}-base-url`}
                    value={baseUrl}
                    onChange={(event) => setBaseUrl(event.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
                <Field label={t("settings.timeout")} htmlFor={`${kind}-timeout`}>
                  <input
                    id={`${kind}-timeout`}
                    type="number"
                    min="1"
                    value={timeoutSeconds}
                    onChange={(event) => setTimeoutSeconds(event.target.value)}
                    className={INPUT_CLASS}
                  />
                </Field>
                {isNl2Sql ? (
                  <Field label={t("settings.defaultLimit")} htmlFor="nl2sql-default-limit">
                    <input
                      id="nl2sql-default-limit"
                      type="number"
                      min="1"
                      value={defaultLimit}
                      onChange={(event) => setDefaultLimit(event.target.value)}
                      className={INPUT_CLASS}
                    />
                  </Field>
                ) : null}
              </div>
              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              <Button onClick={save} loading={mutation.isPending} icon={Save}>
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </div>
</PageBody>
    </>
  );
}

function McpDiscoveryPanel({ configured }: { configured: boolean }) {
  const [serverId, setServerId] = useState("");
  const [traceId, setTraceId] = useState("");
  const filters = useMemo(
    () => ({
      server_id: serverId.trim() || undefined,
      trace_id: traceId.trim() || undefined,
    }),
    [serverId, traceId]
  );
  const tools = useQuery({
    queryKey: ["external-mcp-tools", filters.server_id ?? "", filters.trace_id ?? ""],
    queryFn: () => agentApi.listExternalMcpTools(filters),
    enabled: configured,
    retry: false,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.mcpDiscovery.title")}</CardTitle>
        <CardDescription>{t("settings.mcpDiscovery.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
          <Field label={t("settings.mcpDiscovery.serverId")} htmlFor="mcp-discovery-server-id">
            <input
              id="mcp-discovery-server-id"
              value={serverId}
              onChange={(event) => setServerId(event.target.value)}
              className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
            />
          </Field>
          <Field label={t("settings.mcpDiscovery.traceId")} htmlFor="mcp-discovery-trace-id">
            <input
              id="mcp-discovery-trace-id"
              value={traceId}
              onChange={(event) => setTraceId(event.target.value)}
              className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
            />
          </Field>
          <Button
            variant="secondary"
            onClick={() => void tools.refetch()}
            disabled={!configured}
            aria-describedby={!configured ? "mcp-discovery-configure-hint" : undefined}
            loading={tools.isFetching}
            className="min-h-10" icon={RefreshCw}>
            {t("settings.mcpDiscovery.refresh")}
          </Button>
        </div>

        {!configured ? (
          // 未設定は通常の初期状態。警告にせず「取得」が使えない理由として補助テキストで伝える。
          <p id="mcp-discovery-configure-hint" className="text-sm leading-6 text-fg-muted">
            {t("settings.mcpDiscovery.configureFirst")}
          </p>
        ) : tools.error ? (
          <Banner severity="danger">{tools.error.message}</Banner>
        ) : tools.isLoading ? (
          <LoadingState rows={3} label={t("common.loading")} />
        ) : (tools.data?.tools ?? []).length ? (
          <McpToolsList tools={tools.data?.tools ?? []} />
        ) : (
          <EmptyState title={t("settings.mcpDiscovery.empty")} />
        )}
      </CardContent>
    </Card>
  );
}

function McpToolsList({ tools }: { tools: ExternalMcpToolInfo[] }) {
  const mcpToolColumns: DataTableColumn<ExternalMcpToolInfo>[] = [
    { key: "name", header: t("settings.mcpDiscovery.tool"), className: "font-mono text-xs text-fg" },
    {
      key: "description",
      header: t("settings.mcpDiscovery.descriptionColumn"),
      className: "max-w-sm text-fg-muted",
      render: (tool) => tool.description || "-",
    },
    {
      key: "server_id",
      header: t("settings.mcpDiscovery.server"),
      className: "text-fg-muted",
      render: (tool) => tool.server_id ?? "-",
    },
    {
      key: "input_schema",
      header: t("settings.mcpDiscovery.inputSchema"),
      className: "text-fg-muted",
      render: (tool) => schemaSummary(tool.input_schema),
    },
    {
      key: "output_schema",
      header: t("settings.mcpDiscovery.outputSchema"),
      className: "text-fg-muted",
      render: (tool) => schemaSummary(tool.output_schema),
    },
  ];

  return (
    <div className="min-w-0">
      <DataTable
        className="hidden md:block"
        rows={tools}
        columns={mcpToolColumns}
        getRowKey={(tool) => `${tool.server_id ?? "default"}:${tool.name}`}
        rowProps={() => ({ className: "align-top" })}
        tableClassName="w-full min-w-[720px]"
        ariaLabel={t("settings.mcpDiscovery.title")}
      />
      <div className="grid gap-3 md:hidden">
        {tools.map((tool) => (
          <div key={`${tool.server_id ?? "default"}:${tool.name}`} className="rounded-md border border-border p-3">
            <div className="min-w-0 space-y-1">
              <p className="break-words font-mono text-xs font-medium text-fg">{tool.name}</p>
              <p className="text-sm leading-6 text-fg-muted">{tool.description || "-"}</p>
            </div>
            <dl className="mt-3 grid grid-cols-1 gap-2 text-xs text-fg-muted">
              <McpToolMeta label={t("settings.mcpDiscovery.server")} value={tool.server_id ?? "-"} />
              <McpToolMeta label={t("settings.mcpDiscovery.inputSchema")} value={schemaSummary(tool.input_schema)} />
              <McpToolMeta label={t("settings.mcpDiscovery.outputSchema")} value={schemaSummary(tool.output_schema)} />
            </dl>
          </div>
        ))}
      </div>
    </div>
  );
}

function McpToolMeta({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
      <dt className="font-medium text-fg">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
}

function schemaSummary(schema?: Record<string, unknown> | null): string {
  if (!schema || Object.keys(schema).length === 0) {
    return "-";
  }
  const type = typeof schema.type === "string" ? schema.type : "schema";
  const properties = schema.properties;
  if (properties && typeof properties === "object" && !Array.isArray(properties)) {
    const count = Object.keys(properties).length;
    return `${type} / ${count} fields`;
  }
  return type;
}

const INPUT_CLASS =
  "h-10 w-full rounded-md border border-border bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";
const TEXTAREA_CLASS =
  "w-full rounded-md border border-border bg-surface-sunken p-3 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";

function mcpAuthLabel(mode?: string | null): string {
  if (mode === "oauth_client_credentials") {
    return t("settings.mcpServers.authOauth");
  }
  if (mode === "api_key") {
    return t("settings.mcpServers.authApiKey");
  }
  return t("settings.mcpServers.authNone");
}

interface McpServerFormState {
  serverId: string;
  label: string;
  baseUrl: string;
  timeoutSeconds: string;
  sessionId: string;
  oauthTokenUrl: string;
  oauthClientId: string;
  oauthClientSecret: string;
  oauthScope: string;
}

const EMPTY_MCP_FORM: McpServerFormState = {
  serverId: "",
  label: "",
  baseUrl: "",
  timeoutSeconds: "10",
  sessionId: "",
  oauthTokenUrl: "",
  oauthClientId: "",
  oauthClientSecret: "",
  oauthScope: "",
};

function mcpFormOf(server: ExternalMcpServerSettings | undefined): McpServerFormState {
  if (!server) return EMPTY_MCP_FORM;
  return {
    ...EMPTY_MCP_FORM,
    serverId: server.server_id,
    label: server.label ?? "",
    baseUrl: server.base_url ?? "",
    timeoutSeconds: String(server.timeout_seconds),
  };
}

export function McpServersPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  const servers = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: agentApi.listExternalMcpServers,
  });

  function invalidate() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] }),
      queryClient.invalidateQueries({ queryKey: ["external-mcp-tools"] }),
    ]);
  }

  const setDefaultMutation = useMutation({
    mutationFn: (serverId: string) => agentApi.setDefaultExternalMcpServer(serverId),
    onSuccess: () => {
      toast.success(t("settings.mcpServers.defaultUpdated"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  const deleteMutation = useMutation({
    mutationFn: (serverId: string) => agentApi.deleteExternalMcpServer(serverId),
    onSuccess: () => {
      toast.success(t("settings.mcpServers.deleted"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  async function remove(server: ExternalMcpServerSettings) {
    const ok = await confirm({
      title: t("settings.mcpServers.confirmDeleteTitle"),
      description: t("settings.mcpServers.confirmDeleteMessage", { id: server.server_id }),
      confirmLabel: t("settings.mcpServers.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate(server.server_id, {
      // エディタから削除したら、消えた対象へ戻れないよう履歴を置き換えて一覧へ戻る。
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  const busy = setDefaultMutation.isPending || deleteMutation.isPending;
  // 一覧の行と詳細（エディタの概要）で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const serverActions = (server: ExternalMcpServerSettings): EntityAction[] => [
    {
      id: "set-default",
      label: t("settings.mcpServers.setDefault"),
      icon: Star,
      visible: !server.is_default,
      disabled: busy,
      onSelect: () => setDefaultMutation.mutate(server.server_id),
    },
    {
      id: "delete",
      label: t("settings.mcpServers.delete"),
      icon: Trash2,
      tone: "danger",
      disabled: server.server_id === "default" || busy,
      onSelect: () => remove(server),
    },
  ];

  const list = servers.data?.servers ?? [];
  const { target } = editor;
  const listTitle = t("nav.settingsExternalMcp");

  if (target.kind === "list") {
    const anyConfigured = list.some((server) => server.configured);
    return (
      <>
        <PageHeader
          wide
          title={listTitle}
          subtitle={t("page.settings.mcp.subtitle")}
          actions={[
            {
              id: "create",
              kind: "primary",
              label: t("settings.mcpServers.add"),
              icon: Plus,
              onClick: editor.openNew,
            },
          ]}
        />
        <PageBody wide className="space-y-6">
          <QueryState query={servers}>
            <Section title={t("settings.mcpServers.title")} description={t("settings.mcpServers.description")}>
              <McpServerTable
                servers={list}
                onOpen={(server) => editor.openItem(server.server_id)}
                actionsFor={serverActions}
              />
            </Section>
            <McpDiscoveryPanel configured={anyConfigured} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  const server = target.kind === "edit" ? list.find((candidate) => candidate.server_id === target.id) : undefined;
  if (target.kind === "edit" && !server) {
    return (
      <>
        <PageHeader
          wide
          title={listTitle}
          breadcrumbs={
            <EditorBreadcrumbs listLabel={listTitle} listHref={APP_ROUTES.settingsExternalMcp} current={target.id} />
          }
        />
        <PageBody wide>
          <QueryState query={servers}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <McpServerEditor
      key={server?.server_id ?? "new"}
      server={server}
      actions={server ? serverActions(server) : []}
      onBack={() => editor.backToList()}
      onSaved={async (serverId) => {
        await invalidate();
        editor.openItem(serverId, { replace: true });
      }}
    />
  );
}

/** 外部 MCP サーバーの全画面エディタ（A 型。`?id=new` / `?id=<server id>`）。 */
function McpServerEditor({
  server,
  actions,
  onBack,
  onSaved,
}: {
  server?: ExternalMcpServerSettings;
  actions: EntityAction[];
  onBack: () => void;
  onSaved: (serverId: string) => Promise<void>;
}) {
  const [form, setForm] = useState<McpServerFormState>(() => mcpFormOf(server));
  const [formBaseline, setFormBaseline] = useState<McpServerFormState>(() => mcpFormOf(server));
  const editingId = server?.server_id ?? null;

  // 送る内容は mutate の引数で渡す（クリック直前の入力を closure の古い state で送らない）。
  const saveMutation = useMutation({
    mutationFn: (current: McpServerFormState) => {
      const payload = {
        label: current.label || null,
        base_url: current.baseUrl,
        timeout_seconds: Number(current.timeoutSeconds),
        session_id: current.sessionId || undefined,
        oauth_token_url: current.oauthTokenUrl || undefined,
        oauth_client_id: current.oauthClientId || undefined,
        oauth_client_secret: current.oauthClientSecret || undefined,
        oauth_scope: current.oauthScope || undefined,
      };
      if (editingId) {
        return agentApi.updateExternalMcpServer(editingId, payload);
      }
      return agentApi.createExternalMcpServer({ server_id: current.serverId.trim(), ...payload });
    },
    onSuccess: async (saved, current) => {
      toast.success(editingId ? t("settings.mcpServers.updated") : t("settings.mcpServers.created"));
      // secret 欄は保存後に空へ戻す（値は保持も表示もしない）。
      const next = { ...current, sessionId: "", oauthClientSecret: "" };
      setForm(next);
      setFormBaseline(next);
      await onSaved(saved.server_id ?? current.serverId.trim());
    },
  });

  // 開いた時点の内容から変わっていれば未保存（secret 欄も含む。値は保存しない）。#87
  const formDirty = !sameDraft(form, formBaseline);
  const { confirmClose } = useEditorLeaveGuard(formDirty, saveMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function save() {
    if (!editingId && !form.serverId.trim()) {
      toast.error(t("settings.mcpServers.idRequired"));
      return;
    }
    saveMutation.mutate(form);
  }

  const listTitle = t("nav.settingsExternalMcp");
  const title = server ? server.label || server.server_id : t("settings.mcpServers.addTitle");

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={server ? server.server_id : t("page.settings.mcp.subtitle")}
        breadcrumbs={
          <EditorBreadcrumbs listLabel={listTitle} listHref={APP_ROUTES.settingsExternalMcp} current={title} />
        }
        actions={[
          { id: "back", kind: "secondary", label: t("common.backToList"), icon: ArrowLeft, onClick: () => void back() },
          {
            id: "save",
            kind: "primary",
            label: editingId ? t("common.save") : t("common.create"),
            icon: Save,
            loading: saveMutation.isPending,
            onClick: save,
          },
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {server ? (
          <Section
            title={t("editor.overview")}
            actions={
              <ObjectActionBar
                actions={actions}
                ariaLabel={t("common.entityActions", { name: server.server_id })}
                moreLabel={t("common.moreActions")}
                testId="mcp-server-object-actions"
              />
            }
          >
            <div className="flex flex-wrap items-center gap-2">
              {server.is_default ? (
                <StatusBadge variant="info" label={t("settings.mcpServers.default")} icon={false} />
              ) : null}
              <StatusBadge
                variant={server.configured ? "success" : "warning"}
                label={server.configured ? t("common.configured") : t("common.notConfigured")}
              />
              <span className="text-xs text-fg-muted">
                {`${t("settings.mcpServers.auth")}: ${mcpAuthLabel(server.auth_mode)}`}
              </span>
            </div>
          </Section>
        ) : null}
        {saveMutation.error ? <Banner severity="danger">{(saveMutation.error as Error).message}</Banner> : null}
        <Section title={t("mcpServers.connection")} description={t("settings.apiKeyManaged")}>
          <Card className="min-w-0">
            <CardContent className="space-y-4 pt-5">
              <Field label={t("settings.mcpServers.serverId")} htmlFor="mcp-server-id">
                <input
                  id="mcp-server-id"
                  value={form.serverId}
                  disabled={Boolean(editingId)}
                  onChange={(event) => setForm({ ...form, serverId: event.target.value })}
                  className={editingId ? `${INPUT_CLASS} opacity-60` : INPUT_CLASS}
                />
                <p className="mt-1 text-xs leading-5 text-fg-muted">{t("settings.mcpServers.serverIdHint")}</p>
              </Field>
              <Field label={t("settings.mcpServers.label")} htmlFor="mcp-server-label">
                <input
                  id="mcp-server-label"
                  value={form.label}
                  onChange={(event) => setForm({ ...form, label: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("settings.baseUrl")} htmlFor="mcp-server-base-url">
                <input
                  id="mcp-server-base-url"
                  value={form.baseUrl}
                  onChange={(event) => setForm({ ...form, baseUrl: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("settings.timeout")} htmlFor="mcp-server-timeout">
                <input
                  id="mcp-server-timeout"
                  type="number"
                  min="1"
                  value={form.timeoutSeconds}
                  onChange={(event) => setForm({ ...form, timeoutSeconds: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("settings.mcpSessionId")} htmlFor="mcp-server-session">
                <input
                  id="mcp-server-session"
                  value={form.sessionId}
                  autoComplete="off"
                  onChange={(event) => setForm({ ...form, sessionId: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
            </CardContent>
          </Card>
        </Section>
        <Section title={t("mcpServers.oauth")}>
          <Card className="min-w-0">
            <CardContent className="grid gap-4 pt-5 md:grid-cols-2">
              <Field label={t("settings.mcpServers.oauthTokenUrl")} htmlFor="mcp-server-oauth-token">
                <input
                  id="mcp-server-oauth-token"
                  value={form.oauthTokenUrl}
                  onChange={(event) => setForm({ ...form, oauthTokenUrl: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("settings.mcpServers.oauthScope")} htmlFor="mcp-server-oauth-scope">
                <input
                  id="mcp-server-oauth-scope"
                  value={form.oauthScope}
                  onChange={(event) => setForm({ ...form, oauthScope: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("settings.mcpServers.oauthClientId")} htmlFor="mcp-server-oauth-client">
                <input
                  id="mcp-server-oauth-client"
                  value={form.oauthClientId}
                  autoComplete="off"
                  onChange={(event) => setForm({ ...form, oauthClientId: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("settings.mcpServers.oauthClientSecret")} htmlFor="mcp-server-oauth-secret">
                <input
                  id="mcp-server-oauth-secret"
                  type="password"
                  value={form.oauthClientSecret}
                  autoComplete="off"
                  onChange={(event) => setForm({ ...form, oauthClientSecret: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
            </CardContent>
          </Card>
        </Section>
      </PageBody>
    </>
  );
}

function McpServerTable({
  servers,
  onOpen,
  actionsFor,
}: {
  servers: ExternalMcpServerSettings[];
  onOpen: (server: ExternalMcpServerSettings) => void;
  actionsFor: (server: ExternalMcpServerSettings) => EntityAction[];
}) {
  const columns: DataTableColumn<ExternalMcpServerSettings>[] = [
    {
      key: "server_id",
      header: t("settings.mcpServers.serverId"),
      rowHeader: true,
      render: (server) => (
        <div className="flex flex-wrap items-center gap-2">
          <RowTitleButton title={server.server_id} onClick={() => onOpen(server)} />
          {server.is_default ? (
            <StatusBadge variant="info" label={t("settings.mcpServers.default")} icon={false} />
          ) : null}
        </div>
      ),
    },
    {
      key: "label",
      header: t("settings.mcpServers.label"),
      className: "text-fg-muted",
      render: (server) => server.label || "-",
    },
    {
      key: "base_url",
      header: t("settings.baseUrl"),
      className: "max-w-xs break-all text-fg-muted",
      render: (server) => server.base_url || "-",
    },
    {
      key: "auth_mode",
      header: t("settings.mcpServers.auth"),
      className: "text-fg-muted",
      render: (server) => mcpAuthLabel(server.auth_mode),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (server) => (
        <RowActionMenu
          actions={actionsFor(server)}
          ariaLabel={t("common.entityActions", { name: server.server_id })}
          // 既定の default サーバーは既定化も削除もできない。使える項目の無いメニューは開かせない。
          disabled={visibleEntityActions(actionsFor(server)).every((action) => action.disabled)}
          testId={`mcp-server-row-actions-${server.server_id}`}
        />
      ),
    },
  ];

  return (
    <DataTable
      rows={servers}
      columns={columns}
      getRowKey={(server) => server.server_id}
      onRowClick={onOpen}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[44rem]"
      ariaLabel={t("settings.mcpServers.title")}
      empty={<EmptyState title={t("settings.mcpServers.empty")} />}
    />
  );
}

interface SkillFormState {
  id: string;
  name: string;
  description: string;
  instructions: string;
  tags: string;
  enabled: boolean;
  mcpRequirementsJson: string;
  resourceIdsJson: string;
}

const EMPTY_SKILL_FORM: SkillFormState = {
  id: "",
  name: "",
  description: "",
  instructions: "",
  tags: "",
  enabled: true,
  mcpRequirementsJson: "[]",
  resourceIdsJson: "[]",
};

function skillSourceLabel(source: string): string {
  switch (source) {
    case "builtin":
      return t("skills.sourceBuiltin");
    case "project":
      return t("skills.sourceProject");
    case "env":
      return t("skills.sourceEnv");
    default:
      return t("skills.sourceRuntime");
  }
}

function skillSourceVariant(source: string): StatusVariant {
  if (source === "runtime") {
    return "success";
  }
  return source === "builtin" ? "neutral" : "info";
}

function skillFormOf(skill: AgentSkill | undefined): SkillFormState {
  if (!skill) return EMPTY_SKILL_FORM;
  return {
    id: skill.id,
    name: skill.name,
    description: skill.description,
    instructions: skill.instructions,
    tags: skill.tags.join(", "),
    enabled: skill.enabled,
    mcpRequirementsJson: JSON.stringify(skill.mcp_requirements, null, 2),
    resourceIdsJson: JSON.stringify(skill.resource_ids, null, 2),
  };
}

export function SkillsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  const skills = useQuery({ queryKey: ["skills"], queryFn: agentApi.listSkills });

  function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ["skills"] });
  }

  const deleteMutation = useMutation({
    mutationFn: (skillId: string) => agentApi.deleteSkill(skillId),
    onSuccess: () => {
      toast.success(t("skills.deleted"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  const reloadMutation = useMutation({
    mutationFn: () => agentApi.reloadSkills(),
    onSuccess: () => {
      toast.success(t("skills.reloaded"));
      void invalidate();
    },
  });

  async function remove(skill: AgentSkill) {
    const ok = await confirm({
      title: t("skills.confirmDeleteTitle"),
      description: t("skills.confirmDeleteMessage", { id: skill.id }),
      confirmLabel: t("skills.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate(skill.id, {
      // エディタから削除したら、消えた対象へ戻れないよう履歴を置き換えて一覧へ戻る。
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  // 一覧の行と詳細（エディタの概要）で同じ定義を使う。ビルトイン / ファイル / env は読み取り専用。
  const skillActions = (skill: AgentSkill): EntityAction[] => [
    {
      id: "delete",
      label: t("skills.delete"),
      icon: Trash2,
      tone: "danger",
      visible: skill.source === "runtime",
      disabled: deleteMutation.isPending,
      onSelect: () => remove(skill),
    },
  ];

  const list = skills.data?.skills ?? [];
  const { target } = editor;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("skills.title")}
          subtitle={t("page.skills.subtitle")}
          actions={[
            {
              id: "reload",
              kind: "utility",
              label: t("skills.reload"),
              icon: RefreshCw,
              loading: reloadMutation.isPending,
              onClick: () => reloadMutation.mutate(),
            },
            { id: "create", kind: "primary", label: t("skills.add"), icon: Plus, onClick: editor.openNew },
          ]}
          moreActionsLabel={t("common.moreActions")}
        />
        <PageBody wide>
          <QueryState query={skills}>
            <Section title={t("skills.list")} description={t("skills.description")}>
              <SkillTable
                skills={list}
                onOpen={(skill) => editor.openItem(skill.id)}
                actionsFor={skillActions}
              />
            </Section>
          </QueryState>
        </PageBody>
      </>
    );
  }

  const skill = target.kind === "edit" ? list.find((candidate) => candidate.id === target.id) : undefined;
  if (target.kind === "edit" && !skill) {
    return (
      <>
        <PageHeader
          wide
          title={t("skills.title")}
          breadcrumbs={<EditorBreadcrumbs listLabel={t("skills.title")} listHref={APP_ROUTES.skills} current={target.id} />}
        />
        <PageBody wide>
          <QueryState query={skills}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <SkillEditor
      key={skill?.id ?? "new"}
      skill={skill}
      actions={skill ? skillActions(skill) : []}
      onBack={() => editor.backToList()}
      onSaved={async (skillId) => {
        await invalidate();
        editor.openItem(skillId, { replace: true });
      }}
    />
  );
}

/**
 * Skill の全画面エディタ（A 型。`?id=new` / `?id=<skill id>`）。
 * 実行時に追加した Skill だけを編集でき、ビルトイン / ファイル / env の Skill は読み取り専用の詳細を出す。
 */
function SkillEditor({
  skill,
  actions,
  onBack,
  onSaved,
}: {
  skill?: AgentSkill;
  actions: EntityAction[];
  onBack: () => void;
  onSaved: (skillId: string) => Promise<void>;
}) {
  const [form, setForm] = useState<SkillFormState>(() => skillFormOf(skill));
  const [formBaseline, setFormBaseline] = useState<SkillFormState>(() => skillFormOf(skill));
  const [formError, setFormError] = useState<string | null>(null);
  const editingId = skill?.id ?? null;
  const editable = !skill || skill.source === "runtime";

  // 送る内容は mutate の引数で渡す（クリック直前の入力を closure の古い state で送らない）。
  const saveMutation = useMutation({
    mutationFn: (current: SkillFormState) => {
      const payload = {
        name: current.name,
        description: current.description,
        instructions: current.instructions,
        tags: current.tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        enabled: current.enabled,
        mcp_requirements: JSON.parse(current.mcpRequirementsJson) as { server_id: string; tool_names: string[] }[],
        resource_ids: JSON.parse(current.resourceIdsJson) as string[],
        tool_calls: [],
      };
      if (editingId) {
        return agentApi.updateSkill(editingId, payload);
      }
      return agentApi.createSkill({ id: current.id.trim(), ...payload });
    },
    onSuccess: async (saved, current) => {
      toast.success(editingId ? t("skills.updated") : t("skills.created"));
      setFormBaseline(current);
      await onSaved(saved.id);
    },
  });

  const formDirty = editable && !sameDraft(form, formBaseline);
  const { confirmClose } = useEditorLeaveGuard(formDirty, saveMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function save() {
    setFormError(null);
    if (!editingId && !form.id.trim()) {
      setFormError(t("skills.idRequired"));
      return;
    }
    if (!form.name.trim()) {
      setFormError(t("skills.nameRequired"));
      return;
    }
    for (const json of [form.mcpRequirementsJson, form.resourceIdsJson]) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(json);
      } catch {
        setFormError(t("skills.invalidJson"));
        return;
      }
      if (!Array.isArray(parsed)) {
        setFormError(t("skills.invalidJson"));
        return;
      }
    }
    saveMutation.mutate(form);
  }

  const title = skill ? skill.name : t("skills.addTitle");
  const headerActions: PageHeaderAction[] = [
    { id: "back", kind: "secondary", label: t("common.backToList"), icon: ArrowLeft, onClick: () => void back() },
  ];
  if (editable) {
    headerActions.push({
      id: "save",
      kind: "primary",
      label: editingId ? t("common.save") : t("common.create"),
      icon: Save,
      loading: saveMutation.isPending,
      onClick: save,
    });
  }

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={skill ? skill.id : t("page.skills.subtitle")}
        breadcrumbs={<EditorBreadcrumbs listLabel={t("skills.title")} listHref={APP_ROUTES.skills} current={title} />}
        actions={headerActions}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {skill ? (
          <Section
            title={t("editor.overview")}
            description={skill.description || undefined}
            actions={
              <ObjectActionBar
                actions={actions}
                ariaLabel={t("common.entityActions", { name: skill.name })}
                moreLabel={t("common.moreActions")}
                testId="skill-object-actions"
              />
            }
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge variant={skillSourceVariant(skill.source)} label={skillSourceLabel(skill.source)} icon={false} />
              <StatusBadge
                variant={skill.enabled ? "success" : "neutral"}
                label={skill.enabled ? t("agent.enabled") : t("agent.disabled")}
              />
            </div>
            {!editable ? <Banner severity="info">{t("skills.readOnly")}</Banner> : null}
          </Section>
        ) : null}
        {skill && !editable ? (
          <SkillReadOnlyDetail skill={skill} />
        ) : (
          <>
            {formError ? <Banner severity="danger">{formError}</Banner> : null}
            {saveMutation.error ? <Banner severity="danger">{(saveMutation.error as Error).message}</Banner> : null}
            <Section title={t("skills.basic")}>
              <Card className="min-w-0">
                <CardContent className="space-y-4 pt-5">
                  <Field label={t("skills.id")} htmlFor="skill-id">
                    <input
                      id="skill-id"
                      value={form.id}
                      disabled={Boolean(editingId)}
                      onChange={(event) => setForm({ ...form, id: event.target.value })}
                      className={editingId ? `${INPUT_CLASS} opacity-60` : INPUT_CLASS}
                    />
                  </Field>
                  <Field label={t("skills.name")} htmlFor="skill-name">
                    <input
                      id="skill-name"
                      value={form.name}
                      onChange={(event) => setForm({ ...form, name: event.target.value })}
                      className={INPUT_CLASS}
                    />
                  </Field>
                  <Field label={t("agent.description")} htmlFor="skill-description">
                    <input
                      id="skill-description"
                      value={form.description}
                      onChange={(event) => setForm({ ...form, description: event.target.value })}
                      className={INPUT_CLASS}
                    />
                  </Field>
                  <Field label={t("skills.instructions")} htmlFor="skill-instructions">
                    <textarea
                      id="skill-instructions"
                      value={form.instructions}
                      rows={3}
                      onChange={(event) => setForm({ ...form, instructions: event.target.value })}
                      className={TEXTAREA_CLASS}
                    />
                  </Field>
                  <Field label={t("skills.tags")} htmlFor="skill-tags">
                    <input
                      id="skill-tags"
                      value={form.tags}
                      onChange={(event) => setForm({ ...form, tags: event.target.value })}
                      className={INPUT_CLASS}
                    />
                    <p className="mt-1 text-xs leading-5 text-fg-muted">{t("skills.tagsHint")}</p>
                  </Field>
                  <label className="flex items-center gap-2 text-sm text-fg">
                    <Switch
                      checked={form.enabled}
                      aria-label={t("skills.enabledLabel")}
                      onCheckedChange={(checked) => setForm({ ...form, enabled: checked })}
                    />
                    {t("skills.enabledLabel")}
                  </label>
                </CardContent>
              </Card>
            </Section>
            <Section title={t("skills.dependencies")}>
              <Card className="min-w-0">
                <CardContent className="space-y-4 pt-5">
                  <Field label={t("skills.mcpRequirements")} htmlFor="skill-mcp-requirements">
                    <textarea
                      id="skill-mcp-requirements"
                      value={form.mcpRequirementsJson}
                      rows={8}
                      spellCheck={false}
                      onChange={(event) => setForm({ ...form, mcpRequirementsJson: event.target.value })}
                      className={`${TEXTAREA_CLASS} font-mono`}
                    />
                    <p className="mt-1 text-xs leading-5 text-fg-muted">{t("skills.mcpRequirementsHint")}</p>
                  </Field>
                  <Field label={t("skills.resourceIds")} htmlFor="skill-resource-ids">
                    <textarea
                      id="skill-resource-ids"
                      value={form.resourceIdsJson}
                      rows={4}
                      spellCheck={false}
                      onChange={(event) => setForm({ ...form, resourceIdsJson: event.target.value })}
                      className={`${TEXTAREA_CLASS} font-mono`}
                    />
                  </Field>
                </CardContent>
              </Card>
            </Section>
          </>
        )}
      </PageBody>
    </>
  );
}

function SkillTable({
  skills,
  onOpen,
  actionsFor,
}: {
  skills: AgentSkill[];
  onOpen: (skill: AgentSkill) => void;
  actionsFor: (skill: AgentSkill) => EntityAction[];
}) {
  const columns: DataTableColumn<AgentSkill>[] = [
    {
      key: "name",
      header: t("skills.skill"),
      rowHeader: true,
      render: (skill) => <RowTitleButton title={skill.name} subtitle={skill.id} onClick={() => onOpen(skill)} />,
    },
    {
      key: "source",
      header: t("skills.source"),
      render: (skill) => (
        <StatusBadge variant={skillSourceVariant(skill.source)} label={skillSourceLabel(skill.source)} icon={false} />
      ),
    },
    {
      key: "enabled",
      header: t("common.status"),
      render: (skill) => (
        <StatusBadge
          variant={skill.enabled ? "success" : "neutral"}
          label={skill.enabled ? t("agent.enabled") : t("agent.disabled")}
        />
      ),
    },
    {
      key: "tags",
      header: t("skills.tags"),
      className: "text-xs text-fg-muted",
      render: (skill) => (skill.tags.length ? skill.tags.join(", ") : "-"),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (skill) => (
        <RowActionMenu
          actions={actionsFor(skill)}
          ariaLabel={t("common.entityActions", { name: skill.name })}
          testId={`skill-row-actions-${skill.id}`}
        />
      ),
    },
  ];

  return (
    <DataTable
      rows={skills}
      columns={columns}
      getRowKey={(skill) => skill.id}
      onRowClick={onOpen}
      rowProps={(skill) => ({ className: "align-top", "data-testid": `skill-row-${skill.id}` })}
      tableClassName="w-full min-w-[44rem]"
      ariaLabel={t("skills.list")}
      empty={<EmptyState title={t("skills.empty")} />}
    />
  );
}

function SkillReadOnlyDetail({ skill }: { skill: AgentSkill }) {
  return (
    <>
      <Section title={t("skills.instructions")}>
        <Card className="min-w-0">
          <CardContent className="space-y-3 pt-5">
            <p className="whitespace-pre-wrap text-sm leading-6 text-fg">{skill.instructions || "-"}</p>
            <p className="text-xs text-fg-muted">{`${t("skills.tags")}: ${skill.tags.length ? skill.tags.join(", ") : "-"}`}</p>
          </CardContent>
        </Card>
      </Section>
      <Section title={t("skills.dependencies")}>
        <Card className="min-w-0">
          <CardContent className="grid min-w-0 gap-4 pt-5 md:grid-cols-2">
            <JsonPanel title={t("skills.mcpRequirements")} value={skill.mcp_requirements} />
            <JsonPanel title={t("skills.resourceIds")} value={skill.resource_ids} />
          </CardContent>
        </Card>
      </Section>
    </>
  );
}

export function PluginsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: agentApi.listPlugins });

  function invalidate() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ["plugins"] }),
      queryClient.invalidateQueries({ queryKey: ["plugin"] }),
      queryClient.invalidateQueries({ queryKey: ["skills"] }),
      queryClient.invalidateQueries({ queryKey: ["mcp-servers"] }),
      queryClient.invalidateQueries({ queryKey: ["agents"] }),
    ]);
  }

  const enabledMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => agentApi.setPluginEnabled(id, enabled),
    onSuccess: () => {
      toast.success(t("plugins.enabledUpdated"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });
  const uninstallMutation = useMutation({
    mutationFn: (id: string) => agentApi.uninstallPlugin(id),
    onSuccess: () => {
      toast.success(t("plugins.uninstalled"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });
  const reloadMutation = useMutation({
    mutationFn: () => agentApi.reloadPlugins(),
    onSuccess: () => void invalidate(),
  });

  async function uninstall(plugin: PluginSummary) {
    const ok = await confirm({
      title: t("plugins.confirmUninstallTitle"),
      description: t("plugins.confirmUninstallMessage", { id: plugin.id }),
      confirmLabel: t("plugins.uninstall"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    uninstallMutation.mutate(plugin.id, {
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  const busy = uninstallMutation.isPending || enabledMutation.isPending;
  // 一覧の行と詳細で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const pluginActions = (plugin: PluginSummary): EntityAction[] => [
    {
      id: "toggle-enabled",
      label: plugin.enabled ? t("plugins.disable") : t("plugins.enable"),
      icon: plugin.enabled ? PowerOff : Power,
      disabled: busy,
      onSelect: () => enabledMutation.mutate({ id: plugin.id, enabled: !plugin.enabled }),
    },
    {
      id: "uninstall",
      label: t("plugins.uninstall"),
      icon: Trash2,
      tone: "danger",
      disabled: busy,
      onSelect: () => uninstall(plugin),
    },
  ];

  const list = plugins.data?.plugins ?? [];
  const { target } = editor;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("plugins.title")}
          subtitle={t("page.plugins.subtitle")}
          actions={[
            {
              id: "reload",
              kind: "utility",
              label: t("skills.reload"),
              icon: RefreshCw,
              loading: reloadMutation.isPending,
              onClick: () => reloadMutation.mutate(),
            },
            { id: "install", kind: "primary", label: t("plugins.install"), icon: Plus, onClick: editor.openNew },
          ]}
          moreActionsLabel={t("common.moreActions")}
        />
        <PageBody wide>
          <QueryState query={plugins}>
            <Section title={t("plugins.title")} description={t("plugins.description")}>
              <PluginTable
                plugins={list}
                onOpen={(plugin) => editor.openItem(plugin.id)}
                actionsFor={pluginActions}
              />
            </Section>
          </QueryState>
        </PageBody>
      </>
    );
  }

  if (target.kind === "new") {
    return (
      <PluginInstallEditor
        onBack={() => editor.backToList()}
        onInstalled={async (pluginId) => {
          await invalidate();
          editor.openItem(pluginId, { replace: true });
        }}
      />
    );
  }

  const plugin = list.find((candidate) => candidate.id === target.id);
  if (!plugin) {
    return (
      <>
        <PageHeader
          wide
          title={t("plugins.title")}
          breadcrumbs={<EditorBreadcrumbs listLabel={t("plugins.title")} listHref={APP_ROUTES.plugins} current={target.id} />}
        />
        <PageBody wide>
          <QueryState query={plugins}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return <PluginDetail plugin={plugin} actions={pluginActions(plugin)} onBack={() => editor.backToList()} />;
}

/** manifest から install する全画面エディタ（`?id=new`）。入力中の manifest を未保存の変更として守る（#87）。 */
function PluginInstallEditor({
  onBack,
  onInstalled,
}: {
  onBack: () => void;
  onInstalled: (pluginId: string) => Promise<void>;
}) {
  const [manifestJson, setManifestJson] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const installMutation = useMutation({
    mutationFn: (manifest: PluginManifest) => agentApi.installPlugin({ manifest }),
    onSuccess: async (record) => {
      toast.success(t("plugins.installed"));
      setManifestJson("");
      await onInstalled(record.id);
    },
  });
  const { confirmClose } = useEditorLeaveGuard(manifestJson.trim() !== "", installMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function install() {
    setFormError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(manifestJson);
    } catch {
      setFormError(t("plugins.invalidJson"));
      return;
    }
    if (typeof parsed !== "object" || parsed === null) {
      setFormError(t("plugins.invalidJson"));
      return;
    }
    installMutation.mutate(parsed as PluginManifest);
  }

  const title = t("plugins.installTitle");
  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={t("page.plugins.subtitle")}
        breadcrumbs={<EditorBreadcrumbs listLabel={t("plugins.title")} listHref={APP_ROUTES.plugins} current={title} />}
        actions={[
          { id: "back", kind: "secondary", label: t("common.backToList"), icon: ArrowLeft, onClick: () => void back() },
          {
            id: "install",
            kind: "primary",
            label: t("plugins.installSubmit"),
            icon: Download,
            loading: installMutation.isPending,
            onClick: install,
          },
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {formError ? <Banner severity="danger">{formError}</Banner> : null}
        {installMutation.error ? <Banner severity="danger">{(installMutation.error as Error).message}</Banner> : null}
        <Section title={t("plugins.manifest")} description={t("plugins.manifestHint")}>
          <Card className="min-w-0">
            <CardContent className="pt-5">
              <Field label={t("plugins.manifest")} htmlFor="plugin-manifest">
                <textarea
                  id="plugin-manifest"
                  value={manifestJson}
                  rows={16}
                  spellCheck={false}
                  onChange={(event) => setManifestJson(event.target.value)}
                  className={`${TEXTAREA_CLASS} font-mono`}
                />
              </Field>
            </CardContent>
          </Card>
        </Section>
      </PageBody>
    </>
  );
}

/** インストール済み連携の詳細（`?id=<plugin id>`）。manifest は変更できないため閲覧と対象の操作だけを出す。 */
function PluginDetail({
  plugin,
  actions,
  onBack,
}: {
  plugin: PluginSummary;
  actions: EntityAction[];
  onBack: () => void;
}) {
  const record = useQuery({
    queryKey: ["plugin", plugin.id],
    queryFn: () => agentApi.getPlugin(plugin.id),
  });
  const manifest = record.data?.manifest;

  return (
    <>
      <PageHeader
        wide
        title={plugin.name}
        subtitle={`${plugin.id} · v${plugin.version}`}
        breadcrumbs={<EditorBreadcrumbs listLabel={t("plugins.title")} listHref={APP_ROUTES.plugins} current={plugin.name} />}
        actions={[
          { id: "back", kind: "secondary", label: t("common.backToList"), icon: ArrowLeft, onClick: onBack },
        ]}
      />
      <PageBody wide className="space-y-6">
        <Section
          title={t("editor.overview")}
          description={plugin.description || undefined}
          actions={
            <ObjectActionBar
              actions={actions}
              ariaLabel={t("common.entityActions", { name: plugin.name })}
              moreLabel={t("common.moreActions")}
              testId="plugin-object-actions"
            />
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              variant={plugin.enabled ? "success" : "neutral"}
              label={plugin.enabled ? t("agent.enabled") : t("agent.disabled")}
            />
            <span className="text-xs text-fg-muted">
              {`${t("plugins.source")}: ${plugin.marketplace_id ? plugin.marketplace_id : t("plugins.sourceManual")}`}
            </span>
          </div>
          <PluginBundle plugin={plugin} />
          {plugin.warnings.map((warning) => (
            <Banner key={warning} severity="warning">
              {warning}
            </Banner>
          ))}
        </Section>
        <Section title={t("plugins.contents")}>
          <QueryState query={record}>
            {manifest ? (
              <Card className="min-w-0">
                <CardContent className="grid min-w-0 gap-4 pt-5 xl:grid-cols-3">
                  <JsonPanel
                    title={t("plugins.skills")}
                    value={(manifest.skills ?? []).map((skill) => ({ id: skill.id, name: skill.name }))}
                  />
                  <JsonPanel title={t("plugins.mcp")} value={manifest.mcp_servers ?? []} />
                  <JsonPanel
                    title={t("plugins.resources")}
                    value={(manifest.resources ?? []).map((resource) => ({
                      id: resource.id,
                      kind: resource.kind,
                      name: resource.name,
                    }))}
                  />
                </CardContent>
              </Card>
            ) : null}
          </QueryState>
        </Section>
      </PageBody>
    </>
  );
}

function PluginBundle({ plugin }: { plugin: PluginSummary }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <StatusBadge variant="info" label={`${t("plugins.skills")} ${plugin.skill_count}`} icon={false} />
      <StatusBadge variant="info" label={`${t("plugins.mcp")} ${plugin.mcp_count}`} icon={false} />
      <StatusBadge variant="info" label={`${t("plugins.resources")} ${plugin.resource_count}`} icon={false} />
    </div>
  );
}

function PluginTable({
  plugins,
  onOpen,
  actionsFor,
}: {
  plugins: PluginSummary[];
  onOpen: (plugin: PluginSummary) => void;
  actionsFor: (plugin: PluginSummary) => EntityAction[];
}) {
  const columns: DataTableColumn<PluginSummary>[] = [
    {
      key: "name",
      header: t("plugins.title"),
      rowHeader: true,
      render: (plugin) => (
        <RowTitleButton title={plugin.name} subtitle={`${plugin.id} · v${plugin.version}`} onClick={() => onOpen(plugin)} />
      ),
    },
    {
      key: "source",
      header: t("plugins.source"),
      className: "text-fg-muted",
      render: (plugin) => (plugin.marketplace_id ? plugin.marketplace_id : t("plugins.sourceManual")),
    },
    {
      key: "bundle",
      header: t("plugins.bundle"),
      render: (plugin) => <PluginBundle plugin={plugin} />,
    },
    {
      key: "enabled",
      header: t("plugins.enabledLabel"),
      render: (plugin) => (
        <StatusBadge
          variant={plugin.enabled ? "success" : "neutral"}
          label={plugin.enabled ? t("agent.enabled") : t("agent.disabled")}
        />
      ),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (plugin) => (
        <RowActionMenu
          actions={actionsFor(plugin)}
          ariaLabel={t("common.entityActions", { name: plugin.id })}
          testId={`plugin-row-actions-${plugin.id}`}
        />
      ),
    },
  ];

  return (
    <DataTable
      rows={plugins}
      columns={columns}
      getRowKey={(plugin) => plugin.id}
      onRowClick={onOpen}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[46rem]"
      ariaLabel={t("plugins.title")}
      empty={<EmptyState title={t("plugins.empty")} />}
    />
  );
}

const EMPTY_MARKETPLACE_FORM = { id: "", name: "", url: "" };

export function PluginMarketplacesPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  const markets = useQuery({
    queryKey: ["plugin-marketplaces"],
    queryFn: agentApi.listPluginMarketplaces,
  });

  function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ["plugin-marketplaces"] });
  }

  const refreshMutation = useMutation({
    mutationFn: (id: string) => agentApi.refreshPluginMarketplace(id),
    onSuccess: (_data, id) => {
      toast.success(t("marketplaces.refreshed"));
      void invalidate();
      void queryClient.invalidateQueries({ queryKey: ["marketplace-plugins", id] });
    },
    onError: (error) => toast.error(error.message),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => agentApi.deletePluginMarketplace(id),
    onSuccess: () => {
      toast.success(t("marketplaces.deleted"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  async function remove(source: MarketplaceSource) {
    const ok = await confirm({
      title: t("marketplaces.confirmDeleteTitle"),
      description: t("marketplaces.confirmDeleteMessage", { id: source.id }),
      confirmLabel: t("marketplaces.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate(source.id, {
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  const busy = refreshMutation.isPending || deleteMutation.isPending;
  // 一覧の行と詳細で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const marketplaceActions = (source: MarketplaceSource): EntityAction[] => [
    {
      id: "refresh",
      label: t("marketplaces.refresh"),
      icon: RefreshCw,
      disabled: busy,
      loading: refreshMutation.isPending && refreshMutation.variables === source.id,
      onSelect: () => refreshMutation.mutate(source.id),
    },
    {
      id: "delete",
      label: t("marketplaces.delete"),
      icon: Trash2,
      tone: "danger",
      disabled: busy,
      onSelect: () => remove(source),
    },
  ];

  const list = markets.data?.marketplaces ?? [];
  const { target } = editor;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("marketplaces.title")}
          subtitle={t("page.pluginMarketplaces.subtitle")}
          actions={[
            { id: "create", kind: "primary", label: t("marketplaces.add"), icon: Plus, onClick: editor.openNew },
          ]}
        />
        <PageBody wide>
          <QueryState query={markets}>
            <Section title={t("marketplaces.list")} description={t("marketplaces.description")}>
              <MarketplaceTable
                sources={list}
                onOpen={(source) => editor.openItem(source.id)}
                actionsFor={marketplaceActions}
              />
            </Section>
          </QueryState>
        </PageBody>
      </>
    );
  }

  if (target.kind === "new") {
    return (
      <MarketplaceAddEditor
        onBack={() => editor.backToList()}
        onAdded={async (id) => {
          await invalidate();
          editor.openItem(id, { replace: true });
        }}
      />
    );
  }

  const source = list.find((candidate) => candidate.id === target.id);
  if (!source) {
    return (
      <>
        <PageHeader
          wide
          title={t("marketplaces.title")}
          breadcrumbs={
            <EditorBreadcrumbs listLabel={t("marketplaces.title")} listHref={APP_ROUTES.pluginMarketplaces} current={target.id} />
          }
        />
        <PageBody wide>
          <QueryState query={markets}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <MarketplaceDetail
      source={source}
      actions={marketplaceActions(source)}
      onBack={() => editor.backToList()}
      onInstalled={() => {
        void queryClient.invalidateQueries({ queryKey: ["plugins"] });
        void queryClient.invalidateQueries({ queryKey: ["skills"] });
        void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
        void queryClient.invalidateQueries({ queryKey: ["agents"] });
      }}
    />
  );
}

/** マーケットプレイスを追加する全画面エディタ（`?id=new`）。 */
function MarketplaceAddEditor({ onBack, onAdded }: { onBack: () => void; onAdded: (id: string) => Promise<void> }) {
  const [form, setForm] = useState(EMPTY_MARKETPLACE_FORM);
  const addMutation = useMutation({
    mutationFn: (current: typeof EMPTY_MARKETPLACE_FORM) =>
      agentApi.addPluginMarketplace({
        id: current.id.trim(),
        name: current.name || undefined,
        url: current.url || undefined,
      }),
    onSuccess: async (source) => {
      toast.success(t("marketplaces.added"));
      setForm(EMPTY_MARKETPLACE_FORM);
      await onAdded(source.id);
    },
  });
  const { confirmClose } = useEditorLeaveGuard(!sameDraft(form, EMPTY_MARKETPLACE_FORM), addMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function add() {
    if (!form.id.trim()) {
      toast.error(t("marketplaces.idRequired"));
      return;
    }
    addMutation.mutate(form);
  }

  const title = t("marketplaces.addTitle");
  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={t("page.pluginMarketplaces.subtitle")}
        breadcrumbs={
          <EditorBreadcrumbs listLabel={t("marketplaces.title")} listHref={APP_ROUTES.pluginMarketplaces} current={title} />
        }
        actions={[
          { id: "back", kind: "secondary", label: t("common.backToList"), icon: ArrowLeft, onClick: () => void back() },
          {
            id: "create",
            kind: "primary",
            label: t("common.create"),
            icon: Save,
            loading: addMutation.isPending,
            onClick: add,
          },
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {addMutation.error ? <Banner severity="danger">{(addMutation.error as Error).message}</Banner> : null}
        <Section title={t("marketplaces.overview")}>
          <Card className="min-w-0">
            <CardContent className="space-y-4 pt-5">
              <Field label={t("marketplaces.id")} htmlFor="mkt-id">
                <input
                  id="mkt-id"
                  value={form.id}
                  onChange={(event) => setForm({ ...form, id: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("marketplaces.name")} htmlFor="mkt-name">
                <input
                  id="mkt-name"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={t("marketplaces.url")} htmlFor="mkt-url">
                <input
                  id="mkt-url"
                  value={form.url}
                  onChange={(event) => setForm({ ...form, url: event.target.value })}
                  className={INPUT_CLASS}
                />
                <p className="mt-1 text-xs leading-5 text-fg-muted">{t("marketplaces.urlHint")}</p>
              </Field>
            </CardContent>
          </Card>
        </Section>
      </PageBody>
    </>
  );
}

function MarketplaceTable({
  sources,
  onOpen,
  actionsFor,
}: {
  sources: MarketplaceSource[];
  onOpen: (source: MarketplaceSource) => void;
  actionsFor: (source: MarketplaceSource) => EntityAction[];
}) {
  const columns: DataTableColumn<MarketplaceSource>[] = [
    {
      key: "name",
      header: t("marketplaces.name"),
      rowHeader: true,
      render: (source) => (
        <RowTitleButton title={source.name || source.id} subtitle={source.id} onClick={() => onOpen(source)} />
      ),
    },
    {
      key: "url",
      header: t("marketplaces.url"),
      className: "max-w-xs break-all text-xs text-fg-muted",
      render: (source) => source.url || "-",
    },
    {
      key: "plugin_count",
      header: t("marketplaces.pluginCount"),
      align: "right",
      className: "tabular-nums",
      render: (source) => source.plugin_count,
    },
    {
      key: "status",
      header: t("common.status"),
      render: (source) =>
        source.last_error ? (
          <StatusBadge variant="warning" label={t("common.error")} />
        ) : (
          <StatusBadge variant="success" label={t("common.valid")} />
        ),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (source) => (
        <RowActionMenu
          actions={actionsFor(source)}
          ariaLabel={t("common.entityActions", { name: source.id })}
          testId={`marketplace-row-actions-${source.id}`}
        />
      ),
    },
  ];

  return (
    <DataTable
      rows={sources}
      columns={columns}
      getRowKey={(source) => source.id}
      onRowClick={onOpen}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[44rem]"
      ariaLabel={t("marketplaces.list")}
      empty={<EmptyState title={t("marketplaces.empty")} />}
    />
  );
}

/** マーケットプレイスの詳細（`?id=<marketplace id>`）。配布元の情報と、そこから install できる連携機能を出す。 */
function MarketplaceDetail({
  source,
  actions,
  onBack,
  onInstalled,
}: {
  source: MarketplaceSource;
  actions: EntityAction[];
  onBack: () => void;
  onInstalled: () => void;
}) {
  const listing = useQuery({
    queryKey: ["marketplace-plugins", source.id],
    queryFn: () => agentApi.listMarketplacePlugins(source.id),
  });
  const installMutation = useMutation({
    mutationFn: (pluginId: string) => agentApi.installPlugin({ marketplace_id: source.id, plugin_id: pluginId }),
    onSuccess: () => {
      toast.success(t("plugins.installed"));
      onInstalled();
      void listing.refetch();
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const plugins = listing.data?.plugins ?? [];
  const title = source.name || source.id;

  const columns: DataTableColumn<PluginManifest>[] = [
    {
      key: "name",
      header: t("plugins.title"),
      rowHeader: true,
      render: (manifest) => (
        <div className="min-w-0">
          <p className="text-sm font-medium text-fg">{manifest.name}</p>
          <p className="font-mono text-xs text-fg-muted">
            {manifest.id}
            {manifest.version ? ` · v${manifest.version}` : ""}
          </p>
        </div>
      ),
    },
    {
      key: "description",
      header: t("agent.description"),
      className: "max-w-sm text-xs text-fg-muted",
      render: (manifest) => manifest.description || "-",
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (manifest) => (
        <RowActionMenu
          actions={[
            {
              id: "install",
              label: t("marketplaces.install"),
              icon: Download,
              disabled: installMutation.isPending,
              loading: installMutation.isPending && installMutation.variables === manifest.id,
              onSelect: () => installMutation.mutate(manifest.id),
            },
          ]}
          ariaLabel={t("common.entityActions", { name: manifest.id })}
          testId={`marketplace-plugin-row-actions-${manifest.id}`}
        />
      ),
    },
  ];

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={source.id}
        breadcrumbs={
          <EditorBreadcrumbs listLabel={t("marketplaces.title")} listHref={APP_ROUTES.pluginMarketplaces} current={title} />
        }
        actions={[
          { id: "back", kind: "secondary", label: t("common.backToList"), icon: ArrowLeft, onClick: onBack },
        ]}
      />
      <PageBody wide className="space-y-6">
        <Section
          title={t("editor.overview")}
          actions={
            <ObjectActionBar
              actions={actions}
              ariaLabel={t("common.entityActions", { name: source.id })}
              moreLabel={t("common.moreActions")}
              testId="marketplace-object-actions"
            />
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              variant="info"
              label={`${t("marketplaces.pluginCount")}: ${source.plugin_count}`}
              icon={false}
            />
            <span className="break-all text-xs text-fg-muted">{source.url || "-"}</span>
          </div>
          {source.last_error ? <Banner severity="warning">{source.last_error}</Banner> : null}
        </Section>
        <Section title={t("marketplaces.available")}>
          {listing.isLoading ? (
            <LoadingState rows={3} label={t("common.loading")} />
          ) : listing.error ? (
            <Banner severity="danger">{(listing.error as Error).message}</Banner>
          ) : (
            <DataTable
              rows={plugins}
              columns={columns}
              getRowKey={(manifest) => manifest.id}
              rowProps={() => ({ className: "align-top" })}
              tableClassName="w-full min-w-[36rem]"
              ariaLabel={t("marketplaces.available")}
              empty={<EmptyState title={t("marketplaces.availableEmpty")} />}
            />
          )}
        </Section>
      </PageBody>
    </>
  );
}

function parseCommandPrefixes(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
    )
  );
}

interface CommandPolicyDraft {
  enabled: boolean;
  workspaceRoot: string;
  allowedPrefixes: string[];
  defaultTimeout: string;
  maxTimeout: string;
  outputLimit: string;
  artifactStorageBackend: "inline" | "filesystem";
  artifactStoragePath: string;
}

export function CommandPolicySettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings", "command-policy"],
    queryFn: agentApi.getCommandPolicySettings,
  });
  const mutation = useMutation({
    mutationFn: agentApi.patchCommandPolicySettings,
    onSuccess: () => {
      toast.success(t("common.saved"));
      void queryClient.invalidateQueries({ queryKey: ["settings", "command-policy"] });
    },
  });
  const [enabled, setEnabled] = useState(false);
  const [workspaceRoot, setWorkspaceRoot] = useState(".");
  const [allowedPrefixes, setAllowedPrefixes] = useState("");
  const [defaultTimeout, setDefaultTimeout] = useState("10");
  const [maxTimeout, setMaxTimeout] = useState("30");
  const [outputLimit, setOutputLimit] = useState("20000");
  const [artifactStorageBackend, setArtifactStorageBackend] = useState<"inline" | "filesystem">("inline");
  const [artifactStoragePath, setArtifactStoragePath] = useState(".agent-artifacts");
  const [formError, setFormError] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<CommandPolicyDraft | null>(null);

  // server 値が変わったレンダーで、フォームと比較の基準を server 値に戻す（effect で setState しない）。
  const serverChanged = useValuesChanged([settings.data]);
  if (serverChanged && settings.data) {
    const current = settings.data;
    setEnabled(current.enabled);
    setWorkspaceRoot(current.workspace_root);
    setAllowedPrefixes(current.allowed_prefixes.join("\n"));
    setDefaultTimeout(String(current.default_timeout_seconds));
    setMaxTimeout(String(current.max_timeout_seconds));
    setOutputLimit(String(current.output_limit_bytes));
    setArtifactStorageBackend(current.artifact_storage_backend);
    setArtifactStoragePath(current.artifact_storage_path);
    setBaseline({
      enabled: current.enabled,
      workspaceRoot: current.workspace_root,
      allowedPrefixes: parseCommandPrefixes(current.allowed_prefixes.join("\n")).sort(),
      defaultTimeout: String(current.default_timeout_seconds),
      maxTimeout: String(current.max_timeout_seconds),
      outputLimit: String(current.output_limit_bytes),
      artifactStorageBackend: current.artifact_storage_backend,
      artifactStoragePath: current.artifact_storage_path,
    });
    setFormError(null);
  }

  // prefix は集合として比べる（順序・重複・空行の違いは変更に数えない）。#87
  const draft: CommandPolicyDraft = {
    enabled,
    workspaceRoot,
    allowedPrefixes: parseCommandPrefixes(allowedPrefixes).sort(),
    defaultTimeout,
    maxTimeout,
    outputLimit,
    artifactStorageBackend,
    artifactStoragePath,
  };
  useSettingsLeaveGuard(baseline !== null && !sameDraft(draft, baseline), mutation.isPending);

  function save() {
    const parsedDefaultTimeout = Number(defaultTimeout);
    const parsedMaxTimeout = Number(maxTimeout);
    const parsedOutputLimit = Number(outputLimit);
    if (
      !Number.isFinite(parsedDefaultTimeout) ||
      !Number.isFinite(parsedMaxTimeout) ||
      !Number.isInteger(parsedOutputLimit) ||
      parsedDefaultTimeout <= 0 ||
      parsedMaxTimeout <= 0 ||
      parsedOutputLimit <= 0
    ) {
      setFormError(t("settings.commandPolicy.invalidNumber"));
      return;
    }
    if (parsedDefaultTimeout > parsedMaxTimeout) {
      setFormError(t("settings.commandPolicy.timeoutOrderInvalid"));
      return;
    }
    setFormError(null);
    const submitted = draft;
    mutation.mutate(
      {
        enabled,
        workspace_root: workspaceRoot,
        allowed_prefixes: parseCommandPrefixes(allowedPrefixes),
        default_timeout_seconds: parsedDefaultTimeout,
        max_timeout_seconds: parsedMaxTimeout,
        output_limit_bytes: parsedOutputLimit,
        artifact_storage_backend: artifactStorageBackend,
        artifact_storage_path: artifactStoragePath,
      },
      { onSuccess: () => setBaseline(submitted) }
    );
  }

  return (
    <>
      <PageHeader wide title={t("nav.settingsCommandPolicy")} subtitle={t("page.settings.commandPolicy.subtitle")} />
      <PageBody wide>
<div className="space-y-5">
        <QueryState query={settings}>
          <Banner severity="info">{t("settings.commandPolicy.enabledHint")}</Banner>
          <Card className="min-w-0">
            <CardHeader>
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle>{t("nav.settingsCommandPolicy")}</CardTitle>
                <StatusBadge variant={enabled ? "success" : "neutral"} label={enabled ? t("agent.enabled") : t("agent.disabled")} />
                <StatusBadge
                  variant={artifactStorageBackend === "filesystem" ? "info" : "neutral"}
                  label={
                    artifactStorageBackend === "filesystem"
                      ? t("settings.commandPolicy.filesystem")
                      : t("settings.commandPolicy.inline")
                  }
                  icon={false}
                />
              </div>
              <CardDescription>{t("page.settings.commandPolicy.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border border-border px-3 py-2 text-sm text-fg">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                  className="size-4 rounded border-border text-accent-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
                <span>{t("settings.commandPolicy.enabled")}</span>
              </label>

              <div className="grid gap-4 md:grid-cols-2">
                <Field label={t("settings.commandPolicy.workspaceRoot")} htmlFor="command-policy-workspace-root">
                  <input
                    id="command-policy-workspace-root"
                    value={workspaceRoot}
                    onChange={(event) => setWorkspaceRoot(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field label={t("settings.commandPolicy.outputLimit")} htmlFor="command-policy-output-limit">
                  <input
                    id="command-policy-output-limit"
                    type="number"
                    min="1"
                    value={outputLimit}
                    onChange={(event) => setOutputLimit(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field label={t("settings.commandPolicy.defaultTimeout")} htmlFor="command-policy-default-timeout">
                  <input
                    id="command-policy-default-timeout"
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={defaultTimeout}
                    onChange={(event) => setDefaultTimeout(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field label={t("settings.commandPolicy.maxTimeout")} htmlFor="command-policy-max-timeout">
                  <input
                    id="command-policy-max-timeout"
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={maxTimeout}
                    onChange={(event) => setMaxTimeout(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
              </div>

              <Field label={t("settings.commandPolicy.allowedPrefixes")} htmlFor="command-policy-allowed-prefixes">
                <textarea
                  id="command-policy-allowed-prefixes"
                  value={allowedPrefixes}
                  onChange={(event) => setAllowedPrefixes(event.target.value)}
                  rows={5}
                  className="min-h-32 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 font-mono text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
                <p className="mt-1 text-xs leading-5 text-fg-muted">{t("settings.commandPolicy.allowedPrefixesHint")}</p>
              </Field>

              <div className="grid gap-4 md:grid-cols-2">
                <Field label={t("settings.commandPolicy.artifactStorage")} htmlFor="command-policy-artifact-storage">
                  <select
                    id="command-policy-artifact-storage"
                    value={artifactStorageBackend}
                    onChange={(event) => setArtifactStorageBackend(event.target.value as "inline" | "filesystem")}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  >
                    <option value="inline">{t("settings.commandPolicy.inline")}</option>
                    <option value="filesystem">{t("settings.commandPolicy.filesystem")}</option>
                  </select>
                  <p className="mt-1 text-xs leading-5 text-fg-muted">{t("settings.commandPolicy.storageHint")}</p>
                </Field>
                <Field label={t("settings.commandPolicy.artifactPath")} htmlFor="command-policy-artifact-path">
                  <input
                    id="command-policy-artifact-path"
                    value={artifactStoragePath}
                    onChange={(event) => setArtifactStoragePath(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
              </div>

              {formError ? <Banner severity="danger">{formError}</Banner> : null}
              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              <Button onClick={save} loading={mutation.isPending} icon={Save}>
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </div>
</PageBody>
    </>
  );
}

interface ToolPolicyDraft {
  defaultMode: "approval" | "deny";
  policies: Array<[string, ToolPolicyChoice]>;
}

/** 「既定」は未指定と同じ意味なので外し、ツール名で並べて比べる。 */
function toolPolicyDraftOf(
  defaultMode: "approval" | "deny",
  policies: Record<string, ToolPolicyChoice>
): ToolPolicyDraft {
  return {
    defaultMode,
    policies: Object.entries(policies)
      .filter(([, policy]) => policy !== "default")
      .sort(([left], [right]) => left.localeCompare(right)),
  };
}

export function ToolPolicySettingsPage() {
  const queryClient = useQueryClient();
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const settings = useQuery({
    queryKey: ["settings", "tool-policy"],
    queryFn: agentApi.getToolPolicySettings,
  });
  const mutation = useMutation({
    mutationFn: agentApi.patchToolPolicySettings,
    onSuccess: () => {
      toast.success(t("common.saved"));
      void queryClient.invalidateQueries({ queryKey: ["settings", "tool-policy"] });
    },
  });
  const [defaultMode, setDefaultMode] = useState<"approval" | "deny">("approval");
  const [toolPolicies, setToolPolicies] = useState<Record<string, ToolPolicyChoice>>({});
  const [baseline, setBaseline] = useState<ToolPolicyDraft | null>(null);

  // server 値が変わったレンダーで、フォームと比較の基準を server 値に戻す（effect で setState しない）。
  const serverChanged = useValuesChanged([settings.data]);
  if (serverChanged && settings.data) {
    const current = settings.data;
    const nextPolicies: Record<string, ToolPolicyChoice> = {};
    current.allow.forEach((name) => {
      nextPolicies[name] = "allow";
    });
    current.ask.forEach((name) => {
      nextPolicies[name] = "ask";
    });
    current.deny.forEach((name) => {
      nextPolicies[name] = "deny";
    });
    setDefaultMode(current.default_mode);
    setToolPolicies(nextPolicies);
    setBaseline(toolPolicyDraftOf(current.default_mode, nextPolicies));
  }

  const draft = toolPolicyDraftOf(defaultMode, toolPolicies);
  useSettingsLeaveGuard(baseline !== null && !sameDraft(draft, baseline), mutation.isPending);

  function setPolicy(toolName: string, policy: ToolPolicyChoice) {
    setToolPolicies((current) => ({ ...current, [toolName]: policy }));
  }

  function save() {
    const allow: string[] = [];
    const ask: string[] = [];
    const deny: string[] = [];
    for (const [toolName, policy] of Object.entries(toolPolicies)) {
      if (policy === "allow") {
        allow.push(toolName);
      } else if (policy === "ask") {
        ask.push(toolName);
      } else if (policy === "deny") {
        deny.push(toolName);
      }
    }
    const submitted = draft;
    mutation.mutate(
      {
        default_mode: defaultMode,
        allow: allow.sort(),
        ask: ask.sort(),
        deny: deny.sort(),
      },
      { onSuccess: () => setBaseline(submitted) }
    );
  }

  return (
    <>
      <PageHeader wide title={t("nav.settingsToolPolicy")} subtitle={t("page.settings.toolPolicy.subtitle")} />
      <PageBody wide>
<div className="space-y-5">
        <QueryState query={settings}>
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("nav.settingsToolPolicy")}</CardTitle>
              <CardDescription>{t("page.settings.toolPolicy.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <Field label={t("settings.toolPolicy.defaultMode")} htmlFor="tool-policy-default-mode">
                <select
                  id="tool-policy-default-mode"
                  value={defaultMode}
                  onChange={(event) => setDefaultMode(event.target.value as "approval" | "deny")}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring sm:max-w-xs"
                >
                  <option value="approval">{t("settings.toolPolicy.defaultModeApproval")}</option>
                  <option value="deny">{t("settings.toolPolicy.defaultModeDeny")}</option>
                </select>
              </Field>

              {tools.error ? <Banner severity="danger">{tools.error.message}</Banner> : null}
              {tools.isLoading ? (
                <LoadingState rows={4} label={t("common.loading")} />
              ) : (tools.data?.tools ?? []).length ? (
                <div className="grid gap-3" role="list" aria-label={t("nav.settingsToolPolicy")}>
                  {(tools.data?.tools ?? []).map((tool) => {
                    const policy = toolPolicies[tool.name] ?? "default";
                    return (
                      <div
                        key={tool.name}
                        className="grid min-w-0 gap-3 rounded-md border border-border p-3 md:grid-cols-[minmax(0,1fr)_190px] md:items-center"
                      >
                        <div className="min-w-0 space-y-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="break-all text-sm font-medium text-fg">{tool.name}</p>
                            <StatusBadge
                              variant={
                                tool.permission_level === "read"
                                  ? "success"
                                  : tool.permission_level === "write"
                                    ? "warning"
                                    : "danger"
                              }
                              label={tool.permission_level}
                              icon={false}
                            />
                            {tool.side_effects ? (
                              <StatusBadge variant="warning" label="side_effects" icon={false} />
                            ) : null}
                          </div>
                          <p className="break-words text-xs leading-5 text-fg-muted [overflow-wrap:anywhere]">
                            {tool.description}
                          </p>
                        </div>
                        <Field label={t("settings.toolPolicy.policy")} htmlFor={`tool-policy-${tool.name}`}>
                          <select
                            id={`tool-policy-${tool.name}`}
                            value={policy}
                            onChange={(event) => setPolicy(tool.name, event.target.value as ToolPolicyChoice)}
                            className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                          >
                            <option value="default">{t("settings.toolPolicy.default")}</option>
                            <option value="allow">{t("settings.toolPolicy.allow")}</option>
                            <option value="ask">{t("settings.toolPolicy.ask")}</option>
                            <option value="deny">{t("settings.toolPolicy.deny")}</option>
                          </select>
                        </Field>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <EmptyState title={t("common.empty.title")} />
              )}

              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              <Button onClick={save} loading={mutation.isPending} icon={Save}>
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </div>
</PageBody>
    </>
  );
}

export function RuntimeSafetySettingsPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({
    queryKey: ["settings", "runtime-safety"],
    queryFn: agentApi.getRuntimeSafetySettings,
  });
  const mutation = useMutation({
    mutationFn: agentApi.patchRuntimeSafetySettings,
    onSuccess: () => {
      toast.success(t("common.saved"));
      void queryClient.invalidateQueries({ queryKey: ["settings", "runtime-safety"] });
    },
  });
  const [maxToolCalls, setMaxToolCalls] = useState("20");
  const [maxPendingApprovals, setMaxPendingApprovals] = useState("5");
  const [formError, setFormError] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<{ maxToolCalls: string; maxPendingApprovals: string } | null>(null);

  // server 値が変わったレンダーで、フォームと比較の基準を server 値に戻す（effect で setState しない）。
  const serverChanged = useValuesChanged([settings.data]);
  if (serverChanged && settings.data) {
    const current = settings.data;
    setMaxToolCalls(String(current.max_tool_calls_per_run));
    setMaxPendingApprovals(String(current.max_pending_approvals_per_run));
    setBaseline({
      maxToolCalls: String(current.max_tool_calls_per_run),
      maxPendingApprovals: String(current.max_pending_approvals_per_run),
    });
    setFormError(null);
  }

  const draft = { maxToolCalls, maxPendingApprovals };
  useSettingsLeaveGuard(baseline !== null && !sameDraft(draft, baseline), mutation.isPending);

  function save() {
    const parsedMaxToolCalls = Number(maxToolCalls);
    const parsedMaxPendingApprovals = Number(maxPendingApprovals);
    if (
      !Number.isInteger(parsedMaxToolCalls) ||
      !Number.isInteger(parsedMaxPendingApprovals) ||
      parsedMaxToolCalls < 0 ||
      parsedMaxPendingApprovals < 0
    ) {
      setFormError("0 以上の整数を入力してください。");
      return;
    }
    setFormError(null);
    const submitted = draft;
    mutation.mutate(
      {
        max_tool_calls_per_run: parsedMaxToolCalls,
        max_pending_approvals_per_run: parsedMaxPendingApprovals,
      },
      { onSuccess: () => setBaseline(submitted) }
    );
  }

  return (
    <>
      <PageHeader wide title={t("nav.settingsRuntimeSafety")} subtitle={t("page.settings.runtimeSafety.subtitle")} />
      <PageBody wide>
<div className="space-y-5">
        <QueryState query={settings}>
          <Banner severity="info">{t("settings.runtimeSafety.guardrail")}</Banner>
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("nav.settingsRuntimeSafety")}</CardTitle>
              <CardDescription>{t("page.settings.runtimeSafety.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-x-6 gap-y-4 lg:grid-cols-2">
                <Field label={t("settings.runtimeSafety.maxToolCalls")} htmlFor="runtime-safety-max-tool-calls">
                  <input
                    id="runtime-safety-max-tool-calls"
                    type="number"
                    min="0"
                    value={maxToolCalls}
                    onChange={(event) => setMaxToolCalls(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field
                  label={t("settings.runtimeSafety.maxPendingApprovals")}
                  htmlFor="runtime-safety-max-pending-approvals"
                >
                  <input
                    id="runtime-safety-max-pending-approvals"
                    type="number"
                    min="0"
                    value={maxPendingApprovals}
                    onChange={(event) => setMaxPendingApprovals(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
              </div>
              {formError ? <Banner severity="danger">{formError}</Banner> : null}
              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              <Button onClick={save} loading={mutation.isPending} icon={Save}>
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </div>
</PageBody>
    </>
  );
}

export function RuntimeSnapshotSettingsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const snapshot = useQuery({
    queryKey: ["runtime", "snapshot"],
    queryFn: agentApi.exportRuntimeSnapshot,
  });
  const importSnapshot = useMutation({
    mutationFn: agentApi.importRuntimeSnapshot,
    onSuccess: (result) => {
      if (result.imported) {
        toast.success(t("settings.snapshot.imported"));
        void queryClient.invalidateQueries();
        // 置換が済んだ入力は下書きではなくなるので空に戻す（確認語も解除する）。#87
        setImportText("");
        setReason("");
        setConfirmText("");
      } else {
        toast.success(t("settings.snapshot.validated"));
      }
      setValidationResult(result);
    },
  });
  const [exportText, setExportText] = useState("");
  const [importText, setImportText] = useState("");
  const [reason, setReason] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<RuntimeSnapshotImportResult | null>(null);
  // インポート JSON と理由は未保存の下書き。確認語は保存も復元もしない（離脱で state ごと消える）。#87
  useSettingsLeaveGuard(importText.trim() !== "" || reason.trim() !== "", importSnapshot.isPending);

  // 取得したスナップショットが変わったレンダーで、エクスポート欄を取り直す（effect で setState しない）。
  const snapshotChanged = useValuesChanged([snapshot.data]);
  if (snapshotChanged && snapshot.data) {
    setExportText(JSON.stringify(snapshot.data, null, 2));
  }

  function parseImportSnapshot(): RuntimeSnapshot | null {
    setFormError(null);
    try {
      const parsed = JSON.parse(importText) as RuntimeSnapshot;
      return parsed;
    } catch {
      setFormError(t("settings.snapshot.invalidJson"));
      return null;
    }
  }

  function copyCurrentSnapshotToImport() {
    setImportText(exportText);
    setValidationResult(null);
    setFormError(null);
  }

  function downloadSnapshot() {
    if (!exportText) {
      return;
    }
    const blob = new Blob([exportText], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `agent-runtime-snapshot-${new Date().toISOString()}.json`;
    link.click();
    URL.revokeObjectURL(url);
    toast.success(t("settings.snapshot.downloaded"));
  }

  function dryRunImport() {
    const parsed = parseImportSnapshot();
    if (!parsed) {
      return;
    }
    importSnapshot.mutate({
      snapshot: parsed,
      dry_run: true,
      confirm_replace: false,
      reason: reason.trim() || null,
    });
  }

  async function replaceRuntimeSnapshot() {
    const parsed = parseImportSnapshot();
    if (!parsed) {
      return;
    }
    if (confirmText !== "REPLACE") {
      setFormError(t("settings.snapshot.confirmRequired"));
      return;
    }
    const ok = await confirm({
      title: t("settings.snapshot.replaceTitle"),
      description: t("settings.snapshot.replaceDescription"),
      confirmLabel: t("common.replace"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) {
      return;
    }
    importSnapshot.mutate({
      snapshot: parsed,
      dry_run: false,
      confirm_replace: true,
      reason: reason.trim() || null,
    });
  }

  const currentSummary = snapshot.data ? summarizeSnapshot(snapshot.data) : null;

  return (
    <>
      <PageHeader
        wide
        title={t("nav.settingsRuntimeSnapshot")}
        subtitle={t("page.settings.runtimeSnapshot.subtitle")}
        actions={
          <Button
            variant="secondary"
            onClick={() => void snapshot.refetch()}
            aria-label={t("common.retry")} icon={RefreshCw}>
            {t("common.retry")}
          </Button>
        }
      />
      <PageBody wide className="grid min-w-0 grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <QueryState query={snapshot}>
          <Card className="min-w-0">
            <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <CardTitle>{t("settings.snapshot.export")}</CardTitle>
                <CardDescription>{t("settings.snapshot.current")}</CardDescription>
              </div>
              {currentSummary ? <SnapshotSummaryBadge summary={currentSummary} /> : null}
            </CardHeader>
            <CardContent className="space-y-4">
              {currentSummary ? <SnapshotSummaryGrid summary={currentSummary} /> : null}
              <Field label={t("settings.snapshot.current")} htmlFor="runtime-snapshot-export">
                <textarea
                  id="runtime-snapshot-export"
                  value={exportText}
                  readOnly
                  className="min-h-80 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  spellCheck={false}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={downloadSnapshot} icon={Download}>
                  {t("common.download")}
                </Button>
                <Button variant="secondary" onClick={copyCurrentSnapshotToImport} icon={Upload}>
                  {t("settings.snapshot.copyCurrent")}
                </Button>
              </div>
            </CardContent>
          </Card>
        </QueryState>

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("settings.snapshot.import")}</CardTitle>
            <CardDescription>{t("page.settings.runtimeSnapshot.subtitle")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label={t("settings.snapshot.importJson")} htmlFor="runtime-snapshot-import">
              <textarea
                id="runtime-snapshot-import"
                value={importText}
                onChange={(event) => {
                  setImportText(event.target.value);
                  setValidationResult(null);
                }}
                className="min-h-80 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                spellCheck={false}
              />
            </Field>
            <Field label={t("settings.snapshot.reason")} htmlFor="runtime-snapshot-reason">
              <input
                id="runtime-snapshot-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
              />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                onClick={dryRunImport}
                loading={importSnapshot.isPending} icon={ShieldAlert}>
                {t("common.validate")}
              </Button>
            </div>
            {validationResult ? <SnapshotValidationPanel result={validationResult} /> : null}
            <div className="rounded-md border border-danger-border p-3">
              <Field label={t("settings.snapshot.confirmText")} htmlFor="runtime-snapshot-confirm">
                <input
                  id="runtime-snapshot-confirm"
                  value={confirmText}
                  onChange={(event) => setConfirmText(event.target.value)}
                  placeholder={t("settings.snapshot.confirmPlaceholder")}
                  aria-describedby="runtime-snapshot-confirm-hint"
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
                <p id="runtime-snapshot-confirm-hint" className="mt-1 text-xs leading-5 text-fg-muted">
                  {t("settings.snapshot.confirmRequired")}
                </p>
              </Field>
              <Button
                variant="danger"
                className="mt-3"
                onClick={() => void replaceRuntimeSnapshot()}
                loading={importSnapshot.isPending}
                disabled={confirmText !== "REPLACE"}
                aria-describedby={confirmText !== "REPLACE" ? "runtime-snapshot-confirm-hint" : undefined}
                icon={Upload}>
                {t("common.replace")}
              </Button>
            </div>
            {formError ? <Banner severity="danger">{formError}</Banner> : null}
            {importSnapshot.error ? <Banner severity="danger">{importSnapshot.error.message}</Banner> : null}
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}

function SnapshotSummaryBadge({ summary }: { summary: RuntimeSnapshotSummary }) {
  return (
    // 件数の表示。保留中の承認・tool call の有無は隣の集計（SnapshotSummaryGrid）が数値で示すので、
    // ここで色だけで状態を表さない。
    <StatusBadge variant="neutral" label={`${summary.runs} runs`} icon={false} />
  );
}

function SnapshotSummaryGrid({ summary }: { summary: RuntimeSnapshotSummary }) {
  const items = [
    ["runs", summary.runs],
    ["agents", summary.agents],
    ["memory", summary.memory],
    ["events", summary.events],
    ["steps", summary.steps],
    ["approvals", summary.approvals],
    ["artifacts", summary.artifacts],
    ["pending_tool_calls", summary.pending_tool_calls],
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label={t("settings.snapshot.summary")}>
      {items.map(([label, value]) => (
        <MetricPill key={label} label={String(label)} value={String(value)} />
      ))}
    </div>
  );
}

function SnapshotValidationPanel({ result }: { result: RuntimeSnapshotImportResult }) {
  const validation = result.validation;
  return (
    <div className="space-y-3">
      <Banner severity={validation.valid ? "success" : "danger"}>
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge
            variant={validation.valid ? "success" : "danger"}
            label={validation.valid ? t("common.valid") : t("common.invalid")}
          />
          <span>{result.dry_run ? t("common.validate") : t("common.replace")}</span>
        </div>
      </Banner>
      <SnapshotSummaryGrid summary={validation.summary} />
      {validation.errors.length ? (
        <Banner severity="danger" title={t("settings.snapshot.errors")}>
          <div className="space-y-1">
            {validation.errors.map((error) => (
              <p key={error} className="break-words [overflow-wrap:anywhere]">
                {error}
              </p>
            ))}
          </div>
        </Banner>
      ) : (
        <Banner severity="success">{t("settings.snapshot.noIssues")}</Banner>
      )}
      {validation.warnings.length ? (
        <Banner severity="warning" title={t("settings.snapshot.warnings")}>
          <div className="space-y-1">
            {validation.warnings.map((warning) => (
              <p key={warning} className="break-words [overflow-wrap:anywhere]">
                {warning}
              </p>
            ))}
          </div>
        </Banner>
      ) : null}
    </div>
  );
}

function summarizeSnapshot(snapshot: RuntimeSnapshot): RuntimeSnapshotSummary {
  return {
    runs: snapshot.runs.length,
    agents: snapshot.agents.length,
    memory: snapshot.memory.length,
    events: snapshot.runs.reduce((sum, run) => sum + run.events.length, 0),
    steps: snapshot.runs.reduce((sum, run) => sum + run.steps.length, 0),
    approvals: snapshot.runs.reduce((sum, run) => sum + run.approvals.length, 0),
    artifacts: snapshot.runs.reduce((sum, run) => sum + run.artifacts.length, 0),
    pending_tool_calls: snapshot.runs.reduce((sum, run) => sum + run.pending_tool_calls.length, 0),
  };
}

interface AgentDraft {
  name: string;
  description: string;
  instructions: string;
  skill_ids: string[];
}

function agentDraftOf(agent: AgentProfile | undefined): AgentDraft {
  return {
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    instructions: agent?.instructions ?? "",
    // Skill の選択は集合なので並べ替えて比べる。
    skill_ids: [...(agent?.skill_ids ?? [])].sort(),
  };
}

/** エディタの dirty を親へ知らせる。unmount 時は false を知らせる。 */
function useReportDirty(dirty: boolean, onDirtyChange: (dirty: boolean) => void) {
  const callbackRef = useRef(onDirtyChange);
  // 最新の callback を commit 時に入れる（render 中に ref を書かない）。下の effect より先に走る。
  useLayoutEffect(() => {
    callbackRef.current = onDirtyChange;
  });
  useEffect(() => {
    callbackRef.current(dirty);
  }, [dirty]);
  useEffect(() => () => callbackRef.current(false), []);
}

/**
 * 業務 Agent の全画面エディタ（A 型。`?id=new` / `?id=<agent id>`）。
 * 有効 / 無効は対象の操作（一覧の行メニュー・概要の ObjectActionBar）で切り替え、フォームの下書きには含めない
 * （切り替えで一覧を取り直しても、編集中の内容を上書きしない）。新規作成だけは初期状態をフォームで選ぶ。
 */
function AgentEditorView({
  agent,
  availableSkills,
  skillsError,
  bindings,
  runtimes,
  actions,
  onBack,
  onCreated,
}: {
  agent?: AgentProfile;
  availableSkills: AgentSkill[];
  skillsError: Error | null;
  bindings: RuntimeBinding[];
  runtimes: RuntimeDefinition[];
  actions: EntityAction[];
  onBack: () => void;
  onCreated: (agent: AgentProfile) => void;
}) {
  const queryClient = useQueryClient();
  const saved = agentDraftOf(agent);
  const savedKey = JSON.stringify(saved);
  const [name, setName] = useState(saved.name);
  const [agentDescription, setAgentDescription] = useState(saved.description);
  const [instructions, setInstructions] = useState(saved.instructions);
  const [newEnabled, setNewEnabled] = useState(true);
  const [skillIds, setSkillIds] = useState<string[]>(saved.skill_ids);
  const [baseline, setBaseline] = useState<AgentDraft>(saved);
  const [formError, setFormError] = useState<string | null>(null);

  const createAgent = useMutation({
    mutationFn: agentApi.createAgent,
    onSuccess: async (created) => {
      toast.success(t("agent.created"));
      setBaseline(draft);
      // 一覧を取り直してから作成した Agent のエディタへ移る（戻るで空の新規フォームへ戻さない）。
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      onCreated(created);
    },
  });
  const patchAgent = useMutation({
    mutationFn: (payload: AgentProfilePatchPayload) => agentApi.patchAgent(agent?.id ?? "", payload),
    onSuccess: (_data, payload) => {
      toast.success(t("agent.saved"));
      // 保存に成功した内容を基準にする（一覧の再取得を待たずに dirty を解く）。
      setBaseline({
        name: payload.name ?? "",
        description: payload.description ?? "",
        instructions: payload.instructions ?? "",
        skill_ids: [...(payload.skill_ids ?? [])].sort(),
      });
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const pending = createAgent.isPending || patchAgent.isPending;

  // 保存済みの内容が変わったときだけフォームを取り直す。有効状態の切替や他の Agent の保存による
  // 一覧の再取得で、編集中の内容を上書きしない（#87）。
  const isExisting = Boolean(agent);
  const savedChanged = useValuesChanged([isExisting, savedKey]);
  if (savedChanged && isExisting) {
    const next = JSON.parse(savedKey) as AgentDraft;
    setName(next.name);
    setAgentDescription(next.description);
    setInstructions(next.instructions);
    setSkillIds(next.skill_ids);
    setBaseline(next);
    setFormError(null);
  }

  const draft: AgentDraft = {
    name,
    description: agentDescription,
    instructions,
    skill_ids: [...skillIds].sort(),
  };
  // Agent のフォームと Binding 追加フォームの dirty を集約して 1 つの離脱ガードで守る（#87）。
  const dirtySources = useDirtySources();
  const formDirty = !sameDraft(draft, baseline) || (!agent && !newEnabled);
  useReportDirty(formDirty, (dirty) => dirtySources.report("agent", dirty));
  const { confirmClose } = useEditorLeaveGuard(dirtySources.anyDirty, pending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function toggleSkill(skillId: string) {
    setSkillIds((current) =>
      current.includes(skillId)
        ? current.filter((id) => id !== skillId)
        : [...current, skillId].sort()
    );
  }

  function saveAgent() {
    setFormError(null);
    if (!name.trim()) {
      setFormError(t("agent.nameRequired"));
      return;
    }
    const payload = {
      name: name.trim(),
      description: agentDescription.trim(),
      instructions: instructions.trim(),
      skill_ids: skillIds,
    };
    if (agent) {
      setName(payload.name);
      setAgentDescription(payload.description);
      setInstructions(payload.instructions);
      patchAgent.mutate(payload);
      return;
    }
    createAgent.mutate({ ...payload, enabled: newEnabled });
  }

  const fieldId = agent?.id ?? "new";
  const title = agent ? agent.name : t("agent.create");
  const error = createAgent.error ?? patchAgent.error;

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={agent ? agent.id : t("page.agents.subtitle")}
        breadcrumbs={<EditorBreadcrumbs listLabel={t("nav.agents")} listHref={APP_ROUTES.agents} current={title} />}
        actions={[
          { id: "back", kind: "secondary", label: t("common.backToList"), icon: ArrowLeft, onClick: () => void back() },
          {
            id: "save",
            kind: "primary",
            label: agent ? t("common.save") : t("common.create"),
            icon: Save,
            loading: pending,
            onClick: saveAgent,
          },
        ]}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {agent ? (
          <Section
            title={t("editor.overview")}
            actions={
              <ObjectActionBar
                actions={actions}
                ariaLabel={t("common.entityActions", { name: agent.name })}
                moreLabel={t("common.moreActions")}
                testId="agent-object-actions"
              />
            }
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge
                variant={agent.enabled ? "success" : "neutral"}
                label={agent.enabled ? t("agent.enabled") : t("agent.disabled")}
              />
              <span className="text-xs text-fg-muted">{`${t("common.updatedAt")}: ${formatDate(agent.updated_at)}`}</span>
            </div>
            {agent.migration_required ? <Banner severity="warning">{t("agent.migrationRequired")}</Banner> : null}
          </Section>
        ) : null}
        {formError ? <Banner severity="danger">{formError}</Banner> : null}
        {error ? <Banner severity="danger">{error.message}</Banner> : null}
        <Section title={t("agent.basic")}>
          <Card className="min-w-0">
            <CardContent className="space-y-4 pt-5">
              <Field label={t("agent.name")} htmlFor={`${fieldId}-agent-name`}>
                <input
                  id={`${fieldId}-agent-name`}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              <Field label={t("agent.description")} htmlFor={`${fieldId}-agent-description`}>
                <input
                  id={`${fieldId}-agent-description`}
                  value={agentDescription}
                  onChange={(event) => setAgentDescription(event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              <Field label={t("agent.instructions")} htmlFor={`${fieldId}-agent-instructions`}>
                <textarea
                  id={`${fieldId}-agent-instructions`}
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                  className="min-h-24 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              {!agent ? (
                <label className="flex min-h-11 items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-fg">
                  <input
                    type="checkbox"
                    checked={newEnabled}
                    onChange={(event) => setNewEnabled(event.target.checked)}
                    className="h-4 w-4"
                  />
                  {t("agent.enabled")}
                </label>
              ) : null}
            </CardContent>
          </Card>
        </Section>
        <Section title={t("agent.skills")}>
          {skillsError ? <Banner severity="danger">{skillsError.message}</Banner> : null}
          {availableSkills.length ? (
            <div className="grid gap-2 md:grid-cols-2">
              {availableSkills.map((skill) => (
                <label
                  key={skill.id}
                  className="flex min-h-11 min-w-0 flex-col items-stretch justify-between gap-2 rounded-md border border-border bg-surface px-3 py-2 text-sm md:flex-row md:items-center"
                >
                  <span className="flex min-w-0 flex-1 items-start gap-2">
                    <input
                      type="checkbox"
                      checked={skillIds.includes(skill.id)}
                      onChange={() => toggleSkill(skill.id)}
                      className="mt-0.5 h-4 w-4 shrink-0"
                    />
                    <span className="min-w-0">
                      <span className="block break-words font-medium leading-5 text-fg [overflow-wrap:anywhere]">
                        {skill.name}
                      </span>
                      <span className="mt-1 block text-xs leading-5 text-fg-muted">{skill.description}</span>
                    </span>
                  </span>
                  <span className="flex shrink-0 flex-wrap items-center gap-1.5">
                    <StatusBadge
                      variant={skillSourceVariant(skill.source)}
                      label={skillSourceLabel(skill.source)}
                      icon={false}
                    />
                    {skill.enabled ? null : <StatusBadge variant="neutral" label={t("agent.disabled")} />}
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <Banner severity="warning">{t("agent.skillsUnavailable")}</Banner>
          )}
        </Section>
        {agent ? (
          <RuntimeBindingsPanel
            agent={agent}
            bindings={bindings}
            runtimes={runtimes}
            onDirtyChange={(dirty) => dirtySources.report("binding", dirty)}
          />
        ) : null}
      </PageBody>
    </>
  );
}

function bindingSyncVariant(status: string): StatusVariant {
  if (status === "ready") return "success";
  if (status === "error") return "danger";
  return "warning";
}

/** Agent の実行先（Runtime Binding）。登録済みの行の操作は RowActionMenu にまとめ、削除は確認する。 */
function RuntimeBindingsPanel({
  agent,
  bindings,
  runtimes,
  onDirtyChange,
}: {
  agent: AgentProfile;
  bindings: RuntimeBinding[];
  runtimes: RuntimeDefinition[];
  onDirtyChange: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const candidates = runtimes.filter((runtime) => runtime.kind !== "legacy_native");
  const defaultRuntimeId = candidates[0]?.id ?? "";
  const [runtimeId, setRuntimeId] = useState(defaultRuntimeId);
  const [nativeAgentRef, setNativeAgentRef] = useState(agent.id);
  // 既定値（先頭の Runtime と Agent ID）から変えた入力を未保存の変更として扱う（#87）。
  const dirty =
    nativeAgentRef !== agent.id || (runtimeId !== "" && runtimeId !== defaultRuntimeId);
  useReportDirty(dirty, onDirtyChange);

  const refreshBindings = () => {
    void queryClient.invalidateQueries({ queryKey: ["runtime-bindings"] });
  };
  const createBinding = useMutation({
    mutationFn: agentApi.createRuntimeBinding,
    onSuccess: () => {
      toast.success(t("binding.saved"));
      setRuntimeId(defaultRuntimeId);
      setNativeAgentRef(agent.id);
      refreshBindings();
    },
  });
  const patchBinding = useMutation({
    mutationFn: ({ binding, payload }: { binding: RuntimeBinding; payload: Partial<RuntimeBinding> }) =>
      agentApi.patchRuntimeBinding(binding.id, payload),
    onSuccess: refreshBindings,
  });
  const deleteBinding = useMutation({
    mutationFn: agentApi.deleteRuntimeBinding,
    onSuccess: () => {
      toast.success(t("binding.deleted"));
      refreshBindings();
    },
  });
  const syncBinding = useMutation({
    mutationFn: agentApi.syncRuntimeBinding,
    onSuccess: refreshBindings,
  });
  const rowBusy = patchBinding.isPending || deleteBinding.isPending || syncBinding.isPending;
  const error = createBinding.error ?? patchBinding.error ?? deleteBinding.error ?? syncBinding.error;

  // Runtime が未選択のまま候補が揃ったら、先頭の候補を選ぶ（effect で setState しない。選べば条件が外れる）。
  if (!runtimeId && candidates[0]) {
    setRuntimeId(candidates[0].id);
  }

  async function removeBinding(binding: RuntimeBinding) {
    const ok = await confirm({
      title: t("binding.deleteTitle"),
      description: t("binding.deleteMessage", { ref: binding.native_agent_ref, runtime: binding.runtime_id }),
      confirmLabel: t("common.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (ok) deleteBinding.mutate(binding.id);
  }

  const bindingActions = (binding: RuntimeBinding): EntityAction[] => [
    {
      id: "make-default",
      label: t("binding.makeDefault"),
      icon: Star,
      visible: !binding.is_default,
      disabled: rowBusy,
      onSelect: () => patchBinding.mutate({ binding, payload: { is_default: true } }),
    },
    {
      id: "sync",
      label: t("binding.sync"),
      icon: RefreshCw,
      disabled: rowBusy,
      onSelect: () => syncBinding.mutate(binding.id),
    },
    {
      id: "delete",
      label: t("common.delete"),
      icon: Trash2,
      tone: "danger",
      disabled: rowBusy,
      onSelect: () => removeBinding(binding),
    },
  ];

  const columns: DataTableColumn<RuntimeBinding>[] = [
    {
      key: "native_agent_ref",
      header: t("binding.nativeAgentRef"),
      rowHeader: true,
      render: (binding) => (
        <div className="min-w-0">
          <p className="break-words text-sm font-medium text-fg">{binding.native_agent_ref}</p>
          <p className="mt-0.5 break-all text-xs text-fg-muted">{binding.runtime_id}</p>
        </div>
      ),
    },
    {
      key: "sync_status",
      header: t("binding.syncStatus"),
      render: (binding) => (
        <div className="space-y-1">
          <StatusBadge variant={bindingSyncVariant(binding.sync_status)} label={binding.sync_status} />
          {binding.sync_error ? <p className="text-xs text-danger-fg">{binding.sync_error}</p> : null}
        </div>
      ),
    },
    {
      key: "is_default",
      header: t("binding.default"),
      render: (binding) =>
        binding.is_default ? <StatusBadge variant="info" label={t("binding.default")} icon={false} /> : "-",
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (binding) => (
        <RowActionMenu
          actions={bindingActions(binding)}
          ariaLabel={t("common.entityActions", { name: binding.native_agent_ref })}
          testId={`binding-row-actions-${binding.id}`}
        />
      ),
    },
  ];

  return (
    <Section title={t("binding.title")} description={t("binding.description")}>
      <Card className="min-w-0">
        <CardContent className="space-y-4 pt-5">
          {bindings.length ? (
            <DataTable
              rows={bindings}
              columns={columns}
              getRowKey={(binding) => binding.id}
              rowProps={() => ({ className: "align-top" })}
              tableClassName="w-full min-w-[36rem]"
              ariaLabel={t("binding.list")}
            />
          ) : (
            <Banner severity="warning">{t("binding.empty")}</Banner>
          )}
          <div className="grid gap-3 md:grid-cols-2">
            <Field label={t("binding.runtime")} htmlFor={`${agent.id}-binding-runtime`}>
              <select
                id={`${agent.id}-binding-runtime`}
                value={runtimeId}
                onChange={(event) => setRuntimeId(event.target.value)}
                className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm"
              >
                {candidates.map((runtime) => (
                  <option key={runtime.id} value={runtime.id}>
                    {runtime.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("binding.nativeAgentRef")} htmlFor={`${agent.id}-binding-native-ref`}>
              <input
                id={`${agent.id}-binding-native-ref`}
                value={nativeAgentRef}
                onChange={(event) => setNativeAgentRef(event.target.value)}
                className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm"
              />
            </Field>
          </div>
          {error ? <Banner severity="danger">{error.message}</Banner> : null}
          <Button
            variant="secondary"
            loading={createBinding.isPending}
            disabled={!runtimeId || !nativeAgentRef.trim()}
            onClick={() =>
              createBinding.mutate({
                agent_id: agent.id,
                runtime_id: runtimeId,
                native_agent_ref: nativeAgentRef.trim(),
                is_default: !bindings.length,
                enabled: true,
              })
            }
            icon={Plus}
          >
            {t("binding.add")}
          </Button>
        </CardContent>
      </Card>
    </Section>
  );
}
/** Run の取消・再開の可否。一覧の行メニューと詳細の ObjectActionBar・ストリーム操作で同じ判定を使う。 */
function runCapabilities(run: RunState): { isExternal: boolean; canCancel: boolean; canResume: boolean } {
  const isExternal = Boolean(run.binding_id);
  return {
    isExternal,
    canCancel:
      ["queued", "running", "waiting_approval"].includes(run.status) &&
      (!isExternal || run.runtime_capabilities.cancel),
    canResume: !isExternal && ["running", "waiting_approval"].includes(run.status),
  };
}

function RunHistoryList({
  runs,
  selectedRunId,
  onSelect,
  actionsFor,
}: {
  runs: RunState[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  actionsFor: (run: RunState) => EntityAction[];
}) {
  const columns: DataTableColumn<RunState>[] = [
    {
      key: "goal",
      header: t("run.form.goal"),
      rowHeader: true,
      render: (run) => (
        <RowTitleButton
          title={run.goal}
          subtitle={`${run.agent_id} / ${formatDate(run.created_at)}`}
          current={run.id === selectedRunId}
          onClick={() => onSelect(run.id)}
        />
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      render: (run) => <StatusBadge variant={statusVariant[run.status]} label={run.status} />,
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
      <CardContent>
        <DataTable
          rows={runs}
          columns={columns}
          getRowKey={(run) => run.id}
          selectedRowKey={selectedRunId}
          onRowClick={(run) => onSelect(run.id)}
          rowProps={(run) => ({ className: "align-top", "data-testid": `run-row-${run.id}` })}
          ariaLabel={t("run.history")}
          empty={<EmptyState title={t("common.empty.title")} />}
        />
      </CardContent>
    </Card>
  );
}

function RunDetail({
  run,
  actions,
  actionPending,
  onWebSocketCancel,
  onWebSocketResume,
  onWebSocketApprovalDecision,
  streamMode,
  onStreamModeChange,
  websocketState,
}: {
  run: RunState;
  actions: EntityAction[];
  actionPending: boolean;
  onWebSocketCancel: () => void;
  onWebSocketResume: () => void;
  onWebSocketApprovalDecision: (approvalId: string, approved: boolean) => void;
  streamMode: RunStreamMode;
  onStreamModeChange: (mode: RunStreamMode) => void;
  websocketState: RunWebSocketState;
}) {
  const structured = getStructuredResult(run);
  const { isExternal, canCancel, canResume } = runCapabilities(run);
  const pendingApproval = run.approvals.find((approval) => approval.status === "pending");

  return (
    <section className="space-y-5" aria-label={t("run.detail")}>
      <Card>
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle>{t("run.detail")}</CardTitle>
              <StatusBadge variant={statusVariant[run.status]} label={run.status} />
            </div>
            <CardDescription className="break-words [overflow-wrap:anywhere]">{run.id}</CardDescription>
          </div>
          <ObjectActionBar
            actions={actions}
            ariaLabel={t("run.actions")}
            moreLabel={t("common.moreActions")}
            testId="run-object-actions"
          />
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm leading-6 text-fg">{run.goal}</p>
          <div className="grid gap-2 text-xs text-fg-muted sm:grid-cols-2">
            <span>{`${t("run.form.agent")}: ${run.agent_id}`}</span>
            <span>{`${t("run.runtime")}: ${run.runtime_id}`}</span>
            <span>{`${t("run.form.binding")}: ${run.binding_id ?? t("runtime.legacyReadOnly")}`}</span>
            <span>{`${t("common.createdAt")}: ${formatDate(run.created_at)}`}</span>
            <span>{`${t("common.updatedAt")}: ${formatDate(run.updated_at)}`}</span>
          </div>
          {isExternal && !run.runtime_capabilities.cancel && !isRunTerminal(run.status) ? (
            <Banner severity="warning">{t("run.cancelUnsupported")}</Banner>
          ) : null}
        </CardContent>
      </Card>

      <RunStreamControls
        mode={streamMode}
        onModeChange={onStreamModeChange}
        websocketState={websocketState}
        canCancel={canCancel}
        canResume={canResume}
        pendingApproval={pendingApproval}
        actionPending={actionPending}
        onWebSocketCancel={onWebSocketCancel}
        onWebSocketResume={onWebSocketResume}
        onWebSocketApprovalDecision={onWebSocketApprovalDecision}
      />

      {run.status === "waiting_approval" ? (
        <Banner severity="warning" title={t("run.waitingApproval")}>
          {pendingApproval?.tool_call.name}
        </Banner>
      ) : null}

      <div className="grid min-w-0 gap-5 xl:grid-cols-2">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("run.steps")}</CardTitle>
          </CardHeader>
          <CardContent>
            {run.steps.length ? (
              <div className="space-y-3">
                {run.steps.map((step) => (
                  <div key={step.id} className="min-w-0 rounded-md border border-border p-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-fg">{step.tool_call?.name ?? step.kind}</span>
                      <StatusBadge
                        variant={stepStatusVariant[step.status] ?? "neutral"}
                        label={step.status}
                      />
                    </div>
                    {step.tool_result?.error ? (
                      <p className="mt-2 text-xs text-danger-fg">{step.tool_result.error}</p>
                    ) : null}
                    {step.tool_result?.output ? <JsonPreview value={step.tool_result.output} /> : null}
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title={t("common.empty.title")} />
            )}
          </CardContent>
        </Card>

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("run.timeline")}</CardTitle>
            <CardDescription>{t("run.timelineDescription")}</CardDescription>
          </CardHeader>
          <CardContent>
            <RunTimeline events={run.events} />
          </CardContent>
        </Card>
      </div>

      <ArtifactsPanel run={run} />
      <AuditPanel runId={run.id} />

      {structured ? <StructuredResultTable result={structured} /> : null}
    </section>
  );
}

interface TimelineEventView {
  title: string;
  subtitle: string;
  icon: ReactNode;
  badgeLabel: string;
  badgeVariant: StatusVariant;
  details: Array<{ label: string; value: string }>;
  warnings: string[];
  payloadPreview?: Record<string, unknown>;
}

function RunTimeline({ events }: { events: RunEvent[] }) {
  if (!events.length) {
    return <EmptyState title={t("run.timeline.empty")} />;
  }
  return (
    <ol className="relative space-y-3" aria-label={t("run.timeline")}>
      {events.map((event) => (
        <RunTimelineItem key={event.id} event={event} />
      ))}
    </ol>
  );
}

function RunTimelineItem({ event }: { event: RunEvent }) {
  const view = timelineEventView(event);
  return (
    <li className="grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] gap-3">
      <div className="flex justify-center">
        <div className="mt-1 flex size-8 items-center justify-center rounded-full border border-border bg-surface-sunken text-fg-muted">
          {view.icon}
        </div>
      </div>
      <div className="min-w-0 rounded-md border border-border bg-surface-sunken p-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="break-words text-sm font-medium text-fg [overflow-wrap:anywhere]">
              {view.title}
            </p>
            <p className="mt-1 break-words text-xs leading-5 text-fg-muted [overflow-wrap:anywhere]">
              {view.subtitle}
            </p>
          </div>
          <StatusBadge variant={view.badgeVariant} label={view.badgeLabel} />
        </div>

        <div className="mt-3 grid min-w-0 gap-2 text-xs sm:grid-cols-2">
          <TimelineFact label={t("run.timeline.eventType")} value={event.type} />
          <TimelineFact label={t("run.timeline.time")} value={formatDate(event.created_at)} />
          {view.details.map((detail) => (
            <TimelineFact key={`${detail.label}:${detail.value}`} label={detail.label} value={detail.value} />
          ))}
        </div>

        {view.warnings.length ? (
          <div className="mt-3 flex min-w-0 flex-wrap gap-2">
            {view.warnings.map((warning) => (
              <span
                key={warning}
                className="max-w-full break-all rounded-md border border-warning-border bg-warning-subtle px-2 py-1 text-xs text-warning-fg"
              >
                {warning}
              </span>
            ))}
          </div>
        ) : null}

        {view.payloadPreview ? <JsonPreview value={view.payloadPreview} /> : null}
      </div>
    </li>
  );
}

function TimelineFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md bg-surface-hover px-2 py-1.5">
      <span className="text-fg-muted">{label}</span>
      <span className="mx-1 text-fg-muted">/</span>
      <span className="break-words font-medium text-fg [overflow-wrap:anywhere]">{value}</span>
    </div>
  );
}

function timelineEventView(event: RunEvent): TimelineEventView {
  if (event.type === "planner.completed") {
    return plannerTimelineView(event);
  }
  if (event.type === "skill.planned") {
    return skillTimelineView(event);
  }
  if (event.type.startsWith("runtime.")) {
    return {
      title: t("run.runtime"),
      subtitle: event.message,
      icon: <Server size={16} aria-hidden />,
      badgeLabel: event.type.replace("runtime.", ""),
      badgeVariant: event.type === "runtime.failed" ? "danger" : "info",
      details: compactTimelineDetails([
        [t("run.timeline.runtime"), payloadString(event.payload, "runtime_id")],
        [t("run.timeline.externalRun"), payloadString(event.payload, "external_run_id")],
      ]),
      warnings: [],
      payloadPreview: event.type === "runtime.failed" ? event.payload : undefined,
    };
  }
  if (event.type === "tool.guardrail_warning") {
    return {
      title: t("run.guardrailWarning"),
      subtitle: event.message,
      icon: <ShieldAlert size={16} aria-hidden />,
      badgeLabel: t("run.timeline.warning"),
      badgeVariant: "warning",
      details: [],
      warnings: payloadStringArray(event.payload, "warnings"),
      payloadPreview: event.payload,
    };
  }
  if (event.type.startsWith("tool.")) {
    const toolName = payloadString(event.payload, "tool_name") ?? t("common.tool");
    return {
      title: toolName,
      subtitle: event.message,
      icon: <PlayCircle size={16} aria-hidden />,
      badgeLabel: event.type.replace("tool.", ""),
      badgeVariant: event.type === "tool.failed" ? "danger" : "success",
      details: compactTimelineDetails([
        [t("run.timeline.step"), payloadString(event.payload, "step_id")],
        [t("run.auditDuration"), payloadNumberText(event.payload, "duration_ms", "ms")],
      ]),
      warnings: payloadStringArray(event.payload, "guardrail_warnings"),
    };
  }
  if (event.type.startsWith("approval.")) {
    return {
      title: t("run.auditApproval"),
      subtitle: event.message,
      icon: <Check size={16} aria-hidden />,
      badgeLabel: event.type.replace("approval.", ""),
      badgeVariant: "warning",
      details: compactTimelineDetails([
        [t("run.timeline.approval"), payloadString(event.payload, "approval_id")],
        [t("run.timeline.step"), payloadString(event.payload, "step_id")],
      ]),
      warnings: [],
    };
  }
  if (event.type === "artifact.created") {
    return {
      title: payloadString(event.payload, "name") ?? t("run.artifacts"),
      subtitle: event.message,
      icon: <FileText size={16} aria-hidden />,
      badgeLabel: payloadString(event.payload, "kind") ?? t("run.artifacts"),
      badgeVariant: "info",
      details: compactTimelineDetails([[t("run.auditArtifacts"), payloadString(event.payload, "artifact_id")]]),
      warnings: [],
    };
  }
  if (event.type.startsWith("memory.")) {
    return {
      title: t("nav.memory"),
      subtitle: event.message,
      icon: <ListChecks size={16} aria-hidden />,
      badgeLabel: event.type.replace("memory.", ""),
      badgeVariant: "neutral",
      details: compactTimelineDetails([[t("memory.kind"), payloadString(event.payload, "kind")]]),
      warnings: [],
    };
  }
  return {
    title: event.message,
    subtitle: `${event.type} / ${formatDate(event.created_at)}`,
    icon: <GitBranch size={16} aria-hidden />,
    badgeLabel: event.type.split(".")[0] ?? "event",
    badgeVariant: event.type.includes("failed") ? "danger" : "neutral",
    details: [],
    warnings: [],
  };
}

function plannerTimelineView(event: RunEvent): TimelineEventView {
  const provider = payloadString(event.payload, "provider") ?? "-";
  const planned = event.payload.planned === true;
  const warnings = payloadStringArray(event.payload, "warnings");
  const metadata = payloadRecord(event.payload.metadata);
  const phase = payloadString(metadata, "planner_phase") ?? "-";
  const selectedSkill = payloadString(event.payload, "selected_skill_id");
  const confidence = payloadNumberText(event.payload, "confidence");
  const duplicateSuppressed = warnings.some((warning) =>
    warning.includes("planner.duplicate_tool_call_suppressed")
  );
  const fallbackUsed =
    provider.includes("fallback") ||
    warnings.some((warning) => warning.includes("planner.oci_responses_failed"));
  const badgeLabel = duplicateSuppressed
    ? t("run.timeline.duplicateSuppressed")
    : fallbackUsed
      ? t("run.timeline.fallback")
      : planned
        ? t("run.timeline.planned")
        : t("run.timeline.noop");
  const title = duplicateSuppressed
    ? t("run.timeline.plannerDuplicate")
    : fallbackUsed
      ? t("run.timeline.plannerFallback")
      : phase === "continue"
        ? planned
          ? t("run.timeline.plannerContinue")
          : t("run.timeline.plannerStop")
        : planned
          ? t("run.timeline.plannerInitial")
          : t("run.timeline.plannerNoPlan");

  return {
    title,
    subtitle: payloadString(event.payload, "reason") ?? event.message,
    icon: <Brain size={16} aria-hidden />,
    badgeLabel,
    badgeVariant: duplicateSuppressed || fallbackUsed ? "warning" : planned ? "success" : "neutral",
    details: compactTimelineDetails([
      [t("run.timeline.provider"), provider],
      [t("run.timeline.phase"), phase],
      [t("run.timeline.skill"), selectedSkill],
      [t("run.timeline.confidence"), confidence],
      [t("run.timeline.toolCalls"), plannerToolCallNames(event.payload)],
    ]),
    warnings,
  };
}

function skillTimelineView(event: RunEvent): TimelineEventView {
  const skillId = payloadString(event.payload, "skill_id");
  const skillName = payloadString(event.payload, "skill_name");
  const plannedCount = payloadNumberText(event.payload, "planned_tool_call_count");
  return {
    title: t("run.timeline.skillPlanned"),
    subtitle: skillName ?? skillId ?? event.message,
    icon: <ListChecks size={16} aria-hidden />,
    badgeLabel: plannedCount ? `${plannedCount} ${t("run.timeline.tools")}` : t("run.timeline.planned"),
    badgeVariant: "info",
    details: compactTimelineDetails([
      [t("run.timeline.skill"), skillId],
      [t("run.timeline.toolCalls"), plannedToolCallNames(event.payload)],
      [t("run.timeline.step"), payloadString(event.payload, "step_id")],
    ]),
    warnings: [],
  };
}

function plannedToolCallNames(payload: Record<string, unknown>): string | null {
  const calls = payload.planned_tool_calls;
  if (!Array.isArray(calls)) {
    return null;
  }
  const names = calls
    .map((call) => (payloadRecord(call)?.name ? String(payloadRecord(call)?.name) : null))
    .filter((name): name is string => Boolean(name));
  return names.length ? names.join(" -> ") : null;
}

function plannerToolCallNames(payload: Record<string, unknown>): string | null {
  const calls = payload.tool_calls;
  if (!Array.isArray(calls)) {
    return null;
  }
  const names = calls
    .map((call) => (payloadRecord(call)?.name ? String(payloadRecord(call)?.name) : null))
    .filter((name): name is string => Boolean(name));
  return names.length ? names.join(" -> ") : null;
}

function compactTimelineDetails(items: Array<[string, string | null]>): Array<{ label: string; value: string }> {
  return items
    .filter((item): item is [string, string] => Boolean(item[1]))
    .map(([label, value]) => ({ label, value }));
}

function payloadRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function payloadString(payload: Record<string, unknown> | null, key: string): string | null {
  if (!payload) {
    return null;
  }
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function payloadStringArray(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function payloadNumberText(
  payload: Record<string, unknown>,
  key: string,
  suffix = ""
): string | null {
  const value = payload[key];
  if (typeof value !== "number" || Number.isNaN(value)) {
    return null;
  }
  return suffix ? `${value}${suffix}` : String(value);
}

function RunStreamControls({
  mode,
  onModeChange,
  websocketState,
  canCancel,
  canResume,
  pendingApproval,
  actionPending,
  onWebSocketCancel,
  onWebSocketResume,
  onWebSocketApprovalDecision,
}: {
  mode: RunStreamMode;
  onModeChange: (mode: RunStreamMode) => void;
  websocketState: RunWebSocketState;
  canCancel: boolean;
  canResume: boolean;
  pendingApproval: ApprovalRequest | undefined;
  actionPending: boolean;
  onWebSocketCancel: () => void;
  onWebSocketResume: () => void;
  onWebSocketApprovalDecision: (approvalId: string, approved: boolean) => void;
}) {
  return (
    <Card className="min-w-0">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle>{t("run.stream")}</CardTitle>
          <CardDescription>{t("run.streamDescription")}</CardDescription>
        </div>
        <StatusBadge
          variant={mode === "websocket" ? websocketStatusVariant[websocketState.status] : "info"}
          label={mode === "websocket" ? websocketStatusLabel(websocketState.status) : t("run.stream.sse")}
          // WebSocket は接続状態（アイコンあり）、SSE は方式名（状態ではない）
          icon={mode === "websocket"}
        />
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          className="grid min-h-11 grid-cols-2 overflow-hidden rounded-md border border-border text-sm"
          role="group"
          aria-label={t("run.streamMode")}
        >
          <button
            type="button"
            aria-pressed={mode === "sse"}
            onClick={() => onModeChange("sse")}
            className={`px-3 py-2 font-medium outline-none transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring ${
              mode === "sse"
                ? "bg-accent-emphasis text-fg-on-accent"
                : "bg-surface-sunken text-fg-muted hover:bg-surface-hover hover:text-fg"
            }`}
          >
            {t("run.stream.sse")}
          </button>
          <button
            type="button"
            aria-pressed={mode === "websocket"}
            onClick={() => onModeChange("websocket")}
            className={`border-l border-border px-3 py-2 font-medium outline-none transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring ${
              mode === "websocket"
                ? "bg-accent-emphasis text-fg-on-accent"
                : "bg-surface-sunken text-fg-muted hover:bg-surface-hover hover:text-fg"
            }`}
          >
            {t("run.stream.websocket")}
          </button>
        </div>

        {mode === "websocket" ? (
          <div className="grid min-w-0 gap-3 text-sm md:grid-cols-3 xl:grid-cols-5">
            <StreamMetric label={t("run.stream.heartbeat")} value={websocketState.lastHeartbeat ?? "-"} />
            <StreamMetric label={t("run.stream.ack")} value={websocketState.lastAck ?? "-"} />
            <StreamMetric label={t("run.stream.error")} value={websocketState.lastError ?? "-"} />
            <StreamMetric label={t("run.stream.lastEvent")} value={websocketState.lastEventId ?? "-"} />
            <StreamMetric
              label={t("run.stream.reconnects")}
              value={String(websocketState.reconnectAttempts)}
            />
          </div>
        ) : (
          <p className="text-sm leading-6 text-fg-muted">{t("run.stream.sseDescription")}</p>
        )}

        {mode === "websocket" && (pendingApproval || canResume || canCancel) ? (
          <div className="flex flex-wrap gap-2">
            {pendingApproval ? (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => onWebSocketApprovalDecision(pendingApproval.id, true)}
                  loading={actionPending}
                  disabled={websocketState.status !== "open"}
                  aria-label={t("run.stream.wsApprove")} icon={Check}>
                  {t("run.stream.wsApprove")}
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => onWebSocketApprovalDecision(pendingApproval.id, false)}
                  loading={actionPending}
                  disabled={websocketState.status !== "open"}
                  aria-label={t("run.stream.wsReject")} icon={X}>
                  {t("run.stream.wsReject")}
                </Button>
              </>
            ) : null}
            {canResume ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={onWebSocketResume}
                loading={actionPending}
                disabled={websocketState.status !== "open"}
                aria-label={t("run.stream.wsResume")} icon={PlayCircle}>
                {t("run.stream.wsResume")}
              </Button>
            ) : null}
            {canCancel ? (
              <Button
                variant="danger"
                size="sm"
                onClick={onWebSocketCancel}
                loading={actionPending}
                disabled={websocketState.status !== "open"}
                aria-label={t("run.stream.wsCancel")} icon={X}>
                {t("run.stream.wsCancel")}
              </Button>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function StreamMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-surface-hover px-3 py-2">
      <p className="text-xs font-medium text-fg-muted">{label}</p>
      <p className="mt-1 break-words text-sm text-fg [overflow-wrap:anywhere]">{value}</p>
    </div>
  );
}

function websocketStatusLabel(status: WebSocketStreamStatus): string {
  const labels: Record<WebSocketStreamStatus, string> = {
    idle: t("run.stream.wsIdle"),
    connecting: t("run.stream.wsConnecting"),
    open: t("run.stream.wsOpen"),
    reconnecting: t("run.stream.wsReconnecting"),
    closed: t("run.stream.wsClosed"),
    error: t("run.stream.wsError"),
  };
  return labels[status];
}

function AuditPanel({ runId }: { runId: string }) {
  const audit = useQuery({
    queryKey: ["runs", runId, "audit"],
    queryFn: () => agentApi.getRunAudit(runId),
  });

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.audit")}</CardTitle>
        <CardDescription>{t("run.auditDescription")}</CardDescription>
      </CardHeader>
      <CardContent>
        <QueryState query={audit}>
          {audit.data?.records.length ? (
            <div className="grid min-w-0 gap-3">
              {audit.data.records.map((record) => (
                <AuditRecordItem key={record.step_id} audit={audit.data} record={record} />
              ))}
            </div>
          ) : (
            <EmptyState title={t("run.noAudit")} />
          )}
        </QueryState>
      </CardContent>
    </Card>
  );
}

function AuditRecordItem({ audit, record }: { audit: RunAuditData; record: ToolAuditRecord }) {
  const status: StatusVariant =
    record.status === "completed"
      ? "success"
      : record.status === "failed"
        ? "danger"
        : record.status === "waiting_approval"
          ? "warning"
          : "neutral";

  return (
    <div className="min-w-0 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-all text-sm font-medium text-fg">{record.tool_name}</p>
          <p className="mt-0.5 break-all text-xs text-fg-muted">{`${audit.status} / ${record.step_id}`}</p>
        </div>
        <StatusBadge variant={status} label={record.status} />
      </div>

      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
        <AuditFact label={t("run.auditPolicy")} value={record.policy_decision ?? "-"} />
        <AuditFact label={t("common.permission")} value={record.permission_level ?? "-"} />
        <AuditFact label={t("run.auditApproval")} value={record.approval_status ?? "-"} />
        <AuditFact
          label={t("run.auditDuration")}
          value={record.duration_ms === null || record.duration_ms === undefined ? "-" : `${record.duration_ms}ms`}
        />
      </div>

      {record.trace_id ? (
        <p className="mt-3 break-all text-xs text-fg-muted">{`${t("run.auditTrace")}: ${record.trace_id}`}</p>
      ) : null}
      {record.artifact_ids.length ? (
        <div className="mt-3 flex min-w-0 flex-wrap gap-2">
          {record.artifact_ids.map((artifactId) => (
            <span key={artifactId} className="max-w-full break-all rounded-md border border-border px-2 py-1 text-xs text-fg-muted">
              {`${t("run.auditArtifacts")}: ${artifactId}`}
            </span>
          ))}
        </div>
      ) : null}
      {record.guardrail_warnings.length ? (
        <Banner severity="warning" title={t("run.auditWarnings")}>
          <div className="space-y-1">
            {record.guardrail_warnings.map((warning) => (
              <p key={warning} className="break-words [overflow-wrap:anywhere]">{warning}</p>
            ))}
          </div>
        </Banner>
      ) : null}
      {record.error ? (
        <Banner severity="danger" title={record.error_code ?? t("common.error")}>
          {record.error}
        </Banner>
      ) : null}
      <div className="mt-3">
        <JsonPanel title={t("run.auditMetadata")} value={record.audit_metadata} />
      </div>
    </div>
  );
}

function AuditFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-surface-sunken px-3 py-2">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className="mt-1 break-words text-xs font-medium text-fg [overflow-wrap:anywhere]">{value}</p>
    </div>
  );
}

function ArtifactsPanel({ run }: { run: RunState }) {
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.artifacts")}</CardTitle>
        <CardDescription>{t("run.artifactsDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {run.artifacts.length ? (
          run.artifacts.map((artifact) => (
            <div key={artifact.id} className="min-w-0 rounded-md border border-border p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-all text-sm font-medium text-fg">{artifact.name}</p>
                  <p className="mt-0.5 text-xs text-fg-muted">{formatDate(artifact.created_at)}</p>
                </div>
                <StatusBadge variant={artifact.kind === "rag_evidence" ? "info" : "success"} label={artifact.kind} icon={false} />
              </div>
              {artifact.kind === "rag_evidence" ? (
                <RagEvidenceArtifact artifact={artifact} />
              ) : artifact.kind === "structured_table" ? (
                <StructuredArtifactSummary artifact={artifact} />
              ) : (
                <JsonPreview value={artifact.content} />
              )}
            </div>
          ))
        ) : (
          <EmptyState title={t("run.noArtifacts")} />
        )}
      </CardContent>
    </Card>
  );
}

function RagEvidenceArtifact({ artifact }: { artifact: Artifact }) {
  const answer = typeof artifact.content.answer === "string" ? artifact.content.answer : null;
  const citations = arrayOfRecords(artifact.content.citations);
  const contexts = arrayOfRecords(artifact.content.contexts);

  return (
    <div className="mt-3 space-y-4">
      {answer ? (
        <section className="space-y-1">
          <h3 className="text-sm font-medium text-fg">{t("run.ragAnswer")}</h3>
          <p className="break-words text-sm leading-6 text-fg [overflow-wrap:anywhere]">{answer}</p>
        </section>
      ) : null}
      {citations.length ? (
        <section className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-medium text-fg">
            <FileText size={16} aria-hidden />
            {t("run.citations")}
          </h3>
          <div className="grid gap-2">
            {citations.map((citation, index) => (
              <EvidenceItem
                key={String(citation.id ?? citation.url ?? index)}
                title={textValue(citation.title) ?? textValue(citation.source) ?? `#${index + 1}`}
                subtitle={textValue(citation.url) ?? textValue(citation.source)}
                detail={textValue(citation.snippet) ?? textValue(citation.text)}
              />
            ))}
          </div>
        </section>
      ) : null}
      {contexts.length ? (
        <section className="space-y-2">
          <h3 className="text-sm font-medium text-fg">{t("run.contexts")}</h3>
          <div className="grid gap-2">
            {contexts.slice(0, 6).map((context, index) => (
              <EvidenceItem
                key={String(context.id ?? index)}
                title={textValue(context.title) ?? textValue(context.source) ?? `context ${index + 1}`}
                subtitle={formatContextSubtitle(context)}
                detail={textValue(context.text) ?? textValue(context.snippet) ?? textValue(context.content)}
              />
            ))}
          </div>
        </section>
      ) : null}
      <JsonPreview value={artifact.content} />
    </div>
  );
}

function StructuredArtifactSummary({ artifact }: { artifact: Artifact }) {
  const rowCount = numericValue(artifact.content.row_count);
  const truncated = typeof artifact.content.truncated === "boolean" ? artifact.content.truncated : null;
  const warnings = arrayOfText(artifact.content.warnings);

  return (
    <div className="mt-3 space-y-3">
      <div className="grid gap-2 text-sm sm:grid-cols-3">
        <MetricPill label={t("run.rowCount")} value={rowCount === null ? "-" : String(rowCount)} />
        <MetricPill label={t("run.truncated")} value={truncated === null ? "-" : truncated ? "true" : "false"} />
        <MetricPill label={t("run.columns")} value={String(arrayOfRecords(artifact.content.columns).length)} />
      </div>
      {typeof artifact.content.sql === "string" ? (
        <JsonPanel title="sql" value={artifact.content.sql} />
      ) : null}
      {warnings.length ? (
        <Banner severity="warning">
          <div className="space-y-1">
            {warnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </div>
        </Banner>
      ) : null}
      <JsonPreview value={artifact.content} />
    </div>
  );
}

function EvidenceItem({
  title,
  subtitle,
  detail,
}: {
  title: string;
  subtitle?: string | null;
  detail?: string | null;
}) {
  return (
    <div className="min-w-0 rounded-md bg-surface-sunken p-3">
      <p className="break-words text-sm font-medium text-fg [overflow-wrap:anywhere]">{title}</p>
      {subtitle ? <p className="mt-1 break-all text-xs text-fg-muted">{subtitle}</p> : null}
      {detail ? (
        <p className="mt-2 line-clamp-4 break-words text-xs leading-5 text-fg [overflow-wrap:anywhere]">
          {detail}
        </p>
      ) : null}
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border px-3 py-2">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className="mt-1 text-sm font-medium text-fg">{value}</p>
    </div>
  );
}

function ToolCard({ tool }: { tool: ToolDefinition }) {
  const permissionVariant: StatusVariant =
    tool.permission_level === "read" ? "success" : tool.permission_level === "write" ? "warning" : "danger";
  return (
    <Card className="min-w-0">
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle>{tool.name}</CardTitle>
          <CardDescription>{tool.description}</CardDescription>
        </div>
        <StatusBadge variant={permissionVariant} label={tool.permission_level} icon={false} />
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {tool.audit_tags.map((tag) => (
            <span key={tag} className="rounded-md border border-border px-2 py-1 text-xs text-fg-muted">
              {tag}
            </span>
          ))}
        </div>
        <div className="grid min-w-0 gap-3 md:grid-cols-2">
          <JsonPanel title="input_schema" value={tool.input_schema} />
          <JsonPanel title="output_schema" value={tool.output_schema} />
        </div>
      </CardContent>
    </Card>
  );
}

function StructuredResultTable({ result }: { result: StructuredResult }) {
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.structuredResult")}</CardTitle>
        <CardDescription>{result.sql ?? t("run.sqlHidden")}</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          rows={result.rows}
          columns={result.columns.map((column): DataTableColumn<Record<string, unknown>> => ({
            key: column.name,
            header: column.label ?? column.name,
            render: (row) => formatValue(row[column.name]),
          }))}
          getRowKey={(_, index) => index}
          tableClassName="w-full min-w-[560px]"
          ariaLabel={t("run.structuredResult")}
          empty={t("common.empty.title")}
        />
      </CardContent>
    </Card>
  );
}

function ConnectionBanner({ settings }: { settings?: ExternalServiceSettings }) {
  if (!settings) {
    return null;
  }
  return (
    <Banner severity={settings.configured ? "success" : "warning"}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span>{settings.configured ? t("common.configured") : t("common.notConfigured")}</span>
        <span>{`${t("settings.timeout")}: ${settings.timeout_seconds}`}</span>
        <span>{`${t("settings.apiKey")}: ${settings.api_key_configured ? t("common.configured") : t("common.notConfigured")}`}</span>
      </div>
    </Banner>
  );
}

function Field({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={className ? `space-y-1.5 ${className}` : "space-y-1.5"}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-fg">
        {label}
      </label>
      {children}
    </div>
  );
}

function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs font-medium text-fg-muted">{title}</p>
      <JsonPreview value={value} />
    </div>
  );
}

function JsonPreview({ value }: { value: unknown }) {
  return (
    <pre className="mt-2 max-h-64 w-full min-w-0 max-w-full overflow-auto rounded-md bg-surface-sunken p-3 text-xs leading-5 text-fg">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function QueryState<T>({
  query,
  children,
}: {
  query: { isLoading: boolean; error: Error | null; data?: T };
  children: ReactNode;
}) {
  if (query.isLoading) {
    return <LoadingState rows={4} label={t("common.loading")} />;
  }
  if (query.error) {
    return <ErrorState message={query.error.message} retryLabel={t("common.retry")} />;
  }
  return <>{children}</>;
}

interface StructuredColumn {
  name: string;
  type: string;
  label?: string | null;
  unit?: string | null;
}

interface StructuredResult {
  sql?: string | null;
  columns: StructuredColumn[];
  rows: Record<string, unknown>[];
}

function getStructuredResult(run: RunState): StructuredResult | null {
  const output = run.steps
    .map((step) => step.tool_result?.output)
    .find((candidate) => candidate && Array.isArray(candidate.columns) && Array.isArray(candidate.rows));
  if (!output) {
    return null;
  }
  const columns = output.columns;
  const rows = output.rows;
  if (!Array.isArray(columns) || !Array.isArray(rows)) {
    return null;
  }
  return {
    sql: typeof output.sql === "string" ? output.sql : null,
    columns: columns.filter(isStructuredColumn),
    rows: rows.filter(isRecord),
  };
}

function isStructuredColumn(value: unknown): value is StructuredColumn {
  return isRecord(value) && typeof value.name === "string" && typeof value.type === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function arrayOfRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function arrayOfText(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function textValue(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}

function numericValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatContextSubtitle(context: Record<string, unknown>): string | null {
  const parts = [
    textValue(context.source),
    numericValue(context.score) === null ? null : `${t("run.score")}: ${numericValue(context.score)}`,
  ].filter((part): part is string => Boolean(part));
  return parts.length ? parts.join(" / ") : null;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatValue(value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
