// Run のイベントの購読（SSE / WebSocket）と WebSocket のコマンド（#215 / #814。旧 AgentRuntimePages.tsx から分けた。#818）。
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiErrorMessage, type StatusVariant } from "@engchina/production-ready-ui";
import { type RunEvent, type RunState } from "@/lib/api";
import { t } from "@/lib/i18n";
import { securityApi } from "@/lib/security-api";
import { useValuesChanged } from "@/lib/render-sync";
import { formatDate } from "@/pages/shared/page-helpers";

export type RunStreamMode = "sse" | "websocket";

export type WebSocketStreamStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed"
  | "error"
  | "stopped";

export interface RunWebSocketState {
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
  /** 送って受付（ack）・拒否を待っているコマンド。押した操作だけを loading にする（messaging.md §3.7）。 */
  pendingCommand: RunCommandKind | null;
  sendCancel: () => void;
  sendResume: () => void;
  sendApprovalDecision: (approvalId: string, approved: boolean) => void;
}

/** Run の詳細の操作（`ObjectActionBar`）のうち、WebSocket でも送れるもの。 */
export type RunCommandKind = "cancel" | "resume" | "approve" | "reject";

type SseStreamStatus = "idle" | "open" | "failed";

export interface RunEventSourceState {
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

export const websocketStatusVariant: Record<WebSocketStreamStatus, StatusVariant> = {
  idle: "neutral",
  connecting: "info",
  open: "success",
  reconnecting: "info",
  closed: "neutral",
  error: "danger",
  stopped: "warning",
};

export function useRunEventWebSocket(
  run: RunState | undefined,
  enabled: boolean,
  onRuntimeEvent: () => void,
  /** コマンド単位の拒否（権限のない取消など）。接続は保ったまま、操作の失敗として利用者に返す。 */
  onCommandError: (message: string) => void
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
  const [pendingCommand, setPendingCommand] = useState<RunCommandKind | null>(null);
  // 最新の callback を effect の中から呼ぶ（callback が変わっても接続し直さない）。
  const onCommandErrorRef = useRef(onCommandError);
  useLayoutEffect(() => {
    onCommandErrorRef.current = onCommandError;
  });
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
    setPendingCommand(null);
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
        // 閉じた接続のコマンドの応答はもう来ない。
        setPendingCommand(null);
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
          setPendingCommand(null);
          onRuntimeEvent();
          return;
        }
        if (message.type === "error") {
          const code = message.error_code ?? null;
          if (message.command) {
            // コマンド単位の拒否（権限のない取消など）は接続を保ったまま、操作の失敗として返す。
            // 権限以外は backend の利用者向けの文（日本語）を出し、文が無ければ既定の文にする。
            // error code（技術情報）は Toast に出さず「接続の詳細」だけに残す（#1031）。
            const reason = code?.startsWith("rbac.")
              ? t("run.stream.commandForbidden")
              : apiErrorMessage(new Error(message.message ?? ""), t("run.stream.commandFailedDefault"));
            setLastError(`${message.command}: ${code ?? reason}`);
            setPendingCommand(null);
            onCommandErrorRef.current(reason);
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
    setPendingCommand("cancel");
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
    setPendingCommand("resume");
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
    setPendingCommand(approved ? "approve" : "reject");
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
    pendingCommand,
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
export function useRunEventSource(
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
