/**
 * RAG 検索の SSE ストリーミングクライアント（POST /api/search/stream）。
 * バックエンドは stage(複数) / metadata / delta(複数) / citations / done のイベントを送る。
 */

import { ApiError, type RetrievedChunk, type SearchDiagnostics, type SearchRequestBody } from "./api";

export interface SearchStageEvent {
  trace_id: string;
  stage: string;
  outcome: "started" | "success" | "error" | "cancelled";
  elapsed_ms: number;
  attributes: Record<string, unknown>;
}

export interface SearchStreamHandlers {
  onStage?: (stage: SearchStageEvent) => void;
  onMetadata?: (meta: {
    trace_id: string;
    elapsed_ms: number;
    guardrail_warnings: string[];
    diagnostics: SearchDiagnostics;
  }) => void;
  onDelta?: (text: string) => void;
  onCitations?: (citations: RetrievedChunk[]) => void;
  onDone?: (meta: { trace_id: string }) => void;
}

/** SSE の `event:`/`data:` ブロックを解析しながらハンドラへ流す。 */
export async function streamSearch(
  body: SearchRequestBody,
  handlers: SearchStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch("/api/search/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    let messages = [`APIエラー (${res.status})`];
    try {
      const envelope = await res.json();
      if (Array.isArray(envelope?.error_messages) && envelope.error_messages.length) {
        messages = envelope.error_messages;
      }
    } catch {
      // SSE エラー時に JSON でない場合は既定メッセージを使う
    }
    throw new ApiError(res.status, messages);
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

function dispatchEvent(block: string, handlers: SearchStreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }

  switch (event) {
    case "stage":
      handlers.onStage?.(payload as SearchStageEvent);
      break;
    case "metadata":
      handlers.onMetadata?.(payload as Parameters<NonNullable<SearchStreamHandlers["onMetadata"]>>[0]);
      break;
    case "delta":
      handlers.onDelta?.((payload as { text: string }).text);
      break;
    case "citations":
      handlers.onCitations?.(payload as RetrievedChunk[]);
      break;
    case "done":
      handlers.onDone?.(payload as { trace_id: string });
      break;
  }
}
