import type { AnswerRoute } from "@/lib/answer-diagnostics";

/**
 * 固定の RAG から Agent のチャットへ質問を引き継ぐ URL（#1283）。
 *
 * 製品どうしはコードで依存しないので、Agent のチャットの画面の URL の約束だけで連携する:
 * `?question=<質問>&entry=rag_escalation&reason=<回答の対応>`。Agent のチャットは質問を入力欄に入れるだけで、
 * 送信は利用者が行う（自動では送らない）。送ると Agent は Run の metadata に `entry` と `reason` を残す。
 */
export const AGENT_ESCALATION_ENTRY = "rag_escalation";
/** URL に載せる質問の上限（URL を長くし過ぎない。Agent のチャットも同じ長さで切る）。 */
export const AGENT_ESCALATION_QUESTION_MAX_CHARS = 2000;

export function agentEscalationUrl(
  agentChatUrl: string | null | undefined,
  route: AnswerRoute | null,
  question: string,
): string | null {
  const text = question.trim();
  if (!agentChatUrl || !route?.escalationSuggested || !text) return null;
  let url: URL;
  try {
    url = new URL(agentChatUrl);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  url.searchParams.set("question", Array.from(text).slice(0, AGENT_ESCALATION_QUESTION_MAX_CHARS).join(""));
  url.searchParams.set("entry", AGENT_ESCALATION_ENTRY);
  if (route.escalationReason) url.searchParams.set("reason", route.escalationReason);
  return url.toString();
}
