import { Banner } from "../ui/banner";
import { MessageText } from "../ui/message-text";

export interface ChatGuardrailNoticeLabels {
  /** 質問の吹き出しの下の状態の文（`ChatPendingTurn` の `blockedLabel`）。 */
  status: string;
  /** backend の理由の文が無いときの理由。 */
  fallback: string;
  /** 次の操作（送り直しても同じ結果になること・書き換えて送ること）。質問を止めたとき。 */
  guidance: string;
  /** 回答前の確認への答え（自由入力）を止めたときの次の操作（#5205）。 */
  answerGuidance: string;
}

/**
 * 安全チェックが止めた質問の案内の文言（3 製品で同じ。#5191）。製品は i18n で上書きできるが、3 製品で同じ
 * 機能の文は同じにする（AGENTS.md「言語」）。
 */
export const DEFAULT_CHAT_GUARDRAIL_NOTICE_LABELS: ChatGuardrailNoticeLabels = {
  status: "安全チェックで停止しました",
  fallback: "安全チェックにより処理を停止しました。",
  guidance: "同じ質問を送り直しても同じ結果になります。入力欄で質問の内容を書き換えてから送信してください。",
  answerGuidance: "同じ答えを送り直しても同じ結果になります。答えの内容を書き換えてから送信してください。",
};

export interface ChatGuardrailNoticeProps {
  /** backend の理由の文（API の `error_messages[0]` / problem の `detail`。「安全チェックにより…。OCI Guardrails が…」）。 */
  message?: string | null;
  labels?: Partial<ChatGuardrailNoticeLabels>;
  /** 止めた入力。`answer`（回答前の確認への答え）は `answerGuidance` を出す（既定 `question`。#5205）。 */
  subject?: "question" | "answer";
  testId?: string;
}

/**
 * 安全チェック（OCI Guardrails）が止めた質問の、回答の場所の案内（3 製品共通。messaging.md §11.3。#5191）。
 *
 * 送信の失敗ではない（同じ質問を送り直しても必ず同じ結果になる）ので、再送信・確認の省略のボタンを出さず、
 * 理由（backend の文）と書き換えて送る案内だけを warning の `Banner` 1 つで出す（RAG の回答の安全チェックの
 * 警告と同じ warning）。製品は `ChatPendingTurn` の `blocked` に渡す。回答前の確認への答えを止めたときは確認の
 * カードの `error` に `subject="answer"` で、SQL 生成の画面（NL2SQL）は質問の欄の下に置く（#5205）。
 */
export function ChatGuardrailNotice({
  message,
  labels: labelOverrides,
  subject = "question",
  testId,
}: ChatGuardrailNoticeProps) {
  const labels = { ...DEFAULT_CHAT_GUARDRAIL_NOTICE_LABELS, ...labelOverrides };
  const reason = message?.trim() || labels.fallback;
  return (
    <div data-testid={testId} data-chat-guardrail-notice="">
      <Banner severity="warning">
        <p>
          <MessageText text={reason} />
        </p>
        <p>
          <MessageText text={subject === "answer" ? labels.answerGuidance : labels.guidance} />
        </p>
      </Banner>
    </div>
  );
}
