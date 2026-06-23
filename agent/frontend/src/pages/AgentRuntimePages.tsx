import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  Check,
  Download,
  FileText,
  GitBranch,
  ListChecks,
  Pencil,
  PlayCircle,
  Plus,
  RefreshCw,
  Save,
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
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
  Switch,
  toast,
  useConfirm,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import {
  agentApi,
  type AgentProfile,
  type AgentProfileWritePayload,
  type AgentSkill,
  type AgentSkillToolCall,
  type Artifact,
  type ApprovalRequest,
  type ExternalMcpServerSettings,
  type ExternalMcpToolInfo,
  type ExternalServiceSettings,
  type MarketplaceSource,
  type PluginManifest,
  type PluginSummary,
  type MemoryKind,
  type RuntimeSnapshot,
  type RuntimeSnapshotImportResult,
  type RuntimeSnapshotSummary,
  type RunAuditData,
  type RunEvent,
  type RunState,
  type ToolCallAuditFilters,
  type ToolCallAuditRecord,
  type ToolAuditRecord,
  type ToolDefinition,
} from "@/lib/api";
import { t } from "@/lib/i18n";

type ToolChoice = "none" | "echo" | "external_rag_search" | "external_nl2sql_query";
type ToolPolicyChoice = "default" | "allow" | "ask" | "deny";
type RunStreamMode = "sse" | "websocket";
type WebSocketStreamStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error";

const DEFAULT_ARGUMENTS: Record<ToolChoice, string> = {
  none: "{}",
  echo: '{\n  "sample": true\n}',
  external_rag_search: '{\n  "query": "確認したい業務質問",\n  "top_k": 5\n}',
  external_nl2sql_query:
    '{\n  "question": "今月の売上を部門別に集計して",\n  "mode": "dry_run",\n  "limit": 100\n}',
};

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
  waiting_approval: "pending",
  completed: "success",
  failed: "danger",
  cancelled: "warning",
};

const stepStatusVariant: Record<string, StatusVariant> = {
  pending: "neutral",
  running: "info",
  waiting_approval: "pending",
  completed: "success",
  failed: "danger",
  cancelled: "warning",
};

const websocketStatusVariant: Record<WebSocketStreamStatus, StatusVariant> = {
  idle: "neutral",
  connecting: "pending",
  open: "success",
  reconnecting: "pending",
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

  useEffect(() => {
    if (activeRunIdRef.current !== runId) {
      activeRunIdRef.current = runId ?? null;
      reconnectAttemptRef.current = 0;
      lastEventIdRef.current = null;
      setReconnectAttempts(0);
      setLastEventId(null);
      setLastHeartbeat(null);
      setLastAck(null);
      setLastError(null);
    }

    if (!enabled || !runId || !runStatus || isRunTerminal(runStatus)) {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      socketRef.current?.close();
      socketRef.current = null;
      setStatus("idle");
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
      setStatus(isReconnect ? "reconnecting" : "connecting");
      setLastError(null);

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
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const createAgent = useMutation({
    mutationFn: agentApi.createAgent,
    onSuccess: () => {
      toast.success(t("agent.created"));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const patchAgent = useMutation({
    mutationFn: ({ agent, payload }: { agent: AgentProfile; payload: AgentProfileWritePayload }) =>
      agentApi.patchAgent(agent.id, payload),
    onSuccess: () => {
      toast.success(t("agent.saved"));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const availableTools = tools.data?.tools ?? [];

  return (
    <>
      <PageHeader title={t("nav.agents")} subtitle={t("page.agents.subtitle")} />
      <main className="grid min-w-0 grid-cols-1 gap-5 p-6 xl:grid-cols-[420px_minmax(0,1fr)] md:p-8">
        <AgentEditor
          title={t("agent.create")}
          description={t("page.agents.subtitle")}
          availableTools={availableTools}
          pending={createAgent.isPending}
          error={createAgent.error}
          onSave={(payload) => createAgent.mutate(payload)}
        />
        <QueryState query={agents}>
          <div className="grid min-w-0 gap-4">
            {tools.error ? <Banner severity="danger">{tools.error.message}</Banner> : null}
            {(agents.data?.agents ?? []).length ? (
              (agents.data?.agents ?? []).map((agent) => (
                <AgentEditor
                  key={agent.id}
                  agent={agent}
                  title={agent.name}
                  description={agent.id}
                  availableTools={availableTools}
                  pending={patchAgent.isPending}
                  error={patchAgent.error}
                  onSave={(payload) => patchAgent.mutate({ agent, payload })}
                />
              ))
            ) : (
              <EmptyState title={t("common.empty.title")} />
            )}
          </div>
        </QueryState>
      </main>
    </>
  );
}

export function RunsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: agentApi.listRuns,
    refetchInterval: 5000,
  });
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
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
  const [goal, setGoal] = useState("外部データを確認して要点を整理する");
  const [agentId, setAgentId] = useState("default");
  const [tool, setTool] = useState<ToolChoice>("none");
  const [argumentsText, setArgumentsText] = useState(DEFAULT_ARGUMENTS.none);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [streamMode, setStreamMode] = useState<RunStreamMode>("sse");

  const runItems = runs.data?.runs ?? [];
  const selectedRun = runItems.find((run) => run.id === selectedRunId) ?? runItems[0];
  const selectedAgent = agents.data?.agents.find((agent) => agent.id === agentId);
  const selectableTools = useMemo(() => {
    const allTools = tools.data?.tools ?? [];
    if (!selectedAgent) {
      return allTools;
    }
    const allowed = new Set(selectedAgent.tool_names);
    return allTools.filter((definition) => allowed.has(definition.name));
  }, [selectedAgent, tools.data?.tools]);

  useEffect(() => {
    if (!selectedRunId && runItems.length) {
      setSelectedRunId(runItems[0].id);
    }
    if (selectedRunId && runItems.length && !runItems.some((run) => run.id === selectedRunId)) {
      setSelectedRunId(runItems[0].id);
    }
  }, [runItems, selectedRunId]);

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
      "memory.written",
    ];
    eventTypes.forEach((type) => source.addEventListener(type, refresh));
    source.onerror = () => source.close();
    return () => source.close();
  }, [selectedRun, refreshRuntimeEvents, streamMode]);

  function onToolChange(value: ToolChoice) {
    setTool(value);
    setArgumentsText(DEFAULT_ARGUMENTS[value]);
    setFormError(null);
  }

  function onAgentChange(value: string) {
    setAgentId(value);
    const nextAgent = agents.data?.agents.find((agent) => agent.id === value);
    if (tool !== "none" && nextAgent && !nextAgent.tool_names.includes(tool)) {
      onToolChange("none");
    }
  }

  function submitRun() {
    setFormError(null);
    let parsedArguments: Record<string, unknown> = {};
    if (tool !== "none") {
      try {
        parsedArguments = JSON.parse(argumentsText) as Record<string, unknown>;
      } catch {
        setFormError("引数 JSON を確認してください。");
        return;
      }
    }
    createRun.mutate({
      goal,
      agent_id: agentId,
      tool_calls: tool === "none" ? [] : [{ name: tool, arguments: parsedArguments }],
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

  return (
    <>
      <PageHeader
        title={t("nav.runs")}
        subtitle={t("page.runs.subtitle")}
        actions={
          <Button variant="secondary" onClick={() => void runs.refetch()} aria-label="実行一覧を再読み込み">
            <RefreshCw size={15} aria-hidden />
            {t("common.retry")}
          </Button>
        }
      />
      <main className="grid min-w-0 grid-cols-1 gap-5 p-6 xl:grid-cols-[420px_minmax(0,1fr)] md:p-8">
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
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                  className="min-h-24 w-full rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
              </Field>
              <Field label={t("run.form.tool")} htmlFor="run-tool">
                <select
                  id="run-tool"
                  value={tool}
                  onChange={(event) => onToolChange(event.target.value as ToolChoice)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <option value="none">{t("run.form.noTool")}</option>
                  {selectableTools.map((definition) => (
                    <option key={definition.name} value={definition.name}>
                      {definition.name}
                    </option>
                  ))}
                </select>
              </Field>
              {tool !== "none" ? (
                <Field label={t("run.form.arguments")} htmlFor="run-arguments">
                  <textarea
                    id="run-arguments"
                    value={argumentsText}
                    onChange={(event) => setArgumentsText(event.target.value)}
                    className="min-h-44 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    spellCheck={false}
                  />
                </Field>
              ) : null}
              {formError ? <Banner severity="danger">{formError}</Banner> : null}
              {createRun.error ? <Banner severity="danger">{createRun.error.message}</Banner> : null}
              <Button onClick={submitRun} loading={createRun.isPending} className="w-full">
                <PlayCircle size={16} aria-hidden />
                {t("run.form.submit")}
              </Button>
            </CardContent>
          </Card>

          <QueryState query={runs}>
            <RunHistoryList
              runs={runItems}
              selectedRunId={selectedRun?.id ?? null}
              onSelect={setSelectedRunId}
            />
          </QueryState>
        </div>

        <QueryState query={runs}>
          {selectedRun ? (
            <RunDetail
              run={selectedRun}
              actionPending={cancelRun.isPending || resumeRun.isPending || replayRun.isPending}
              onCancel={() => void cancelLatestRun(selectedRun)}
              onWebSocketCancel={() => void cancelLatestRun(selectedRun, true)}
              onResume={() => resumeRun.mutate(selectedRun.id)}
              onWebSocketResume={() => websocketState.sendResume()}
              onWebSocketApprovalDecision={(approvalId, approved) =>
                websocketState.sendApprovalDecision(approvalId, approved)
              }
              onReplay={() => replayRun.mutate(selectedRun.id)}
              streamMode={streamMode}
              onStreamModeChange={setStreamMode}
              websocketState={websocketState}
            />
          ) : (
            <EmptyState title={t("common.empty.title")} />
          )}
        </QueryState>
      </main>
    </>
  );
}

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
  });
  const approvals = useMemo(
    () => (runs.data?.runs ?? []).flatMap((run) => run.approvals.map((approval) => ({ run, approval }))),
    [runs.data?.runs]
  );

  async function decideApproval(approval: ApprovalRequest, approved: boolean) {
    const ok = await confirm({
      title: approved ? t("run.approveTitle") : t("run.rejectTitle"),
      description: approval.tool_call.name,
      confirmLabel: approved ? t("common.approve") : t("common.reject"),
      tone: approved ? "info" : "danger",
    });
    if (ok) {
      decide.mutate({ approval, approved });
    }
  }

  return (
    <>
      <PageHeader title={t("nav.approvals")} subtitle={t("page.approvals.subtitle")} />
      <main className="p-6 md:p-8">
        <QueryState query={runs}>
          {approvals.length ? (
            <div className="grid gap-4">
              {approvals.map(({ run, approval }) => (
                <Card key={approval.id}>
                  <CardHeader className="flex-row items-start justify-between gap-4">
                    <div>
                      <CardTitle>{approval.tool_call.name}</CardTitle>
                      <CardDescription>{run.goal}</CardDescription>
                    </div>
                    <StatusBadge
                      variant={approval.status === "pending" ? "pending" : approval.status === "approved" ? "success" : "danger"}
                      label={approval.status}
                    />
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <JsonPreview value={approval.tool_call.arguments} />
                    {approval.status === "pending" ? (
                      <div className="flex flex-wrap gap-2">
                        <Button
                          size="sm"
                          onClick={() => void decideApproval(approval, true)}
                          loading={decide.isPending}
                        >
                          <Check size={15} aria-hidden />
                          {t("common.approve")}
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => void decideApproval(approval, false)}
                          loading={decide.isPending}
                        >
                          <X size={15} aria-hidden />
                          {t("common.reject")}
                        </Button>
                      </div>
                    ) : null}
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <EmptyState title={t("common.empty.title")} />
          )}
        </QueryState>
      </main>
    </>
  );
}

type AuditWarningsFilter = "any" | "true" | "false";

export function AuditPage() {
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const [runId, setRunId] = useState("");
  const [toolName, setToolName] = useState("");
  const [stepStatus, setStepStatus] = useState("");
  const [approvalStatus, setApprovalStatus] = useState("");
  const [errorCode, setErrorCode] = useState("");
  const [warnings, setWarnings] = useState<AuditWarningsFilter>("any");
  const [limit, setLimit] = useState("100");
  const [appliedFilters, setAppliedFilters] = useState<ToolCallAuditFilters>({ limit: 100 });
  const audit = useQuery({
    queryKey: ["audit", "tool-calls", appliedFilters],
    queryFn: () => agentApi.listToolCallAudit(appliedFilters),
  });

  function currentFilters(): ToolCallAuditFilters {
    const parsedLimit = Number(limit);
    return {
      run_id: runId.trim() || undefined,
      tool_name: toolName || undefined,
      status: stepStatus || undefined,
      approval_status: approvalStatus || undefined,
      error_code: errorCode.trim() || undefined,
      has_guardrail_warnings: warnings === "any" ? undefined : warnings === "true",
      limit: Number.isInteger(parsedLimit) && parsedLimit > 0 ? parsedLimit : 100,
      offset: 0,
    };
  }

  function applyFilters() {
    setAppliedFilters(currentFilters());
  }

  function downloadCsv() {
    const link = document.createElement("a");
    link.href = agentApi.toolCallAuditCsvUrl(currentFilters());
    link.download = "agent-tool-call-audit.csv";
    link.click();
    toast.success(t("audit.csvDownloaded"));
  }

  return (
    <>
      <PageHeader
        title={t("nav.audit")}
        subtitle={t("page.audit.subtitle")}
        actions={
          <Button variant="secondary" onClick={() => void audit.refetch()} aria-label={t("common.retry")}>
            <RefreshCw size={15} aria-hidden />
            {t("common.retry")}
          </Button>
        }
      />
      <main className="space-y-5 p-6 md:p-8">
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
                  onChange={(event) => setRunId(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
              </Field>
              <Field label={t("audit.toolName")} htmlFor="audit-tool-name">
                <select
                  id="audit-tool-name"
                  value={toolName}
                  onChange={(event) => setToolName(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                  onChange={(event) => setStepStatus(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                  onChange={(event) => setApprovalStatus(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                  onChange={(event) => setErrorCode(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
              </Field>
              <Field label={t("audit.guardrailWarnings")} htmlFor="audit-warning-filter">
                <select
                  id="audit-warning-filter"
                  value={warnings}
                  onChange={(event) => setWarnings(event.target.value as AuditWarningsFilter)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                  onChange={(event) => setLimit(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
              </Field>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={applyFilters} loading={audit.isFetching}>
                <RefreshCw size={15} aria-hidden />
                {t("audit.apply")}
              </Button>
              <Button variant="secondary" onClick={downloadCsv}>
                <Download size={15} aria-hidden />
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
            {audit.data ? <StatusBadge variant="info" label={`${t("audit.total")}: ${audit.data.total}`} /> : null}
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
      </main>
    </>
  );
}

function AuditRecordsTable({ records }: { records: ToolCallAuditRecord[] }) {
  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full min-w-[980px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-border text-xs text-muted">
            <th className="px-3 py-2 font-medium">{t("audit.runGoal")}</th>
            <th className="px-3 py-2 font-medium">{t("audit.toolName")}</th>
            <th className="px-3 py-2 font-medium">{t("audit.stepStatus")}</th>
            <th className="px-3 py-2 font-medium">{t("audit.approvalStatus")}</th>
            <th className="px-3 py-2 font-medium">{t("run.auditPolicy")}</th>
            <th className="px-3 py-2 font-medium">{t("common.permission")}</th>
            <th className="px-3 py-2 font-medium">{t("audit.guardrailWarnings")}</th>
            <th className="px-3 py-2 font-medium">{t("run.auditDuration")}</th>
            <th className="px-3 py-2 font-medium">{t("run.auditTrace")}</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={`${record.run_id}:${record.step_id}`} className="border-b border-border/70 align-top">
              <td className="max-w-72 px-3 py-3">
                <p className="break-words text-sm font-medium text-foreground [overflow-wrap:anywhere]">
                  {record.run_goal}
                </p>
                <p className="mt-1 break-all text-xs text-muted">{record.run_id}</p>
                <p className="mt-1 text-xs text-muted">{formatDate(record.run_created_at)}</p>
              </td>
              <td className="px-3 py-3">
                <p className="break-all font-medium text-foreground">{record.tool_name}</p>
                {record.error_code ? (
                  <p className="mt-1 break-words text-xs text-danger [overflow-wrap:anywhere]">
                    {record.error_code}
                  </p>
                ) : null}
              </td>
              <td className="px-3 py-3">
                <StatusBadge
                  variant={stepStatusVariant[record.status] ?? "neutral"}
                  label={record.status}
                />
              </td>
              <td className="px-3 py-3">
                {record.approval_status ? (
                  <StatusBadge
                    variant={approvalStatusVariant(record.approval_status)}
                    label={record.approval_status}
                  />
                ) : (
                  <span className="text-xs text-muted">-</span>
                )}
              </td>
              <td className="px-3 py-3 text-xs text-foreground">{record.policy_decision ?? "-"}</td>
              <td className="px-3 py-3">
                <StatusBadge
                  variant={permissionStatusVariant(record.permission_level)}
                  label={record.permission_level ?? "-"}
                />
              </td>
              <td className="max-w-64 px-3 py-3">
                {record.guardrail_warnings.length ? (
                  <div className="space-y-1">
                    {record.guardrail_warnings.map((warning) => (
                      <p key={warning} className="break-words text-xs text-warning [overflow-wrap:anywhere]">
                        {warning}
                      </p>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-muted">-</span>
                )}
              </td>
              <td className="px-3 py-3 text-xs text-foreground">
                {record.duration_ms === null || record.duration_ms === undefined ? "-" : `${record.duration_ms}ms`}
              </td>
              <td className="max-w-48 px-3 py-3">
                <p className="break-all text-xs text-muted">{record.trace_id ?? "-"}</p>
                {record.artifact_ids.length ? (
                  <p className="mt-1 text-xs text-muted">{`${t("run.auditArtifacts")}: ${record.artifact_ids.length}`}</p>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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

  return (
    <>
      <PageHeader title={t("nav.tools")} subtitle={t("page.tools.subtitle")} />
      <main className="p-6 md:p-8">
        <QueryState query={tools}>
          <div className="grid gap-4 xl:grid-cols-2">
            {(tools.data?.tools ?? []).map((tool) => (
              <ToolCard key={tool.name} tool={tool} />
            ))}
          </div>
        </QueryState>
      </main>
    </>
  );
}

export function MemoryPage() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<MemoryKind>("user_preference");
  const [content, setContent] = useState("");
  const [metadataText, setMetadataText] = useState("{}");
  const [formError, setFormError] = useState<string | null>(null);
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

  return (
    <>
      <PageHeader title={t("nav.memory")} subtitle={t("page.memory.subtitle")} />
      <main className="space-y-5 p-6 md:p-8">
        <div className="grid min-w-0 grid-cols-1 gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("memory.create")}</CardTitle>
              <CardDescription>{t("page.memory.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Field label={t("memory.kind")} htmlFor="memory-kind">
                <select
                  id="memory-kind"
                  value={kind}
                  onChange={(event) => setKind(event.target.value as MemoryKind)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                  className="min-h-28 w-full rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
              </Field>
              <Field label={t("memory.metadata")} htmlFor="memory-metadata">
                <textarea
                  id="memory-metadata"
                  value={metadataText}
                  onChange={(event) => setMetadataText(event.target.value)}
                  className="min-h-24 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  spellCheck={false}
                />
              </Field>
              {formError ? <Banner severity="danger">{formError}</Banner> : null}
              {addMemory.error ? <Banner severity="danger">{addMemory.error.message}</Banner> : null}
              <Button onClick={submitMemory} loading={addMemory.isPending} className="w-full">
                <Save size={16} aria-hidden />
                {t("memory.create")}
              </Button>
            </CardContent>
          </Card>
          <Card className="min-w-0">
            <CardContent className="pt-5">
            <Field label={t("common.search")} htmlFor="memory-search">
              <input
                id="memory-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              />
            </Field>
            </CardContent>
          </Card>
        </div>
        <QueryState query={memory}>
          {(memory.data?.entries ?? []).length ? (
            <div className="grid min-w-0 gap-3">
              {(memory.data?.entries ?? []).map((entry) => (
                <Card key={entry.id} className="min-w-0">
                  <CardContent className="min-w-0 space-y-2 pt-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge variant="info" label={entry.kind} />
                      <span className="text-xs text-muted">{formatDate(entry.created_at)}</span>
                    </div>
                    <p className="break-words text-sm leading-6 text-foreground [overflow-wrap:anywhere]">{entry.content}</p>
                    <JsonPreview value={entry.metadata} />
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <EmptyState title={t("common.empty.title")} />
          )}
        </QueryState>
      </main>
    </>
  );
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

  useEffect(() => {
    const current = settings.data;
    if (current) {
      setBaseUrl(current.base_url ?? "");
      setTimeoutSeconds(String(current.timeout_seconds));
      setDefaultLimit(String(current.default_limit ?? 100));
    }
  }, [settings.data]);

  function save() {
    mutation.mutate({
      base_url: baseUrl,
      timeout_seconds: Number(timeoutSeconds),
      default_limit: isNl2Sql ? Number(defaultLimit) : undefined,
    });
  }

  return (
    <>
      <PageHeader title={title} subtitle={subtitle} />
      <main className="max-w-3xl space-y-5 p-6 md:p-8">
        <QueryState query={settings}>
          <ConnectionBanner settings={settings.data} />
          <Card>
            <CardHeader>
              <CardTitle>{title}</CardTitle>
              <CardDescription>{t("settings.apiKeyManaged")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Field label={t("settings.baseUrl")} htmlFor={`${kind}-base-url`}>
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
              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              <Button onClick={save} loading={mutation.isPending}>
                <Save size={15} aria-hidden />
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </main>
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
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
          </Field>
          <Field label={t("settings.mcpDiscovery.traceId")} htmlFor="mcp-discovery-trace-id">
            <input
              id="mcp-discovery-trace-id"
              value={traceId}
              onChange={(event) => setTraceId(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
          </Field>
          <Button
            variant="secondary"
            onClick={() => void tools.refetch()}
            disabled={!configured}
            loading={tools.isFetching}
            className="min-h-10"
          >
            <RefreshCw size={15} aria-hidden />
            {t("settings.mcpDiscovery.refresh")}
          </Button>
        </div>

        {!configured ? (
          <Banner severity="warning">{t("settings.mcpDiscovery.configureFirst")}</Banner>
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
  return (
    <div className="min-w-0">
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[720px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs text-muted">
              <th className="px-3 py-2 font-medium">{t("settings.mcpDiscovery.tool")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpDiscovery.descriptionColumn")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpDiscovery.server")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpDiscovery.inputSchema")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpDiscovery.outputSchema")}</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((tool) => (
              <tr key={`${tool.server_id ?? "default"}:${tool.name}`} className="border-b border-border/70 last:border-0">
                <td className="px-3 py-3 align-top font-mono text-xs text-foreground">{tool.name}</td>
                <td className="max-w-sm px-3 py-3 align-top text-muted">{tool.description || "-"}</td>
                <td className="px-3 py-3 align-top text-muted">{tool.server_id ?? "-"}</td>
                <td className="px-3 py-3 align-top text-muted">{schemaSummary(tool.input_schema)}</td>
                <td className="px-3 py-3 align-top text-muted">{schemaSummary(tool.output_schema)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 md:hidden">
        {tools.map((tool) => (
          <div key={`${tool.server_id ?? "default"}:${tool.name}`} className="rounded-md border border-border p-3">
            <div className="min-w-0 space-y-1">
              <p className="break-words font-mono text-xs font-medium text-foreground">{tool.name}</p>
              <p className="text-sm leading-6 text-muted">{tool.description || "-"}</p>
            </div>
            <dl className="mt-3 grid grid-cols-1 gap-2 text-xs text-muted">
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
      <dt className="font-medium text-foreground">{label}</dt>
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
  "h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";
const TEXTAREA_CLASS =
  "w-full rounded-md border border-border bg-background p-3 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

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

export function McpServersPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const servers = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: agentApi.listExternalMcpServers,
  });
  const [form, setForm] = useState<McpServerFormState>(EMPTY_MCP_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    void queryClient.invalidateQueries({ queryKey: ["external-mcp-tools"] });
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        label: form.label || null,
        base_url: form.baseUrl,
        timeout_seconds: Number(form.timeoutSeconds),
        session_id: form.sessionId || undefined,
        oauth_token_url: form.oauthTokenUrl || undefined,
        oauth_client_id: form.oauthClientId || undefined,
        oauth_client_secret: form.oauthClientSecret || undefined,
        oauth_scope: form.oauthScope || undefined,
      };
      if (editingId) {
        return agentApi.updateExternalMcpServer(editingId, payload);
      }
      return agentApi.createExternalMcpServer({ server_id: form.serverId.trim(), ...payload });
    },
    onSuccess: () => {
      toast.success(editingId ? t("settings.mcpServers.updated") : t("settings.mcpServers.created"));
      closeForm();
      invalidate();
    },
  });

  const setDefaultMutation = useMutation({
    mutationFn: (serverId: string) => agentApi.setDefaultExternalMcpServer(serverId),
    onSuccess: () => {
      toast.success(t("settings.mcpServers.defaultUpdated"));
      invalidate();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (serverId: string) => agentApi.deleteExternalMcpServer(serverId),
    onSuccess: () => {
      toast.success(t("settings.mcpServers.deleted"));
      invalidate();
    },
  });

  function openCreate() {
    setEditingId(null);
    setForm(EMPTY_MCP_FORM);
    setFormOpen(true);
  }

  function openEdit(server: ExternalMcpServerSettings) {
    setEditingId(server.server_id);
    setForm({
      ...EMPTY_MCP_FORM,
      serverId: server.server_id,
      label: server.label ?? "",
      baseUrl: server.base_url ?? "",
      timeoutSeconds: String(server.timeout_seconds),
    });
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingId(null);
    setForm(EMPTY_MCP_FORM);
  }

  function save() {
    if (!editingId && !form.serverId.trim()) {
      toast.error(t("settings.mcpServers.idRequired"));
      return;
    }
    saveMutation.mutate();
  }

  async function remove(server: ExternalMcpServerSettings) {
    const ok = await confirm({
      title: t("settings.mcpServers.confirmDeleteTitle"),
      description: t("settings.mcpServers.confirmDeleteMessage", { id: server.server_id }),
      confirmLabel: t("settings.mcpServers.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (ok) {
      deleteMutation.mutate(server.server_id);
    }
  }

  const list = servers.data?.servers ?? [];
  const anyConfigured = list.some((server) => server.configured);
  const busy = setDefaultMutation.isPending || deleteMutation.isPending;

  return (
    <>
      <PageHeader title={t("nav.settingsExternalMcp")} subtitle={t("page.settings.mcp.subtitle")} />
      <main className="max-w-5xl space-y-5 p-6 md:p-8">
        <QueryState query={servers}>
          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-3">
              <div className="space-y-1">
                <CardTitle>{t("settings.mcpServers.title")}</CardTitle>
                <CardDescription>{t("settings.mcpServers.description")}</CardDescription>
              </div>
              <Button size="sm" onClick={openCreate}>
                <Plus size={15} aria-hidden />
                {t("settings.mcpServers.add")}
              </Button>
            </CardHeader>
            <CardContent>
              {list.length === 0 ? (
                <EmptyState title={t("settings.mcpServers.empty")} />
              ) : (
                <McpServerTable
                  servers={list}
                  onEdit={openEdit}
                  onDelete={remove}
                  onSetDefault={(id) => setDefaultMutation.mutate(id)}
                  busy={busy}
                />
              )}
            </CardContent>
          </Card>

          {formOpen ? (
            <Card>
              <CardHeader>
                <CardTitle>
                  {editingId ? t("settings.mcpServers.editTitle") : t("settings.mcpServers.addTitle")}
                </CardTitle>
                <CardDescription>{t("settings.apiKeyManaged")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Field label={t("settings.mcpServers.serverId")} htmlFor="mcp-server-id">
                  <input
                    id="mcp-server-id"
                    value={form.serverId}
                    disabled={Boolean(editingId)}
                    onChange={(event) => setForm({ ...form, serverId: event.target.value })}
                    className={editingId ? `${INPUT_CLASS} opacity-60` : INPUT_CLASS}
                  />
                  <p className="mt-1 text-xs leading-5 text-muted">
                    {t("settings.mcpServers.serverIdHint")}
                  </p>
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
                <div className="grid gap-4 md:grid-cols-2">
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
                  <Field
                    label={t("settings.mcpServers.oauthClientSecret")}
                    htmlFor="mcp-server-oauth-secret"
                  >
                    <input
                      id="mcp-server-oauth-secret"
                      type="password"
                      value={form.oauthClientSecret}
                      autoComplete="off"
                      onChange={(event) => setForm({ ...form, oauthClientSecret: event.target.value })}
                      className={INPUT_CLASS}
                    />
                  </Field>
                </div>
                {saveMutation.error ? (
                  <Banner severity="danger">{(saveMutation.error as Error).message}</Banner>
                ) : null}
                <div className="flex gap-2">
                  <Button onClick={save} loading={saveMutation.isPending}>
                    <Save size={15} aria-hidden />
                    {editingId ? t("common.save") : t("common.create")}
                  </Button>
                  <Button variant="ghost" onClick={closeForm}>
                    <X size={15} aria-hidden />
                    {t("common.cancel")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}

          <McpDiscoveryPanel configured={anyConfigured} />
        </QueryState>
      </main>
    </>
  );
}

function McpServerTable({
  servers,
  onEdit,
  onDelete,
  onSetDefault,
  busy,
}: {
  servers: ExternalMcpServerSettings[];
  onEdit: (server: ExternalMcpServerSettings) => void;
  onDelete: (server: ExternalMcpServerSettings) => void;
  onSetDefault: (serverId: string) => void;
  busy: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs text-muted">
              <th className="px-3 py-2 font-medium">{t("settings.mcpServers.serverId")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpServers.label")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.baseUrl")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpServers.auth")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpServers.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {servers.map((server) => (
              <tr key={server.server_id} className="border-b border-border/70 last:border-0">
                <td className="px-3 py-3 align-top">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs text-foreground">{server.server_id}</span>
                    {server.is_default ? (
                      <StatusBadge variant="info" label={t("settings.mcpServers.default")} />
                    ) : null}
                  </div>
                </td>
                <td className="px-3 py-3 align-top text-muted">{server.label || "-"}</td>
                <td className="max-w-xs break-all px-3 py-3 align-top text-muted">
                  {server.base_url || "-"}
                </td>
                <td className="px-3 py-3 align-top text-muted">{mcpAuthLabel(server.auth_mode)}</td>
                <td className="px-3 py-3 align-top">
                  <McpServerActions
                    server={server}
                    onEdit={onEdit}
                    onDelete={onDelete}
                    onSetDefault={onSetDefault}
                    busy={busy}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 md:hidden">
        {servers.map((server) => (
          <div key={server.server_id} className="space-y-2 rounded-md border border-border p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-xs text-foreground">{server.server_id}</span>
              {server.is_default ? (
                <StatusBadge variant="info" label={t("settings.mcpServers.default")} />
              ) : null}
            </div>
            <p className="text-sm text-muted">{server.label || "-"}</p>
            <p className="break-all text-xs text-muted">{server.base_url || "-"}</p>
            <p className="text-xs text-muted">
              {t("settings.mcpServers.auth")}: {mcpAuthLabel(server.auth_mode)}
            </p>
            <McpServerActions
              server={server}
              onEdit={onEdit}
              onDelete={onDelete}
              onSetDefault={onSetDefault}
              busy={busy}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function McpServerActions({
  server,
  onEdit,
  onDelete,
  onSetDefault,
  busy,
}: {
  server: ExternalMcpServerSettings;
  onEdit: (server: ExternalMcpServerSettings) => void;
  onDelete: (server: ExternalMcpServerSettings) => void;
  onSetDefault: (serverId: string) => void;
  busy: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {!server.is_default ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onSetDefault(server.server_id)}
          disabled={busy}
          aria-label={`${t("settings.mcpServers.setDefault")} ${server.server_id}`}
        >
          <Star size={14} aria-hidden />
          {t("settings.mcpServers.setDefault")}
        </Button>
      ) : null}
      <Button
        size="sm"
        variant="secondary"
        onClick={() => onEdit(server)}
        aria-label={`${t("settings.mcpServers.edit")} ${server.server_id}`}
      >
        <Pencil size={14} aria-hidden />
        {t("settings.mcpServers.edit")}
      </Button>
      <Button
        size="sm"
        variant="danger"
        onClick={() => onDelete(server)}
        disabled={server.server_id === "default" || busy}
        aria-label={`${t("settings.mcpServers.delete")} ${server.server_id}`}
      >
        <Trash2 size={14} aria-hidden />
        {t("settings.mcpServers.delete")}
      </Button>
    </div>
  );
}

interface SkillFormState {
  id: string;
  name: string;
  description: string;
  instructions: string;
  tags: string;
  enabled: boolean;
  toolCallsJson: string;
}

const EMPTY_SKILL_FORM: SkillFormState = {
  id: "",
  name: "",
  description: "",
  instructions: "",
  tags: "",
  enabled: true,
  toolCallsJson: "[]",
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

export function SkillsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const skills = useQuery({ queryKey: ["skills"], queryFn: agentApi.listSkills });
  const [form, setForm] = useState<SkillFormState>(EMPTY_SKILL_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["skills"] });
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const toolCalls = JSON.parse(form.toolCallsJson) as AgentSkillToolCall[];
      const payload = {
        name: form.name,
        description: form.description,
        instructions: form.instructions,
        tags: form.tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        enabled: form.enabled,
        tool_calls: toolCalls,
      };
      if (editingId) {
        return agentApi.updateSkill(editingId, payload);
      }
      return agentApi.createSkill({ id: form.id.trim(), ...payload });
    },
    onSuccess: () => {
      toast.success(editingId ? t("skills.updated") : t("skills.created"));
      closeForm();
      invalidate();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (skillId: string) => agentApi.deleteSkill(skillId),
    onSuccess: () => {
      toast.success(t("skills.deleted"));
      invalidate();
    },
  });

  const reloadMutation = useMutation({
    mutationFn: () => agentApi.reloadSkills(),
    onSuccess: () => {
      toast.success(t("skills.reloaded"));
      invalidate();
    },
  });

  function openCreate() {
    setEditingId(null);
    setForm(EMPTY_SKILL_FORM);
    setFormError(null);
    setFormOpen(true);
  }

  function openEdit(skill: AgentSkill) {
    setEditingId(skill.id);
    setForm({
      id: skill.id,
      name: skill.name,
      description: skill.description,
      instructions: skill.instructions,
      tags: skill.tags.join(", "),
      enabled: skill.enabled,
      toolCallsJson: JSON.stringify(skill.tool_calls, null, 2),
    });
    setFormError(null);
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingId(null);
    setForm(EMPTY_SKILL_FORM);
    setFormError(null);
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
    let parsed: unknown;
    try {
      parsed = JSON.parse(form.toolCallsJson);
    } catch {
      setFormError(t("skills.invalidJson"));
      return;
    }
    if (!Array.isArray(parsed)) {
      setFormError(t("skills.invalidJson"));
      return;
    }
    saveMutation.mutate();
  }

  async function remove(skill: AgentSkill) {
    const ok = await confirm({
      title: t("skills.confirmDeleteTitle"),
      description: t("skills.confirmDeleteMessage", { id: skill.id }),
      confirmLabel: t("skills.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (ok) {
      deleteMutation.mutate(skill.id);
    }
  }

  const list = skills.data?.skills ?? [];
  const detail = detailId ? (list.find((skill) => skill.id === detailId) ?? null) : null;

  return (
    <>
      <PageHeader title={t("skills.title")} subtitle={t("page.skills.subtitle")} />
      <main className="max-w-5xl space-y-5 p-6 md:p-8">
        <QueryState query={skills}>
          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-3">
              <div className="space-y-1">
                <CardTitle>{t("skills.title")}</CardTitle>
                <CardDescription>{t("skills.description")}</CardDescription>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => reloadMutation.mutate()}
                  loading={reloadMutation.isPending}
                >
                  <RefreshCw size={15} aria-hidden />
                  {t("skills.reload")}
                </Button>
                <Button size="sm" onClick={openCreate}>
                  <Plus size={15} aria-hidden />
                  {t("skills.add")}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {list.length === 0 ? (
                <EmptyState title={t("skills.empty")} />
              ) : (
                <SkillTable
                  skills={list}
                  onEdit={openEdit}
                  onDelete={remove}
                  onDetail={(id) => setDetailId((current) => (current === id ? null : id))}
                  busy={deleteMutation.isPending}
                />
              )}
            </CardContent>
          </Card>

          {detail ? <SkillDetailCard skill={detail} onClose={() => setDetailId(null)} /> : null}

          {formOpen ? (
            <Card>
              <CardHeader>
                <CardTitle>{editingId ? t("skills.editTitle") : t("skills.addTitle")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
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
                  <p className="mt-1 text-xs leading-5 text-muted">{t("skills.tagsHint")}</p>
                </Field>
                <Field label={t("skills.toolCalls")} htmlFor="skill-tool-calls">
                  <textarea
                    id="skill-tool-calls"
                    value={form.toolCallsJson}
                    rows={8}
                    spellCheck={false}
                    onChange={(event) => setForm({ ...form, toolCallsJson: event.target.value })}
                    className={`${TEXTAREA_CLASS} font-mono`}
                  />
                  <p className="mt-1 text-xs leading-5 text-muted">{t("skills.toolCallsHint")}</p>
                </Field>
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <Switch
                    checked={form.enabled}
                    aria-label={t("skills.enabledLabel")}
                    onCheckedChange={(checked) => setForm({ ...form, enabled: checked })}
                  />
                  {t("skills.enabledLabel")}
                </label>
                {formError ? <Banner severity="danger">{formError}</Banner> : null}
                {saveMutation.error ? (
                  <Banner severity="danger">{(saveMutation.error as Error).message}</Banner>
                ) : null}
                <div className="flex gap-2">
                  <Button onClick={save} loading={saveMutation.isPending}>
                    <Save size={15} aria-hidden />
                    {editingId ? t("common.save") : t("common.create")}
                  </Button>
                  <Button variant="ghost" onClick={closeForm}>
                    <X size={15} aria-hidden />
                    {t("common.cancel")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}
        </QueryState>
      </main>
    </>
  );
}

function SkillTable({
  skills,
  onEdit,
  onDelete,
  onDetail,
  busy,
}: {
  skills: AgentSkill[];
  onEdit: (skill: AgentSkill) => void;
  onDelete: (skill: AgentSkill) => void;
  onDetail: (skillId: string) => void;
  busy: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs text-muted">
              <th className="px-3 py-2 font-medium">{t("skills.skill")}</th>
              <th className="px-3 py-2 font-medium">{t("skills.source")}</th>
              <th className="px-3 py-2 font-medium">{t("common.status")}</th>
              <th className="px-3 py-2 font-medium">{t("skills.tags")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpServers.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {skills.map((skill) => (
              <tr key={skill.id} className="border-b border-border/70 last:border-0">
                <td className="px-3 py-3 align-top">
                  <p className="text-sm font-medium text-foreground">{skill.name}</p>
                  <p className="font-mono text-xs text-muted">{skill.id}</p>
                </td>
                <td className="px-3 py-3 align-top">
                  <StatusBadge
                    variant={skillSourceVariant(skill.source)}
                    label={skillSourceLabel(skill.source)}
                  />
                </td>
                <td className="px-3 py-3 align-top">
                  <StatusBadge
                    variant={skill.enabled ? "success" : "neutral"}
                    label={skill.enabled ? t("agent.enabled") : t("agent.disabled")}
                  />
                </td>
                <td className="px-3 py-3 align-top text-xs text-muted">
                  {skill.tags.length ? skill.tags.join(", ") : "-"}
                </td>
                <td className="px-3 py-3 align-top">
                  <SkillActions
                    skill={skill}
                    onEdit={onEdit}
                    onDelete={onDelete}
                    onDetail={onDetail}
                    busy={busy}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 md:hidden">
        {skills.map((skill) => (
          <div key={skill.id} className="space-y-2 rounded-md border border-border p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">{skill.name}</p>
                <p className="font-mono text-xs text-muted">{skill.id}</p>
              </div>
              <StatusBadge
                variant={skillSourceVariant(skill.source)}
                label={skillSourceLabel(skill.source)}
              />
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge
                variant={skill.enabled ? "success" : "neutral"}
                label={skill.enabled ? t("agent.enabled") : t("agent.disabled")}
              />
              <span className="text-xs text-muted">
                {skill.tags.length ? skill.tags.join(", ") : "-"}
              </span>
            </div>
            <SkillActions
              skill={skill}
              onEdit={onEdit}
              onDelete={onDelete}
              onDetail={onDetail}
              busy={busy}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function SkillActions({
  skill,
  onEdit,
  onDelete,
  onDetail,
  busy,
}: {
  skill: AgentSkill;
  onEdit: (skill: AgentSkill) => void;
  onDelete: (skill: AgentSkill) => void;
  onDetail: (skillId: string) => void;
  busy: boolean;
}) {
  const editable = skill.source === "runtime";
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        size="sm"
        variant="ghost"
        onClick={() => onDetail(skill.id)}
        aria-label={`${t("skills.detail")} ${skill.id}`}
      >
        <FileText size={14} aria-hidden />
        {t("skills.detail")}
      </Button>
      {editable ? (
        <>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => onEdit(skill)}
            aria-label={`${t("skills.edit")} ${skill.id}`}
          >
            <Pencil size={14} aria-hidden />
            {t("skills.edit")}
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => onDelete(skill)}
            disabled={busy}
            aria-label={`${t("skills.delete")} ${skill.id}`}
          >
            <Trash2 size={14} aria-hidden />
            {t("skills.delete")}
          </Button>
        </>
      ) : null}
    </div>
  );
}

function SkillDetailCard({ skill, onClose }: { skill: AgentSkill; onClose: () => void }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>{skill.name}</CardTitle>
          <CardDescription>{skill.description || skill.id}</CardDescription>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("common.cancel")}>
          <X size={15} aria-hidden />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge
            variant={skillSourceVariant(skill.source)}
            label={skillSourceLabel(skill.source)}
          />
          <StatusBadge
            variant={skill.enabled ? "success" : "neutral"}
            label={skill.enabled ? t("agent.enabled") : t("agent.disabled")}
          />
        </div>
        {skill.source !== "runtime" ? (
          <Banner severity="info">{t("skills.readOnly")}</Banner>
        ) : null}
        {skill.instructions ? (
          <div>
            <p className="mb-1 text-xs font-medium text-muted">{t("skills.instructions")}</p>
            <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
              {skill.instructions}
            </p>
          </div>
        ) : null}
        <JsonPanel title={t("skills.toolCalls")} value={skill.tool_calls} />
      </CardContent>
    </Card>
  );
}

export function PluginsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: agentApi.listPlugins });
  const [manifestJson, setManifestJson] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["plugins"] });
    void queryClient.invalidateQueries({ queryKey: ["skills"] });
    void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    void queryClient.invalidateQueries({ queryKey: ["agents"] });
  }

  const installMutation = useMutation({
    mutationFn: () =>
      agentApi.installPlugin({ manifest: JSON.parse(manifestJson) as PluginManifest }),
    onSuccess: () => {
      toast.success(t("plugins.installed"));
      setFormOpen(false);
      setManifestJson("");
      invalidate();
    },
  });
  const enabledMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      agentApi.setPluginEnabled(id, enabled),
    onSuccess: () => {
      toast.success(t("plugins.enabledUpdated"));
      invalidate();
    },
  });
  const uninstallMutation = useMutation({
    mutationFn: (id: string) => agentApi.uninstallPlugin(id),
    onSuccess: () => {
      toast.success(t("plugins.uninstalled"));
      invalidate();
    },
  });
  const reloadMutation = useMutation({
    mutationFn: () => agentApi.reloadPlugins(),
    onSuccess: invalidate,
  });

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
    installMutation.mutate();
  }

  async function uninstall(plugin: PluginSummary) {
    const ok = await confirm({
      title: t("plugins.confirmUninstallTitle"),
      description: t("plugins.confirmUninstallMessage", { id: plugin.id }),
      confirmLabel: t("plugins.uninstall"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (ok) {
      uninstallMutation.mutate(plugin.id);
    }
  }

  const list = plugins.data?.plugins ?? [];
  const busy = uninstallMutation.isPending || enabledMutation.isPending;

  return (
    <>
      <PageHeader title={t("plugins.title")} subtitle={t("page.plugins.subtitle")} />
      <main className="max-w-5xl space-y-5 p-6 md:p-8">
        <QueryState query={plugins}>
          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-3">
              <div className="space-y-1">
                <CardTitle>{t("plugins.title")}</CardTitle>
                <CardDescription>{t("plugins.description")}</CardDescription>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => reloadMutation.mutate()}
                  loading={reloadMutation.isPending}
                >
                  <RefreshCw size={15} aria-hidden />
                  {t("skills.reload")}
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    setFormOpen(true);
                    setFormError(null);
                  }}
                >
                  <Plus size={15} aria-hidden />
                  {t("plugins.install")}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {list.length === 0 ? (
                <EmptyState title={t("plugins.empty")} />
              ) : (
                <PluginTable
                  plugins={list}
                  onToggle={(id, enabled) => enabledMutation.mutate({ id, enabled })}
                  onUninstall={uninstall}
                  busy={busy}
                />
              )}
            </CardContent>
          </Card>

          {formOpen ? (
            <Card>
              <CardHeader>
                <CardTitle>{t("plugins.installTitle")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Field label={t("plugins.manifest")} htmlFor="plugin-manifest">
                  <textarea
                    id="plugin-manifest"
                    value={manifestJson}
                    rows={12}
                    spellCheck={false}
                    onChange={(event) => setManifestJson(event.target.value)}
                    className={`${TEXTAREA_CLASS} font-mono`}
                  />
                  <p className="mt-1 text-xs leading-5 text-muted">{t("plugins.manifestHint")}</p>
                </Field>
                {formError ? <Banner severity="danger">{formError}</Banner> : null}
                {installMutation.error ? (
                  <Banner severity="danger">{(installMutation.error as Error).message}</Banner>
                ) : null}
                <div className="flex gap-2">
                  <Button onClick={install} loading={installMutation.isPending}>
                    <Download size={15} aria-hidden />
                    {t("plugins.installSubmit")}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setFormOpen(false);
                      setFormError(null);
                    }}
                  >
                    <X size={15} aria-hidden />
                    {t("common.cancel")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}
        </QueryState>
      </main>
    </>
  );
}

function PluginBundle({ plugin }: { plugin: PluginSummary }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <StatusBadge variant="info" label={`${t("plugins.skills")} ${plugin.skill_count}`} />
      <StatusBadge variant="info" label={`${t("plugins.mcp")} ${plugin.mcp_count}`} />
      <StatusBadge variant="info" label={`${t("plugins.agents")} ${plugin.agent_count}`} />
    </div>
  );
}

function PluginTable({
  plugins,
  onToggle,
  onUninstall,
  busy,
}: {
  plugins: PluginSummary[];
  onToggle: (id: string, enabled: boolean) => void;
  onUninstall: (plugin: PluginSummary) => void;
  busy: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border text-xs text-muted">
              <th className="px-3 py-2 font-medium">{t("plugins.title")}</th>
              <th className="px-3 py-2 font-medium">{t("plugins.source")}</th>
              <th className="px-3 py-2 font-medium">{t("plugins.bundle")}</th>
              <th className="px-3 py-2 font-medium">{t("plugins.enabledLabel")}</th>
              <th className="px-3 py-2 font-medium">{t("settings.mcpServers.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {plugins.map((plugin) => (
              <tr key={plugin.id} className="border-b border-border/70 last:border-0">
                <td className="px-3 py-3 align-top">
                  <p className="text-sm font-medium text-foreground">{plugin.name}</p>
                  <p className="font-mono text-xs text-muted">
                    {plugin.id} · v{plugin.version}
                  </p>
                </td>
                <td className="px-3 py-3 align-top text-muted">
                  {plugin.marketplace_id ? plugin.marketplace_id : t("plugins.sourceManual")}
                </td>
                <td className="px-3 py-3 align-top">
                  <PluginBundle plugin={plugin} />
                </td>
                <td className="px-3 py-3 align-top">
                  <Switch
                    checked={plugin.enabled}
                    aria-label={`${t("plugins.enabledLabel")} ${plugin.id}`}
                    onCheckedChange={(checked) => onToggle(plugin.id, checked)}
                  />
                </td>
                <td className="px-3 py-3 align-top">
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => onUninstall(plugin)}
                    disabled={busy}
                    aria-label={`${t("plugins.uninstall")} ${plugin.id}`}
                  >
                    <Trash2 size={14} aria-hidden />
                    {t("plugins.uninstall")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 md:hidden">
        {plugins.map((plugin) => (
          <div key={plugin.id} className="space-y-2 rounded-md border border-border p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">{plugin.name}</p>
                <p className="font-mono text-xs text-muted">
                  {plugin.id} · v{plugin.version}
                </p>
              </div>
              <Switch
                checked={plugin.enabled}
                aria-label={`${t("plugins.enabledLabel")} ${plugin.id}`}
                onCheckedChange={(checked) => onToggle(plugin.id, checked)}
              />
            </div>
            <PluginBundle plugin={plugin} />
            <Button
              size="sm"
              variant="danger"
              onClick={() => onUninstall(plugin)}
              disabled={busy}
              aria-label={`${t("plugins.uninstall")} ${plugin.id}`}
            >
              <Trash2 size={14} aria-hidden />
              {t("plugins.uninstall")}
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}

export function PluginMarketplacesPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const markets = useQuery({
    queryKey: ["plugin-marketplaces"],
    queryFn: agentApi.listPluginMarketplaces,
  });
  const [form, setForm] = useState({ id: "", name: "", url: "" });
  const [formOpen, setFormOpen] = useState(false);
  const [browseId, setBrowseId] = useState<string | null>(null);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["plugin-marketplaces"] });
  }
  function invalidatePlugins() {
    void queryClient.invalidateQueries({ queryKey: ["plugins"] });
    void queryClient.invalidateQueries({ queryKey: ["skills"] });
    void queryClient.invalidateQueries({ queryKey: ["mcp-servers"] });
    void queryClient.invalidateQueries({ queryKey: ["agents"] });
  }

  const addMutation = useMutation({
    mutationFn: () =>
      agentApi.addPluginMarketplace({
        id: form.id.trim(),
        name: form.name || undefined,
        url: form.url || undefined,
      }),
    onSuccess: () => {
      toast.success(t("marketplaces.added"));
      setFormOpen(false);
      setForm({ id: "", name: "", url: "" });
      invalidate();
    },
  });
  const refreshMutation = useMutation({
    mutationFn: (id: string) => agentApi.refreshPluginMarketplace(id),
    onSuccess: (_data, id) => {
      toast.success(t("marketplaces.refreshed"));
      invalidate();
      void queryClient.invalidateQueries({ queryKey: ["marketplace-plugins", id] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => agentApi.deletePluginMarketplace(id),
    onSuccess: () => {
      toast.success(t("marketplaces.deleted"));
      invalidate();
    },
  });

  function add() {
    if (!form.id.trim()) {
      toast.error(t("marketplaces.idRequired"));
      return;
    }
    addMutation.mutate();
  }

  async function remove(source: MarketplaceSource) {
    const ok = await confirm({
      title: t("marketplaces.confirmDeleteTitle"),
      description: t("marketplaces.confirmDeleteMessage", { id: source.id }),
      confirmLabel: t("marketplaces.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (ok) {
      if (browseId === source.id) {
        setBrowseId(null);
      }
      deleteMutation.mutate(source.id);
    }
  }

  const list = markets.data?.marketplaces ?? [];
  const busy = refreshMutation.isPending || deleteMutation.isPending;

  return (
    <>
      <PageHeader title={t("marketplaces.title")} subtitle={t("page.pluginMarketplaces.subtitle")} />
      <main className="max-w-5xl space-y-5 p-6 md:p-8">
        <QueryState query={markets}>
          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-3">
              <div className="space-y-1">
                <CardTitle>{t("marketplaces.title")}</CardTitle>
                <CardDescription>{t("marketplaces.description")}</CardDescription>
              </div>
              <Button size="sm" onClick={() => setFormOpen(true)}>
                <Plus size={15} aria-hidden />
                {t("marketplaces.add")}
              </Button>
            </CardHeader>
            <CardContent>
              {list.length === 0 ? (
                <EmptyState title={t("marketplaces.empty")} />
              ) : (
                <MarketplaceTable
                  sources={list}
                  onRefresh={(id) => refreshMutation.mutate(id)}
                  onBrowse={(id) => setBrowseId((current) => (current === id ? null : id))}
                  onDelete={remove}
                  busy={busy}
                />
              )}
            </CardContent>
          </Card>

          {browseId ? (
            <MarketplaceBrowse marketplaceId={browseId} onInstalled={invalidatePlugins} />
          ) : null}

          {formOpen ? (
            <Card>
              <CardHeader>
                <CardTitle>{t("marketplaces.addTitle")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
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
                  <p className="mt-1 text-xs leading-5 text-muted">{t("marketplaces.urlHint")}</p>
                </Field>
                {addMutation.error ? (
                  <Banner severity="danger">{(addMutation.error as Error).message}</Banner>
                ) : null}
                <div className="flex gap-2">
                  <Button onClick={add} loading={addMutation.isPending}>
                    <Save size={15} aria-hidden />
                    {t("common.create")}
                  </Button>
                  <Button variant="ghost" onClick={() => setFormOpen(false)}>
                    <X size={15} aria-hidden />
                    {t("common.cancel")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : null}
        </QueryState>
      </main>
    </>
  );
}

function MarketplaceTable({
  sources,
  onRefresh,
  onBrowse,
  onDelete,
  busy,
}: {
  sources: MarketplaceSource[];
  onRefresh: (id: string) => void;
  onBrowse: (id: string) => void;
  onDelete: (source: MarketplaceSource) => void;
  busy: boolean;
}) {
  return (
    <div className="grid gap-3">
      {sources.map((source) => (
        <div key={source.id} className="space-y-2 rounded-md border border-border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{source.name || source.id}</p>
              <p className="font-mono text-xs text-muted">{source.id}</p>
            </div>
            <StatusBadge
              variant="info"
              label={`${t("marketplaces.pluginCount")}: ${source.plugin_count}`}
            />
          </div>
          {source.url ? <p className="break-all text-xs text-muted">{source.url}</p> : null}
          {source.last_error ? (
            <Banner severity="warning">{source.last_error}</Banner>
          ) : null}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onRefresh(source.id)}
              disabled={busy}
              aria-label={`${t("marketplaces.refresh")} ${source.id}`}
            >
              <RefreshCw size={14} aria-hidden />
              {t("marketplaces.refresh")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onBrowse(source.id)}
              aria-label={`${t("marketplaces.browse")} ${source.id}`}
            >
              <FileText size={14} aria-hidden />
              {t("marketplaces.browse")}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => onDelete(source)}
              disabled={busy}
              aria-label={`${t("marketplaces.delete")} ${source.id}`}
            >
              <Trash2 size={14} aria-hidden />
              {t("marketplaces.delete")}
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

function MarketplaceBrowse({
  marketplaceId,
  onInstalled,
}: {
  marketplaceId: string;
  onInstalled: () => void;
}) {
  const listing = useQuery({
    queryKey: ["marketplace-plugins", marketplaceId],
    queryFn: () => agentApi.listMarketplacePlugins(marketplaceId),
  });
  const installMutation = useMutation({
    mutationFn: (pluginId: string) =>
      agentApi.installPlugin({ marketplace_id: marketplaceId, plugin_id: pluginId }),
    onSuccess: () => {
      toast.success(t("plugins.installed"));
      onInstalled();
      void listing.refetch();
    },
    onError: (error) => toast.error((error as Error).message),
  });
  const plugins = listing.data?.plugins ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("marketplaces.available")}</CardTitle>
      </CardHeader>
      <CardContent>
        {listing.isLoading ? (
          <LoadingState rows={3} label={t("common.loading")} />
        ) : listing.error ? (
          <Banner severity="danger">{(listing.error as Error).message}</Banner>
        ) : plugins.length === 0 ? (
          <EmptyState title={t("marketplaces.availableEmpty")} />
        ) : (
          <div className="grid gap-3">
            {plugins.map((manifest) => (
              <div
                key={manifest.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">{manifest.name}</p>
                  <p className="font-mono text-xs text-muted">
                    {manifest.id}
                    {manifest.version ? ` · v${manifest.version}` : ""}
                  </p>
                  {manifest.description ? (
                    <p className="text-xs text-muted">{manifest.description}</p>
                  ) : null}
                </div>
                <Button
                  size="sm"
                  onClick={() => installMutation.mutate(manifest.id)}
                  loading={installMutation.isPending}
                  aria-label={`${t("marketplaces.installFrom")} ${manifest.id}`}
                >
                  <Download size={14} aria-hidden />
                  {t("marketplaces.installFrom")}
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
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

  useEffect(() => {
    const current = settings.data;
    if (!current) {
      return;
    }
    setEnabled(current.enabled);
    setWorkspaceRoot(current.workspace_root);
    setAllowedPrefixes(current.allowed_prefixes.join("\n"));
    setDefaultTimeout(String(current.default_timeout_seconds));
    setMaxTimeout(String(current.max_timeout_seconds));
    setOutputLimit(String(current.output_limit_bytes));
    setArtifactStorageBackend(current.artifact_storage_backend);
    setArtifactStoragePath(current.artifact_storage_path);
    setFormError(null);
  }, [settings.data]);

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
    mutation.mutate({
      enabled,
      workspace_root: workspaceRoot,
      allowed_prefixes: parseCommandPrefixes(allowedPrefixes),
      default_timeout_seconds: parsedDefaultTimeout,
      max_timeout_seconds: parsedMaxTimeout,
      output_limit_bytes: parsedOutputLimit,
      artifact_storage_backend: artifactStorageBackend,
      artifact_storage_path: artifactStoragePath,
    });
  }

  return (
    <>
      <PageHeader title={t("nav.settingsCommandPolicy")} subtitle={t("page.settings.commandPolicy.subtitle")} />
      <main className="max-w-4xl space-y-5 p-6 md:p-8">
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
                />
              </div>
              <CardDescription>{t("page.settings.commandPolicy.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border border-border px-3 py-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                  className="size-4 rounded border-border text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
                <span>{t("settings.commandPolicy.enabled")}</span>
              </label>

              <div className="grid gap-4 md:grid-cols-2">
                <Field label={t("settings.commandPolicy.workspaceRoot")} htmlFor="command-policy-workspace-root">
                  <input
                    id="command-policy-workspace-root"
                    value={workspaceRoot}
                    onChange={(event) => setWorkspaceRoot(event.target.value)}
                    className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  />
                </Field>
                <Field label={t("settings.commandPolicy.outputLimit")} htmlFor="command-policy-output-limit">
                  <input
                    id="command-policy-output-limit"
                    type="number"
                    min="1"
                    value={outputLimit}
                    onChange={(event) => setOutputLimit(event.target.value)}
                    className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                    className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                    className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  />
                </Field>
              </div>

              <Field label={t("settings.commandPolicy.allowedPrefixes")} htmlFor="command-policy-allowed-prefixes">
                <textarea
                  id="command-policy-allowed-prefixes"
                  value={allowedPrefixes}
                  onChange={(event) => setAllowedPrefixes(event.target.value)}
                  rows={5}
                  className="min-h-32 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
                <p className="mt-1 text-xs leading-5 text-muted">{t("settings.commandPolicy.allowedPrefixesHint")}</p>
              </Field>

              <div className="grid gap-4 md:grid-cols-2">
                <Field label={t("settings.commandPolicy.artifactStorage")} htmlFor="command-policy-artifact-storage">
                  <select
                    id="command-policy-artifact-storage"
                    value={artifactStorageBackend}
                    onChange={(event) => setArtifactStorageBackend(event.target.value as "inline" | "filesystem")}
                    className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    <option value="inline">{t("settings.commandPolicy.inline")}</option>
                    <option value="filesystem">{t("settings.commandPolicy.filesystem")}</option>
                  </select>
                  <p className="mt-1 text-xs leading-5 text-muted">{t("settings.commandPolicy.storageHint")}</p>
                </Field>
                <Field label={t("settings.commandPolicy.artifactPath")} htmlFor="command-policy-artifact-path">
                  <input
                    id="command-policy-artifact-path"
                    value={artifactStoragePath}
                    onChange={(event) => setArtifactStoragePath(event.target.value)}
                    className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  />
                </Field>
              </div>

              {formError ? <Banner severity="danger">{formError}</Banner> : null}
              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              <Button onClick={save} loading={mutation.isPending}>
                <Save size={15} aria-hidden />
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </main>
    </>
  );
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

  useEffect(() => {
    const current = settings.data;
    if (!current) {
      return;
    }
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
  }, [settings.data]);

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
    mutation.mutate({
      default_mode: defaultMode,
      allow: allow.sort(),
      ask: ask.sort(),
      deny: deny.sort(),
    });
  }

  return (
    <>
      <PageHeader title={t("nav.settingsToolPolicy")} subtitle={t("page.settings.toolPolicy.subtitle")} />
      <main className="max-w-5xl space-y-5 p-6 md:p-8">
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
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:max-w-xs"
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
                            <p className="break-all text-sm font-medium text-foreground">{tool.name}</p>
                            <StatusBadge
                              variant={
                                tool.permission_level === "read"
                                  ? "success"
                                  : tool.permission_level === "write"
                                    ? "warning"
                                    : "danger"
                              }
                              label={tool.permission_level}
                            />
                            {tool.side_effects ? (
                              <StatusBadge variant="warning" label="side_effects" />
                            ) : null}
                          </div>
                          <p className="break-words text-xs leading-5 text-muted [overflow-wrap:anywhere]">
                            {tool.description}
                          </p>
                        </div>
                        <Field label={t("settings.toolPolicy.policy")} htmlFor={`tool-policy-${tool.name}`}>
                          <select
                            id={`tool-policy-${tool.name}`}
                            value={policy}
                            onChange={(event) => setPolicy(tool.name, event.target.value as ToolPolicyChoice)}
                            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
              <Button onClick={save} loading={mutation.isPending}>
                <Save size={15} aria-hidden />
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </main>
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

  useEffect(() => {
    const current = settings.data;
    if (!current) {
      return;
    }
    setMaxToolCalls(String(current.max_tool_calls_per_run));
    setMaxPendingApprovals(String(current.max_pending_approvals_per_run));
    setFormError(null);
  }, [settings.data]);

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
    mutation.mutate({
      max_tool_calls_per_run: parsedMaxToolCalls,
      max_pending_approvals_per_run: parsedMaxPendingApprovals,
    });
  }

  return (
    <>
      <PageHeader title={t("nav.settingsRuntimeSafety")} subtitle={t("page.settings.runtimeSafety.subtitle")} />
      <main className="max-w-3xl space-y-5 p-6 md:p-8">
        <QueryState query={settings}>
          <Banner severity="info">{t("settings.runtimeSafety.guardrail")}</Banner>
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("nav.settingsRuntimeSafety")}</CardTitle>
              <CardDescription>{t("page.settings.runtimeSafety.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Field label={t("settings.runtimeSafety.maxToolCalls")} htmlFor="runtime-safety-max-tool-calls">
                <input
                  id="runtime-safety-max-tool-calls"
                  type="number"
                  min="0"
                  value={maxToolCalls}
                  onChange={(event) => setMaxToolCalls(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
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
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
              </Field>
              {formError ? <Banner severity="danger">{formError}</Banner> : null}
              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              <Button onClick={save} loading={mutation.isPending}>
                <Save size={15} aria-hidden />
                {t("common.save")}
              </Button>
            </CardContent>
          </Card>
        </QueryState>
      </main>
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

  useEffect(() => {
    if (!snapshot.data) {
      return;
    }
    setExportText(JSON.stringify(snapshot.data, null, 2));
  }, [snapshot.data]);

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
        title={t("nav.settingsRuntimeSnapshot")}
        subtitle={t("page.settings.runtimeSnapshot.subtitle")}
        actions={
          <Button
            variant="secondary"
            onClick={() => void snapshot.refetch()}
            aria-label={t("common.retry")}
          >
            <RefreshCw size={15} aria-hidden />
            {t("common.retry")}
          </Button>
        }
      />
      <main className="grid min-w-0 grid-cols-1 gap-5 p-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:p-8">
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
                  className="min-h-80 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  spellCheck={false}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={downloadSnapshot}>
                  <Download size={15} aria-hidden />
                  {t("common.download")}
                </Button>
                <Button variant="secondary" onClick={copyCurrentSnapshotToImport}>
                  <Upload size={15} aria-hidden />
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
                className="min-h-80 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs leading-5 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                spellCheck={false}
              />
            </Field>
            <Field label={t("settings.snapshot.reason")} htmlFor="runtime-snapshot-reason">
              <input
                id="runtime-snapshot-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              />
            </Field>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                onClick={dryRunImport}
                loading={importSnapshot.isPending}
              >
                <ShieldAlert size={15} aria-hidden />
                {t("common.validate")}
              </Button>
            </div>
            {validationResult ? <SnapshotValidationPanel result={validationResult} /> : null}
            <div className="rounded-md border border-danger/40 p-3">
              <Field label={t("settings.snapshot.confirmText")} htmlFor="runtime-snapshot-confirm">
                <input
                  id="runtime-snapshot-confirm"
                  value={confirmText}
                  onChange={(event) => setConfirmText(event.target.value)}
                  placeholder={t("settings.snapshot.confirmPlaceholder")}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                />
              </Field>
              <Button
                variant="danger"
                className="mt-3"
                onClick={() => void replaceRuntimeSnapshot()}
                loading={importSnapshot.isPending}
                disabled={confirmText !== "REPLACE"}
              >
                <Upload size={15} aria-hidden />
                {t("common.replace")}
              </Button>
            </div>
            {formError ? <Banner severity="danger">{formError}</Banner> : null}
            {importSnapshot.error ? <Banner severity="danger">{importSnapshot.error.message}</Banner> : null}
          </CardContent>
        </Card>
      </main>
    </>
  );
}

function SnapshotSummaryBadge({ summary }: { summary: RuntimeSnapshotSummary }) {
  return (
    <StatusBadge
      variant={summary.pending_tool_calls || summary.approvals ? "warning" : "success"}
      label={`${summary.runs} runs`}
    />
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

function AgentEditor({
  agent,
  title,
  description,
  availableTools,
  pending,
  error,
  onSave,
}: {
  agent?: AgentProfile;
  title: string;
  description: string;
  availableTools: ToolDefinition[];
  pending: boolean;
  error: Error | null;
  onSave: (payload: AgentProfileWritePayload) => void;
}) {
  const [name, setName] = useState(agent?.name ?? "");
  const [agentDescription, setAgentDescription] = useState(agent?.description ?? "");
  const [instructions, setInstructions] = useState(agent?.instructions ?? "");
  const [enabled, setEnabled] = useState(agent?.enabled ?? true);
  const [toolNames, setToolNames] = useState<string[]>(agent?.tool_names ?? []);
  const [commandPrefixes, setCommandPrefixes] = useState(
    (agent?.command_allowed_prefixes ?? []).join("\n")
  );
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (!agent) {
      return;
    }
    setName(agent.name);
    setAgentDescription(agent.description);
    setInstructions(agent.instructions);
    setEnabled(agent.enabled);
    setToolNames(agent.tool_names);
    setCommandPrefixes(agent.command_allowed_prefixes.join("\n"));
    setFormError(null);
  }, [agent]);

  useEffect(() => {
    if (agent || toolNames.length || !availableTools.length) {
      return;
    }
    const echo = availableTools.find((tool) => tool.name === "echo");
    setToolNames([echo?.name ?? availableTools[0].name]);
  }, [agent, availableTools, toolNames.length]);

  function toggleTool(toolName: string) {
    setToolNames((current) =>
      current.includes(toolName)
        ? current.filter((name) => name !== toolName)
        : [...current, toolName].sort()
    );
  }

  function saveAgent() {
    setFormError(null);
    if (!name.trim()) {
      setFormError(t("agent.nameRequired"));
      return;
    }
    onSave({
      name: name.trim(),
      description: agentDescription.trim(),
      instructions: instructions.trim(),
      tool_names: toolNames,
      command_allowed_prefixes: parseCommandPrefixes(commandPrefixes),
      enabled,
    });
  }

  return (
    <Card className="min-w-0">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <StatusBadge
          variant={enabled ? "success" : "neutral"}
          label={enabled ? t("agent.enabled") : t("agent.disabled")}
        />
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label={t("agent.name")} htmlFor={`${agent?.id ?? "new"}-agent-name`}>
          <input
            id={`${agent?.id ?? "new"}-agent-name`}
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </Field>
        <Field label={t("agent.description")} htmlFor={`${agent?.id ?? "new"}-agent-description`}>
          <input
            id={`${agent?.id ?? "new"}-agent-description`}
            value={agentDescription}
            onChange={(event) => setAgentDescription(event.target.value)}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </Field>
        <Field label={t("agent.instructions")} htmlFor={`${agent?.id ?? "new"}-agent-instructions`}>
          <textarea
            id={`${agent?.id ?? "new"}-agent-instructions`}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            className="min-h-24 w-full rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
        </Field>
        <label className="flex min-h-11 items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
            className="h-4 w-4"
          />
          {t("agent.enabled")}
        </label>
        <Field
          label={t("agent.commandAllowedPrefixes")}
          htmlFor={`${agent?.id ?? "new"}-agent-command-prefixes`}
        >
          <textarea
            id={`${agent?.id ?? "new"}-agent-command-prefixes`}
            value={commandPrefixes}
            onChange={(event) => setCommandPrefixes(event.target.value)}
            className="min-h-20 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm leading-6 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
          <p className="mt-1 text-xs leading-5 text-muted">{t("agent.commandAllowedPrefixesHint")}</p>
        </Field>
        <div className="space-y-2">
          <p className="text-sm font-medium text-foreground">{t("agent.tools")}</p>
          {availableTools.length ? (
            <div className="grid gap-2 md:grid-cols-2">
              {availableTools.map((tool) => (
                <label
                  key={tool.name}
                  className="flex min-h-11 min-w-0 flex-col items-start justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm md:flex-row md:items-center"
                >
                  <span className="flex min-w-0 flex-1 items-start gap-2">
                    <input
                      type="checkbox"
                      checked={toolNames.includes(tool.name)}
                      onChange={() => toggleTool(tool.name)}
                      className="mt-0.5 h-4 w-4 shrink-0"
                    />
                    <span className="break-all font-medium leading-5 text-foreground">{tool.name}</span>
                  </span>
                  <StatusBadge
                    variant={
                      tool.permission_level === "read"
                        ? "success"
                        : tool.permission_level === "write"
                          ? "warning"
                          : "danger"
                    }
                    label={tool.permission_level}
                  />
                </label>
              ))}
            </div>
          ) : (
            <Banner severity="warning">{t("agent.toolsUnavailable")}</Banner>
          )}
        </div>
        {formError ? <Banner severity="danger">{formError}</Banner> : null}
        {error ? <Banner severity="danger">{error.message}</Banner> : null}
        <Button onClick={saveAgent} loading={pending}>
          <Save size={15} aria-hidden />
          {agent ? t("common.save") : t("common.create")}
        </Button>
      </CardContent>
    </Card>
  );
}

function RunHistoryList({
  runs,
  selectedRunId,
  onSelect,
}: {
  runs: RunState[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}) {
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.history")}</CardTitle>
        <CardDescription>{t("run.historyDescription")}</CardDescription>
      </CardHeader>
      <CardContent>
        {runs.length ? (
          <div className="space-y-2" role="list" aria-label={t("run.history")}>
            {runs.map((run) => {
              const selected = run.id === selectedRunId;
              return (
                <button
                  key={run.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onSelect(run.id)}
                  className={`min-h-16 w-full min-w-0 max-w-full overflow-hidden rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
                    selected
                      ? "border-primary bg-primary/5"
                      : "border-border bg-card hover:bg-muted/40"
                  }`}
                >
                  <span className="flex items-start justify-between gap-2">
                    <span className="min-w-0">
                      <span className="line-clamp-2 text-sm font-medium leading-5 text-foreground">
                        {run.goal}
                      </span>
                      <span className="mt-1 block break-words text-xs text-muted [overflow-wrap:anywhere]">
                        {`${run.agent_id} / ${formatDate(run.created_at)}`}
                      </span>
                    </span>
                    <StatusBadge variant={statusVariant[run.status]} label={run.status} />
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <EmptyState title={t("common.empty.title")} />
        )}
      </CardContent>
    </Card>
  );
}

function RunDetail({
  run,
  actionPending,
  onCancel,
  onWebSocketCancel,
  onResume,
  onWebSocketResume,
  onWebSocketApprovalDecision,
  onReplay,
  streamMode,
  onStreamModeChange,
  websocketState,
}: {
  run: RunState;
  actionPending: boolean;
  onCancel: () => void;
  onWebSocketCancel: () => void;
  onResume: () => void;
  onWebSocketResume: () => void;
  onWebSocketApprovalDecision: (approvalId: string, approved: boolean) => void;
  onReplay: () => void;
  streamMode: RunStreamMode;
  onStreamModeChange: (mode: RunStreamMode) => void;
  websocketState: RunWebSocketState;
}) {
  const structured = getStructuredResult(run);
  const canCancel = ["queued", "running", "waiting_approval"].includes(run.status);
  const canResume = ["running", "waiting_approval"].includes(run.status);
  const pendingApproval = run.approvals.find((approval) => approval.status === "pending");

  return (
    <section className="space-y-5">
      <Card>
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <CardTitle>{t("run.detail")}</CardTitle>
            <CardDescription className="break-words [overflow-wrap:anywhere]">{run.id}</CardDescription>
          </div>
          <StatusBadge variant={statusVariant[run.status]} label={run.status} />
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm leading-6 text-foreground">{run.goal}</p>
          <div className="grid gap-2 text-xs text-muted sm:grid-cols-2">
            <span>{`${t("run.form.agent")}: ${run.agent_id}`}</span>
            <span>{`${t("common.createdAt")}: ${formatDate(run.created_at)}`}</span>
            <span>{`${t("common.updatedAt")}: ${formatDate(run.updated_at)}`}</span>
          </div>
          <div className="flex flex-wrap gap-2 pt-1" aria-label={t("run.actions")}>
            {canCancel ? (
              <Button
                variant="danger"
                size="sm"
                onClick={onCancel}
                loading={actionPending}
                aria-label={t("run.cancel")}
              >
                <X size={15} aria-hidden />
                {t("run.cancel")}
              </Button>
            ) : null}
            {canResume ? (
              <Button
                variant="secondary"
                size="sm"
                onClick={onResume}
                loading={actionPending}
                aria-label={t("run.resume")}
              >
                <PlayCircle size={15} aria-hidden />
                {t("run.resume")}
              </Button>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              onClick={onReplay}
              loading={actionPending}
              aria-label={t("run.replay")}
            >
              <RefreshCw size={15} aria-hidden />
              {t("run.replay")}
            </Button>
          </div>
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
                      <span className="text-sm font-medium text-foreground">{step.tool_call?.name ?? step.kind}</span>
                      <StatusBadge
                        variant={stepStatusVariant[step.status] ?? "neutral"}
                        label={step.status}
                      />
                    </div>
                    {step.tool_result?.error ? (
                      <p className="mt-2 text-xs text-danger">{step.tool_result.error}</p>
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
        <div className="mt-1 flex size-8 items-center justify-center rounded-full border border-border bg-background text-muted">
          {view.icon}
        </div>
      </div>
      <div className="min-w-0 rounded-md border border-border bg-background p-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="break-words text-sm font-medium text-foreground [overflow-wrap:anywhere]">
              {view.title}
            </p>
            <p className="mt-1 break-words text-xs leading-5 text-muted [overflow-wrap:anywhere]">
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
                className="max-w-full break-all rounded-md border border-warning/40 bg-warning/10 px-2 py-1 text-xs text-warning"
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
    <div className="min-w-0 rounded-md bg-muted/20 px-2 py-1.5">
      <span className="text-muted">{label}</span>
      <span className="mx-1 text-muted">/</span>
      <span className="break-words font-medium text-foreground [overflow-wrap:anywhere]">{value}</span>
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
      badgeVariant: "pending",
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
            className={`px-3 py-2 font-medium outline-none transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
              mode === "sse"
                ? "bg-primary text-primary-foreground"
                : "bg-background text-muted hover:bg-accent hover:text-accent-foreground"
            }`}
          >
            {t("run.stream.sse")}
          </button>
          <button
            type="button"
            aria-pressed={mode === "websocket"}
            onClick={() => onModeChange("websocket")}
            className={`border-l border-border px-3 py-2 font-medium outline-none transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
              mode === "websocket"
                ? "bg-primary text-primary-foreground"
                : "bg-background text-muted hover:bg-accent hover:text-accent-foreground"
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
          <p className="text-sm leading-6 text-muted">{t("run.stream.sseDescription")}</p>
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
                  aria-label={t("run.stream.wsApprove")}
                >
                  <Check size={15} aria-hidden />
                  {t("run.stream.wsApprove")}
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => onWebSocketApprovalDecision(pendingApproval.id, false)}
                  loading={actionPending}
                  disabled={websocketState.status !== "open"}
                  aria-label={t("run.stream.wsReject")}
                >
                  <X size={15} aria-hidden />
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
                aria-label={t("run.stream.wsResume")}
              >
                <PlayCircle size={15} aria-hidden />
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
                aria-label={t("run.stream.wsCancel")}
              >
                <X size={15} aria-hidden />
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
    <div className="min-w-0 rounded-md border border-border bg-muted/20 px-3 py-2">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 break-words text-sm text-foreground [overflow-wrap:anywhere]">{value}</p>
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
          ? "pending"
          : "neutral";

  return (
    <div className="min-w-0 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-all text-sm font-medium text-foreground">{record.tool_name}</p>
          <p className="mt-0.5 break-all text-xs text-muted">{`${audit.status} / ${record.step_id}`}</p>
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
        <p className="mt-3 break-all text-xs text-muted">{`${t("run.auditTrace")}: ${record.trace_id}`}</p>
      ) : null}
      {record.artifact_ids.length ? (
        <div className="mt-3 flex min-w-0 flex-wrap gap-2">
          {record.artifact_ids.map((artifactId) => (
            <span key={artifactId} className="max-w-full break-all rounded-md border border-border px-2 py-1 text-xs text-muted">
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
    <div className="rounded-md bg-background px-3 py-2">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 break-words text-xs font-medium text-foreground [overflow-wrap:anywhere]">{value}</p>
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
                  <p className="break-all text-sm font-medium text-foreground">{artifact.name}</p>
                  <p className="mt-0.5 text-xs text-muted">{formatDate(artifact.created_at)}</p>
                </div>
                <StatusBadge variant={artifact.kind === "rag_evidence" ? "info" : "success"} label={artifact.kind} />
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
          <h3 className="text-sm font-medium text-foreground">{t("run.ragAnswer")}</h3>
          <p className="break-words text-sm leading-6 text-foreground [overflow-wrap:anywhere]">{answer}</p>
        </section>
      ) : null}
      {citations.length ? (
        <section className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-medium text-foreground">
            <FileText size={15} aria-hidden />
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
          <h3 className="text-sm font-medium text-foreground">{t("run.contexts")}</h3>
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
    <div className="min-w-0 rounded-md bg-background p-3">
      <p className="break-words text-sm font-medium text-foreground [overflow-wrap:anywhere]">{title}</p>
      {subtitle ? <p className="mt-1 break-all text-xs text-muted">{subtitle}</p> : null}
      {detail ? (
        <p className="mt-2 line-clamp-4 break-words text-xs leading-5 text-foreground [overflow-wrap:anywhere]">
          {detail}
        </p>
      ) : null}
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border px-3 py-2">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-sm font-medium text-foreground">{value}</p>
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
        <StatusBadge variant={permissionVariant} label={tool.permission_level} />
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {tool.audit_tags.map((tag) => (
            <span key={tag} className="rounded-md border border-border px-2 py-1 text-xs text-muted">
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
      <CardContent className="overflow-x-auto">
        <table className="w-full min-w-[560px] border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-border">
              {result.columns.map((column) => (
                <th key={column.name} className="px-3 py-2 font-medium text-muted">
                  {column.label ?? column.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, index) => (
              <tr key={String(index)} className="border-b border-border/70">
                {result.columns.map((column) => (
                  <td key={column.name} className="px-3 py-2 text-foreground">
                    {formatValue(row[column.name])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
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

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs font-medium text-muted">{title}</p>
      <JsonPreview value={value} />
    </div>
  );
}

function JsonPreview({ value }: { value: unknown }) {
  return (
    <pre className="mt-2 max-h-64 w-full min-w-0 max-w-full overflow-auto rounded-md bg-background p-3 text-xs leading-5 text-foreground">
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
