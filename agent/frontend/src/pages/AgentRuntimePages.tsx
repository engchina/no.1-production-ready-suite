import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Check,
  Download,
  FileText,
  GitBranch,
  PlayCircle,
  Plus,
  Power,
  PowerOff,
  Rocket,
  RotateCcw,
  RefreshCw,
  Save,
  Server,
  ShieldAlert,
  SlidersHorizontal,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import {
  Banner,
  Button,
  buttonVariants,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  ExecutionConfirmationField,
  DEFAULT_PAGE_SIZE,
  Disclosure,
  EmptyState,
  FormActionBar,
  FormStatus,
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
  SaveErrorBanner,
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
  SelectField,
  TextareaField,
  TextField,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";

import {
  agentApi,
  type AgentProfile,
  type AgentTemplate,
  type AgentProfilePatchPayload,
  type AgentSkill,
  type AgentVersion,
  type Artifact,
  type ApprovalRequest,
  type ExternalMcpToolInfo,
  type McpAuthMode,
  type McpConnectionSettings,
  type McpConnectionWritePayload,
  type MarketplaceSource,
  type PluginManifest,
  type PluginSummary,
  type RuntimeSnapshot,
  type RuntimeSnapshotImportResult,
  type RuntimeSnapshotSummary,
  type RunAuditData,
  type RunEvent,
  type RunState,
  type BuiltinRuntimeModel,
  type ToolCallAuditFilters,
  type ToolCallAuditRecord,
  type ToolAuditRecord,
  type ToolDefinition,
} from "@/lib/api";
import {
  AgentSplitPane,
  MissingEditorTarget,
} from "@/components/EntityLayout";
import { agentPaginationLabels, listScrollLabel, PagedDataTable, QueryState } from "@/components/ListViews";
import { AgentTemplatePicker } from "@/components/agents/AgentTemplatePicker";
import { AnswerFeedback } from "@/components/chat/AnswerFeedback";
import { useEditorRoute } from "@/lib/editor-route";
import {
  focusFirstInvalidField,
  numberFieldError,
  parseJsonField,
} from "@/lib/field-validation";
import { formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { MENU_PERMISSIONS, useCapabilities, type AgentCapabilities } from "@/lib/permissions";
import { APP_ROUTES } from "@/lib/routes";
import { useAuth } from "@/components/security/AuthProvider";
import { securityApi } from "@/lib/security-api";
import { useValuesChanged } from "@/lib/render-sync";
import {
  approvalStatusOptions,
  approvalStatusView,
  artifactKindView,
  eventTypeView,
  permissionView,
  policyDecisionView,
  runStatusView,
  stepStatusOptions,
  stepStatusView,
} from "@/lib/status-labels";
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
  const confirm = useConfirm();
  const editor = useEditorRoute();
  // 業務 Agent の変更は Agent 管理の権限（admin）だけ。それ以外は閲覧だけにする（#215）。
  const { admin: canManage } = useCapabilities();
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const skills = useQuery({ queryKey: ["skills"], queryFn: agentApi.listSkills });
  // 組み込み Runtime で選べるモデル（システム設定 > モデル の登録モデル。#754）。
  const runtimeStatus = useQuery({ queryKey: ["runtime-status"], queryFn: agentApi.getRuntimeStatus });
  const toggleAgent = useMutation({
    mutationFn: (agent: AgentProfile) => agentApi.patchAgent(agent.id, { enabled: !agent.enabled }),
    onSuccess: () => {
      toast.success(t("agent.enabledUpdated"));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const publishAgent = useMutation({
    mutationFn: (agent: AgentProfile) => agentApi.publishAgent(agent.id),
    onSuccess: (published) => {
      toast.success(t("agent.version.published", { version: published.published_version ?? 0 }));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error) => toast.error(error.message),
  });

  async function confirmPublish(agent: AgentProfile) {
    const next = Math.max(0, ...(agent.versions ?? []).map((item) => item.version)) + 1;
    const ok = await confirm({
      title: t("agent.version.publishTitle", { version: next }),
      description: t("agent.version.publishMessage"),
      confirmLabel: t("agent.version.publish"),
      cancelLabel: t("common.cancel"),
    });
    if (ok) publishAgent.mutate(agent);
  }

  // 一覧の行と詳細（エディタの概要）で同じ定義を使う（UX 契約 buttons.md §5.1）。
  const agentActions = (agent: AgentProfile): EntityAction[] =>
    canManage
      ? [
          {
            // 下書きを版として公開する（#770）。公開していない変更があるときだけ出す。
            id: "publish",
            label: t("agent.version.publish"),
            icon: Rocket,
            visible: agent.unpublished_changes,
            disabled: publishAgent.isPending,
            onSelect: () => void confirmPublish(agent),
          },
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
      models={runtimeStatus.data?.models ?? []}
      defaultModelId={runtimeStatus.data?.model_id ?? ""}
      modelsLoading={runtimeStatus.isLoading}
      actions={agent ? agentActions(agent) : []}
      readOnly={!canManage}
      onBack={() => editor.backToList()}
      onCreated={(created) => editor.openItem(created.id, { replace: true })}
    />
  );
}

/** 公開の状態（公開中 vN / 未公開 / 公開していない変更あり。#770）。 */
function AgentVersionBadges({ agent }: { agent: AgentProfile }) {
  if (agent.published_version === null) {
    return <StatusBadge variant="warning" label={t("agent.version.unpublished")} />;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <StatusBadge
        variant="success"
        label={t("agent.version.publishedBadge", { version: agent.published_version })}
      />
      {agent.unpublished_changes ? (
        <StatusBadge variant="info" label={t("agent.version.changed")} icon={false} />
      ) : null}
    </span>
  );
}

/** 公開した版の一覧と「この版に戻す」（#770）。 */
function AgentVersionsSection({ agent, readOnly }: { agent: AgentProfile; readOnly: boolean }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const restore = useMutation({
    mutationFn: (version: number) => agentApi.restoreAgentVersion(agent.id, version),
    onSuccess: (restored) => {
      toast.success(t("agent.version.restored", { version: restored.published_version ?? 0 }));
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: (error) => toast.error(error.message),
  });

  async function confirmRestore(version: AgentVersion) {
    const ok = await confirm({
      title: t("agent.version.restoreTitle", { version: version.version }),
      description: t("agent.version.restoreMessage"),
      confirmLabel: t("agent.version.restore"),
      cancelLabel: t("common.cancel"),
    });
    if (ok) restore.mutate(version.version);
  }

  const rows = [...(agent.versions ?? [])].reverse();
  const columns: DataTableColumn<AgentVersion>[] = [
    {
      key: "version",
      header: t("agent.version.number"),
      rowHeader: true,
      render: (version) => (
        <span className="inline-flex flex-wrap items-center gap-2">
          <span className="font-medium text-fg">{`v${version.version}`}</span>
          {version.version === agent.published_version ? (
            <StatusBadge variant="success" label={t("agent.version.current")} />
          ) : null}
        </span>
      ),
    },
    {
      key: "published_at",
      header: t("agent.version.publishedAt"),
      className: "text-fg-muted",
      render: (version) => formatDate(version.published_at),
    },
    {
      key: "published_by",
      header: t("agent.version.publishedBy"),
      className: "text-fg-muted",
      render: (version) => version.published_by || "-",
    },
    {
      key: "note",
      header: t("agent.version.note"),
      className: "max-w-sm break-words text-fg-muted",
      render: (version) => version.note || "-",
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (version) => (
        <RowActionMenu
          actions={
            readOnly
              ? []
              : [
                  {
                    id: "restore",
                    label: t("agent.version.restore"),
                    icon: RotateCcw,
                    visible: version.version !== agent.published_version || agent.unpublished_changes,
                    disabled: restore.isPending,
                    onSelect: () => void confirmRestore(version),
                  },
                ]
          }
          ariaLabel={t("common.entityActions", { name: `v${version.version}` })}
          testId={`agent-version-actions-${version.version}`}
        />
      ),
    },
  ];

  return (
    <Section title={t("agent.version.title")} description={t("agent.version.description")}>
      {rows.length === 0 ? (
        <EmptyState title={t("agent.version.empty")} />
      ) : (
        <DataTable
          rows={rows}
          columns={columns}
          getRowKey={(version) => String(version.version)}
          rowProps={() => ({ className: "align-top" })}
          tableClassName="w-full min-w-[40rem]"
          ariaLabel={t("agent.version.title")}
        />
      )}
    </Section>
  );
}

function AgentTable({
  agents,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  agents: AgentProfile[];
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
      key: "model",
      header: t("agent.model"),
      // 空は「既定のテキストモデル」（システム設定 > モデル）。
      render: (agent) => (
        <span className="break-all text-xs text-fg">{agent.model_id || t("agent.modelDefault")}</span>
      ),
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
      key: "version",
      header: t("agent.version.column"),
      render: (agent) => <AgentVersionBadges agent={agent} />,
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
/**
 * Runtime（組み込み Runtime の状態。#754）。業務 Agent は Control Plane の中の OpenAI Agents SDK で実行し、
 * モデルは「システム設定 > モデル」の OCI Enterprise AI を使う。外部 Runtime の登録・Docker の操作は無い。
 */
export function RuntimesPage() {
  const { hasPermission } = useAuth();
  const status = useQuery({ queryKey: ["runtime-status"], queryFn: agentApi.getRuntimeStatus });
  const data = status.data;
  return (
    <>
      <PageHeader
        wide
        title={t("nav.runtimes")}
        subtitle={t("page.runtimes.subtitle")}
        actions={[
          {
            // 外部の状態（モデルの設定）の再確認。ページツールなので utility（buttons.md §5）。文言は製品のまま。
            id: "refresh",
            kind: "utility",
            label: t("runtime.refresh"),
            icon: RefreshCw,
            loading: status.isFetching && !status.isLoading,
            onClick: () => void status.refetch(),
          },
        ]}
      />
      <PageBody wide>
        <QueryState query={status} loadingLabel={t("loading.runtimes")} skeleton={<RuntimeCardsSkeleton />}>
          {data ? (
            <Card className="min-w-0" data-testid="builtin-runtime-card">
              <CardHeader className="flex-row items-start justify-between gap-4">
                <div className="min-w-0">
                  <CardTitle className="flex items-center gap-2">
                    <Server size={20} aria-hidden />
                    {t("runtime.builtin.title")}
                  </CardTitle>
                  <CardDescription>{t("runtime.builtin.description")}</CardDescription>
                </div>
                <StatusBadge
                  variant={data.ready ? "success" : "warning"}
                  label={data.ready ? t("runtime.builtin.ready") : t("runtime.builtin.notReady")}
                />
              </CardHeader>
              <CardContent className="space-y-4">
                <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
                  <dt className="text-fg-muted">{t("runtime.builtin.sdk")}</dt>
                  <dd className="break-words">
                    <code>{data.sdk}</code> {data.sdk_version}
                  </dd>
                  <dt className="text-fg-muted">{t("runtime.builtin.provider")}</dt>
                  <dd>{data.model_provider}</dd>
                  <dt className="text-fg-muted">{t("runtime.builtin.defaultModel")}</dt>
                  <dd className="break-words">{data.model_id || t("runtime.builtin.modelUnset")}</dd>
                  <dt className="text-fg-muted">{t("runtime.builtin.models")}</dt>
                  <dd className="break-words">
                    {data.models.length
                      ? data.models.map((model) => model.display_name).join("、")
                      : t("runtime.builtin.modelsEmpty")}
                  </dd>
                </dl>
                {!data.ready ? (
                  // 実行できない理由と、直す場所を出す（モデルの設定はシステム設定の権限がある利用者だけが開ける）。
                  <Banner severity="warning" title={t("runtime.builtin.notReadyTitle")}>
                    <div className="space-y-3">
                      <p>{data.message ?? t("runtime.builtin.notReadyDefault")}</p>
                      {hasPermission(MENU_PERMISSIONS.settingsModel) ? (
                        <Link
                          to={APP_ROUTES.settingsModel}
                          className={buttonVariants({ variant: "secondary", size: "sm" })}
                          data-testid="builtin-runtime-open-model-settings"
                        >
                          <SlidersHorizontal size={16} aria-hidden="true" />
                          <span>{t("runtime.builtin.openModelSettings")}</span>
                        </Link>
                      ) : (
                        <p className="text-fg-muted">{t("runtime.builtin.askAdmin")}</p>
                      )}
                    </div>
                  </Banner>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </QueryState>
      </PageBody>
    </>
  );
}

/** 組み込み Runtime のカードの形。読み込み後のカードの高さを予約する。 */
function RuntimeCardsSkeleton() {
  return (
    <div aria-hidden="true">
      <Skeleton className="h-56" />
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
  const runIds = useMemo(() => runs.data?.runs.map((run) => run.id), [runs.data?.runs]);
  const restoredSelection = useRestoredSelectionCheck(selectedRunId, runIds);
  const selectedRun = runItems.find((run) => run.id === selectedRunId) ?? runItems[0];
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
  const websocketState = useRunEventWebSocket(
    selectedRun,
    streamMode === "websocket",
    refreshRuntimeEvents
  );

  const sseState = useRunEventSource(selectedRun, streamMode === "sse", refreshRuntimeEvents);

  const agentNames = useMemo(
    () => new Map((agents.data?.agents ?? []).map((agent) => [agent.id, agent.name])),
    [agents.data?.agents]
  );
  const agentNameOf = (agentId: string) => agentNames.get(agentId) ?? agentId;

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
    const { canCancel, canResume } = runCapabilities(run);
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
        visible: capabilities.operateRuns,
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
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: runs.isFetching && !runs.isLoading,
            onClick: () => void runs.refetch(),
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
                    <SelectField
                      id="run-agent"
                      label={t("run.form.agent")}
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
                      textareaClassName="min-h-24"
                    />
                    {/* 組み込み Runtime が実行できないとき（モデル未設定など）は、理由と直す場所を知らせる。 */}
                    {runtimeStatus.data && !runtimeStatus.data.ready ? (
                      <Banner severity="warning" title={t("runtime.builtin.notReadyTitle")}>
                        <p>{runtimeStatus.data.message ?? t("runtime.builtin.notReadyDefault")}</p>
                      </Banner>
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
        <StatusBadge {...approvalStatusView(approval.status)} />
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
                      <StatusBadge {...approvalStatusView(selected.approval.status)} />
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="grid gap-2 text-xs text-fg-muted sm:grid-cols-2">
                        <span className="break-all">{`${t("audit.runId")}: ${selected.run.id}`}</span>
                        <span>{`${t("audit.runStatus")}: ${runStatusView(selected.run.status).label}`}</span>
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
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: audit.isFetching && !audit.isLoading,
            onClick: () => void audit.refetch(),
          },
        ]}
      />
      <PageBody wide>
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("audit.filters")}</CardTitle>
            <CardDescription>{t("page.audit.subtitle")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <TextField
                id="audit-run-id"
                label={t("audit.runId")}
                value={runId}
                onValueChange={(value) => setFilter("runId", value)}
                // 条件フォームの Enter は「条件を適用」と同じ（IME の変換を確定する Enter では適用しない。#535）。
                onKeyDown={(event) => {
                  if (isSubmitEnter(event)) applyFilters();
                }}
              />
              <SelectField
                id="audit-tool-name"
                label={t("audit.toolName")}
                value={toolName}
                options={[
                  { value: "", label: t("common.all") },
                  ...(tools.data?.tools ?? []).map((tool) => ({ value: tool.name, label: tool.name })),
                ]}
                onValueChange={(value) => setFilter("toolName", value)}
              />
              <SelectField
                id="audit-step-status"
                label={t("audit.stepStatus")}
                value={stepStatus}
                options={[
                  { value: "", label: t("common.all") },
                  ...stepStatusOptions(),
                ]}
                onValueChange={(value) => setFilter("stepStatus", value)}
              />
              <SelectField
                id="audit-approval-status"
                label={t("audit.approvalStatus")}
                value={approvalStatus}
                options={[
                  { value: "", label: t("common.all") },
                  ...approvalStatusOptions(),
                ]}
                onValueChange={(value) => setFilter("approvalStatus", value)}
              />
              <TextField
                id="audit-error-code"
                label={t("audit.errorCode")}
                value={errorCode}
                onValueChange={(value) => setFilter("errorCode", value)}
                // 条件フォームの Enter は「条件を適用」と同じ（IME の変換を確定する Enter では適用しない。#535）。
                onKeyDown={(event) => {
                  if (isSubmitEnter(event)) applyFilters();
                }}
              />
              <SelectField<AuditWarningsFilter>
                id="audit-warning-filter"
                label={t("audit.guardrailWarnings")}
                value={warnings}
                options={[
                  { value: "any", label: t("common.all") },
                  { value: "true", label: t("audit.hasWarnings") },
                  { value: "false", label: t("audit.noWarnings") },
                ]}
                onValueChange={(value) => setFilter("warnings", value)}
              />
              <TextField
                id="audit-limit"
                label={t("audit.limit")}
                type="number"
                min="1"
                max="1000"
                value={limit}
                onValueChange={(value) => setFilter("limit", value)}
              />
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
        <StatusBadge {...stepStatusView(record.status)} />
      ),
    },
    {
      key: "approval_status",
      header: t("audit.approvalStatus"),
      render: (record) =>
        record.approval_status ? (
          <StatusBadge {...approvalStatusView(record.approval_status)} />
        ) : (
          <span className="text-xs text-fg-muted">-</span>
        ),
    },
    {
      key: "policy_decision",
      header: t("run.auditPolicy"),
      render: (record) =>
        record.policy_decision ? (
          <StatusBadge {...policyDecisionView(record.policy_decision)} icon={false} />
        ) : (
          <span className="text-xs text-fg-muted">-</span>
        ),
    },
    {
      key: "permission_level",
      header: t("common.permission"),
      render: (record) => (
        record.permission_level ? (
          <StatusBadge {...permissionView(record.permission_level)} icon={false} />
        ) : (
          <span className="text-xs text-fg-muted">-</span>
        )
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
        <StatusBadge {...permissionView(tool.permission_level)} icon={false} />
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

function SettingsSaveBar({
  section,
  onSave,
  saving,
  error,
}: {
  section: string;
  onSave: () => void;
  saving: boolean;
  error: Error | null;
}) {
  return (
    <FormActionBar
      ariaLabel={t("settings.saveActions", { section })}
      primaryActions={[{ id: "save", label: t("common.save"), icon: Save, loading: saving, onClick: onSave }]}
      status={error ? <FormStatus tone="danger" message={t("settings.saveFailed", { reason: error.message })} /> : null}
    />
  );
}

/** 接続を使えない理由と直し方（使えるときは出さない。常設の success の面にしない）。 */
function McpConnectionNotice({ connection }: { connection: McpConnectionSettings }) {
  if (connection.configured) {
    return null;
  }
  const missing = [
    connection.base_url ? null : t("settings.mcpConnections.missingUrl"),
    connection.auth_mode === "service_token" && !connection.service_token_configured
      ? t("settings.mcpConnections.missingSecret")
      : null,
    connection.auth_mode === "api_key" && !connection.api_key_configured
      ? t("settings.mcpConnections.missingApiKey")
      : null,
    connection.auth_mode === "oauth_client_credentials" && !connection.oauth_configured
      ? t("settings.mcpConnections.missingOauth")
      : null,
  ].filter((item): item is string => item !== null);
  return (
    <Banner severity="warning" title={t("settings.mcpConnections.notReady")}>
      {/* 環境変数名は長く区切りがないため、狭い幅では任意の位置で折り返す。 */}
      <ul className="list-disc space-y-1 pl-5 break-words [overflow-wrap:anywhere]">
        {missing.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Banner>
  );
}

/** サービストークンの準備の状態（.env で管理。値は表示しない）。 */
function McpServiceTokenStatus({ connection }: { connection: McpConnectionSettings }) {
  const rows = [
    {
      id: "secret",
      label: t("settings.productMcp.secret"),
      hint: t("settings.productMcp.secretHint"),
      configured: connection.service_token_configured,
      missingVariant: "warning" as const,
    },
    {
      id: "service-user",
      label: t("settings.productMcp.serviceUser"),
      hint: t("settings.productMcp.serviceUserHint"),
      configured: connection.service_user_configured,
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

/**
 * 接続のツール（MCP の tools/list）。接続の確認を兼ね、ログイン中の利用者として取得する。
 * 結果は「ツールを取得」の直下に、カードの全幅で出す（messaging.md §10）。
 */
function McpConnectionToolsPanel({ connection }: { connection: McpConnectionSettings }) {
  const [requested, setRequested] = useState(false);
  const tools = useQuery({
    queryKey: ["mcp-connection-tools", connection.server_id],
    queryFn: () => agentApi.listMcpConnectionTools(connection.server_id),
    enabled: requested && connection.configured,
    retry: false,
  });

  return (
    <Section title={t("settings.mcpConnections.tools")} description={t("settings.mcpConnections.toolsDescription")}>
      <Card className="min-w-0">
        <CardContent className="space-y-4 pt-5">
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              onClick={() => (requested ? void tools.refetch() : setRequested(true))}
              disabled={!connection.configured}
              aria-describedby={!connection.configured ? "mcp-tools-configure-hint" : undefined}
              loading={tools.isFetching}
              icon={RefreshCw}
            >
              {t("settings.mcpConnections.fetchTools")}
            </Button>
            {!connection.configured ? (
              // 未設定は通常の状態。警告にせず「ツールを取得」が使えない理由として補助テキストで伝える。
              <p id="mcp-tools-configure-hint" className="text-sm leading-6 text-fg-muted">
                {t("settings.mcpConnections.configureFirst")}
              </p>
            ) : null}
          </div>
          {!requested || !connection.configured ? null : tools.error ? (
            <Banner severity="danger" title={t("settings.mcpConnections.fetchFailed")}>
              {tools.error.message}
            </Banner>
          ) : tools.isLoading ? (
            <TimedLoadingState label={t("loading.mcpTools")} testId="mcp-tools-loading">
              <TableSkeleton columns={4} />
            </TimedLoadingState>
          ) : (tools.data?.tools ?? []).length ? (
            <McpToolsList tools={tools.data?.tools ?? []} />
          ) : (
            <EmptyState title={t("settings.mcpDiscovery.empty")} />
          )}
        </CardContent>
      </Card>
    </Section>
  );
}

function McpToolApprovalBadge({ tool }: { tool: ExternalMcpToolInfo }) {
  // readOnlyHint の無いツールは、ツール権限で許可しない限り実行の前に承認を求める。
  return tool.read_only ? (
    <StatusBadge variant="neutral" label={t("settings.mcpConnections.readOnly")} icon={false} />
  ) : (
    <StatusBadge variant="warning" label={t("settings.mcpConnections.needsApproval")} icon={false} />
  );
}

function McpToolsList({ tools }: { tools: ExternalMcpToolInfo[] }) {
  // 取り直した一覧は別の結果なので、そのときだけ 1 ページ目へ戻す。
  const { page, setPage, totalPages, pageItems, range } = usePagination(tools, DEFAULT_PAGE_SIZE, { resetKey: tools });
  const labels = agentPaginationLabels();
  const mcpToolColumns: DataTableColumn<ExternalMcpToolInfo>[] = [
    {
      key: "name",
      header: t("settings.mcpDiscovery.tool"),
      rowHeader: true,
      render: (tool) => (
        <div className="min-w-0 space-y-0.5">
          <p className="break-words font-mono text-xs font-medium text-fg">{tool.name}</p>
          {tool.function_name ? (
            <p className="break-words font-mono text-xs text-fg-muted">{tool.function_name}</p>
          ) : null}
        </div>
      ),
    },
    {
      key: "description",
      header: t("settings.mcpDiscovery.descriptionColumn"),
      className: "max-w-sm text-fg-muted",
      render: (tool) => tool.description || "-",
    },
    {
      key: "approval",
      header: t("settings.mcpConnections.approval"),
      render: (tool) => <McpToolApprovalBadge tool={tool} />,
    },
    {
      key: "input_schema",
      header: t("settings.mcpDiscovery.inputSchema"),
      className: "text-fg-muted",
      render: (tool) => schemaSummary(tool.input_schema),
    },
  ];

  return (
    <div className="grid min-w-0 gap-2">
      {/* md 以上は表、md 未満はカード。どちらも同じページ（10 件）を出し、Pagination は 1 つにする。 */}
      <DataTable
        className="hidden md:block"
        rows={pageItems}
        columns={mcpToolColumns}
        getRowKey={(tool) => tool.name}
        rowProps={() => ({ className: `align-top ${INFORMATION_TABLE_ROW_CLASS}` })}
        tableClassName="w-full min-w-[640px]"
        ariaLabel={t("settings.mcpConnections.tools")}
        scrollAriaLabel={listScrollLabel(t("settings.mcpConnections.tools"))}
        stickyHeader
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
      />
      <div
        className={`grid gap-3 md:hidden ${INFORMATION_LIST_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
        role="region"
        aria-label={listScrollLabel(t("settings.mcpConnections.tools"))}
        tabIndex={0}
      >
        {pageItems.map((tool) => (
          <div key={tool.name} className="rounded-md border border-border p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <p className="min-w-0 break-words font-mono text-xs font-medium text-fg">{tool.name}</p>
              <McpToolApprovalBadge tool={tool} />
            </div>
            <p className="mt-1 text-sm leading-6 text-fg-muted">{tool.description || "-"}</p>
            <dl className="mt-3 grid grid-cols-1 gap-2 text-xs text-fg-muted">
              <McpToolMeta label={t("settings.mcpConnections.functionName")} value={tool.function_name ?? "-"} />
              <McpToolMeta label={t("settings.mcpDiscovery.inputSchema")} value={schemaSummary(tool.input_schema)} />
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
    return t("settings.mcpDiscovery.schemaSummary", { type, count });
  }
  return type;
}

function mcpAuthLabel(mode?: McpAuthMode | null): string {
  if (mode === "service_token") {
    return t("settings.mcpConnections.authServiceToken");
  }
  if (mode === "oauth_client_credentials") {
    return t("settings.mcpServers.authOauth");
  }
  if (mode === "api_key") {
    return t("settings.mcpServers.authApiKey");
  }
  return t("settings.mcpServers.authNone");
}

function mcpSourceLabel(source: string): string {
  if (source === "builtin") return t("settings.mcpConnections.sourceBuiltin");
  if (source === "env") return t("settings.mcpConnections.sourceEnv");
  if (source.startsWith("plugin:")) return t("settings.mcpConnections.sourcePlugin");
  return t("settings.mcpConnections.sourceRuntime");
}

interface McpConnectionFormState {
  serverId: string;
  label: string;
  baseUrl: string;
  authMode: McpAuthMode;
  apiKey: string;
  serviceAudience: string;
  timeoutSeconds: string;
  sessionId: string;
  oauthTokenUrl: string;
  oauthClientId: string;
  oauthClientSecret: string;
  oauthScope: string;
}

const EMPTY_MCP_FORM: McpConnectionFormState = {
  serverId: "",
  label: "",
  baseUrl: "",
  authMode: "none",
  apiKey: "",
  serviceAudience: "",
  timeoutSeconds: "10",
  sessionId: "",
  oauthTokenUrl: "",
  oauthClientId: "",
  oauthClientSecret: "",
  oauthScope: "",
};

function mcpFormOf(connection: McpConnectionSettings | undefined): McpConnectionFormState {
  if (!connection) return EMPTY_MCP_FORM;
  return {
    ...EMPTY_MCP_FORM,
    serverId: connection.server_id,
    label: connection.label ?? "",
    baseUrl: connection.base_url ?? "",
    authMode: connection.auth_mode,
    serviceAudience: connection.service_audience ?? "",
    timeoutSeconds: String(connection.timeout_seconds),
  };
}

export function McpConnectionsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const editor = useEditorRoute();
  // 接続の追加・変更・削除は Agent 管理の権限（admin）だけ（#215）。
  const capabilities = useCapabilities();
  const canManage = capabilities.admin;
  const connections = useQuery({
    queryKey: ["mcp-connections"],
    queryFn: agentApi.listMcpConnections,
  });

  function invalidate() {
    return Promise.all([
      queryClient.invalidateQueries({ queryKey: ["mcp-connections"] }),
      queryClient.invalidateQueries({ queryKey: ["mcp-connection-tools"] }),
    ]);
  }

  const deleteMutation = useMutation({
    mutationFn: (serverId: string) => agentApi.deleteMcpConnection(serverId),
    onSuccess: () => {
      toast.success(t("settings.mcpConnections.deleted"));
      void invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  async function remove(connection: McpConnectionSettings) {
    const ok = await confirm({
      title: t("settings.mcpConnections.confirmDeleteTitle"),
      description: t("settings.mcpServers.confirmDeleteMessage", { id: connection.server_id }),
      confirmLabel: t("settings.mcpServers.delete"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
    });
    if (!ok) return;
    deleteMutation.mutate(connection.server_id, {
      // エディタから削除したら、消えた対象へ戻れないよう履歴を置き換えて一覧へ戻る。
      onSuccess: () => {
        if (editor.target.kind === "edit") editor.backToList({ replace: true });
      },
    });
  }

  // 一覧の行と詳細（エディタの概要）で同じ定義を使う（UX 契約 buttons.md §5.1）。
  // RAG / NL2SQL・宣言・連携機能の接続は削除できない（項目を出さない）。
  const connectionActions = (connection: McpConnectionSettings): EntityAction[] =>
    canManage && connection.removable
      ? [
          {
            id: "delete",
            label: t("settings.mcpServers.delete"),
            icon: Trash2,
            tone: "danger",
            disabled: deleteMutation.isPending,
            onSelect: () => remove(connection),
          },
        ]
      : [];

  const list = connections.data?.connections ?? [];
  // 追加できない利用者が `?id=new` を開いたら一覧を出す。
  const target = !canManage && editor.target.kind === "new" ? ({ kind: "list" } as const) : editor.target;
  const listTitle = t("nav.settingsMcpConnections");

  if (target.kind === "list") {
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
                    label: t("settings.mcpConnections.add"),
                    icon: Plus,
                    onClick: editor.openNew,
                  },
                ]
              : []
          }
        />
        <PageBody wide className="space-y-6">
          <Section title={t("settings.mcpConnections.title")} description={t("settings.mcpConnections.description")}>
            <QueryState query={connections} loadingLabel={t("loading.mcpServers")} skeleton={<TableSkeleton columns={6} />}>
              <McpConnectionTable
                connections={list}
                onOpen={(connection) => editor.openItem(connection.server_id)}
                hrefFor={(connection) => editor.itemHref(connection.server_id)}
                actionsFor={connectionActions}
              />
            </QueryState>
          </Section>
        </PageBody>
      </>
    );
  }

  const connection =
    target.kind === "edit" ? list.find((candidate) => candidate.server_id === target.id) : undefined;
  if (target.kind === "edit" && !connection) {
    return (
      <>
        <PageHeader wide title={listTitle} />
        <PageBody wide>
          <QueryState query={connections} loadingLabel={t("loading.mcpServers")} skeleton={<FormSkeleton fields={4} />}>
            <MissingEditorTarget id={target.id} onBack={() => editor.backToList()} />
          </QueryState>
        </PageBody>
      </>
    );
  }

  return (
    <McpConnectionEditor
      key={connection?.server_id ?? "new"}
      connection={connection}
      actions={connection ? connectionActions(connection) : []}
      readOnly={!canManage}
      onBack={() => editor.backToList()}
      onSaved={async (serverId) => {
        await invalidate();
        editor.openItem(serverId, { replace: true });
      }}
    />
  );
}

/** MCP 接続の全画面エディタ（A 型。`?id=new` / `?id=<接続 ID>`）。 */
function McpConnectionEditor({
  connection,
  actions,
  readOnly,
  onBack,
  onSaved,
}: {
  connection?: McpConnectionSettings;
  actions: EntityAction[];
  /** 変更の権限がない利用者は閲覧だけ（保存を出さず、入力を無効にする）。 */
  readOnly: boolean;
  onBack: () => void;
  onSaved: (serverId: string) => Promise<void>;
}) {
  const [form, setForm] = useState<McpConnectionFormState>(() => mcpFormOf(connection));
  const [formBaseline, setFormBaseline] = useState<McpConnectionFormState>(() => mcpFormOf(connection));
  const [serverIdError, setServerIdError] = useState<string | null>(null);
  const [timeoutError, setTimeoutError] = useState<string | null>(null);
  const editingId = connection?.server_id ?? null;
  // RAG / NL2SQL は Run の利用者のサービストークンで呼ぶ接続（認証方式は変えられない）。
  const builtin = connection?.source === "builtin";

  // 送る内容は mutate の引数で渡す（クリック直前の入力を closure の古い state で送らない）。
  const saveMutation = useMutation({
    mutationFn: (current: McpConnectionFormState) => {
      const payload: McpConnectionWritePayload = {
        label: current.label || null,
        base_url: current.baseUrl.trim(),
        timeout_seconds: Number(current.timeoutSeconds),
        session_id: current.sessionId || undefined,
      };
      if (!builtin) {
        payload.auth_mode = current.authMode;
      }
      if (current.authMode === "api_key" && current.apiKey) {
        payload.api_key = current.apiKey;
      }
      if (current.authMode === "service_token" && !builtin) {
        payload.service_audience = current.serviceAudience.trim();
      }
      if (current.authMode === "oauth_client_credentials") {
        payload.oauth_token_url = current.oauthTokenUrl || undefined;
        payload.oauth_client_id = current.oauthClientId || undefined;
        payload.oauth_client_secret = current.oauthClientSecret || undefined;
        payload.oauth_scope = current.oauthScope || undefined;
      }
      if (editingId) {
        return agentApi.updateMcpConnection(editingId, payload);
      }
      return agentApi.createMcpConnection({ server_id: current.serverId.trim(), ...payload });
    },
    onSuccess: async (saved, current) => {
      toast.success(editingId ? t("settings.mcpConnections.updated") : t("settings.mcpConnections.created"));
      // 秘密の欄は保存後に空へ戻す（値は保持も表示もしない）。
      const next = { ...current, apiKey: "", sessionId: "", oauthClientSecret: "" };
      setForm(next);
      setFormBaseline(next);
      await onSaved(saved.server_id ?? current.serverId.trim());
    },
  });

  // 開いた時点の内容から変わっていれば未保存（秘密の欄も含む。値は保存しない）。#87
  const formDirty = !sameDraft(form, formBaseline);
  const { confirmClose } = useEditorLeaveGuard(formDirty, saveMutation.isPending);

  async function back() {
    if (await confirmClose()) onBack();
  }

  function save() {
    const nextServerIdError =
      !editingId && !form.serverId.trim() ? t("settings.mcpServers.idRequired") : null;
    // 空のタイムアウトを 0 として保存しない。規則は backend（McpConnectionCreate / Patch）と同じ（#540）。
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

  const authOptions: SelectFieldOption<McpAuthMode>[] = [
    { value: "none", label: t("settings.mcpServers.authNone") },
    { value: "api_key", label: t("settings.mcpServers.authApiKey") },
    { value: "oauth_client_credentials", label: t("settings.mcpServers.authOauth") },
    { value: "service_token", label: t("settings.mcpConnections.authServiceToken") },
  ];
  const listTitle = t("nav.settingsMcpConnections");
  const title = connection ? connection.label || connection.server_id : t("settings.mcpConnections.addTitle");

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={connection ? connection.server_id : t("page.settings.mcp.subtitle")}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: listTitle }), onClick: () => void back(), testId: "editor-back" }}
        actions={[
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
        {/* 保存の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={saveMutation.error ? (saveMutation.error as Error).message : null}
          attemptKey={saveMutation.submittedAt}
          testId="mcp-server-save-error"
        />
        {connection ? (
          <Section
            title={t("editor.overview")}
            actions={
              <ObjectActionBar
                actions={actions}
                ariaLabel={t("common.entityActions", { name: connection.server_id })}
                moreLabel={t("common.moreActions")}
                testId="mcp-server-object-actions"
              />
            }
          >
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge
                  variant={connection.configured ? "success" : "warning"}
                  label={connection.configured ? t("common.configured") : t("common.notConfigured")}
                />
                <StatusBadge variant="neutral" label={mcpSourceLabel(connection.source)} icon={false} />
                <span className="text-xs text-fg-muted">
                  {`${t("settings.mcpServers.auth")}: ${mcpAuthLabel(connection.auth_mode)}`}
                </span>
              </div>
              <McpConnectionNotice connection={connection} />
            </div>
          </Section>
        ) : null}
        <fieldset disabled={readOnly} className="min-w-0 space-y-6">
          <Section title={t("mcpServers.connection")}>
            <Card className="min-w-0">
              <CardContent className="grid gap-4 pt-5 md:grid-cols-2">
                {/* 接続 ID は作成時だけ入力でき、必須。モデルに渡すツール名（<接続>__<ツール>）の先頭になる。 */}
                <TextField
                  id="mcp-server-id"
                  label={t("settings.mcpConnections.serverId")}
                  className="min-w-0"
                  required={!editingId}
                  error={serverIdError ?? undefined}
                  helper={t("settings.mcpConnections.serverIdHint")}
                  value={form.serverId}
                  disabled={Boolean(editingId)}
                  onValueChange={(value) => {
                    setForm({ ...form, serverId: value });
                    setServerIdError(null);
                  }}
                />
                <TextField
                  id="mcp-server-label"
                  label={t("settings.mcpServers.label")}
                  className="min-w-0"
                  value={form.label}
                  onValueChange={(value) => setForm({ ...form, label: value })}
                />
                <div className="min-w-0 md:col-span-2">
                  <TextField
                    id="mcp-server-base-url"
                    label={t("settings.mcpConnections.url")}
                    helper={builtin ? t("settings.mcpConnections.urlHintBuiltin") : t("settings.mcpConnections.urlHint")}
                    value={form.baseUrl}
                    onValueChange={(value) => setForm({ ...form, baseUrl: value })}
                  />
                </div>
                <TextField
                  id="mcp-server-timeout"
                  label={t("settings.timeout")}
                  className="min-w-0"
                  required
                  error={timeoutError ?? undefined}
                  type="number"
                  min="1"
                  value={form.timeoutSeconds}
                  onValueChange={(value) => {
                    setForm({ ...form, timeoutSeconds: value });
                    setTimeoutError(null);
                  }}
                />
                <TextField
                  id="mcp-server-session"
                  label={t("settings.mcpSessionId")}
                  className="min-w-0"
                  helper={t("settings.mcpSessionHint")}
                  value={form.sessionId}
                  autoComplete="off"
                  onValueChange={(value) => setForm({ ...form, sessionId: value })}
                />
              </CardContent>
            </Card>
          </Section>
          <Section title={t("settings.mcpConnections.authSection")}>
            <Card className="min-w-0">
              <CardContent className="grid gap-4 pt-5 md:grid-cols-2">
                <SelectField<McpAuthMode>
                  id="mcp-server-auth-mode"
                  label={t("settings.mcpServers.auth")}
                  value={form.authMode}
                  options={authOptions}
                  disabled={builtin}
                  helper={builtin ? t("settings.mcpConnections.authBuiltinHint") : undefined}
                  onValueChange={(value) => setForm({ ...form, authMode: value })}
                />
                {form.authMode === "service_token" ? (
                  <TextField
                    id="mcp-server-audience"
                    label={t("settings.mcpConnections.audience")}
                    className="min-w-0"
                    helper={t("settings.mcpConnections.audienceHint")}
                    value={form.serviceAudience}
                    disabled={builtin}
                    onValueChange={(value) => setForm({ ...form, serviceAudience: value })}
                  />
                ) : null}
                {form.authMode === "api_key" ? (
                  // API が保存済みの有無だけを返すため、値は表示しない password の欄にする（#631）。
                  <TextField
                    id="mcp-server-api-key"
                    label={t("settings.mcpServers.authApiKey")}
                    className="min-w-0"
                    type="password"
                    helper={
                      connection?.api_key_configured
                        ? t("settings.mcpServers.secretManaged")
                        : t("settings.mcpConnections.apiKeyHint")
                    }
                    value={form.apiKey}
                    autoComplete="off"
                    onValueChange={(value) => setForm({ ...form, apiKey: value })}
                  />
                ) : null}
                {form.authMode === "oauth_client_credentials" ? (
                  <>
                    <TextField
                      id="mcp-server-oauth-token"
                      label={t("settings.mcpServers.oauthTokenUrl")}
                      className="min-w-0"
                      value={form.oauthTokenUrl}
                      onValueChange={(value) => setForm({ ...form, oauthTokenUrl: value })}
                    />
                    <TextField
                      id="mcp-server-oauth-scope"
                      label={t("settings.mcpServers.oauthScope")}
                      className="min-w-0"
                      value={form.oauthScope}
                      onValueChange={(value) => setForm({ ...form, oauthScope: value })}
                    />
                    <TextField
                      id="mcp-server-oauth-client"
                      label={t("settings.mcpServers.oauthClientId")}
                      className="min-w-0"
                      value={form.oauthClientId}
                      autoComplete="off"
                      onValueChange={(value) => setForm({ ...form, oauthClientId: value })}
                    />
                    <TextField
                      id="mcp-server-oauth-secret"
                      label={t("settings.mcpServers.oauthClientSecret")}
                      className="min-w-0"
                      type="password"
                      helper={connection?.oauth_configured ? t("settings.mcpServers.secretManaged") : undefined}
                      value={form.oauthClientSecret}
                      autoComplete="off"
                      onValueChange={(value) => setForm({ ...form, oauthClientSecret: value })}
                    />
                  </>
                ) : null}
                {form.authMode === "service_token" && connection ? (
                  <div className="min-w-0 md:col-span-2">
                    <McpServiceTokenStatus connection={connection} />
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </Section>
        </fieldset>
        {/* 保存した接続だけツールを取得できる（入力中の値ではなく保存済みの設定で呼ぶ）。 */}
        {connection ? <McpConnectionToolsPanel connection={connection} /> : null}
      </PageBody>
    </>
  );
}

function McpConnectionTable({
  connections,
  onOpen,
  hrefFor,
  actionsFor,
}: {
  connections: McpConnectionSettings[];
  onOpen: (connection: McpConnectionSettings) => void;
  /** 名前のリンクの URL（新しいタブで開ける。#583）。 */
  hrefFor: (connection: McpConnectionSettings) => string;
  actionsFor: (connection: McpConnectionSettings) => EntityAction[];
}) {
  const columns: DataTableColumn<McpConnectionSettings>[] = [
    {
      key: "server_id",
      header: t("settings.mcpConnections.serverId"),
      rowHeader: true,
      render: (connection) => (
        <RowTitleButton
          title={connection.label || connection.server_id}
          subtitle={connection.server_id}
          href={hrefFor(connection)}
          onClick={() => onOpen(connection)}
        />
      ),
    },
    {
      key: "base_url",
      header: t("settings.mcpConnections.url"),
      className: "max-w-xs break-all text-fg-muted",
      render: (connection) => connection.base_url || "-",
    },
    {
      key: "auth_mode",
      header: t("settings.mcpServers.auth"),
      className: "text-fg-muted",
      render: (connection) => mcpAuthLabel(connection.auth_mode),
    },
    {
      key: "source",
      header: t("settings.mcpConnections.source"),
      render: (connection) => (
        <StatusBadge variant="neutral" label={mcpSourceLabel(connection.source)} icon={false} />
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      render: (connection) => (
        <StatusBadge
          variant={connection.configured ? "success" : "warning"}
          label={connection.configured ? t("common.configured") : t("common.notConfigured")}
        />
      ),
    },
    {
      key: "actions",
      header: t("settings.mcpServers.actions"),
      align: "right",
      render: (connection) => (
        <RowActionMenu
          actions={actionsFor(connection)}
          ariaLabel={t("common.entityActions", { name: connection.server_id })}
          // RAG / NL2SQL などは削除できない。使える項目の無いメニューは開かせない。
          disabled={visibleEntityActions(actionsFor(connection)).every((action) => action.disabled)}
          testId={`mcp-server-row-actions-${connection.server_id}`}
        />
      ),
    },
  ];

  return (
    <PagedDataTable
      pageKey="mcpServers"
      rows={connections}
      columns={columns}
      getRowKey={(connection) => connection.server_id}
      onRowClick={onOpen}
      rowProps={() => ({ className: "align-top" })}
      tableClassName="w-full min-w-[48rem]"
      ariaLabel={t("settings.mcpConnections.title")}
      empty={<EmptyState title={t("settings.mcpConnections.empty")} />}
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
    // ヘッダーの操作で固定の面が無いため、失敗は danger の Toast（messaging.md §1「失敗を黙って捨てない」）。
    onError: (error) => toast.error(t("skills.reloadFailed"), { description: error.message }),
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
  const headerActions: PageHeaderAction[] = [];
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
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("skills.title") }), onClick: () => void back(), testId: "editor-back" }}
        actions={headerActions}
        moreActionsLabel={t("common.moreActions")}
      />
      <PageBody wide className="space-y-6">
        {/* 保存の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={saveMutation.error ? (saveMutation.error as Error).message : null}
          attemptKey={saveMutation.submittedAt}
          testId="skill-save-error"
        />
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
            <Section title={t("skills.basic")}>
              <Card className="min-w-0">
                <CardContent className="space-y-4 pt-5">
                  {/* ID は作成時だけ入力でき、必須（backend の create_agent_skill と送信ガード）。 */}
                  <TextField
                    id="skill-id"
                    label={t("skills.id")}
                    required={!editingId}
                    error={fieldErrors.id}
                    value={form.id}
                    disabled={Boolean(editingId)}
                    onValueChange={(value) => {
                      setForm({ ...form, id: value });
                      setFieldErrors((current) => ({ ...current, id: undefined }));
                    }}
                  />
                  <TextField
                    id="skill-name"
                    label={t("skills.name")}
                    required
                    error={fieldErrors.name}
                    value={form.name}
                    onValueChange={(value) => {
                      setForm({ ...form, name: value });
                      setFieldErrors((current) => ({ ...current, name: undefined }));
                    }}
                  />
                  <TextField
                    id="skill-description"
                    label={t("agent.description")}
                    value={form.description}
                    onValueChange={(value) => setForm({ ...form, description: value })}
                  />
                  <TextareaField
                    id="skill-instructions"
                    label={t("skills.instructions")}
                    value={form.instructions}
                    onValueChange={(value) => setForm({ ...form, instructions: value })}
                  />
                  <TextField
                    id="skill-tags"
                    label={t("skills.tags")}
                    helper={t("skills.tagsHint")}
                    value={form.tags}
                    onValueChange={(value) => setForm({ ...form, tags: value })}
                  />
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
    onSuccess: () => {
      toast.success(t("plugins.reloaded"));
      void invalidate();
    },
    onError: (error) => toast.error(t("plugins.reloadFailed"), { description: error.message }),
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
          <Section title={t("plugins.list")} description={t("plugins.description")}>
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
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("plugins.title") }), onClick: () => void back(), testId: "editor-back" }}
        actions={[
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
        {/* 導入の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={installMutation.error ? (installMutation.error as Error).message : null}
          attemptKey={installMutation.submittedAt}
          testId="plugin-install-error"
        />
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
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("plugins.title") }), onClick: onBack, testId: "editor-back" }}
        actions={[
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
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("marketplaces.title") }), onClick: () => void back(), testId: "editor-back" }}
        actions={[
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
        {/* 追加の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={addMutation.error ? (addMutation.error as Error).message : null}
          attemptKey={addMutation.submittedAt}
          testId="marketplace-add-error"
        />
        <Section title={t("marketplaces.overview")}>
          <Card className="min-w-0">
            <CardContent className="space-y-4 pt-5">
              <TextField
                id="mkt-id"
                label={t("marketplaces.id")}
                required
                error={idError ?? undefined}
                value={form.id}
                onValueChange={(value) => {
                  setForm({ ...form, id: value });
                  setIdError(null);
                }}
              />
              <TextField
                id="mkt-name"
                label={t("marketplaces.name")}
                value={form.name}
                onValueChange={(value) => setForm({ ...form, name: value })}
              />
              <TextField
                id="mkt-url"
                label={t("marketplaces.url")}
                helper={t("marketplaces.urlHint")}
                value={form.url}
                onValueChange={(value) => setForm({ ...form, url: value })}
              />
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
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("marketplaces.title") }), onClick: onBack, testId: "editor-back" }}
        actions={[
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

  const toolPolicyOptions: SelectFieldOption<ToolPolicyChoice>[] = [
    { value: "default", label: t("settings.toolPolicy.default") },
    { value: "allow", label: t("settings.toolPolicy.allow") },
    { value: "ask", label: t("settings.toolPolicy.ask") },
    { value: "deny", label: t("settings.toolPolicy.deny") },
  ];

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
              <SelectField<"approval" | "deny">
                id="tool-policy-default-mode"
                label={t("settings.toolPolicy.defaultMode")}
                width="md"
                value={defaultMode}
                options={[
                  { value: "approval", label: t("settings.toolPolicy.defaultModeApproval") },
                  { value: "deny", label: t("settings.toolPolicy.defaultModeDeny") },
                ]}
                onValueChange={setDefaultMode}
              />

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
                            <StatusBadge {...permissionView(tool.permission_level)} icon={false} />
                            {tool.side_effects ? (
                              <StatusBadge variant="warning" label={t("status.sideEffects")} icon={false} />
                            ) : null}
                          </div>
                          <p className="break-words text-xs leading-5 text-fg-muted [overflow-wrap:anywhere]">
                            {tool.description}
                          </p>
                        </div>
                        <SelectField<ToolPolicyChoice>
                          id={`tool-policy-${tool.name}`}
                          label={t("settings.toolPolicy.policy")}
                          className="min-w-0"
                          value={policy}
                          options={toolPolicyOptions}
                          onValueChange={(value) => setPolicy(tool.name, value)}
                        />
                      </div>
                    );
                  })}
                </div>
              ) : (
                <EmptyState title={t("common.empty.title")} />
              )}

              <SettingsSaveBar
                section={t("nav.settingsToolPolicy")}
                onSave={save}
                saving={mutation.isPending}
                error={mutation.error}
              />
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
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: snapshot.isFetching && !snapshot.isLoading,
            onClick: () => void snapshot.refetch(),
          },
        ]}
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
            <TextField
              id="runtime-snapshot-reason"
              label={t("settings.snapshot.reason")}
              width="full"
              value={reason}
              onValueChange={setReason}
            />
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
    <StatusBadge variant="neutral" label={t("settings.snapshot.runCount", { count: formatNumber(summary.runs) })} icon={false} />
  );
}

function SnapshotSummaryGrid({ summary }: { summary: RuntimeSnapshotSummary }) {
  const items: Array<[I18nKey, number]> = [
    ["settings.snapshot.count.runs", summary.runs],
    ["settings.snapshot.count.agents", summary.agents],
    ["settings.snapshot.count.events", summary.events],
    ["settings.snapshot.count.steps", summary.steps],
    ["settings.snapshot.count.approvals", summary.approvals],
    ["settings.snapshot.count.artifacts", summary.artifacts],
    ["settings.snapshot.count.pendingToolCalls", summary.pending_tool_calls],
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label={t("settings.snapshot.summary")}>
      {items.map(([label, value]) => (
        <MetricPill key={label} label={t(label)} value={formatNumber(value)} />
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
  model_id: string;
}

function agentDraftOf(agent: AgentProfile | undefined): AgentDraft {
  return {
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    instructions: agent?.instructions ?? "",
    // Skill の選択は集合なので並べ替えて比べる。
    skill_ids: [...(agent?.skill_ids ?? [])].sort(),
    model_id: agent?.model_id ?? "",
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
  models,
  defaultModelId,
  modelsLoading,
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
  /** 組み込み Runtime で選べるモデル（システム設定 > モデル の登録モデル。#754）。 */
  models: BuiltinRuntimeModel[];
  /** 空を選んだときに使う既定のテキストモデル（表示用）。 */
  defaultModelId: string;
  modelsLoading: boolean;
  actions: EntityAction[];
  /** 変更の権限がない利用者は閲覧だけ（保存を出さず、入力を無効にする）。 */
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
  const [modelId, setModelId] = useState(saved.model_id);
  const [baseline, setBaseline] = useState<AgentDraft>(saved);
  const [nameError, setNameError] = useState<string | null>(null);
  // 新規作成で選んだ業種テンプレート（#780）。
  const [templateId, setTemplateId] = useState<string | null>(null);
  const confirmTemplate = useConfirm();

  async function applyTemplate(template: AgentTemplate) {
    const touched = Boolean(name.trim() || agentDescription.trim() || instructions.trim() || skillIds.length);
    if (touched && templateId !== template.id) {
      const ok = await confirmTemplate({
        title: t("agent.template.replaceTitle"),
        description: t("agent.template.replaceDescription", { name: template.name }),
        confirmLabel: t("agent.template.replace"),
        tone: "warning",
      });
      if (!ok) return;
    }
    // 使えない（登録されていない）Skill は外して知らせる。
    const known = new Set(availableSkills.map((skill) => skill.id));
    const usable = template.skill_ids.filter((skillId) => known.has(skillId));
    const missing = template.skill_ids.filter((skillId) => !known.has(skillId));
    setName(template.name);
    setNameError(null);
    setAgentDescription(template.description);
    setInstructions(template.instructions);
    setSkillIds([...usable].sort());
    setTemplateId(template.id);
    toast.success(t("agent.template.applied", { name: template.name }), {
      description: missing.length ? t("agent.template.skillsMissing", { skills: missing.join("、") }) : undefined,
    });
  }

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
        model_id: payload.model_id ?? "",
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
    setModelId(next.model_id);
    setBaseline(next);
    setNameError(null);
  }

  const draft: AgentDraft = {
    name,
    description: agentDescription,
    instructions,
    skill_ids: [...skillIds].sort(),
    model_id: modelId,
  };
  // Agent のフォームの dirty を 1 つの離脱ガードで守る（#87）。
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
      model_id: modelId,
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
  const saveAttemptKey = Math.max(createAgent.submittedAt, patchAgent.submittedAt);

  return (
    <>
      <PageHeader
        wide
        title={title}
        subtitle={agent ? agent.id : t("page.agents.subtitle")}
        // 一覧へ戻るは左上、保存は右端の primary（#618）。
        back={{ label: t("common.backToList"), ariaLabel: t("editor.backToListOf", { list: t("nav.agents") }), onClick: () => void back(), testId: "editor-back" }}
        actions={[
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
        {/* 保存の失敗はヘッダーの直下の 1 か所だけ（messaging.md §3.3.1。#585）。 */}
        <SaveErrorBanner
          message={error?.message ?? null}
          attemptKey={saveAttemptKey}
          testId="agent-save-error"
        />
        {!agent && !readOnly ? (
          <AgentTemplatePicker selectedId={templateId} onApply={(template) => void applyTemplate(template)} />
        ) : null}
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
              <AgentVersionBadges agent={agent} />
              <span className="text-xs text-fg-muted">{`${t("common.updatedAt")}: ${formatDate(agent.updated_at)}`}</span>
            </div>
            {agent.migration_required ? <Banner severity="warning">{t("agent.migrationRequired")}</Banner> : null}
            {agent.published_version === null ? (
              // 公開するまで利用者のチャット・Run には使えない（管理者は Run の画面の「下書きで実行」で試せる）。
              <Banner severity="info">{t("agent.version.unpublishedHint")}</Banner>
            ) : null}
          </Section>
        ) : null}
        <fieldset disabled={readOnly} className="min-w-0 space-y-6">
          <Section title={t("agent.basic")}>
            <Card className="min-w-0">
              <CardContent className="space-y-4 pt-5">
                <TextField
                  id={`${fieldId}-agent-name`}
                  label={t("agent.name")}
                  required
                  error={nameError ?? undefined}
                  value={name}
                  onValueChange={(value) => {
                    setName(value);
                    setNameError(null);
                  }}
                />
                <TextField
                  id={`${fieldId}-agent-description`}
                  label={t("agent.description")}
                  value={agentDescription}
                  onValueChange={setAgentDescription}
                />
                <TextareaField
                  id={`${fieldId}-agent-instructions`}
                  label={t("agent.instructions")}
                  value={instructions}
                  onValueChange={setInstructions}
                  textareaClassName="min-h-24"
                />
                {/* 実行は組み込み Runtime（#754）。空は「既定のテキストモデル」（システム設定 > モデル）。 */}
                <SelectField
                  id={`${fieldId}-agent-model`}
                  label={t("agent.model")}
                  helper={t("agent.modelHint")}
                  width="lg"
                  disabled={modelsLoading}
                  value={modelId}
                  emptyOptionLabel={
                    defaultModelId ? t("agent.modelDefaultWith", { model: defaultModelId }) : t("agent.modelDefault")
                  }
                  options={agentModelOptions(models, modelId)}
                  onValueChange={setModelId}
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
        {agent ? <AgentVersionsSection agent={agent} readOnly={readOnly} /> : null}
      </PageBody>
    </>
  );
}

/** モデルの選択肢（空 =「既定のテキストモデル」は emptyOptionLabel）。登録から消えた保存済みの値も残す。 */
function agentModelOptions(models: BuiltinRuntimeModel[], current: string): SelectFieldOption[] {
  const options: SelectFieldOption[] = models.map((model) => ({
    value: model.model_id,
    label: model.display_name,
  }));
  if (current && !models.some((model) => model.model_id === current)) {
    options.push({ value: current, label: t("agent.modelMissing", { model: current }) });
  }
  return options;
}

/** Run の取消・再開の可否。一覧の行メニューと詳細の ObjectActionBar・ストリーム操作で同じ判定を使う。 */
function runCapabilities(run: RunState): { canCancel: boolean; canResume: boolean } {
  // 組み込み Runtime（#754）は承認がすべて決まると自動で再開する。手動の再開は使わない。
  const isBuiltin = run.runtime_id === "builtin";
  return {
    canCancel: ["queued", "running", "waiting_approval"].includes(run.status),
    canResume: !isBuiltin && ["running", "waiting_approval"].includes(run.status),
  };
}

function RunHistoryList({
  runs,
  selectedRunId,
  onSelect,
  actionsFor,
  agentNameOf,
}: {
  runs: RunState[];
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
  agentName,
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
  /** 業務 Agent の名前（一覧に無いときは ID）。 */
  agentName: string;
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
  const queryClient = useQueryClient();
  const structured = getStructuredResult(run);
  const { canCancel, canResume } = runCapabilities(run);
  const pendingApproval = run.approvals.find((approval) => approval.status === "pending");

  return (
    <section className="space-y-5" aria-label={t("run.detail")}>
      <Card>
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle>{t("run.detail")}</CardTitle>
              <StatusBadge {...runStatusView(run.status)} />
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
            <span>{`${t("run.form.agent")}: ${agentName}`}</span>
            <span>{`${t("run.runtime")}: ${run.runtime_id === "builtin" ? t("runtime.builtin.title") : run.runtime_id}`}</span>
            <span>{`${t("common.createdAt")}: ${formatDate(run.created_at)}`}</span>
            <span>{`${t("common.updatedAt")}: ${formatDate(run.updated_at)}`}</span>
            {/* モデルの利用量（承認待ちからの再開を含めた累計。#772）。 */}
            <span className="sm:col-span-2" data-testid="run-usage">
              {`${t("run.usage")}: ${
                run.usage
                  ? t("run.usage.summary", {
                      model: run.usage.model || t("usage.modelNone"),
                      requests: formatNumber(run.usage.requests),
                      input: formatNumber(run.usage.input_tokens),
                      output: formatNumber(run.usage.output_tokens),
                      total: formatNumber(run.usage.total_tokens),
                    })
                  : t("run.usage.none")
              }`}
            </span>
          </div>
          {/* 管理者の評価（#774）。Agent 管理の権限で、回答が出た Run に付ける（本人の評価とは別）。 */}
          {capabilities.admin && run.status === "completed" && run.artifacts.some((item) => item.kind === "answer") ? (
            <AnswerFeedback
              runId={run.id}
              current={run.admin_review ?? null}
              mode="admin"
              onSaved={() => void queryClient.invalidateQueries({ queryKey: ["runs"] })}
            />
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
                      <StatusBadge {...stepStatusView(step.status)} />
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
  if (event.type.startsWith("runtime.")) {
    return {
      title: t("run.runtime"),
      subtitle: event.message,
      icon: <Server size={16} aria-hidden />,
      ...timelineBadge(event.type),
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
      ...timelineBadge(event.type),
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
      ...timelineBadge(event.type),
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
      ...timelineBadge(event.type),
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
      ...timelineBadge(event.type),
      details: compactTimelineDetails([[t("run.auditArtifacts"), payloadString(event.payload, "artifact_id")]]),
      warnings: [],
    };
  }
  return {
    title: event.message,
    subtitle: `${event.type} / ${formatDate(event.created_at)}`,
    icon: <GitBranch size={16} aria-hidden />,
    ...timelineBadge(event.type),
    details: [],
    warnings: [],
  };
}

/** タイムラインのバッジ（イベントの種類を日本語にする。lib/status-labels.ts）。 */
function timelineBadge(type: string): Pick<TimelineEventView, "badgeLabel" | "badgeVariant"> {
  const view = eventTypeView(type);
  return { badgeLabel: view.label, badgeVariant: view.variant };
}

function compactTimelineDetails(items: Array<[string, string | null]>): Array<{ label: string; value: string }> {
  return items
    .filter((item): item is [string, string] => Boolean(item[1]))
    .map(([label, value]) => ({ label, value }));
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
  return (
    <div className="min-w-0 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-all text-sm font-medium text-fg">{record.tool_name}</p>
          <p className="mt-0.5 break-all text-xs text-fg-muted">{`${runStatusView(audit.status).label} / ${record.step_id}`}</p>
        </div>
        <StatusBadge {...stepStatusView(record.status)} />
      </div>

      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
        <AuditFact label={t("run.auditPolicy")} value={record.policy_decision ? policyDecisionView(record.policy_decision).label : "-"} />
        <AuditFact label={t("common.permission")} value={record.permission_level ? permissionView(record.permission_level).label : "-"} />
        <AuditFact label={t("run.auditApproval")} value={record.approval_status ? approvalStatusView(record.approval_status).label : "-"} />
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
        // title は利用者向けの要約、エラーコードは「詳細」に畳む。失敗なので開いて出す（messaging.md §10.3。#725）。
        <Banner severity="danger" title={t("run.auditErrorTitle")}>
          <div className="min-w-0 space-y-2">
            <p className="break-words [overflow-wrap:anywhere]">{record.error}</p>
            {record.error_code ? (
              <Disclosure variant="plain" size="sm" summary={t("run.auditErrorDetails")} defaultOpen>
                <p className="break-all text-xs text-fg-muted">{`${t("audit.errorCode")}: ${record.error_code}`}</p>
              </Disclosure>
            ) : null}
          </div>
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
                <StatusBadge {...artifactKindView(artifact.kind)} icon={false} />
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
        <MetricPill label={t("run.truncated")} value={truncated === null ? "-" : truncated ? t("common.yes") : t("common.no")} />
        <MetricPill label={t("run.columns")} value={String(arrayOfRecords(artifact.content.columns).length)} />
      </div>
      {typeof artifact.content.sql === "string" ? (
        <JsonPanel title={t("run.sql")} value={artifact.content.sql} />
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
  return (
    <Card className="min-w-0">
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle>{tool.name}</CardTitle>
          <CardDescription>{tool.description}</CardDescription>
        </div>
        <StatusBadge {...permissionView(tool.permission_level)} icon={false} />
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
          <JsonPanel title={t("settings.mcpDiscovery.inputSchema")} value={tool.input_schema} />
          <JsonPanel title={t("tool.outputSchema")} value={tool.output_schema} />
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
