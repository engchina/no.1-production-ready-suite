/**
 * ほかの製品からチャットへ質問を引き継ぐ URL の約束（#1283）。
 *
 * 製品どうしはコードで依存しないので、画面の URL だけで連携する:
 * `/chat?question=<質問>&entry=rag_escalation&reason=<RAG の回答の対応>`。
 *
 * - 質問は入力欄に入れるだけで、自動では送らない（利用者が内容を確かめて送る）。
 * - `entry` が知っている値なら、送った Run の metadata に `entry` と `entry_reason` を残す
 *   （振り分けの評価に使う。backend の RUN_ENTRIES と同じ値）。
 */
export const CHAT_HANDOFF_PARAMS = ["question", "entry", "reason"] as const;
export const CHAT_HANDOFF_ENTRIES = ["rag_escalation"] as const;
/** 引き継ぐ質問の上限（RAG の画面と同じ）。 */
export const CHAT_HANDOFF_QUESTION_MAX_CHARS = 2000;

export type ChatHandoffEntry = (typeof CHAT_HANDOFF_ENTRIES)[number];

/** 送る質問に付ける入口（作業状態に残し、再読込しても送るまで保つ）。 */
export interface ChatEntry {
  entry: ChatHandoffEntry;
  reason: string;
}

export interface ChatHandoff {
  question: string;
  entry: ChatEntry | null;
}

const REASON_PATTERN = /^[a-z][a-z_]{0,63}$/;

export function isChatEntry(value: unknown): value is ChatEntry {
  const entry = value as ChatEntry;
  return (
    typeof entry === "object" &&
    entry !== null &&
    (CHAT_HANDOFF_ENTRIES as readonly string[]).includes(entry.entry) &&
    typeof entry.reason === "string" &&
    (entry.reason === "" || REASON_PATTERN.test(entry.reason))
  );
}

/** URL の query から引き継ぐ質問を読む。質問が無ければ null。 */
export function readChatHandoff(params: URLSearchParams): ChatHandoff | null {
  const question = Array.from((params.get("question") ?? "").trim())
    .slice(0, CHAT_HANDOFF_QUESTION_MAX_CHARS)
    .join("");
  if (!question) return null;
  const entry = params.get("entry") ?? "";
  const reason = params.get("reason") ?? "";
  const candidate = { entry, reason: REASON_PATTERN.test(reason) ? reason : "" };
  return { question, entry: isChatEntry(candidate) ? candidate : null };
}

/** Run の metadata（入口が無ければ undefined）。 */
export function chatEntryMetadata(entry: ChatEntry | null): Record<string, string> | undefined {
  if (!entry) return undefined;
  return entry.reason ? { entry: entry.entry, entry_reason: entry.reason } : { entry: entry.entry };
}
