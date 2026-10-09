/**
 * チャットメッセージの SSE ストリーミングクライアント
 * （POST /api/chat/conversations/{id}/messages/stream、再購読は GET .../messages/{質問の id}/stream）。
 *
 * バックエンドは start → (stage / chat_progress / delta / metadata / citations / done)×モデル → all_done を送る。
 * event の無い間は heartbeat（`: keepalive` のコメント）を送る（#1160）。
 * `chat_progress` は処理の段階のイベント（3 製品共通の契約。#1359）を、記録するたびに 1 件ずつ送る
 * （data は `{model_id, message_id, event}`。`event` は未検証の入力として `parseChatProgressEvent` を通す）。
 * マルチモデル比較では各イベントに model_id が付き、フロントがカラムへ振り分ける。
 *
 * 回答の作成は接続から切り離されている（#1175）。接続が切れても backend は作成を続けるので、画面は最後に
 * 受け取った event の連番（`id:`）から `resumeChatStream` で続きを購読し直す。
 */

import { parseChatProgressEvent, type ChatProgressEvent } from "@production-ready/ui";

import { appPath } from "./base-path";
import { t } from "./i18n";
import {
  ApiError,
  apiErrorFromEnvelope,
  apiErrorFromFetchFailure,
  notifyResponseAuthStatus,
  withCsrfHeaders,
  type ChatMessage,
  type ChatMessageRequestBody,
  type RetrievedChunk,
} from "./api";

export interface ChatColumn {
  model_id: string;
  label: string;
  /** 作成中の回答（保存済み・STREAMING）の id（#1175）。 */
  message_id?: string;
}

export interface ChatStreamHandlers {
  /** 永続化済みユーザー発話 + 比較カラム構成。最初に 1 回だけ届く。 */
  onStart?: (payload: { user_message: ChatMessage; columns: ChatColumn[] }) => void;
  onStage?: (payload: {
    model_id: string;
    stage: string;
    outcome: "started" | "success" | "error" | "cancelled";
    elapsed_ms: number;
  }) => void;
  /** 処理の段階のイベント（#1359）。`messageId` は作成中の回答のメッセージ（イベントの対象）。 */
  onChatProgress?: (payload: { model_id: string; message_id: string; event: ChatProgressEvent }) => void;
  onDelta?: (modelId: string, text: string) => void;
  onMetadata?: (payload: {
    model_id: string;
    message_id: string;
    trace_id: string;
    elapsed_ms: number;
    guardrail_warnings: string[];
    /** 回答の根拠・実行記録(無い回答では null / 未指定)。 */
    answer_diagnostics?: unknown;
  }) => void;
  onCitations?: (modelId: string, citations: RetrievedChunk[]) => void;
  onModelDone?: (payload: { model_id: string; message_id: string }) => void;
  /** 回答の失敗。`cancelled` は利用者の停止（#1175）。 */
  onModelError?: (payload: { model_id: string; message: string; cancelled: boolean }) => void;
  onAllDone?: () => void;
  /**
   * 配信のバイト（event・heartbeat のコメント）を受け取るたびに呼ぶ（#1160）。画面は、これが一定時間
   * 届かなければ配信が途絶えたとみなし、保存済みの会話を取り直す（`useChatProgressTracker` の `touch()`）。
   */
  onActivity?: () => void;
  /** event の連番（`id:`）を受け取ったときに呼ぶ。再購読はこの次から（#1175）。 */
  onEventId?: (id: number) => void;
}

/** 配信の終わり方。`completed` が false なら、`all_done` の前に接続が切れた（#1160）。 */
export interface ChatStreamOutcome {
  completed: boolean;
}

/**
 * 送る質問の id（32 桁の 16 進。#1175）。サーバーは同じ id で質問を保存し、画面は `start` の前でもこの id で
 * 回答の作成を取り消せる。http（非 secure context）でも使えるよう `crypto.randomUUID` ではなく
 * `crypto.getRandomValues` で作る。
 */
export function newChatClientMessageId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

/**
 * 回答（ASSISTANT のメッセージ）の処理の段階の SSE の URL（配信の base を含む。#1359）。保存済みの記録を読み直して
 * 送るので、別の worker が作成している回答・再読込の後でも段階が届く。`since` は `useChatProgressStream` が付ける。
 */
export function chatAnswerProgressStreamUrl(conversationId: string, messageId: string): string {
  return appPath(
    `/api/chat/conversations/${encodeURIComponent(conversationId)}` +
      `/messages/${encodeURIComponent(messageId)}/progress/stream`
  );
}

/** 回答の作成は続いているが、この接続では続きを購読できない（別の worker・再起動の後。#1175）。 */
export function isChatStreamGone(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** SSE の `event:`/`data:` ブロックを解析しながらハンドラへ流す。 */
export async function streamChatMessage(
  conversationId: string,
  body: ChatMessageRequestBody,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<ChatStreamOutcome> {
  const path = `/api/chat/conversations/${encodeURIComponent(conversationId)}/messages/stream`;
  return openChatStream(
    path,
    {
      method: "POST",
      // Cookie セッションの CSRF（#214）。
      headers: withCsrfHeaders("POST", {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      credentials: "same-origin",
      body: JSON.stringify(body),
      signal,
    },
    handlers
  );
}

/**
 * 作成中の回答の配信を、`lastEventId`（最後に受け取った event の連番）の次から購読し直す（#1175）。
 * `messageId` は質問（USER のメッセージ）の id。このプロセスで作成していなければ 404（`isChatStreamGone`）。
 */
export async function resumeChatStream(
  conversationId: string,
  messageId: string,
  lastEventId: number,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<ChatStreamOutcome> {
  const path =
    `/api/chat/conversations/${encodeURIComponent(conversationId)}` +
    `/messages/${encodeURIComponent(messageId)}/stream`;
  return openChatStream(
    path,
    {
      method: "GET",
      headers: { Accept: "text/event-stream", "Last-Event-ID": String(lastEventId) },
      credentials: "same-origin",
      cache: "no-store",
      signal,
    },
    handlers,
    { requireEventStream: true }
  );
}

async function openChatStream(
  path: string,
  init: RequestInit,
  handlers: ChatStreamHandlers,
  options: { requireEventStream?: boolean } = {}
): Promise<ChatStreamOutcome> {
  const request = { method: init.method ?? "GET", path };
  let res: Response;
  try {
    // 送る URL には配信の base を付ける（#1316）。エラーの表示の path は付けない。
    res = await fetch(appPath(path), init);
  } catch (cause) {
    // 通信断（`TypeError: Failed to fetch`）は利用者向けの文の ApiError にする。中止はそのまま（#906）。
    throw apiErrorFromFetchFailure(cause, request) ?? cause;
  }

  if (!res.ok || !res.body) {
    // 401 はログインへ。範囲外の 403（RAG_SCOPE_FORBIDDEN）はチャット内で理由を見せる（#224）。
    notifyResponseAuthStatus(res);
    let envelope: unknown = null;
    try {
      envelope = await res.json();
    } catch {
      // SSE エラー時に JSON でない場合は既定メッセージを使う
    }
    const detail = (envelope as { detail?: unknown } | null)?.detail;
    const error = apiErrorFromEnvelope(res.status, envelope, res.headers.get("X-Request-ID"));
    if (error.isFallbackMessage && typeof detail === "string") {
      throw new ApiError(res.status, [detail], {
        errorCode: error.errorCode,
        requestId: error.requestId,
      });
    }
    throw error;
  }
  if (options.requireEventStream && !(res.headers.get("content-type") ?? "").includes("text/event-stream")) {
    // SSE ではない応答（中継の案内のページなど）は、続きを購読できないものとして扱う（保存済みの会話を取り直す）。
    void res.body.cancel().catch(() => undefined);
    throw new ApiError(404, [t("chat.stream.resumeUnavailable")]);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  const dispatch = (block: string) => {
    if (dispatchEvent(block, handlers) === "all_done") completed = true;
  };
  handlers.onActivity?.();

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch (cause) {
      // 受信の途中で接続が切れた（`TypeError: network error` など）。中止はそのまま（#906）。
      throw apiErrorFromFetchFailure(cause, request) ?? cause;
    }
    const { value, done } = chunk;
    if (done) break;
    handlers.onActivity?.();
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");

    let separator: number;
    while ((separator = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      dispatch(block);
    }
  }

  buffer += decoder.decode();
  buffer = buffer.replace(/\r\n/g, "\n").trim();
  if (buffer) {
    dispatch(buffer);
  }
  return { completed };
}

/** 1 つの event を解析してハンドラへ渡し、event 名を返す（コメント・空の data は null）。 */
function dispatchEvent(block: string, handlers: ChatStreamHandlers): string | null {
  let event = "message";
  let id: number | null = null;
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    else if (line.startsWith("id:")) {
      const parsed = Number.parseInt(line.slice(3).trim(), 10);
      if (Number.isFinite(parsed)) id = parsed;
    }
  }
  if (dataLines.length === 0) return null;

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }

  switch (event) {
    case "start":
      handlers.onStart?.({
        user_message: payload.user_message as ChatMessage,
        columns: (payload.columns as ChatColumn[]) ?? [],
      });
      break;
    case "stage":
      handlers.onStage?.(payload as Parameters<NonNullable<ChatStreamHandlers["onStage"]>>[0]);
      break;
    case "chat_progress": {
      const progress = parseChatProgressEvent(payload.event);
      if (progress) {
        handlers.onChatProgress?.({
          model_id: String(payload.model_id ?? ""),
          message_id: String(payload.message_id ?? progress.target_id),
          event: progress,
        });
      }
      break;
    }
    case "delta":
      handlers.onDelta?.(String(payload.model_id ?? ""), String(payload.text ?? ""));
      break;
    case "metadata":
      handlers.onMetadata?.(
        payload as Parameters<NonNullable<ChatStreamHandlers["onMetadata"]>>[0]
      );
      break;
    case "citations":
      handlers.onCitations?.(
        String(payload.model_id ?? ""),
        (payload.citations as RetrievedChunk[]) ?? []
      );
      break;
    case "done":
      handlers.onModelDone?.({
        model_id: String(payload.model_id ?? ""),
        message_id: String(payload.message_id ?? ""),
      });
      break;
    case "error":
      handlers.onModelError?.({
        model_id: String(payload.model_id ?? ""),
        message: String(payload.message ?? t("chat.error.model")),
        cancelled: payload.cancelled === true,
      });
      break;
    case "all_done":
      handlers.onAllDone?.();
      break;
  }
  // 処理した後に連番を進める（処理の途中で切れたら、同じ event から購読し直す）。
  if (id !== null) handlers.onEventId?.(id);
  return event;
}
