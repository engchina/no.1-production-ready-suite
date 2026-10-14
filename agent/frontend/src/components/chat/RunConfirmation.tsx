import type { ReactNode } from "react";
import {
  ApiErrorBanner,
  ClarificationCard,
  ClarificationRecord,
  type ClarificationBusyAction,
} from "@production-ready/ui";

import type { ConfirmationAction, ConfirmationAnswer, ConfirmationRequest } from "@/lib/api";
import {
  clarificationItems,
  confirmationAnswers,
  confirmationRound,
  type ConfirmationRecordView,
} from "@/lib/confirmation";
import { t } from "@/lib/i18n";

/**
 * 回答前の確認のカード（#1839）。部品・文言・操作は 3 製品共通の `ClarificationCard`（「この内容で回答」「確認せずに
 * 回答」「確認をやめる」と「その他（自由入力）」）。回答の枠の中に出すので `variant="plain"`。
 *
 * - Agent 自身の確認（`ask_user_confirmation`）: 答え・スキップで同じ回答の続きが動く。
 * - ツールの結果の確認（NL2SQL・RAG）: 確認事項は呼び先が作ったまま。答えで次の回答を作る。NL2SQL の確認は
 *   NL2SQL の方針に従うので「確認せずに回答」は出さない（`can_skip`）。製品の操作（「データベースの検索は不要」など）は
 *   `actions` で確定の右に置く。
 */
export function PendingConfirmationCard({
  confirmation,
  disabled,
  busyAction,
  error,
  actions,
  onDecide,
}: {
  confirmation: ConfirmationRequest;
  disabled: boolean;
  busyAction: ClarificationBusyAction | null;
  error: unknown;
  actions?: ReactNode;
  onDecide: (action: ConfirmationAction, answers?: ConfirmationAnswer[]) => void;
}) {
  const sourceNote =
    confirmation.source === "nl2sql"
      ? t("chat.confirmation.source.nl2sql")
      : confirmation.source === "rag"
        ? t("chat.confirmation.source.rag")
        : null;
  return (
    <ClarificationCard
      items={clarificationItems(confirmation)}
      variant="plain"
      round={confirmationRound(confirmation)}
      busyAction={busyAction}
      disabled={disabled}
      onConfirm={(answers) => onDecide("answer", confirmationAnswers(answers))}
      onSkip={confirmation.can_skip ? () => onDecide("skip") : undefined}
      onCancel={() => onDecide("cancel")}
      error={error ? <ApiErrorBanner error={error} fallback={t("chat.confirmation.failed")} testId="chat-confirmation-error" /> : null}
      actions={actions}
      idPrefix={`chat-${confirmation.id}`}
      testId="chat-confirmation"
    >
      {sourceNote ? (
        <p className="text-xs text-fg-muted" data-testid="chat-confirmation-source">
          {sourceNote}
        </p>
      ) : null}
    </ClarificationCard>
  );
}

/** 回答前に確認した内容（会話の利用者の側。3 製品共通の `ClarificationRecord` の吹き出し）。 */
export function ConfirmationRecordBubble({ record, testId }: { record: ConfirmationRecordView; testId: string }) {
  return <ClarificationRecord entries={record.entries} outcome={record.outcome} variant="bubble" testId={testId} />;
}
