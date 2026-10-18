// NL2SQL のチャットの画面の共通部分（#2264）。「チャット」（SqlChatPage）と「DB 管理チャット」
// （DbAdminChatPage）が同じ部品・同じ文言で描くために使う。骨格・入力欄・送信中の往復・SQL の
// 表示は 3 製品共通の部品（ChatLayout / ChatComposer / ChatPendingTurn / ChatProgress）を通し、
// ここでは NL2SQL の文言と組み合わせだけを持つ（2 つの画面で書き写さない）。
import type { ReactNode } from "react";
import { RotateCcw } from "lucide-react";
import {
  ApiErrorBanner,
  Button,
  ChatComposer,
  ChatLayout,
  ChatPendingTurn,
  ChatProgress,
  CopyButton,
  type ChatAutoScroll,
  type ChatComposerProps,
  type ChatHistoryPanel,
  type ChatProgressStep,
  type OptimisticChatMessage,
} from "@production-ready/ui";

import { isTransportError } from "@/lib/api";
import { t } from "@/lib/i18n";

import { CHAT_PROGRESS_LABELS, chatSubmitProgressSteps } from "../chatProgress";

/** 送信の応答が届かなかったときは、ジョブが作られている可能性と確かめ方を出す。 */
export function sendFailureText(error: unknown) {
  if (!isTransportError(error) || error.kind !== "timeout" || !error.timeoutMs) return {};
  return {
    summary: t("chat.sendTimeout", { seconds: Math.ceil(error.timeoutMs / 1000) }),
    nextAction: t("chat.sendTimeout.action"),
  };
}

/** 会話の履歴のパネル（保存しない会話の画面では渡さない）。 */
export interface Nl2SqlChatHistory {
  panel: ChatHistoryPanel;
  content: ReactNode;
  toggleDisabled?: boolean;
}

/**
 * NL2SQL のチャットの骨格（共通の `ChatLayout` に NL2SQL の文言を付ける）。会話を保存しない画面は
 * `history` を渡さない（履歴のパネルと開閉ボタンを出さない）。
 */
export function Nl2SqlChatLayout({
  history,
  label,
  conversationTitle,
  conversationTitleLoading,
  onNewConversation,
  newConversationDisabled,
  autoScroll,
  testIdPrefix,
  composer,
  children,
}: {
  history?: Nl2SqlChatHistory;
  /** 会話の領域の名前（翻訳済み）。 */
  label: string;
  conversationTitle?: string | null;
  conversationTitleLoading?: boolean;
  onNewConversation: () => void;
  newConversationDisabled?: boolean;
  autoScroll: ChatAutoScroll;
  testIdPrefix: string;
  composer: ReactNode;
  children: ReactNode;
}) {
  const historyProps = history
    ? {
        history: history.panel,
        historyTitle: t("chat.history"),
        historyCloseLabel: t("chat.closeHistory"),
        historyContent: history.content,
        historyToggleDisabled: history.toggleDisabled,
      }
    : {};
  return (
    <ChatLayout
      {...historyProps}
      label={label}
      conversationTitle={conversationTitle}
      conversationTitleLoading={conversationTitleLoading}
      newConversation={{
        label: t("chat.new"),
        onClick: onNewConversation,
        disabled: newConversationDisabled,
      }}
      logLabel={t("chat.messages")}
      logRef={autoScroll.logRef}
      latest={{
        visible: autoScroll.showLatest,
        label: t("chat.latest"),
        onClick: () => autoScroll.scrollToLatest("smooth"),
      }}
      testIdPrefix={testIdPrefix}
      composer={composer}
    >
      {children}
    </ChatLayout>
  );
}

/**
 * NL2SQL のチャットの入力欄（共通の `ChatComposer`。Enter で送信・Shift+Enter で改行・送信中は停止）。
 * 名前・placeholder・送信 / 停止の文言と、入力の上限は 2 つの画面で同じ。
 */
export function Nl2SqlChatComposer(
  props: Omit<ChatComposerProps, "label" | "placeholder" | "sendLabel" | "stopLabel" | "maxLength">,
) {
  return (
    <ChatComposer
      {...props}
      label={t("chat.query")}
      placeholder={t("chat.placeholder")}
      sendLabel={t("chat.send")}
      stopLabel={t("chat.stop")}
      maxLength={10000}
    />
  );
}

/**
 * 送った質問（応答の前・送れなかったとき）。共通の `ChatPendingTurn` に、処理の段階（既定は「質問を
 * 送信しています」）と、送れなかったときの原因と「再送信」を付ける。
 */
export function Nl2SqlPendingTurn({
  message,
  error,
  fallback,
  onResend,
  resendDisabled,
  steps,
  testIdPrefix,
}: {
  message: OptimisticChatMessage;
  /** 送れなかったときの原因（送信中・送れたときは null）。 */
  error: unknown;
  /** 原因の文が無いときの文（翻訳済み）。 */
  fallback: string;
  onResend: () => void;
  resendDisabled?: boolean;
  /** 応答を待つ間の段階（省略時は「質問を送信しています」）。 */
  steps?: ChatProgressStep[];
  testIdPrefix: string;
}) {
  return (
    <ChatPendingTurn
      message={message}
      failedLabel={t("chat.sendFailedStatus")}
      testId={`${testIdPrefix}-pending-turn`}
      progress={
        <ChatProgress
          key={message.localId}
          steps={steps ?? chatSubmitProgressSteps(message.sentAtMs)}
          labels={CHAT_PROGRESS_LABELS}
          testId={`${testIdPrefix}-progress`}
        />
      }
      failure={
        error ? (
          // 失敗の理由は送った質問の直下（会話の中）に出す（messaging.md §10.1 のチャットの扱い）。
          <ApiErrorBanner
            error={error}
            fallback={fallback}
            testId={`${testIdPrefix}-send-error`}
            {...sendFailureText(error)}
            action={
              <Button
                type="button"
                variant="secondary"
                size="sm"
                icon={RotateCcw}
                disabled={resendDisabled}
                onClick={onResend}
              >
                {t("chat.resend")}
              </Button>
            }
          />
        ) : null
      }
    />
  );
}

/**
 * 回答の中の SQL（状態のバッジ・コピー・操作と、SQL の本文）。チャットと DB 管理チャットで同じ形にする。
 * 操作（「実行」など）は吹き出しの中の操作なので secondary の sm を渡す。
 */
export function ChatSqlBlock({
  sql,
  badges,
  actions,
  onCopied,
  onCopyError,
  testId,
}: {
  sql: string;
  /** 左の状態のバッジ（`StatusBadge`）。 */
  badges: ReactNode;
  /** コピーの右の操作。 */
  actions?: ReactNode;
  onCopied?: () => void;
  onCopyError?: (message: string) => void;
  testId?: string;
}) {
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">{badges}</div>
        <div className="flex flex-wrap items-center gap-2">
          <CopyButton
            size="sm"
            text={sql}
            label={t("chat.copySql")}
            copiedMessage={t("chat.sqlCopied")}
            failedMessage={t("chat.copyFailed")}
            onCopied={onCopied}
            onCopyError={onCopyError}
          />
          {actions}
        </div>
      </div>
      <pre
        className="max-w-full overflow-x-auto rounded-md border border-border bg-canvas p-3 text-sm [scrollbar-gutter:stable]"
        data-testid={testId}
      >
        <code>{sql}</code>
      </pre>
    </>
  );
}
