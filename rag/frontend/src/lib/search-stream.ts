/**
 * RAG 検索の SSE ストリーミングクライアント（POST /api/search/stream）。
 * バックエンドは stage(複数) / metadata / delta(複数) / citations / done のイベントを送る。
 * stream を始めた後の失敗（timeout・回答形式の検証の失敗・内部エラー）は `error` event で届く。
 */

import { appPath } from "./base-path";
import {
  ApiError,
  apiErrorFromEnvelope,
  apiErrorFromFetchFailure,
  notifyResponseAuthStatus,
  withCsrfHeaders,
  type RetrievedChunk,
  type SearchDiagnostics,
  type SearchRequestBody,
} from "./api";
import { t } from "./i18n";

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
  /** realtime stream 後にガードレールがマスク/差し替えした本文で累積回答を置換する。 */
  onReplace?: (text: string) => void;
  onCitations?: (citations: RetrievedChunk[]) => void;
  onDone?: (meta: { trace_id: string }) => void;
}

/** backend の `error` event の payload（`app/api/routes/search.py`）。 */
interface SearchStreamErrorPayload {
  trace_id?: string;
  message?: string;
  error_type?: string;
  validation_codes?: string[];
}

const SEARCH_STREAM_PATH = "/api/search/stream";

/** `error` event の error_type を、同じ失敗を REST（`POST /api/search`）が返す HTTP status に揃える。 */
const STREAM_ERROR_STATUS: Record<string, number> = {
  TimeoutError: 504,
  GenerationContractError: 502,
};

/**
 * SSE の `event:`/`data:` ブロックを解析しながらハンドラへ流す。
 *
 * `error` event を受けたら、または `done` を受けずに stream が終わったら ApiError を投げる
 * （途中の失敗を「該当なし」の完了として見せない。#285）。
 */
export async function streamSearch(
  body: SearchRequestBody,
  handlers: SearchStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const request = { method: "POST", path: SEARCH_STREAM_PATH };
  let res: Response;
  try {
    // 送る URL には配信の base を付ける（#1316）。エラーの表示の path は付けない。
    res = await fetch(appPath(SEARCH_STREAM_PATH), {
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
    // 401 はログインへ。範囲外の 403（RAG_SCOPE_FORBIDDEN）は検索結果の位置で理由を見せる（#224）。
    notifyResponseAuthStatus(res);
    let envelope: unknown = null;
    try {
      envelope = await res.json();
    } catch {
      // SSE エラー時に JSON でない場合は既定メッセージを使う
    }
    throw apiErrorFromEnvelope(res.status, envelope, res.headers.get("X-Request-ID"));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const requestId = res.headers.get("X-Request-ID") ?? undefined;
  let buffer = "";
  let outcome: StreamOutcome = null;

  while (outcome === null) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch (cause) {
      // 受信の途中で接続が切れた（`TypeError: network error` など）。中止はそのまま（#906）。
      throw apiErrorFromFetchFailure(cause, request) ?? cause;
    }
    const { value, done } = chunk;
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");

    let separator: number;
    while (outcome === null && (separator = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      outcome = dispatchEvent(block, handlers);
    }
  }

  if (outcome === null) {
    buffer += decoder.decode();
    buffer = buffer.replace(/\r\n/g, "\n").trim();
    if (buffer) {
      outcome = dispatchEvent(buffer, handlers);
    }
  } else {
    // error / done の後は読まない（backend はその後に event を送らない）。
    void reader.cancel().catch(() => undefined);
  }

  if (outcome === "done") return;
  if (outcome === null) {
    // proxy の timeout・接続断などで done を受けずに終わった。
    throw new ApiError(502, [t("search.stream.incomplete")], { requestId });
  }
  throw new ApiError(
    STREAM_ERROR_STATUS[outcome.error_type ?? ""] ?? 500,
    [outcome.message?.trim() || t("search.stream.failed")],
    { requestId }
  );
}

/** stream の終わり方。null はまだ終わっていない（done も error も受けていない）。 */
type StreamOutcome = "done" | SearchStreamErrorPayload | null;

function dispatchEvent(block: string, handlers: SearchStreamHandlers): StreamOutcome {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
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
    case "replace":
      handlers.onReplace?.((payload as { text: string }).text);
      break;
    case "citations":
      handlers.onCitations?.(payload as RetrievedChunk[]);
      break;
    case "done":
      handlers.onDone?.(payload as { trace_id: string });
      return "done";
    case "error":
      return (payload && typeof payload === "object" ? payload : {}) as SearchStreamErrorPayload;
  }
  return null;
}
