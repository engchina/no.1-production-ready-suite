/**
 * チャットメッセージの SSE ストリーミングクライアント
 * （POST /api/chat/conversations/{id}/messages/stream）。
 *
 * バックエンドは start → (stage / progress / delta / metadata / citations / done)×モデル → all_done を送る。
 * event の無い間は heartbeat（`: keepalive` のコメント）を送る（#1160）。
 * `progress` は処理の段階（3 製品共通の ChatProgressStep。#1146）の一覧を、段階が変わるたびに全体で送る。
 * マルチモデル比較では各イベントに model_id が付き、フロントがカラムへ振り分ける。
 */

import type { ChatProgressStep } from "@engchina/production-ready-ui";

import { chatProgressStepsFromEvent } from "./chat-progress";
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
  /** 処理の段階（#1146）。段階が変わるたびに一覧の全体が届く。 */
  onProgress?: (modelId: string, steps: ChatProgressStep[]) => void;
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
  onModelError?: (payload: { model_id: string; message: string }) => void;
  onAllDone?: () => void;
  /**
   * 配信のバイト（event・heartbeat のコメント）を受け取るたびに呼ぶ（#1160）。画面は、これが一定時間
   * 届かなければ配信が途絶えたとみなし、保存済みの会話を取り直す（`useChatProgressTracker` の `touch()`）。
   */
  onActivity?: () => void;
}

/** 配信の終わり方。`completed` が false なら、`all_done` の前に接続が切れた（#1160）。 */
export interface ChatStreamOutcome {
  completed: boolean;
}

/** SSE の `event:`/`data:` ブロックを解析しながらハンドラへ流す。 */
export async function streamChatMessage(
  conversationId: string,
  body: ChatMessageRequestBody,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<ChatStreamOutcome> {
  const path = `/api/chat/conversations/${encodeURIComponent(conversationId)}/messages/stream`;
  const request = { method: "POST", path };
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      // Cookie セッションの CSRF（#214）。
      headers: withCsrfHeaders("POST", {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }),
      credentials: "same-origin",
      body: JSON.stringify(body),
      signal,
    });
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
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
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
    case "progress":
      handlers.onProgress?.(String(payload.model_id ?? ""), chatProgressStepsFromEvent(payload.steps));
      break;
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
      });
      break;
    case "all_done":
      handlers.onAllDone?.();
      break;
  }
  return event;
}
