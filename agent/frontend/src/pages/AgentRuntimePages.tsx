import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  RotateCw,
  Save,
  Server,
  ShieldAlert,
  Star,
  Trash2,
  Upload,
  X,
  type LucideIcon,
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
  ExecutionConfirmationField,
  DEFAULT_PAGE_SIZE,
  EmptyState,
  FieldError,
  FieldLabel,
  FormSkeleton,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ListSkeleton,
  ObjectActionBar,
  offsetForPage,
  offsetPagination,
  Pagination,
  Skeleton,
  TableSkeleton,
  TimedLoadingState,
  usePagination,
  PageHeader,
  ProcessingIndicator,
  RowActionMenu,
  Section,
  StatusBadge,
  Switch,
  ToggleChip,
  toast,
  useConfirm,
  type DataTableColumn,
  visibleEntityActions,
  type EntityAction,
  type PageHeaderAction,
  type StatusVariant,
  PageBody,
  RowTitleButton,
  isSubmitEnter,
  SearchField,
  TextareaField,
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
  type ProductMcpSettings,
  type ProductMcpSettingsPatch,
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
} from "@/components/EntityLayout";
import { agentPaginationLabels, listScrollLabel, PagedDataTable, QueryState } from "@/components/ListViews";
import { useEditorRoute } from "@/lib/editor-route";
import {
  focusFirstInvalidField,
  numberFieldError,
  parseJsonField,
  requiredSelectError,
  requiredTextError,
} from "@/lib/field-validation";
import { t, type I18nKey } from "@/lib/i18n";
import { useCapabilities, type AgentCapabilities } from "@/lib/permissions";
import { securityApi } from "@/lib/security-api";
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
type WebSocketStreamStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed"
  | "error"
  | "stopped";

interface RunWebSocketState {
  status: WebSocketStreamStatus;
  lastHeartbeat: string | null;
  lastAck: string | null;
  lastError: string | null;
  lastEventId: string | null;
  reconnectAttempts: number;
  /** 権限・認証・接続の理由で再接続をやめたときの説明（i18n 済み）。null は購読中または未接続。 */
  stopReason: string | null;
  /** 停止した購読を利用者の操作でつなぎ直す。 */
  reconnect: () => void;
  sendCancel: () => void;
  sendResume: () => void;
  sendApprovalDecision: (approvalId: string, approved: boolean) => void;
}

type SseStreamStatus = "idle" | "open" | "failed";

interface RunEventSourceState {
  status: SseStreamStatus;
  reconnect: () => void;
}

/** 接続の確立前に閉じられた回数がこの値に達したら、再接続をやめて理由を出す（#215）。 */
const MAX_WEBSOCKET_HANDSHAKE_FAILURES = 3;
/** backend が WebSocket を権限・認証で拒否したときの close code（policy violation）。 */
const WEBSOCKET_POLICY_VIOLATION = 1008;

/** 購読を止めた理由（error_code は backend の WebSocket の `{type: "error", error_code}`）。 */
function streamStopReason(errorCode: string | null): string {
  if (errorCode?.startsWith("auth.")) return t("run.stream.stoppedUnauthenticated");
  if (errorCode === "run.not_found") return t("run.stream.stoppedNotFound");
  return t("run.stream.stoppedForbidden");
}

/** 接続できなかった理由がログインの失効かを確かめる（401 なら共通の認証イベントでログイン画面へ移る）。 */
function verifySession(): void {
  void securityApi.me().catch(() => undefined);
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
  stopped: "warning",
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
  const [stopReason, setStopReason] = useState<string | null>(null);
  // 利用者の「再接続」で増やし、effect をつなぎ直す。
  const [generation, setGeneration] = useState(0);
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
  const connectionChanged = useValuesChanged([enabled, onRuntimeEvent, runId, runStatus, generation]);
  if (connectionChanged) {
    setStopReason(null);
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
    let stopped = false;
    let handshakeFailures = 0;

    // 権限・認証・接続の理由で購読をやめる。無限に再接続しない（#215）。
    function stop(reason: string) {
      if (disposed || stopped) return;
      stopped = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      setStatus("stopped");
      setStopReason(reason);
    }

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
      if (disposed || stopped) {
        return;
      }
      const socket = new WebSocket(runEventWebSocketUrl(activeRunId, lastEventIdRef.current));
      socketRef.current = socket;
      let opened = false;
      // 接続単位のエラー（権限・認証・Run なし）。この後に close 1008 が来る。
      let rejectedCode: string | null = null;
      // 初回の接続（connecting）は render 中に設定済み。再接続だけここで状態を変える。
      if (isReconnect) {
        setStatus("reconnecting");
        setLastError(null);
      }

      socket.onopen = () => {
        opened = true;
        handshakeFailures = 0;
        if (socketRef.current === socket && !disposed) {
          setStatus("open");
        }
      };
      socket.onclose = (event) => {
        if (socketRef.current === socket) {
          socketRef.current = null;
        }
        if (disposed) return;
        // backend は権限・認証で拒否すると error を送ってから 1008 で閉じる。つなぎ直しても同じ結果になる。
        if (event.code === WEBSOCKET_POLICY_VIOLATION || rejectedCode) {
          if (rejectedCode?.startsWith("auth.")) verifySession();
          stop(streamStopReason(rejectedCode));
          return;
        }
        // 接続の確立前に閉じられる（Cookie・Origin の拒否は accept 前に閉じる）のが続いたら、再接続をやめる。
        if (!opened) {
          handshakeFailures += 1;
          if (handshakeFailures >= MAX_WEBSOCKET_HANDSHAKE_FAILURES) {
            verifySession();
            stop(t("run.stream.stoppedUnreachable"));
            return;
          }
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
          const code = message.error_code ?? null;
          if (message.command) {
            // コマンド単位の拒否（権限のない取消など）は接続を保ったまま理由だけを出す。
            setLastError(
              code?.startsWith("rbac.")
                ? `${message.command}: ${t("run.stream.commandForbidden")}`
                : code ?? message.message ?? "websocket.error"
            );
            return;
          }
          if (code && (code.startsWith("rbac.") || code.startsWith("auth.") || code === "run.not_found")) {
            rejectedCode = code;
          }
          setLastError(code ?? message.message ?? "websocket.error");
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
  }, [enabled, onRuntimeEvent, runId, runStatus, generation]);

  const reconnect = useCallback(() => {
    reconnectAttemptRef.current = 0;
    setReconnectAttempts(0);
    setGeneration((current) => current + 1);
  }, []);

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
        // 決定者はログイン中の利用者から server が決める（#215）。
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
    stopReason,
    reconnect,
    sendCancel,
    sendResume,
    sendApprovalDecision,
  };
}

const RUN_EVENT_TYPES = [
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

/**
 * 非終端の Run のイベントを SSE（Cookie セッション）で購読する。
 * EventSource は status code を読めないため、失敗したら自動の再接続をやめ、ログインの失効かを確かめて
 * 利用者に停止を示す（#215）。
 */
function useRunEventSource(
  run: RunState | undefined,
  enabled: boolean,
  onRuntimeEvent: () => void
): RunEventSourceState {
  const runId = run?.id;
  const active = enabled && Boolean(runId) && Boolean(run) && !isRunTerminal(run?.status ?? "completed");
  const [status, setStatus] = useState<SseStreamStatus>("idle");
  const [generation, setGeneration] = useState(0);

  // 購読の条件が変わったレンダーで状態を初期化する（effect で setState しない）。
  if (useValuesChanged([active, runId ?? null, generation])) {
    setStatus(active ? "open" : "idle");
  }

  useEffect(() => {
    if (!active || !runId) return;
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events?follow=true`, {
      withCredentials: true,
    });
    const refresh = () => {
      onRuntimeEvent();
    };
    RUN_EVENT_TYPES.forEach((type) => source.addEventListener(type, refresh));
    source.onerror = () => {
      // 終端した Run の stream の終了も error になる。状態は一覧の再取得で分かるので、再接続だけやめる。
      source.close();
      setStatus("failed");
      verifySession();
      onRuntimeEvent();
    };
    return () => source.close();
  }, [active, runId, onRuntimeEvent, generation]);

  const reconnect = useCallback(() => setGeneration((current) => current + 1), []);
  return { status, reconnect };
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
  // 業務 Agent と Binding の変更は Agent 管理の権限（admin）だけ。それ以外は閲覧だけにする（#215）。
  const { admin: canManage } = useCapabilities();
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
  const agentActions = (agent: AgentProfile): EntityAction[] =>
    canManage
      ? [
          {
            id: "toggle-enabled",
            label: agent.enabled ? t("agent.disable") : t("agent.enable"),
            icon: agent.enabled ? PowerOff : Power,
            disabled: toggleAgent.isPending,
            onSelect: () => toggleAgent.mutate(agent),
          },
        ]
      : [];

  const agentList = agents.data?.agents ?? [];
  const bindingList = bindings.data?.bindings ?? [];
  // 作成できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("nav.agents")}
          subtitle={t("page.agents.subtitle")}
          actions={
            canManage
              ? [{ id: "create", kind: "primary", label: t("agent.create"), icon: Plus, onClick: editor.openNew }]
              : []
          }
        />
        <PageBody wide>
          {skills.error ? <Banner severity="danger">{skills.error.message}</Banner> : null}
          <Section title={t("agent.list")}>
            <QueryState query={agents} loadingLabel={t("loading.agents")} skeleton={<TableSkeleton columns={6} />}>
              <AgentTable
                agents={agentList}
                bindings={bindingList}
                bindingsLoading={bindings.isLoading}
                onOpen={(agent) => editor.openItem(agent.id)}
                hrefFor={(agent) => editor.itemHref(agent.id)}
                actionsFor={agentActions}
              />
            </QueryState>
          </Section>
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
          <QueryState query={agents} loadingLabel={t("loading.agents")} skeleton={<FormSkeleton fields={3} />}>
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
      skillsLoading={skills.isLoading}
      skillsError={skills.error}
      bindings={agent ? bindingList.filter((binding) => binding.agent_id === agent.id) : []}
      bindingsLoading={bindings.isLoading || runtimes.isLoading}
      runtimes={runtimes.data?.runtimes ?? []}
      actions={agent ? agentActions(agent) : []}
      readOnly={!canManage}
      onBack={() => editor.backToList()}
      onCreated={(created) => editor.openItem(created.id, { replace: true })}
    />
  );
}

function AgentTable({
  agents,
  bindings,
  bindingsLoading,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  agents: AgentProfile[];
  bindings: RuntimeBinding[];
  /** 実行先を取得中は「未設定」と誤って出さず、セルの形の Skeleton にする。 */
  bindingsLoading: boolean;
  onOpen: (agent: AgentProfile) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (agent: AgentProfile) => string;
  actionsFor: (agent: AgentProfile) => EntityAction[];
}) {
  const columns: DataTableColumn<AgentProfile>[] = [
    {
      key: "name",
      header: t("agent.name"),
      rowHeader: true,
      render: (agent) => (
        <RowTitleButton title={agent.name} subtitle={agent.id} href={hrefFor(agent)} onClick={() => onOpen(agent)} />
      ),
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
        if (bindingsLoading) return <Skeleton className="h-4 w-24" testId={`agent-binding-loading-${agent.id}`} />;
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
    <PagedDataTable
      pageKey="agents"
      rows={agents}
      columns={columns}
      getRowKey={(agent) => agent.id}
      onRowClick={onOpen}
      rowProps={(agent) => ({ className: "align-top", "data-testid": `agent-row-${agent.id}` })}
      tableClassName="w-full min-w-[46rem]"
      ariaLabel={t("agent.list")}
      paginationTestId="agent-list-pagination"
      empty={<EmptyState title={t("common.empty.title")} />}
    />
  );
}
export function RuntimesPage() {
  const queryClient = useQueryClient();
  // 有効 / 無効の切替とサービス操作・ログは Agent 管理の権限（admin）だけ。状態の確認は閲覧でもできる（#215）。
  const { admin: canManage } = useCapabilities();
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
  // Runtime ごとに、いま実行中の操作（状態確認・サービス操作）を 1 つだけ求める。
  // サービスの pull / 起動はイメージの取得やコンテナの起動待ちで数十秒以上かかる。
  function runtimeOperation(runtime: RuntimeDefinition): RuntimeOperation | null {
    if (probe.isPending && probe.variables === runtime.id) return "probe";
    if (
      serviceAction.isPending &&
      runtime.managed_service_id &&
      serviceAction.variables?.serviceId === runtime.managed_service_id
    ) {
      return serviceAction.variables.action;
    }
    return null;
  }
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
        <QueryState query={runtimes} loadingLabel={t("loading.runtimes")} skeleton={<RuntimeCardsSkeleton />}>
          <div className="grid gap-4 xl:grid-cols-2">
            {(runtimes.data?.runtimes ?? []).map((runtime) => {
              const operation = runtimeOperation(runtime);
              return (
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
                          disabled={!canManage}
                          onCheckedChange={(enabled) => patchRuntime.mutate({ runtime, enabled })}
                          aria-label={`${runtime.name} ${t("agent.enabled")}`}
                        />
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={operation === "probe"}
                          disabled={operation !== null && operation !== "probe"}
                          onClick={() => probe.mutate(runtime.id)} icon={RefreshCw}>
                          {t("runtime.probe")}
                        </Button>
                      </div>
                      {canManage && runtime.managed_service_id ? (
                        <div className="flex flex-wrap gap-2">
                          {(["pull", "start", "stop", "restart", "remove"] as const).map(
                            (action) => (
                              <Button
                                key={action}
                                size="sm"
                                variant={action === "remove" ? "danger" : "secondary"}
                                icon={RUNTIME_SERVICE_ACTION_ICONS[action]}
                                loading={operation === action}
                                disabled={operation !== null && operation !== action}
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
                      {operation ? (
                        // スピナーは操作したボタンの loading が担う（messaging.md §3.7）。
                        <ProcessingIndicator
                          active
                          label={t(RUNTIME_OPERATION_LABEL_KEYS[operation], { runtime: runtime.name })}
                          operationKey={`${runtime.id}:${operation}`}
                          placement="action"
                          activityIcon="none"
                          className="rounded-md border border-border bg-surface-sunken px-3 py-2"
                          testId={`runtime-processing-${runtime.id}`}
                        />
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
              );
            })}
          </div>
        </QueryState>
      </PageBody>
    </>
  );
}

type RuntimeOperation = "probe" | "pull" | "start" | "stop" | "restart" | "remove";

// loading 中は先頭のアイコンがスピナーに置き換わるため、サービス操作のボタンにもアイコンを付ける（README §4 Button）。
const RUNTIME_SERVICE_ACTION_ICONS = {
  pull: Download,
  start: Power,
  stop: PowerOff,
  restart: RotateCw,
  remove: Trash2,
} as const satisfies Record<Exclude<RuntimeOperation, "probe">, LucideIcon>;

const RUNTIME_OPERATION_LABEL_KEYS = {
  probe: "runtime.processing.probe",
  pull: "runtime.processing.pull",
  start: "runtime.processing.start",
  stop: "runtime.processing.stop",
  restart: "runtime.processing.restart",
  remove: "runtime.processing.remove",
} as const satisfies Record<RuntimeOperation, I18nKey>;

/** Runtime のカード（2 列）の形。読み込み後のカードの高さを予約する。 */
function RuntimeCardsSkeleton() {
  return (
    <div className="grid gap-4 xl:grid-cols-2" aria-hidden="true">
      <Skeleton className="h-64" />
      <Skeleton className="h-64" />
    </div>
  );
}

const DEFAULT_RUN_GOAL = t("run.form.goalDefault");
/** 外部 MCP のタイムアウト秒の上限（backend の `_MCP_TIMEOUT_MAX_SECONDS` と同じ）。 */
const MCP_TIMEOUT_MAX_SECONDS = 600;

export function RunsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const capabilities = useCapabilities();
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
  const [bindingError, setBindingError] = useState<string | null>(null);
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
  const runIds = useMemo(() => runs.data?.runs.map((run) => run.id), [runs.data?.runs]);
  const restoredSelection = useRestoredSelectionCheck(selectedRunId, runIds);
  const selectedRun = runItems.find((run) => run.id === selectedRunId) ?? runItems[0];
  // 利用できるエージェントは backend が絞り込む。既定の Agent を使えない利用者は、使える最初の Agent を選ぶ（#215）。
  const runnableAgents = (agents.data?.agents ?? []).filter((agent) => agent.enabled);
  const selectedAgentId =
    runnableAgents.some((agent) => agent.id === agentId) || !runnableAgents.length ? agentId : runnableAgents[0].id;
  const agentBindings = (bindings.data?.bindings ?? []).filter(
    (binding) => binding.agent_id === selectedAgentId && binding.enabled
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

  const sseState = useRunEventSource(selectedRun, streamMode === "sse", refreshRuntimeEvents);

  function onAgentChange(value: string) {
    setAgentId(value);
    setBindingId("");
    setBindingError(null);
  }

  function submitRun() {
    // ゴールは backend（RunCreateRequest）でも必須（#540）。未入力は欄の下に出し、画面の並び順で最初の欄へ移す。
    const nextGoalError = goal.trim() ? null : t("run.goalRequired");
    const nextBindingError = resolvedBindingId ? null : t("run.bindingRequired");
    setGoalError(nextGoalError);
    setBindingError(nextBindingError);
    if (nextGoalError || nextBindingError) {
      focusField(nextGoalError ? "run-goal" : "run-binding");
      return;
    }
    createRun.mutate({
      goal,
      agent_id: selectedAgentId,
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
        // 取消・再開・再実行は Run の実行・操作の権限（operator）が必要（#215）。
        visible: capabilities.operateRuns && canResume,
        disabled: actionPending,
        onSelect: () => resumeRun.mutate(run.id),
      },
      {
        id: "replay",
        label: t("run.replay"),
        icon: RefreshCw,
        visible: capabilities.operateRuns && !isExternal,
        disabled: actionPending,
        onSelect: () => replayRun.mutate(run.id),
      },
      {
        id: "cancel",
        label: t("run.cancel"),
        icon: X,
        tone: "danger",
        visible: capabilities.operateRuns && canCancel,
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
              {/* Run の作成は Run の実行・操作の権限（operator）がある利用者だけに出す（#215）。 */}
              {capabilities.operateRuns ? (
                <Card className="min-w-0">
                  <CardHeader>
                    <CardTitle>{t("run.form.submit")}</CardTitle>
                    <CardDescription>{t("run.runtime")}</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <Field label={t("run.form.agent")} htmlFor="run-agent">
                      <select
                        id="run-agent"
                        value={selectedAgentId}
                        onChange={(event) => onAgentChange(event.target.value)}
                        className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                      >
                        {runnableAgents.map((agent) => (
                          <option key={agent.id} value={agent.id}>
                            {agent.name}
                          </option>
                        ))}
                      </select>
                    </Field>
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
                      textareaClassName="min-h-24"
                    />
                    {/* 既定の Binding がない Agent だけ、実行先の選択が必須（submitRun の送信ガード）。 */}
                    <Field
                      label={t("run.form.binding")}
                      htmlFor="run-binding"
                      required={!defaultBinding}
                      error={bindingError}
                    >
                      <select
                        id="run-binding"
                        value={bindingId}
                        aria-required={!defaultBinding || undefined}
                        aria-invalid={bindingError ? true : undefined}
                        aria-describedby={bindingError ? fieldErrorId("run-binding") : undefined}
                        onChange={(event) => {
                          setBindingId(event.target.value);
                          setBindingError(null);
                        }}
                        className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
                    {/* Agent・実行先を取得し終えるまでは「実行先がない」と判断できないため出さない。 */}
                    {!agents.isLoading && !bindings.isLoading && !agentBindings.length ? (
                      <Banner severity="warning">{t("run.unbound")}</Banner>
                    ) : null}
                    {!goalSaved ? <Banner severity="warning">{t("workspace.draftNotSaved")}</Banner> : null}
                    {createRun.error ? <Banner severity="danger">{createRun.error.message}</Banner> : null}
                    <Button onClick={submitRun} loading={createRun.isPending} className="w-full" icon={PlayCircle}>
                      {t("run.form.submit")}
                    </Button>
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

type ApprovalRow = { run: RunState; approval: ApprovalRequest };

export function ApprovalsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const capabilities = useCapabilities();
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: agentApi.listRuns,
    refetchInterval: 5000,
  });
  const decide = useMutation({
    mutationFn: ({ approval, approved }: { approval: ApprovalRequest; approved: boolean }) =>
      // 決定者はログイン中の利用者から server が決める（#215）。
      agentApi.decideApproval(approval.id, { approved }),
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
      // 承認・却下は承認の判断の権限（approver）が必要（#215）。
      visible: capabilities.decideApprovals && approval.status === "pending",
      disabled: decide.isPending,
      onSelect: () => decideApproval(approval, true),
    },
    {
      id: "reject",
      label: t("common.reject"),
      icon: X,
      tone: "danger",
      visible: capabilities.decideApprovals && approval.status === "pending",
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
        <QueryState query={runs} loadingLabel={t("loading.approvals")} skeleton={<TableSkeleton columns={3} />}>
          <AgentSplitPane
            splitId="approvals-list"
            left={
              <Section title={t("approval.list")}>
                {/* 5 秒ごとの再取得で行が変わっても、ページは作業状態に残して戻さない。 */}
                <PagedDataTable
                  pageKey="approvals"
                  rows={approvals}
                  columns={columns}
                  getRowKey={({ approval }) => approval.id}
                  selectedRowKey={selected?.approval.id ?? null}
                  onRowClick={({ approval }) => setSelectedId(approval.id)}
                  rowProps={() => ({ className: "align-top" })}
                  ariaLabel={t("approval.list")}
                  paginationTestId="approval-list-pagination"
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
  // 1 ページの件数（#265。以前は 1 度に取得する件数で、既定 100 件）。
  limit: String(DEFAULT_PAGE_SIZE),
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
    limit: Number.isInteger(parsedLimit) && parsedLimit > 0 ? Math.min(parsedLimit, AUDIT_MAX_PAGE_SIZE) : DEFAULT_PAGE_SIZE,
    offset: 0,
  };
}

/** 1 ページの件数の上限（backend の `limit` の上限）。 */
const AUDIT_MAX_PAGE_SIZE = 1000;

const isAuditPage: WorkspaceValidator<number> = (value): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 1;

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
  // ページ番号も作業状態に残す。ページは API の offset / limit に直して取得する（#265）。
  const [auditPage, setAuditPage] = useWorkspaceState("audit", "page", 1, isAuditPage);
  const appliedFilters = useMemo(() => {
    const filters = auditFiltersOf(appliedForm);
    return { ...filters, offset: offsetForPage(auditPage, filters.limit ?? DEFAULT_PAGE_SIZE) };
  }, [appliedForm, auditPage]);
  const audit = useQuery({
    queryKey: ["audit", "tool-calls", appliedFilters],
    queryFn: () => agentApi.listToolCallAudit(appliedFilters),
    // ページを送っている間は今のページを出したまま取り直す（表を Skeleton に戻さない）。
    placeholderData: keepPreviousData,
  });
  const auditPaging = audit.data
    ? offsetPagination({
        offset: audit.data.offset,
        limit: audit.data.limit,
        total: audit.data.total,
        count: audit.data.records.length,
      })
    : null;
  // 残していたページが記録の削除などで範囲外になったら、最後のページへ寄せる。
  const lastAuditPage = auditPaging?.totalPages ?? null;
  useEffect(() => {
    if (lastAuditPage !== null && audit.data?.records.length === 0 && auditPage > lastAuditPage) {
      setAuditPage(lastAuditPage);
    }
  }, [audit.data?.records.length, auditPage, lastAuditPage, setAuditPage]);
  const { runId, toolName, stepStatus, approvalStatus, errorCode, warnings, limit } = filterForm;

  function setFilter<K extends keyof AuditFilterForm>(key: K, value: AuditFilterForm[K]) {
    setFilterForm((current) => ({ ...current, [key]: value }));
  }

  function applyFilters() {
    setAppliedForm(filterForm);
    setAuditPage(1);
  }

  // CSV も Cookie セッションで取得し、401 / 403 は他の API と同じく扱う（#215）。
  const csvDownload = useMutation({
    // CSV は 1 ページではなく、条件に合う記録を backend の既定の件数（1,000 件）まで出力する。
    mutationFn: () =>
      agentApi.downloadToolCallAuditCsv({ ...auditFiltersOf(filterForm), limit: undefined, offset: undefined }),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "agent-tool-call-audit.csv";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      toast.success(t("audit.csvDownloaded"));
    },
    onError: (error) => toast.error(t("audit.csvFailed"), { description: error.message }),
  });

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
                  // 条件フォームの Enter は「条件を適用」と同じ（IME の変換を確定する Enter では適用しない。#535）。
                  onKeyDown={(event) => {
                    if (isSubmitEnter(event)) applyFilters();
                  }}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              <Field label={t("audit.toolName")} htmlFor="audit-tool-name">
                <select
                  id="audit-tool-name"
                  value={toolName}
                  onChange={(event) => setFilter("toolName", event.target.value)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
                  // 条件フォームの Enter は「条件を適用」と同じ（IME の変換を確定する Enter では適用しない。#535）。
                  onKeyDown={(event) => {
                    if (isSubmitEnter(event)) applyFilters();
                  }}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
              <Field label={t("audit.guardrailWarnings")} htmlFor="audit-warning-filter">
                <select
                  id="audit-warning-filter"
                  value={warnings}
                  onChange={(event) => setFilter("warnings", event.target.value as AuditWarningsFilter)}
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                />
              </Field>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={applyFilters} loading={audit.isFetching} icon={RefreshCw}>
                {t("audit.apply")}
              </Button>
              <Button
                variant="secondary"
                onClick={() => csvDownload.mutate()}
                loading={csvDownload.isPending}
                icon={Download}
              >
                {t("audit.downloadCsv")}
              </Button>
            </div>
            {csvDownload.isPending ? (
              // 監査レコードが多いと CSV の作成に数秒以上かかる。スピナーはボタンの loading が担う。
              <ProcessingIndicator
                active
                label={t("audit.progress.downloadingCsv")}
                operationKey="audit-csv-download"
                placement="action"
                activityIcon="none"
                testId="audit-csv-processing"
              />
            ) : null}
            <p className="text-xs leading-5 text-fg-muted">{t("audit.csvHint")}</p>
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
            <QueryState
              query={audit}
              loadingLabel={t("loading.audit")}
              skeleton={<TableSkeleton columns={9} />}
            >
              {audit.data?.records.length ? (
                <div className="grid min-w-0 gap-2">
                  <AuditRecordsTable records={audit.data.records} />
                  {auditPaging ? (
                    <Pagination
                      page={auditPaging.page}
                      totalPages={auditPaging.totalPages}
                      onPageChange={setAuditPage}
                      summary={agentPaginationLabels().summary(auditPaging.range)}
                      pageIndicator={agentPaginationLabels().pageIndicator?.(auditPaging.page, auditPaging.totalPages)}
                      prevLabel={t("pager.prev")}
                      nextLabel={t("pager.next")}
                      ariaLabel={t("audit.pagerLabel")}
                      testId="audit-pagination"
                    />
                  ) : null}
                </div>
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
      rowProps={() => ({ className: `align-top ${INFORMATION_TABLE_ROW_CLASS}` })}
      tableClassName="w-full min-w-[980px]"
      ariaLabel={t("audit.records")}
      scrollAriaLabel={listScrollLabel(t("audit.records"))}
      stickyHeader
      visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
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
        <QueryState query={tools} loadingLabel={t("loading.tools")} skeleton={<TableSkeleton columns={2} />}>
          <AgentSplitPane
            splitId="tools-list"
            left={
              <Section title={t("tool.list")}>
                <PagedDataTable
                  pageKey="tools"
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

/** メモリの検索で取得する件数（backend の上限）。一覧は 10 件/ページで送る（#265。以前は 20 件で打ち切っていた）。 */
const MEMORY_SEARCH_LIMIT = 100;

export function MemoryPage() {
  const queryClient = useQueryClient();
  // メモリの登録は Run の実行・操作の権限（operator）が必要（backend の `POST /memory` と同じ。#215）。
  const { operateRuns: canAdd } = useCapabilities();
  // 検索語は作業状態として残し、登録フォームの未保存の入力は離脱ガードで守る（#87）。
  const [query, setQuery] = useWorkspaceState("memory", "query", "", isString);
  const [kind, setKind] = useState<MemoryKind>("user_preference");
  const [content, setContent] = useState("");
  const [metadataText, setMetadataText] = useState("{}");
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [selectedEntryId, setSelectedEntryId] = useState<string | null>(null);
  const memory = useQuery({
    queryKey: ["memory", query],
    queryFn: () => agentApi.searchMemory(query, MEMORY_SEARCH_LIMIT),
    // 検索語を変えている間は前の結果を出したまま取り直す（入力のたびに一覧を Skeleton に戻さない）。
    placeholderData: keepPreviousData,
  });
  const addMemory = useMutation({
    mutationFn: agentApi.addMemory,
    onSuccess: () => {
      toast.success(t("memory.added"));
      setContent("");
      setMetadataText("{}");
      setMetadataError(null);
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
    },
  });

  useEditorLeaveGuard(content.trim() !== "" || metadataText.trim() !== "{}", addMemory.isPending);

  function submitMemory() {
    const nextContentError = content.trim() ? null : t("memory.contentRequired");
    // メタデータは任意（空は {}）。形式のエラーは欄の直下に出す（#541）。
    const metadata = parseJsonField<Record<string, unknown>>(metadataText, t("memory.metadata"), {
      expect: "object",
    });
    const nextMetadataError = metadata.ok ? null : metadata.error;
    setContentError(nextContentError);
    setMetadataError(nextMetadataError);
    if (
      focusFirstInvalidField([
        ["memory-content", nextContentError],
        ["memory-metadata", nextMetadataError],
      ]) ||
      !metadata.ok
    ) {
      return;
    }
    addMemory.mutate({ kind, content, metadata: metadata.value ?? {} });
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
          // 長い記憶は 2 行で切り詰め、全文はホバー・フォーカスの Tooltip と右の詳細で見せる（#421）。
          title={entry.content}
          maxLines={2}
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
        {canAdd ? (
          <Section title={t("memory.create")} description={t("page.memory.subtitle")}>
            <Card className="min-w-0">
              <CardContent className="grid min-w-0 gap-4 pt-5 lg:grid-cols-2">
                <div className="min-w-0 space-y-4">
                  <Field label={t("memory.kind")} htmlFor="memory-kind">
                    <select
                      id="memory-kind"
                      value={kind}
                      onChange={(event) => setKind(event.target.value as MemoryKind)}
                      className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                    >
                      <option value="user_preference">{t("memory.kind.userPreference")}</option>
                      <option value="tool_learning">{t("memory.kind.toolLearning")}</option>
                      <option value="note">{t("memory.kind.note")}</option>
                      <option value="run_summary">{t("memory.kind.runSummary")}</option>
                    </select>
                  </Field>
                  <TextareaField
                    id="memory-content"
                    label={t("memory.content")}
                    required
                    error={contentError ?? undefined}
                    value={content}
                    onValueChange={(value) => {
                      setContent(value);
                      setContentError(null);
                    }}
                    textareaClassName="min-h-28"
                  />
                </div>
                <div className="min-w-0 space-y-4">
                  <TextareaField
                    id="memory-metadata"
                    label={t("memory.metadata")}
                    error={metadataError ?? undefined}
                    value={metadataText}
                    onValueChange={(value) => {
                      setMetadataText(value);
                      setMetadataError(null);
                    }}
                    monospace
                    spellCheck={false}
                    textareaClassName="min-h-28"
                  />
                  {addMemory.error ? <Banner severity="danger">{addMemory.error.message}</Banner> : null}
                  <Button onClick={submitMemory} loading={addMemory.isPending} icon={Save}>
                    {t("memory.create")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </Section>
        ) : null}
        <AgentSplitPane
          splitId="memory-list"
          left={
            <Section title={t("memory.list")}>
              {/* 一覧の絞り込みは共有の SearchField（入力に合わせて適用・debounce・IME 対応・消去。#535）。 */}
              <SearchField
                id="memory-search"
                label={t("common.search")}
                value={query}
                onSearch={setQuery}
                clearLabel={t("common.clearSearch")}
                resultCountLabel={
                  memory.data ? t("common.searchResultCount", { count: memory.data.entries.length }) : ""
                }
                placeholder={t("memory.searchPlaceholder")}
              />
              <p className="text-xs leading-5 text-fg-muted">{t("memory.limitHint", { limit: MEMORY_SEARCH_LIMIT })}</p>
              <QueryState query={memory} loadingLabel={t("loading.memory")} skeleton={<TableSkeleton columns={2} />}>
                <PagedDataTable
                  pageKey="memory"
                  // 検索語を変えたら 1 ページ目へ戻す。
                  resetKey={query}
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
  mcpUrl: string;
  timeoutSeconds: string;
  defaultLimit: string;
}

/**
 * 外部 RAG / 外部 NL2SQL（各製品の MCP）の接続設定（#233）。
 * 認証は呼び出しごとのサービストークン（Run の利用者として呼ぶ）なので API キー欄はない。
 * 署名鍵とサービス利用者は .env で管理し、ここでは設定済みかどうかだけを表示する。
 */
export function ExternalSettingsPage({ kind }: { kind: "rag" | "nl2sql" }) {
  const queryClient = useQueryClient();
  // 運用設定の変更は Agent 管理の権限（admin）だけ。メニュー権限だけの利用者は閲覧になる（#215）。
  const { admin: canManage } = useCapabilities();
  const isRag = kind === "rag";
  const isNl2Sql = kind === "nl2sql";
  const title = isRag ? t("nav.settingsExternalRag") : t("nav.settingsExternalNl2Sql");
  const subtitle = isRag ? t("page.settings.rag.subtitle") : t("page.settings.nl2sql.subtitle");
  const settings = useQuery({
    queryKey: ["settings", kind],
    queryFn: isRag ? agentApi.getExternalRagSettings : agentApi.getExternalNl2SqlSettings,
  });
  const mutation = useMutation({
    mutationFn: (payload: ProductMcpSettingsPatch) => {
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
  const [mcpUrl, setMcpUrl] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("60");
  const [defaultLimit, setDefaultLimit] = useState("100");
  const [baseline, setBaseline] = useState<ExternalSettingsDraft | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{ timeout?: string | null; defaultLimit?: string | null }>({});

  // server 値が変わったレンダーで、フォームと比較の基準を server 値に戻す。
  const serverChanged = useValuesChanged([settings.data]);
  if (serverChanged) {
    const current = settings.data;
    if (current) {
      const saved = {
        mcpUrl: current.mcp_url ?? "",
        timeoutSeconds: String(current.timeout_seconds),
        defaultLimit: String(current.default_limit ?? 100),
      };
      setMcpUrl(saved.mcpUrl);
      setTimeoutSeconds(saved.timeoutSeconds);
      setDefaultLimit(saved.defaultLimit);
      setBaseline(saved);
    }
  }

  const draft: ExternalSettingsDraft = { mcpUrl, timeoutSeconds, defaultLimit };
  // RAG では既定件数を扱わないので比較から外す。
  const comparable = (value: ExternalSettingsDraft) => (isNl2Sql ? value : { ...value, defaultLimit: "" });
  const isDirty = baseline !== null && !sameDraft(comparable(draft), comparable(baseline));
  useSettingsLeaveGuard(isDirty, mutation.isPending);

  function save() {
    const submitted = draft;
    // 空の数値を 0 として送らない。規則は backend（ProductMcpSettingsPatch）と同じ（#540 / #541）。
    const nextErrors = {
      timeout: numberFieldError(timeoutSeconds, {
        label: t("settings.timeout"),
        min: 0,
        exclusiveMin: true,
        max: MCP_TIMEOUT_MAX_SECONDS,
      }),
      defaultLimit: isNl2Sql
        ? numberFieldError(defaultLimit, { label: t("settings.defaultLimit"), integer: true, min: 1, max: 1000 })
        : null,
    };
    setFieldErrors(nextErrors);
    if (
      focusFirstInvalidField([
        [`${kind}-timeout`, nextErrors.timeout],
        ["nl2sql-default-limit", nextErrors.defaultLimit],
      ])
    ) {
      return;
    }
    mutation.mutate(
      {
        mcp_url: mcpUrl.trim(),
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
        <QueryState query={settings} loadingLabel={t("loading.settings")} skeleton={<FormSkeleton fields={3} />}>
          <ProductMcpNotice settings={settings.data} />
          <Card>
            <CardHeader>
              <CardTitle>{title}</CardTitle>
              <CardDescription>{t("settings.productMcp.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* URL は全幅、タイムアウト・既定件数は 2 列に並べる。 */}
              <fieldset disabled={!canManage} className="grid min-w-0 gap-x-6 gap-y-4 lg:grid-cols-2">
                <Field label={t("settings.productMcp.url")} htmlFor={`${kind}-mcp-url`} className="lg:col-span-2">
                  <input
                    id={`${kind}-mcp-url`}
                    type="url"
                    inputMode="url"
                    autoComplete="off"
                    spellCheck={false}
                    value={mcpUrl}
                    onChange={(event) => setMcpUrl(event.target.value)}
                    placeholder={isRag ? "http://rag-host/api/mcp" : "http://nl2sql-host/api/mcp"}
                    aria-describedby={`${kind}-mcp-url-hint`}
                    className={INPUT_CLASS}
                  />
                  <p id={`${kind}-mcp-url-hint`} className="mt-1 text-xs leading-5 text-fg-muted">
                    {t("settings.productMcp.urlHint")}
                  </p>
                </Field>
                {/* 空欄を 0 として送らない。backend（ProductMcpSettingsPatch）も 0 以下を拒否する（#540）。 */}
                <Field label={t("settings.timeout")} htmlFor={`${kind}-timeout`} required error={fieldErrors.timeout}>
                  <input
                    id={`${kind}-timeout`}
                    type="number"
                    min="1"
                    aria-required="true"
                    aria-invalid={fieldErrors.timeout ? true : undefined}
                    aria-describedby={fieldErrors.timeout ? fieldErrorId(`${kind}-timeout`) : undefined}
                    value={timeoutSeconds}
                    onChange={(event) => {
                      setTimeoutSeconds(event.target.value);
                      setFieldErrors((current) => ({ ...current, timeout: null }));
                    }}
                    className={INPUT_CLASS}
                  />
                </Field>
                {isNl2Sql ? (
                  <Field
                    label={t("settings.defaultLimit")}
                    htmlFor="nl2sql-default-limit"
                    required
                    error={fieldErrors.defaultLimit}
                  >
                    <input
                      id="nl2sql-default-limit"
                      type="number"
                      aria-required="true"
                      min="1"
                      max="1000"
                      value={defaultLimit}
                      aria-invalid={fieldErrors.defaultLimit ? true : undefined}
                      onChange={(event) => {
                        setDefaultLimit(event.target.value);
                        setFieldErrors((current) => ({ ...current, defaultLimit: null }));
                      }}
                      aria-describedby={
                        fieldErrors.defaultLimit
                          ? `nl2sql-default-limit-hint ${fieldErrorId("nl2sql-default-limit")}`
                          : "nl2sql-default-limit-hint"
                      }
                      className={INPUT_CLASS}
                    />
                    <p id="nl2sql-default-limit-hint" className="mt-1 text-xs leading-5 text-fg-muted">
                      {t("settings.productMcp.defaultLimitHint")}
                    </p>
                  </Field>
                ) : null}
              </fieldset>
              {settings.data ? <ProductMcpAuthStatus settings={settings.data} /> : null}
              {mutation.error ? <Banner severity="danger">{mutation.error.message}</Banner> : null}
              {canManage ? (
                <Button onClick={save} loading={mutation.isPending} icon={Save}>
                  {t("common.save")}
                </Button>
              ) : null}
            </CardContent>
          </Card>
        </QueryState>
      </div>
</PageBody>
    </>
  );
}

/** 未設定のときだけ、何が足りずどう直すかを示す（設定済みの常設 success バナーは出さない）。 */
function ProductMcpNotice({ settings }: { settings?: ProductMcpSettings }) {
  if (!settings) {
    return null;
  }
  const missing = [
    settings.configured ? null : t("settings.productMcp.missingUrl"),
    settings.service_token_configured ? null : t("settings.productMcp.missingSecret"),
  ].filter((item): item is string => item !== null);
  if (missing.length === 0) {
    return null;
  }
  return (
    <Banner severity="warning" title={t("settings.productMcp.notReady")}>
      {/* 環境変数名は長く区切りがないため、狭い幅では任意の位置で折り返す。 */}
      <ul className="list-disc space-y-1 pl-5 break-words [overflow-wrap:anywhere]">
        {missing.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Banner>
  );
}

/** サービス間認証の状態（.env で管理。値は表示しない）。 */
function ProductMcpAuthStatus({ settings }: { settings: ProductMcpSettings }) {
  const rows = [
    {
      id: "secret",
      label: t("settings.productMcp.secret"),
      hint: t("settings.productMcp.secretHint"),
      configured: settings.service_token_configured,
      missingVariant: "warning" as const,
    },
    {
      id: "service-user",
      label: t("settings.productMcp.serviceUser"),
      hint: t("settings.productMcp.serviceUserHint"),
      configured: settings.service_user_configured,
      missingVariant: "neutral" as const,
    },
  ];
  return (
    <ul aria-label={t("settings.productMcp.authStatus")} className="divide-y divide-border rounded-md border border-border">
      {rows.map((row) => (
        <li key={row.id} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-0.5 break-words [overflow-wrap:anywhere]">
            <p className="text-sm font-medium text-fg">{row.label}</p>
            <p className="text-xs leading-5 text-fg-muted">{row.hint}</p>
          </div>
          <StatusBadge
            className="shrink-0 self-start"
            variant={row.configured ? "success" : row.missingVariant}
            label={row.configured ? t("common.configured") : t("common.notConfigured")}
          />
        </li>
      ))}
    </ul>
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
              className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
            />
          </Field>
          <Field label={t("settings.mcpDiscovery.traceId")} htmlFor="mcp-discovery-trace-id">
            <input
              id="mcp-discovery-trace-id"
              value={traceId}
              onChange={(event) => setTraceId(event.target.value)}
              className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
          <TimedLoadingState label={t("loading.mcpTools")} testId="mcp-tools-loading">
            <TableSkeleton columns={5} />
          </TimedLoadingState>
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
  // 絞り込み（server / trace）で取り直した一覧は別の結果なので、そのときだけ 1 ページ目へ戻す。
  const { page, setPage, totalPages, pageItems, range } = usePagination(tools, DEFAULT_PAGE_SIZE, { resetKey: tools });
  const labels = agentPaginationLabels();
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
    <div className="grid min-w-0 gap-2">
      {/* md 以上は表、md 未満はカード。どちらも同じページ（10 件）を出し、Pagination は 1 つにする。 */}
      <DataTable
        className="hidden md:block"
        rows={pageItems}
        columns={mcpToolColumns}
        getRowKey={(tool) => `${tool.server_id ?? "default"}:${tool.name}`}
        rowProps={() => ({ className: `align-top ${INFORMATION_TABLE_ROW_CLASS}` })}
        tableClassName="w-full min-w-[720px]"
        ariaLabel={t("settings.mcpDiscovery.title")}
        scrollAriaLabel={listScrollLabel(t("settings.mcpDiscovery.title"))}
        stickyHeader
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
      />
      <div
        className={`grid gap-3 md:hidden ${INFORMATION_LIST_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
        role="region"
        aria-label={listScrollLabel(t("settings.mcpDiscovery.title"))}
        tabIndex={0}
      >
        {pageItems.map((tool) => (
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
      <Pagination
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        summary={labels.summary(range)}
        pageIndicator={labels.pageIndicator?.(page, totalPages)}
        prevLabel={labels.prev}
        nextLabel={labels.next}
        ariaLabel={labels.ariaLabel}
        testId="mcp-tools-pagination"
      />
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
  "h-10 w-full rounded-md border border-border bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";
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
  // 接続先の追加・変更・削除は Agent 管理の権限（admin）だけ。tool 一覧の取得は実データの閲覧権限が要る（#215）。
  const capabilities = useCapabilities();
  const canManage = capabilities.admin;
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
  const serverActions = (server: ExternalMcpServerSettings): EntityAction[] => canManage ? [
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
  ] : [];

  const list = servers.data?.servers ?? [];
  // 追加できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;
  const listTitle = t("nav.settingsExternalMcp");

  if (target.kind === "list") {
    const anyConfigured = list.some((server) => server.configured);
    return (
      <>
        <PageHeader
          wide
          title={listTitle}
          subtitle={t("page.settings.mcp.subtitle")}
          actions={
            canManage
              ? [
                  {
                    id: "create",
                    kind: "primary",
                    label: t("settings.mcpServers.add"),
                    icon: Plus,
                    onClick: editor.openNew,
                  },
                ]
              : []
          }
        />
        <PageBody wide className="space-y-6">
          <Section title={t("settings.mcpServers.title")} description={t("settings.mcpServers.description")}>
            <QueryState query={servers} loadingLabel={t("loading.mcpServers")} skeleton={<TableSkeleton columns={6} />}>
              <McpServerTable
                servers={list}
                onOpen={(server) => editor.openItem(server.server_id)}
                hrefFor={(server) => editor.itemHref(server.server_id)}
                actionsFor={serverActions}
              />
            </QueryState>
          </Section>
          {/* 接続先の一覧を取得し終えるまでは「未設定」と判断できないため、探索のパネルを出さない。 */}
          {capabilities.viewRuns && servers.data ? <McpDiscoveryPanel configured={anyConfigured} /> : null}
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
          <QueryState query={servers} loadingLabel={t("loading.mcpServers")} skeleton={<FormSkeleton fields={4} />}>
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
      readOnly={!canManage}
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
  readOnly,
  onBack,
  onSaved,
}: {
  server?: ExternalMcpServerSettings;
  actions: EntityAction[];
  /** 変更の権限がない利用者は閲覧だけ（保存を出さず、入力を無効にする）。 */
  readOnly: boolean;
  onBack: () => void;
  onSaved: (serverId: string) => Promise<void>;
}) {
  const [form, setForm] = useState<McpServerFormState>(() => mcpFormOf(server));
  const [formBaseline, setFormBaseline] = useState<McpServerFormState>(() => mcpFormOf(server));
  const [serverIdError, setServerIdError] = useState<string | null>(null);
  const [timeoutError, setTimeoutError] = useState<string | null>(null);
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
    const nextServerIdError =
      !editingId && !form.serverId.trim() ? t("settings.mcpServers.idRequired") : null;
    // 空のタイムアウトを 0 として保存しない。規則は backend（ExternalMcpServerCreate / Patch）と同じ（#540）。
    const nextTimeoutError = numberFieldError(form.timeoutSeconds, {
      label: t("settings.timeout"),
      min: 0,
      exclusiveMin: true,
      max: MCP_TIMEOUT_MAX_SECONDS,
    });
    setServerIdError(nextServerIdError);
    setTimeoutError(nextTimeoutError);
    if (
      focusFirstInvalidField([
        ["mcp-server-id", nextServerIdError],
        ["mcp-server-timeout", nextTimeoutError],
      ])
    ) {
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
          ...(readOnly
            ? []
            : [
                {
                  id: "save",
                  kind: "primary" as const,
                  label: editingId ? t("common.save") : t("common.create"),
                  icon: Save,
                  loading: saveMutation.isPending,
                  onClick: save,
                },
              ]),
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
        <fieldset disabled={readOnly} className="min-w-0 space-y-6">
          <Section title={t("mcpServers.connection")} description={t("settings.apiKeyManaged")}>
            <Card className="min-w-0">
              <CardContent className="space-y-4 pt-5">
                {/* Server ID は作成時だけ入力でき、必須（backend の create_external_mcp_server と送信ガード）。 */}
                <Field
                  label={t("settings.mcpServers.serverId")}
                  htmlFor="mcp-server-id"
                  required={!editingId}
                  error={serverIdError}
                >
                  <input
                    id="mcp-server-id"
                    value={form.serverId}
                    disabled={Boolean(editingId)}
                    aria-required={!editingId || undefined}
                    aria-invalid={serverIdError ? true : undefined}
                    aria-describedby={
                      serverIdError ? `mcp-server-id-hint ${fieldErrorId("mcp-server-id")}` : "mcp-server-id-hint"
                    }
                    onChange={(event) => {
                      setForm({ ...form, serverId: event.target.value });
                      setServerIdError(null);
                    }}
                    className={editingId ? `${INPUT_CLASS} opacity-60` : INPUT_CLASS}
                  />
                  <p id="mcp-server-id-hint" className="mt-1 text-xs leading-5 text-fg-muted">
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
                <Field label={t("settings.timeout")} htmlFor="mcp-server-timeout" required error={timeoutError}>
                  <input
                    id="mcp-server-timeout"
                    type="number"
                    min="1"
                    aria-required="true"
                    aria-invalid={timeoutError ? true : undefined}
                    aria-describedby={timeoutError ? fieldErrorId("mcp-server-timeout") : undefined}
                    value={form.timeoutSeconds}
                    onChange={(event) => {
                      setForm({ ...form, timeoutSeconds: event.target.value });
                      setTimeoutError(null);
                    }}
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
        </fieldset>
      </PageBody>
    </>
  );
}

function McpServerTable({
  servers,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  servers: ExternalMcpServerSettings[];
  onOpen: (server: ExternalMcpServerSettings) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (server: ExternalMcpServerSettings) => string;
  actionsFor: (server: ExternalMcpServerSettings) => EntityAction[];
}) {
  const columns: DataTableColumn<ExternalMcpServerSettings>[] = [
    {
      key: "server_id",
      header: t("settings.mcpServers.serverId"),
      rowHeader: true,
      render: (server) => (
        <div className="flex flex-wrap items-center gap-2">
          <RowTitleButton title={server.server_id} href={hrefFor(server)} onClick={() => onOpen(server)} />
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
    <PagedDataTable
      pageKey="mcpServers"
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
  // スキルの追加・変更・削除・再読込は Agent 管理の権限（admin）だけ（#215）。
  const { admin: canManage } = useCapabilities();
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
      visible: canManage && skill.source === "runtime",
      disabled: deleteMutation.isPending,
      onSelect: () => remove(skill),
    },
  ];

  const list = skills.data?.skills ?? [];
  // 追加できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("skills.title")}
          subtitle={t("page.skills.subtitle")}
          actions={
            canManage
              ? [
                  {
                    id: "reload",
                    kind: "utility",
                    label: t("skills.reload"),
                    icon: RefreshCw,
                    loading: reloadMutation.isPending,
                    onClick: () => reloadMutation.mutate(),
                  },
                  { id: "create", kind: "primary", label: t("skills.add"), icon: Plus, onClick: editor.openNew },
                ]
              : []
          }
          moreActionsLabel={t("common.moreActions")}
        />
        <PageBody wide>
          <Section title={t("skills.list")} description={t("skills.description")}>
            <QueryState query={skills} loadingLabel={t("loading.skills")} skeleton={<TableSkeleton columns={5} />}>
              <SkillTable
                skills={list}
                onOpen={(skill) => editor.openItem(skill.id)}
                hrefFor={(skill) => editor.itemHref(skill.id)}
                actionsFor={skillActions}
              />
            </QueryState>
          </Section>
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
          <QueryState query={skills} loadingLabel={t("loading.skills")} skeleton={<FormSkeleton fields={4} />}>
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
      readOnly={!canManage}
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
type SkillFieldErrors = {
  id?: string;
  name?: string;
  mcpRequirements?: string;
  resourceIds?: string;
};

function SkillEditor({
  skill,
  actions,
  readOnly,
  onBack,
  onSaved,
}: {
  skill?: AgentSkill;
  actions: EntityAction[];
  /** 変更の権限がない利用者は、実行時に追加したスキルも読み取り専用の詳細で出す。 */
  readOnly: boolean;
  onBack: () => void;
  onSaved: (skillId: string) => Promise<void>;
}) {
  const [form, setForm] = useState<SkillFormState>(() => skillFormOf(skill));
  const [formBaseline, setFormBaseline] = useState<SkillFormState>(() => skillFormOf(skill));
  const [fieldErrors, setFieldErrors] = useState<SkillFieldErrors>({});
  const editingId = skill?.id ?? null;
  const editable = !readOnly && (!skill || skill.source === "runtime");

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
    // 未入力・JSON の形式のエラーは欄の下に出し、画面の並び順で最初のエラーの欄へフォーカスする（#531 / #541）。
    const mcpRequirements = parseJsonField(form.mcpRequirementsJson, t("skills.mcpRequirements"), {
      required: true,
      expect: "array",
    });
    const resourceIds = parseJsonField(form.resourceIdsJson, t("skills.resourceIds"), {
      required: true,
      expect: "array",
    });
    const errors: SkillFieldErrors = {
      id: !editingId && !form.id.trim() ? t("skills.idRequired") : undefined,
      name: form.name.trim() ? undefined : t("skills.nameRequired"),
      mcpRequirements: mcpRequirements.ok ? undefined : mcpRequirements.error,
      resourceIds: resourceIds.ok ? undefined : resourceIds.error,
    };
    setFieldErrors(errors);
    if (
      focusFirstInvalidField([
        ["skill-id", errors.id],
        ["skill-name", errors.name],
        ["skill-mcp-requirements", errors.mcpRequirements],
        ["skill-resource-ids", errors.resourceIds],
      ])
    ) {
      return;
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
            {!editable && !readOnly ? <Banner severity="info">{t("skills.readOnly")}</Banner> : null}
          </Section>
        ) : null}
        {skill && !editable ? (
          <SkillReadOnlyDetail skill={skill} />
        ) : (
          <>
            {saveMutation.error ? <Banner severity="danger">{(saveMutation.error as Error).message}</Banner> : null}
            <Section title={t("skills.basic")}>
              <Card className="min-w-0">
                <CardContent className="space-y-4 pt-5">
                  {/* ID は作成時だけ入力でき、必須（backend の create_agent_skill と送信ガード）。 */}
                  <Field label={t("skills.id")} htmlFor="skill-id" required={!editingId} error={fieldErrors.id}>
                    <input
                      id="skill-id"
                      value={form.id}
                      disabled={Boolean(editingId)}
                      aria-required={!editingId || undefined}
                      aria-invalid={fieldErrors.id ? true : undefined}
                      aria-describedby={fieldErrors.id ? fieldErrorId("skill-id") : undefined}
                      onChange={(event) => {
                        setForm({ ...form, id: event.target.value });
                        setFieldErrors((current) => ({ ...current, id: undefined }));
                      }}
                      className={editingId ? `${INPUT_CLASS} opacity-60` : INPUT_CLASS}
                    />
                  </Field>
                  <Field label={t("skills.name")} htmlFor="skill-name" required error={fieldErrors.name}>
                    <input
                      id="skill-name"
                      value={form.name}
                      aria-required="true"
                      aria-invalid={fieldErrors.name ? true : undefined}
                      aria-describedby={fieldErrors.name ? fieldErrorId("skill-name") : undefined}
                      onChange={(event) => {
                        setForm({ ...form, name: event.target.value });
                        setFieldErrors((current) => ({ ...current, name: undefined }));
                      }}
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
                  <TextareaField
                    id="skill-instructions"
                    label={t("skills.instructions")}
                    value={form.instructions}
                    onValueChange={(value) => setForm({ ...form, instructions: value })}
                  />
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
                  <TextareaField
                    id="skill-mcp-requirements"
                    label={t("skills.mcpRequirements")}
                    required
                    error={fieldErrors.mcpRequirements}
                    helper={t("skills.mcpRequirementsHint")}
                    value={form.mcpRequirementsJson}
                    rows={8}
                    monospace
                    spellCheck={false}
                    onValueChange={(value) => {
                      setForm({ ...form, mcpRequirementsJson: value });
                      setFieldErrors((current) => ({ ...current, mcpRequirements: undefined }));
                    }}
                  />
                  <TextareaField
                    id="skill-resource-ids"
                    label={t("skills.resourceIds")}
                    required
                    error={fieldErrors.resourceIds}
                    value={form.resourceIdsJson}
                    rows={4}
                    monospace
                    spellCheck={false}
                    onValueChange={(value) => {
                      setForm({ ...form, resourceIdsJson: value });
                      setFieldErrors((current) => ({ ...current, resourceIds: undefined }));
                    }}
                  />
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
  hrefFor,
  actionsFor,
}: {
  skills: AgentSkill[];
  onOpen: (skill: AgentSkill) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (skill: AgentSkill) => string;
  actionsFor: (skill: AgentSkill) => EntityAction[];
}) {
  const columns: DataTableColumn<AgentSkill>[] = [
    {
      key: "name",
      header: t("skills.skill"),
      rowHeader: true,
      render: (skill) => (
        <RowTitleButton title={skill.name} subtitle={skill.id} href={hrefFor(skill)} onClick={() => onOpen(skill)} />
      ),
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
    <PagedDataTable
      pageKey="skills"
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
  // プラグインの install・有効化・削除・再読込は Agent 管理の権限（admin）だけ（#215）。
  const { admin: canManage } = useCapabilities();
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
  const pluginActions = (plugin: PluginSummary): EntityAction[] => canManage ? [
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
  ] : [];

  const list = plugins.data?.plugins ?? [];
  // install できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("plugins.title")}
          subtitle={t("page.plugins.subtitle")}
          actions={
            canManage
              ? [
                  {
                    id: "reload",
                    kind: "utility",
                    label: t("skills.reload"),
                    icon: RefreshCw,
                    loading: reloadMutation.isPending,
                    onClick: () => reloadMutation.mutate(),
                  },
                  { id: "install", kind: "primary", label: t("plugins.install"), icon: Plus, onClick: editor.openNew },
                ]
              : []
          }
          moreActionsLabel={t("common.moreActions")}
        />
        <PageBody wide>
          <Section title={t("plugins.title")} description={t("plugins.description")}>
            <QueryState query={plugins} loadingLabel={t("loading.plugins")} skeleton={<TableSkeleton columns={5} />}>
              <PluginTable
                plugins={list}
                onOpen={(plugin) => editor.openItem(plugin.id)}
                hrefFor={(plugin) => editor.itemHref(plugin.id)}
                actionsFor={pluginActions}
              />
            </QueryState>
          </Section>
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
          <QueryState query={plugins} loadingLabel={t("loading.plugins")} skeleton={<FormSkeleton fields={3} />}>
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
  const [manifestError, setManifestError] = useState<string | null>(null);
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
    // 未入力・JSON の形式のエラーは、どちらも manifest の欄の直下に出す（#541）。
    const manifest = parseJsonField<PluginManifest>(manifestJson, t("plugins.manifest"), {
      required: true,
      expect: "object",
    });
    if (!manifest.ok || !manifest.value) {
      setManifestError(manifest.ok ? t("plugins.manifestRequired") : manifest.error);
      focusField("plugin-manifest");
      return;
    }
    setManifestError(null);
    installMutation.mutate(manifest.value);
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
        {installMutation.error ? <Banner severity="danger">{(installMutation.error as Error).message}</Banner> : null}
        {installMutation.isPending ? (
          // manifest の検証と Skill / MCP の登録を行うため数秒かかる。スピナーはヘッダーの install ボタンが担う。
          <ProcessingIndicator
            active
            label={t("plugins.progress.installing")}
            operationKey="plugin-manifest-install"
            placement="action"
            activityIcon="none"
            className="rounded-md border border-border bg-surface-sunken px-3 py-2"
            testId="plugin-install-processing"
          />
        ) : null}
        <Section title={t("plugins.manifest")} description={t("plugins.manifestHint")}>
          <Card className="min-w-0">
            <CardContent className="pt-5">
              <TextareaField
                id="plugin-manifest"
                label={t("plugins.manifest")}
                required
                error={manifestError ?? undefined}
                value={manifestJson}
                rows={16}
                monospace
                spellCheck={false}
                onValueChange={(value) => {
                  setManifestJson(value);
                  setManifestError(null);
                }}
              />
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
          <QueryState
            query={record}
            loadingLabel={t("loading.pluginContents")}
            skeleton={
              <div className="grid min-w-0 gap-4 xl:grid-cols-3" aria-hidden="true">
                <Skeleton className="h-40" />
                <Skeleton className="h-40" />
                <Skeleton className="h-40" />
              </div>
            }
          >
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
  hrefFor,
  actionsFor,
}: {
  plugins: PluginSummary[];
  onOpen: (plugin: PluginSummary) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (plugin: PluginSummary) => string;
  actionsFor: (plugin: PluginSummary) => EntityAction[];
}) {
  const columns: DataTableColumn<PluginSummary>[] = [
    {
      key: "name",
      header: t("plugins.title"),
      rowHeader: true,
      render: (plugin) => (
        <RowTitleButton
          title={plugin.name}
          subtitle={`${plugin.id} · v${plugin.version}`}
          href={hrefFor(plugin)}
          onClick={() => onOpen(plugin)}
        />
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
    <PagedDataTable
      pageKey="plugins"
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
  // マーケットプレイスの追加・更新・削除とプラグインの install は Agent 管理の権限（admin）だけ（#215）。
  const { admin: canManage } = useCapabilities();
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
  const marketplaceActions = (source: MarketplaceSource): EntityAction[] => canManage ? [
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
  ] : [];

  const list = markets.data?.marketplaces ?? [];
  // 追加できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;

  if (target.kind === "list") {
    return (
      <>
        <PageHeader
          wide
          title={t("marketplaces.title")}
          subtitle={t("page.pluginMarketplaces.subtitle")}
          actions={
            canManage
              ? [{ id: "create", kind: "primary", label: t("marketplaces.add"), icon: Plus, onClick: editor.openNew }]
              : []
          }
        />
        <PageBody wide>
          <Section title={t("marketplaces.list")} description={t("marketplaces.description")}>
            {refreshMutation.isPending ? (
              // 取得元（Git / HTTP）からプラグイン一覧を読み直すため数秒以上かかる。
              // スピナーは行メニューの loading が担う（messaging.md §3.7）。
              <MarketplaceRefreshProcessing id={refreshMutation.variables} />
            ) : null}
            <QueryState query={markets} loadingLabel={t("loading.marketplaces")} skeleton={<TableSkeleton columns={5} />}>
              <MarketplaceTable
                sources={list}
                onOpen={(source) => editor.openItem(source.id)}
                hrefFor={(source) => editor.itemHref(source.id)}
                actionsFor={marketplaceActions}
              />
            </QueryState>
          </Section>
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
          <QueryState query={markets} loadingLabel={t("loading.marketplaces")} skeleton={<FormSkeleton fields={3} />}>
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
      refreshing={refreshMutation.isPending && refreshMutation.variables === source.id}
      canInstall={canManage}
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
  const [idError, setIdError] = useState<string | null>(null);
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
    setIdError(null);
    if (!form.id.trim()) {
      setIdError(t("marketplaces.idRequired"));
      focusField("mkt-id");
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
              <Field label={t("marketplaces.id")} htmlFor="mkt-id" required error={idError}>
                <input
                  id="mkt-id"
                  value={form.id}
                  aria-required="true"
                  aria-invalid={idError ? true : undefined}
                  aria-describedby={idError ? fieldErrorId("mkt-id") : undefined}
                  onChange={(event) => {
                    setForm({ ...form, id: event.target.value });
                    setIdError(null);
                  }}
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
  hrefFor,
  actionsFor,
}: {
  sources: MarketplaceSource[];
  onOpen: (source: MarketplaceSource) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (source: MarketplaceSource) => string;
  actionsFor: (source: MarketplaceSource) => EntityAction[];
}) {
  const columns: DataTableColumn<MarketplaceSource>[] = [
    {
      key: "name",
      header: t("marketplaces.name"),
      rowHeader: true,
      render: (source) => (
        <RowTitleButton title={source.name || source.id} subtitle={source.id} href={hrefFor(source)} onClick={() => onOpen(source)} />
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
    <PagedDataTable
      pageKey="marketplaces"
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
function MarketplaceRefreshProcessing({ id }: { id: string | undefined }) {
  return (
    <ProcessingIndicator
      active
      label={t("marketplaces.progress.refreshing", { id: id ?? "" })}
      operationKey={`marketplace-refresh-${id ?? ""}`}
      placement="action"
      activityIcon="none"
      className="mb-3 rounded-md border border-border bg-surface-sunken px-3 py-2"
      testId="marketplace-refresh-processing"
    />
  );
}

function MarketplaceDetail({
  source,
  actions,
  refreshing,
  canInstall,
  onBack,
  onInstalled,
}: {
  source: MarketplaceSource;
  actions: EntityAction[];
  refreshing: boolean;
  /** プラグインの install（Agent 管理の権限）。無ければ行の操作を出さない。 */
  canInstall: boolean;
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
              visible: canInstall,
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
          {refreshing ? <MarketplaceRefreshProcessing id={source.id} /> : null}
        </Section>
        <Section title={t("marketplaces.available")}>
          {installMutation.isPending ? (
            // プラグインの取得と Skill / MCP の登録を行うため数秒以上かかる。
            <ProcessingIndicator
              active
              label={t("marketplaces.progress.installing", { id: installMutation.variables ?? "" })}
              operationKey={`marketplace-install-${installMutation.variables ?? ""}`}
              placement="action"
              activityIcon="none"
              className="mb-3 rounded-md border border-border bg-surface-sunken px-3 py-2"
              testId="marketplace-install-processing"
            />
          ) : null}
          {listing.isLoading ? (
            <TimedLoadingState label={t("loading.marketplacePlugins")} testId="marketplace-plugins-loading">
              <TableSkeleton columns={3} />
            </TimedLoadingState>
          ) : listing.error ? (
            <Banner severity="danger">{(listing.error as Error).message}</Banner>
          ) : (
            <PagedDataTable
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

type CommandPolicyField = "workspaceRoot" | "outputLimit" | "defaultTimeout" | "maxTimeout" | "artifactStoragePath";
type CommandPolicyFieldErrors = Partial<Record<CommandPolicyField, string | null>>;

/** 画面の並び順（送信に失敗したら最初のエラーの欄へフォーカスする）。 */
const COMMAND_POLICY_FIELD_ORDER: readonly CommandPolicyField[] = [
  "workspaceRoot",
  "outputLimit",
  "defaultTimeout",
  "maxTimeout",
  "artifactStoragePath",
];
const COMMAND_POLICY_FIELD_IDS: Record<CommandPolicyField, string> = {
  workspaceRoot: "command-policy-workspace-root",
  outputLimit: "command-policy-output-limit",
  defaultTimeout: "command-policy-default-timeout",
  maxTimeout: "command-policy-max-timeout",
  artifactStoragePath: "command-policy-artifact-path",
};

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
  const [fieldErrors, setFieldErrors] = useState<CommandPolicyFieldErrors>({});
  const [baseline, setBaseline] = useState<CommandPolicyDraft | null>(null);
  const clearFieldError = (field: CommandPolicyField) =>
    setFieldErrors((current) => (current[field] ? { ...current, [field]: null } : current));
  /** 欄の aria-invalid / aria-describedby（エラーは Field が欄の直下に出す）。 */
  const invalidProps = (field: CommandPolicyField) =>
    fieldErrors[field]
      ? { "aria-invalid": true, "aria-describedby": fieldErrorId(COMMAND_POLICY_FIELD_IDS[field]) }
      : {};

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
    setFieldErrors({});
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
    // 欄ごとに検証し、エラーは欄の直下に出す（#541）。規則と文言は backend（_validate_command_policy_patch）と同じ。
    const nextErrors: CommandPolicyFieldErrors = {
      workspaceRoot: requiredTextError(workspaceRoot, t("settings.commandPolicy.workspaceRoot")),
      outputLimit: numberFieldError(outputLimit, {
        label: t("settings.commandPolicy.outputLimit"),
        integer: true,
        min: 1,
      }),
      defaultTimeout: numberFieldError(defaultTimeout, {
        label: t("settings.commandPolicy.defaultTimeout"),
        min: 0,
        exclusiveMin: true,
      }),
      maxTimeout: numberFieldError(maxTimeout, {
        label: t("settings.commandPolicy.maxTimeout"),
        min: 0,
        exclusiveMin: true,
      }),
      artifactStoragePath: requiredTextError(artifactStoragePath, t("settings.commandPolicy.artifactPath")),
    };
    if (!nextErrors.defaultTimeout && !nextErrors.maxTimeout && Number(defaultTimeout) > Number(maxTimeout)) {
      nextErrors.defaultTimeout = t("settings.commandPolicy.timeoutOrderInvalid");
    }
    setFieldErrors(nextErrors);
    if (
      focusFirstInvalidField(
        COMMAND_POLICY_FIELD_ORDER.map((field) => [COMMAND_POLICY_FIELD_IDS[field], nextErrors[field]] as const)
      )
    ) {
      return;
    }
    const parsedDefaultTimeout = Number(defaultTimeout);
    const parsedMaxTimeout = Number(maxTimeout);
    const parsedOutputLimit = Number(outputLimit);
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
        <QueryState query={settings} loadingLabel={t("loading.settings")} skeleton={<FormSkeleton fields={6} />}>
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
                <Field
                  label={t("settings.commandPolicy.workspaceRoot")}
                  htmlFor="command-policy-workspace-root"
                  required
                  error={fieldErrors.workspaceRoot}
                >
                  <input
                    id="command-policy-workspace-root"
                    aria-required="true"
                    {...invalidProps("workspaceRoot")}
                    value={workspaceRoot}
                    onChange={(event) => {
                      setWorkspaceRoot(event.target.value);
                      clearFieldError("workspaceRoot");
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field
                  label={t("settings.commandPolicy.outputLimit")}
                  htmlFor="command-policy-output-limit"
                  required
                  error={fieldErrors.outputLimit}
                >
                  <input
                    id="command-policy-output-limit"
                    aria-required="true"
                    {...invalidProps("outputLimit")}
                    type="number"
                    min="1"
                    value={outputLimit}
                    onChange={(event) => {
                      setOutputLimit(event.target.value);
                      clearFieldError("outputLimit");
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field
                  label={t("settings.commandPolicy.defaultTimeout")}
                  htmlFor="command-policy-default-timeout"
                  required
                  error={fieldErrors.defaultTimeout}
                >
                  <input
                    id="command-policy-default-timeout"
                    aria-required="true"
                    {...invalidProps("defaultTimeout")}
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={defaultTimeout}
                    onChange={(event) => {
                      setDefaultTimeout(event.target.value);
                      clearFieldError("defaultTimeout");
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field
                  label={t("settings.commandPolicy.maxTimeout")}
                  htmlFor="command-policy-max-timeout"
                  required
                  error={fieldErrors.maxTimeout}
                >
                  <input
                    id="command-policy-max-timeout"
                    aria-required="true"
                    {...invalidProps("maxTimeout")}
                    type="number"
                    min="0.1"
                    step="0.1"
                    value={maxTimeout}
                    onChange={(event) => {
                      setMaxTimeout(event.target.value);
                      clearFieldError("maxTimeout");
                      // 大小の関係のエラーは既定タイムアウト秒の欄に出すので、最大を直したときも消す。
                      clearFieldError("defaultTimeout");
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
              </div>

              <TextareaField
                id="command-policy-allowed-prefixes"
                label={t("settings.commandPolicy.allowedPrefixes")}
                helper={t("settings.commandPolicy.allowedPrefixesHint")}
                value={allowedPrefixes}
                onValueChange={setAllowedPrefixes}
                rows={5}
                monospace
                textareaClassName="min-h-32"
              />

              <div className="grid gap-4 md:grid-cols-2">
                <Field label={t("settings.commandPolicy.artifactStorage")} htmlFor="command-policy-artifact-storage">
                  <select
                    id="command-policy-artifact-storage"
                    value={artifactStorageBackend}
                    onChange={(event) => setArtifactStorageBackend(event.target.value as "inline" | "filesystem")}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  >
                    <option value="inline">{t("settings.commandPolicy.inline")}</option>
                    <option value="filesystem">{t("settings.commandPolicy.filesystem")}</option>
                  </select>
                  <p className="mt-1 text-xs leading-5 text-fg-muted">{t("settings.commandPolicy.storageHint")}</p>
                </Field>
                <Field
                  label={t("settings.commandPolicy.artifactPath")}
                  htmlFor="command-policy-artifact-path"
                  required
                  error={fieldErrors.artifactStoragePath}
                >
                  <input
                    id="command-policy-artifact-path"
                    aria-required="true"
                    {...invalidProps("artifactStoragePath")}
                    value={artifactStoragePath}
                    onChange={(event) => {
                      setArtifactStoragePath(event.target.value);
                      clearFieldError("artifactStoragePath");
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
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
        <QueryState query={settings} loadingLabel={t("loading.settings")} skeleton={<FormSkeleton fields={4} />}>
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
                  className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring sm:max-w-xs"
                >
                  <option value="approval">{t("settings.toolPolicy.defaultModeApproval")}</option>
                  <option value="deny">{t("settings.toolPolicy.defaultModeDeny")}</option>
                </select>
              </Field>

              {tools.error ? <Banner severity="danger">{tools.error.message}</Banner> : null}
              {tools.isLoading ? (
                <TimedLoadingState label={t("loading.tools")} testId="tool-policy-tools-loading">
                  <ListSkeleton rows={4} rowClassName="h-20" />
                </TimedLoadingState>
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
                            className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
  const [fieldErrors, setFieldErrors] = useState<{
    maxToolCalls?: string | null;
    maxPendingApprovals?: string | null;
  }>({});
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
    setFieldErrors({});
  }

  const draft = { maxToolCalls, maxPendingApprovals };
  useSettingsLeaveGuard(baseline !== null && !sameDraft(draft, baseline), mutation.isPending);

  function save() {
    // 0 は「許可しない」という正当な値なので、空を 0 として保存しない（空は未入力のエラー。#540）。
    const nextErrors = {
      maxToolCalls: numberFieldError(maxToolCalls, {
        label: t("settings.runtimeSafety.maxToolCalls"),
        integer: true,
        min: 0,
      }),
      maxPendingApprovals: numberFieldError(maxPendingApprovals, {
        label: t("settings.runtimeSafety.maxPendingApprovals"),
        integer: true,
        min: 0,
      }),
    };
    setFieldErrors(nextErrors);
    if (
      focusFirstInvalidField([
        ["runtime-safety-max-tool-calls", nextErrors.maxToolCalls],
        ["runtime-safety-max-pending-approvals", nextErrors.maxPendingApprovals],
      ])
    ) {
      return;
    }
    const submitted = draft;
    mutation.mutate(
      {
        max_tool_calls_per_run: Number(maxToolCalls),
        max_pending_approvals_per_run: Number(maxPendingApprovals),
      },
      { onSuccess: () => setBaseline(submitted) }
    );
  }

  return (
    <>
      <PageHeader wide title={t("nav.settingsRuntimeSafety")} subtitle={t("page.settings.runtimeSafety.subtitle")} />
      <PageBody wide>
<div className="space-y-5">
        <QueryState query={settings} loadingLabel={t("loading.settings")} skeleton={<FormSkeleton fields={4} />}>
          <Banner severity="info">{t("settings.runtimeSafety.guardrail")}</Banner>
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("nav.settingsRuntimeSafety")}</CardTitle>
              <CardDescription>{t("page.settings.runtimeSafety.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-x-6 gap-y-4 lg:grid-cols-2">
                <Field
                  label={t("settings.runtimeSafety.maxToolCalls")}
                  htmlFor="runtime-safety-max-tool-calls"
                  required
                  error={fieldErrors.maxToolCalls}
                >
                  <input
                    id="runtime-safety-max-tool-calls"
                    type="number"
                    min="0"
                    aria-required="true"
                    aria-invalid={fieldErrors.maxToolCalls ? true : undefined}
                    aria-describedby={
                      fieldErrors.maxToolCalls ? fieldErrorId("runtime-safety-max-tool-calls") : undefined
                    }
                    value={maxToolCalls}
                    onChange={(event) => {
                      setMaxToolCalls(event.target.value);
                      setFieldErrors((current) => ({ ...current, maxToolCalls: null }));
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field
                  label={t("settings.runtimeSafety.maxPendingApprovals")}
                  htmlFor="runtime-safety-max-pending-approvals"
                  required
                  error={fieldErrors.maxPendingApprovals}
                >
                  <input
                    id="runtime-safety-max-pending-approvals"
                    type="number"
                    min="0"
                    aria-required="true"
                    aria-invalid={fieldErrors.maxPendingApprovals ? true : undefined}
                    aria-describedby={
                      fieldErrors.maxPendingApprovals
                        ? fieldErrorId("runtime-safety-max-pending-approvals")
                        : undefined
                    }
                    value={maxPendingApprovals}
                    onChange={(event) => {
                      setMaxPendingApprovals(event.target.value);
                      setFieldErrors((current) => ({ ...current, maxPendingApprovals: null }));
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
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

/** 実行時スナップショットの置換の確認語（入力の完全一致で置換を許す）。 */
const SNAPSHOT_REPLACE_CONFIRMATION = "REPLACE";
/** 確認語欄の入力欄の id と、その説明（置換を使えない理由）の id（`${id}-helper` は ExecutionConfirmationField の決まり）。 */
const SNAPSHOT_REPLACE_CONFIRM_ID = "runtime-snapshot-confirm";
const SNAPSHOT_REPLACE_HELPER_ID = `${SNAPSHOT_REPLACE_CONFIRM_ID}-helper`;

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
  const [importError, setImportError] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<RuntimeSnapshotImportResult | null>(null);
  // インポート JSON と理由は未保存の下書き。確認語は保存も復元もしない（離脱で state ごと消える）。#87
  useSettingsLeaveGuard(importText.trim() !== "" || reason.trim() !== "", importSnapshot.isPending);

  // 取得したスナップショットが変わったレンダーで、エクスポート欄を取り直す（effect で setState しない）。
  const snapshotChanged = useValuesChanged([snapshot.data]);
  if (snapshotChanged && snapshot.data) {
    setExportText(JSON.stringify(snapshot.data, null, 2));
  }

  function parseImportSnapshot(): RuntimeSnapshot | null {
    // 未入力・JSON の形式のエラーは、どちらもインポート JSON の欄の直下に出す（#541）。
    const parsed = parseJsonField<RuntimeSnapshot>(importText, t("settings.snapshot.importJson"), {
      required: true,
      expect: "object",
    });
    if (!parsed.ok || !parsed.value) {
      setImportError(parsed.ok ? t("settings.snapshot.importJsonRequired") : parsed.error);
      focusField("runtime-snapshot-import");
      return null;
    }
    setImportError(null);
    return parsed.value;
  }

  function copyCurrentSnapshotToImport() {
    setImportText(exportText);
    setValidationResult(null);
    setImportError(null);
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
    // 確認語が一致するまで置換のボタンは押せず、理由は確認語の欄の説明（helper）が示す。
    if (confirmText !== SNAPSHOT_REPLACE_CONFIRMATION) {
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
        <QueryState query={snapshot} loadingLabel={t("loading.snapshot")} skeleton={<FormSkeleton fields={2} />}>
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
              <TextareaField
                id="runtime-snapshot-export"
                label={t("settings.snapshot.current")}
                value={exportText}
                readOnly
                monospace
                spellCheck={false}
                textareaClassName="min-h-80"
              />
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
            <TextareaField
              id="runtime-snapshot-import"
              label={t("settings.snapshot.importJson")}
              required
              error={importError ?? undefined}
              value={importText}
              onValueChange={(value) => {
                setImportText(value);
                setValidationResult(null);
                setImportError(null);
              }}
              monospace
              spellCheck={false}
              textareaClassName="min-h-80"
            />
            <Field label={t("settings.snapshot.reason")} htmlFor="runtime-snapshot-reason">
              <input
                id="runtime-snapshot-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
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
            <ExecutionConfirmationField
              id={SNAPSHOT_REPLACE_CONFIRM_ID}
              value={confirmText}
              onChange={setConfirmText}
              confirmed={confirmText === SNAPSHOT_REPLACE_CONFIRMATION}
              expectedLabel={SNAPSHOT_REPLACE_CONFIRMATION}
              placeholder={t("settings.snapshot.confirmPlaceholder")}
              helper={t("settings.snapshot.confirmRequired")}
              labels={{ label: t("settings.snapshot.confirmText") }}
              disabled={importSnapshot.isPending}
              actions={
                <Button
                  variant="danger"
                  size="lg"
                  className="w-full sm:w-auto"
                  onClick={() => void replaceRuntimeSnapshot()}
                  loading={importSnapshot.isPending}
                  disabled={confirmText !== SNAPSHOT_REPLACE_CONFIRMATION}
                  // 使えない間は、理由（確認語欄の説明。id は ExecutionConfirmationField の `${id}-helper`）を
                  // ボタンの説明として読み上げる。一致したら外す（#426。#379 の置き換えで外れていた）。
                  aria-describedby={confirmText !== SNAPSHOT_REPLACE_CONFIRMATION ? SNAPSHOT_REPLACE_HELPER_ID : undefined}
                  icon={Upload}>
                  {t("common.replace")}
                </Button>
              }
            />
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
  skillsLoading,
  skillsError,
  bindings,
  bindingsLoading,
  runtimes,
  actions,
  readOnly,
  onBack,
  onCreated,
}: {
  agent?: AgentProfile;
  availableSkills: AgentSkill[];
  /** Skill を取得中は「取得できません」と誤って出さず、読み込み中の表示にする。 */
  skillsLoading: boolean;
  skillsError: Error | null;
  bindings: RuntimeBinding[];
  /** 実行先（Binding / Runtime）を取得中は「未設定」と誤って出さず、読み込み中の表示にする。 */
  bindingsLoading: boolean;
  runtimes: RuntimeDefinition[];
  actions: EntityAction[];
  /** 変更の権限がない利用者は閲覧だけ（保存・Binding の操作を出さず、入力を無効にする）。 */
  readOnly: boolean;
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
  const [nameError, setNameError] = useState<string | null>(null);

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
    setNameError(null);
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
    setNameError(null);
    if (!name.trim()) {
      setNameError(t("agent.nameRequired"));
      focusField(`${fieldId}-agent-name`);
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
          ...(readOnly
            ? []
            : [
                {
                  id: "save",
                  kind: "primary" as const,
                  label: agent ? t("common.save") : t("common.create"),
                  icon: Save,
                  loading: pending,
                  onClick: saveAgent,
                },
              ]),
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
        {error ? <Banner severity="danger">{error.message}</Banner> : null}
        <fieldset disabled={readOnly} className="min-w-0 space-y-6">
          <Section title={t("agent.basic")}>
            <Card className="min-w-0">
              <CardContent className="space-y-4 pt-5">
                <Field label={t("agent.name")} htmlFor={`${fieldId}-agent-name`} required error={nameError}>
                  <input
                    id={`${fieldId}-agent-name`}
                    value={name}
                    aria-required="true"
                    aria-invalid={nameError ? true : undefined}
                    aria-describedby={nameError ? fieldErrorId(`${fieldId}-agent-name`) : undefined}
                    onChange={(event) => {
                      setName(event.target.value);
                      setNameError(null);
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <Field label={t("agent.description")} htmlFor={`${fieldId}-agent-description`}>
                  <input
                    id={`${fieldId}-agent-description`}
                    value={agentDescription}
                    onChange={(event) => setAgentDescription(event.target.value)}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                  />
                </Field>
                <TextareaField
                  id={`${fieldId}-agent-instructions`}
                  label={t("agent.instructions")}
                  value={instructions}
                  onValueChange={setInstructions}
                  textareaClassName="min-h-24"
                />
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
            {skillsLoading ? (
              <TimedLoadingState label={t("loading.skills")} testId="agent-skills-loading">
                <ListSkeleton rows={4} rowClassName="h-11" className="md:grid-cols-2" />
              </TimedLoadingState>
            ) : availableSkills.length ? (
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
            ) : skillsError ? null : (
              <Banner severity="warning">{t("agent.skillsUnavailable")}</Banner>
            )}
          </Section>
        </fieldset>
        {agent ? (
          <RuntimeBindingsPanel
            agent={agent}
            bindings={bindings}
            loading={bindingsLoading}
            runtimes={runtimes}
            readOnly={readOnly}
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
/** backend の RuntimeBinding の識別子（`_SAFE_ID`）と同じ規則。 */
const BINDING_REF_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

function RuntimeBindingsPanel({
  agent,
  bindings,
  loading,
  runtimes,
  readOnly,
  onDirtyChange,
}: {
  agent: AgentProfile;
  bindings: RuntimeBinding[];
  loading: boolean;
  runtimes: RuntimeDefinition[];
  /** Binding の追加・既定の変更・同期・削除を出さない（Agent 管理の権限がない利用者）。 */
  readOnly: boolean;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const candidates = runtimes.filter((runtime) => runtime.kind !== "legacy_native");
  const defaultRuntimeId = candidates[0]?.id ?? "";
  const [runtimeId, setRuntimeId] = useState(defaultRuntimeId);
  const [nativeAgentRef, setNativeAgentRef] = useState(agent.id);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [nativeAgentRefError, setNativeAgentRefError] = useState<string | null>(null);
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

  // 追加ボタンを押せなくするだけにせず、押したときに欄の直下へ理由を出す（UX 契約 messaging.md §3.2.1。#541）。
  function addBinding() {
    const runtimeFieldId = `${agent.id}-binding-runtime`;
    const refFieldId = `${agent.id}-binding-native-ref`;
    const nextRuntimeError = requiredSelectError(runtimeId, t("binding.runtime"));
    const trimmedRef = nativeAgentRef.trim();
    const nextRefError =
      requiredTextError(trimmedRef, t("binding.nativeAgentRef")) ??
      (BINDING_REF_PATTERN.test(trimmedRef) ? null : t("binding.nativeAgentRefInvalid"));
    setRuntimeError(nextRuntimeError);
    setNativeAgentRefError(nextRefError);
    if (
      focusFirstInvalidField([
        [runtimeFieldId, nextRuntimeError],
        [refFieldId, nextRefError],
      ])
    ) {
      return;
    }
    createBinding.mutate({
      agent_id: agent.id,
      runtime_id: runtimeId,
      native_agent_ref: trimmedRef,
      is_default: !bindings.length,
      enabled: true,
    });
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

  const bindingActions = (binding: RuntimeBinding): EntityAction[] => readOnly ? [] : [
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
      loading: syncBinding.isPending && syncBinding.variables === binding.id,
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
          loading={syncBinding.isPending && syncBinding.variables === binding.id}
          testId={`binding-row-actions-${binding.id}`}
        />
      ),
    },
  ];

  return (
    <Section title={t("binding.title")} description={t("binding.description")}>
      <Card className="min-w-0">
        <CardContent className="space-y-4 pt-5">
          {syncBinding.isPending ? (
            // 実行先の Runtime へ Agent の定義を同期するため数秒以上かかる。スピナーは行メニューの loading が担う。
            <ProcessingIndicator
              active
              label={t("binding.progress.syncing", {
                ref: bindings.find((binding) => binding.id === syncBinding.variables)?.native_agent_ref ?? "",
              })}
              operationKey={`binding-sync-${syncBinding.variables ?? ""}`}
              placement="action"
              activityIcon="none"
              className="rounded-md border border-border bg-surface-sunken px-3 py-2"
              testId="binding-sync-processing"
            />
          ) : null}
          {loading ? (
            <TimedLoadingState label={t("loading.bindings")} testId="agent-bindings-loading">
              <TableSkeleton rows={2} columns={columns.length} />
            </TimedLoadingState>
          ) : bindings.length ? (
            <PagedDataTable
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
          {readOnly ? null : (
            <>
              <div className="grid gap-3 md:grid-cols-2">
                {/* Runtime と Runtime 内 Agent ID は RuntimeBinding の必須項目（未入力は追加を押したときに欄の下へ出す）。 */}
                <Field
                  label={t("binding.runtime")}
                  htmlFor={`${agent.id}-binding-runtime`}
                  required
                  error={runtimeError}
                >
                  <select
                    id={`${agent.id}-binding-runtime`}
                    value={runtimeId}
                    aria-required="true"
                    aria-invalid={runtimeError ? true : undefined}
                    aria-describedby={runtimeError ? fieldErrorId(`${agent.id}-binding-runtime`) : undefined}
                    onChange={(event) => {
                      setRuntimeId(event.target.value);
                      setRuntimeError(null);
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm"
                  >
                    {candidates.map((runtime) => (
                      <option key={runtime.id} value={runtime.id}>
                        {runtime.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label={t("binding.nativeAgentRef")}
                  htmlFor={`${agent.id}-binding-native-ref`}
                  required
                  error={nativeAgentRefError}
                >
                  <input
                    id={`${agent.id}-binding-native-ref`}
                    value={nativeAgentRef}
                    aria-required="true"
                    aria-invalid={nativeAgentRefError ? true : undefined}
                    aria-describedby={
                      nativeAgentRefError ? fieldErrorId(`${agent.id}-binding-native-ref`) : undefined
                    }
                    onChange={(event) => {
                      setNativeAgentRef(event.target.value);
                      setNativeAgentRefError(null);
                    }}
                    className="h-10 w-full rounded-md border border-border-control bg-surface-sunken aria-[invalid=true]:border-danger-fg px-3 text-sm"
                  />
                </Field>
              </div>
              {error ? <Banner severity="danger">{error.message}</Banner> : null}
              <Button
                variant="secondary"
                loading={createBinding.isPending}
                onClick={addBinding}
                icon={Plus}
              >
                {t("binding.add")}
              </Button>
            </>
          )}
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
        {/* 5 秒ごとの再取得で行が変わっても、ページは作業状態に残して戻さない。 */}
        <PagedDataTable
          pageKey="runs"
          rows={runs}
          columns={columns}
          getRowKey={(run) => run.id}
          selectedRowKey={selectedRunId}
          onRowClick={(run) => onSelect(run.id)}
          rowProps={(run) => ({ className: "align-top", "data-testid": `run-row-${run.id}` })}
          ariaLabel={t("run.history")}
          paginationTestId="run-history-pagination"
          empty={<EmptyState title={t("common.empty.title")} />}
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
  sseState,
  capabilities,
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
  sseState: RunEventSourceState;
  capabilities: AgentCapabilities;
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
          <RunProgressIndicator run={run} />
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
        sseState={sseState}
        // WebSocket のコマンドも REST と同じ capability で出し分ける（backend も同じ規則で拒否する）。
        canCancel={canCancel && capabilities.operateRuns}
        canResume={canResume && capabilities.operateRuns}
        pendingApproval={capabilities.decideApprovals ? pendingApproval : undefined}
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
      {/* Run の監査記録は監査の閲覧（auditor）か Agent 管理の権限が必要（#215）。 */}
      {capabilities.viewAudit ? <AuditPanel runId={run.id} /> : null}

      {structured ? <StructuredResultTable key={run.id} result={structured} /> : null}
    </section>
  );
}

/**
 * 実行中の Run の経過時間（durable job。messaging.md §3.7 の placement="job"）。
 * 開始時刻はサーバーの created_at を使い、Run を開き直しても経過時間が 0 に戻らない。
 * 実行中の工程（tool / step）が分かるときは工程名も出す。承認待ちは人の操作を待つため遅延の案内を出さない。
 */
function RunProgressIndicator({ run }: { run: RunState }) {
  if (!["queued", "running", "waiting_approval"].includes(run.status)) return null;
  const currentStep = [...run.steps].reverse().find((step) => step.status === "running");
  const stepName = currentStep ? (currentStep.tool_call?.name ?? currentStep.kind) : null;
  const label =
    run.status === "queued"
      ? t("run.progress.queued")
      : run.status === "waiting_approval"
        ? t("run.progress.waitingApproval")
        : stepName
          ? t("run.progress.runningStep", { step: stepName })
          : t("run.progress.running");
  return (
    <ProcessingIndicator
      active
      label={label}
      operationKey={run.id}
      startedAt={run.created_at}
      placement="job"
      showSlowMessage={run.status !== "waiting_approval"}
      className="rounded-md border border-border bg-surface-sunken px-3 py-2"
      testId="run-progress"
    />
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
  sseState,
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
  sseState: RunEventSourceState;
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
        {/* 購読の方式（モード）の切り替え。共有の ToggleChip を使う（buttons.md §6。手書きのセグメントは作らない）。 */}
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("run.streamMode")}>
          <ToggleChip selected={mode === "sse"} onClick={() => onModeChange("sse")}>
            {t("run.stream.sse")}
          </ToggleChip>
          <ToggleChip selected={mode === "websocket"} onClick={() => onModeChange("websocket")}>
            {t("run.stream.websocket")}
          </ToggleChip>
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

        {mode === "websocket" && websocketState.stopReason ? (
          <Banner
            severity="warning"
            title={t("run.stream.stoppedTitle")}
            action={
              <Button variant="secondary" size="sm" icon={RefreshCw} onClick={websocketState.reconnect}>
                {t("run.stream.reconnect")}
              </Button>
            }
          >
            <span data-testid="run-stream-stopped">{websocketState.stopReason}</span>
          </Banner>
        ) : null}
        {mode === "sse" && sseState.status === "failed" ? (
          <Banner
            severity="warning"
            title={t("run.stream.stoppedTitle")}
            action={
              <Button variant="secondary" size="sm" icon={RefreshCw} onClick={sseState.reconnect}>
                {t("run.stream.reconnect")}
              </Button>
            }
          >
            <span data-testid="run-stream-stopped">{t("run.stream.sseFailed")}</span>
          </Banner>
        ) : null}

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
                {/* 承認の拒否・Run のキャンセルは確定的な破壊ではないため赤塗り（danger）にせず、
                    secondary + tone="danger" で控えめに示す（buttons.md §3、README §4 Button）。 */}
                <Button
                  variant="secondary"
                  tone="danger"
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
                variant="secondary"
                tone="danger"
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
    stopped: t("run.stream.wsStopped"),
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
        <QueryState
          query={audit}
          loadingLabel={t("loading.runAudit")}
          skeleton={<ListSkeleton rows={3} rowClassName="h-24" />}
        >
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

/** Run の構造化結果。別の Run に切り替えると呼び出し側の key で作り直し、1 ページ目から出す。 */
function StructuredResultTable({ result }: { result: StructuredResult }) {
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.structuredResult")}</CardTitle>
        <CardDescription>{result.sql ?? t("run.sqlHidden")}</CardDescription>
      </CardHeader>
      <CardContent>
        <PagedDataTable
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

/** 未入力などの欄のエラーの id。入力の aria-describedby と FieldError の id をそろえる（#531）。 */
function fieldErrorId(htmlFor: string): string {
  return `${htmlFor}-error`;
}

/**
 * 素の input / select / textarea のラベル・必須の表示・欄のエラー（#531）。
 * `required` の欄は入力側に `aria-required="true"` を付ける（タグは FieldLabel が二重に読ませない）。
 * `error` を渡す欄は、入力側に `aria-invalid` と `aria-describedby={fieldErrorId(htmlFor)}` を付ける。
 */
function Field({
  label,
  htmlFor,
  required = false,
  error,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  /** backend の検証か画面の送信ガードで必須の欄だけ true にする。 */
  required?: boolean;
  /** 欄の直下に出すエラー（例:「名前を入力してください。」）。 */
  error?: string | null;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={className ? `space-y-1.5 ${className}` : "space-y-1.5"}>
      <FieldLabel htmlFor={htmlFor} label={label} required={required} />
      {children}
      <FieldError id={fieldErrorId(htmlFor)} message={error} />
    </div>
  );
}

/** 送信に失敗したとき、最初のエラーの欄へフォーカスを移す（UX 契約 messaging.md §3.2.1）。 */
function focusField(id: string): void {
  document.getElementById(id)?.focus();
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
