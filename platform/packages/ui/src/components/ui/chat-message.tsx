import { AlertCircle } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

/** 利用者のメッセージの状態（#907）。`sending` は送信の応答を待つ仮のメッセージ。 */
export type ChatUserMessageStatus = "sending" | "sent" | "failed" | "stopped";

export interface ChatUserMessageProps {
  /** 送った本文。改行はそのまま出す。 */
  children: ReactNode;
  /** 既定 `sent`。`failed` のときは吹き出しの下に `failedLabel` を出す。 */
  status?: ChatUserMessageStatus;
  /** 送信できなかったときの状態の文（翻訳済み。例:「送信できませんでした」）。 */
  failedLabel?: string;
  className?: string;
  testId?: string;
}

/**
 * チャットの利用者のメッセージ（右寄せの吹き出し）。3 製品のチャットで同じ形にする（#907）。
 *
 * - 送信した瞬間に出す（楽観的な表示）。送信中（`sending`）も見た目は送信後と同じにし、
 *   応答を待つ表示は回答の場所（`ProcessingIndicator`）の 1 つだけにする（スピナーを増やさない）。
 * - 送信できなかったとき（`failed`）は、吹き出しを残したまま下に状態の文をアイコン付きで出す
 *   （色だけに頼らない）。原因と「再送信」は製品が回答の場所の Banner に出す（messaging.md §9 P1 / P2）。
 * - 状態は `data-status` にも出す（e2e で状態を確かめる）。
 */
export function ChatUserMessage({
  children,
  status = "sent",
  failedLabel,
  className,
  testId,
}: ChatUserMessageProps) {
  return (
    <div
      className={cn("flex flex-col items-end gap-1", className)}
      data-status={status}
      data-testid={testId}
    >
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-md bg-accent-subtle px-3 py-2 text-sm text-fg">
        {children}
      </div>
      {status === "failed" && failedLabel ? (
        <p className="flex items-center gap-1 text-xs text-danger-fg">
          <AlertCircle size={14} aria-hidden className="shrink-0" />
          {failedLabel}
        </p>
      ) : null}
    </div>
  );
}
