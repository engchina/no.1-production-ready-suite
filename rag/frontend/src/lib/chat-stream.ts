/**
 * チャットメッセージの SSE ストリーミングクライアント
 * （POST /api/chat/conversations/{id}/messages/stream）。
 *
 * バックエンドは start → (stage / delta / metadata / citations / done)×モデル → all_done を送る。
 * マルチモデル比較では各イベントに model_id が付き、フロントがカラムへ振り分ける。
 */

import { t } from "./i18n";
import {
  ApiError,
  apiErrorFromEnvelope,
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
  onDelta?: (modelId: string, text: string) => void;
  onMetadata?: (payload: {
    model_id: string;
    message_id: string;
    trace_id: string;
    elapsed_ms: number;
    guardrail_warnings: string[];
    /** DocRAG 回答エンジンの根拠・実行記録(standard では null / 未指定)。 */
    docrag?: unknown;
  }) => void;
  onCitations?: (modelId: string, citations: RetrievedChunk[]) => void;
  onModelDone?: (payload: { model_id: string; message_id: string }) => void;
  onModelError?: (payload: { model_id: string; message: string }) => void;
  onAllDone?: () => void;
}

/** SSE の `event:`/`data:` ブロックを解析しながらハンドラへ流す。 */
export async function streamChatMessage(
  conversationId: string,
  body: ChatMessageRequestBody,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(
    `/api/chat/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
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
    }
  );

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

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");

    let separator: number;
    while ((separator = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      dispatchEvent(block, handlers);
    }
  }

  buffer += decoder.decode();
  buffer = buffer.replace(/\r\n/g, "\n").trim();
  if (buffer) {
    dispatchEvent(buffer, handlers);
  }
}

function dispatchEvent(block: string, handlers: ChatStreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return;

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
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
}
