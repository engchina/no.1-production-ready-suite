import { ButtonLink } from "@engchina/production-ready-ui";
import { Bot } from "lucide-react";

import { agentEscalationUrl } from "@/lib/agent-escalation";
import { parseAnswerDiagnostics } from "@/lib/answer-diagnostics";
import { t } from "@/lib/i18n";
import { useChatAgentLink } from "@/lib/queries";

/**
 * 固定の RAG では完了できない回答（現場の実データの確認が要る）から、Agent のチャットへ質問を引き継ぐ導線（#1283）。
 *
 * 回答の経路（diagnostics.answer.route）が提案していて、Agent の画面の URL（RAG_AGENT_APP_URL）が設定されている
 * ときだけ出す。押すと質問を入れた Agent のチャットを開く（送信は利用者。同じタブで開き、ブラウザの「戻る」で
 * このチャットへ戻れる）。会話の履歴で書き換えた質問があれば、前の会話を読まなくても通じるそちらを渡す。
 */
export function AgentEscalationAction({
  diagnostics,
  question,
}: {
  diagnostics: unknown;
  question: string;
}) {
  const parsed = parseAnswerDiagnostics(diagnostics);
  const route = parsed?.route ?? null;
  const link = useChatAgentLink(route?.escalationSuggested === true);
  const href = agentEscalationUrl(
    link.data?.agent_chat_url,
    route,
    parsed?.rewrittenQuestion || question,
  );
  if (!href) return null;
  return (
    <div
      className="flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:items-center sm:justify-between"
      data-testid="chat-agent-escalation"
    >
      <p className="text-sm leading-relaxed text-fg-muted">{t("chat.agentEscalation.note")}</p>
      <ButtonLink
        to={href}
        variant="secondary"
        size="sm"
        icon={Bot}
        className="shrink-0 self-start sm:self-auto"
        testId="chat-agent-escalation-link"
      >
        {t("chat.agentEscalation.action")}
      </ButtonLink>
    </div>
  );
}
