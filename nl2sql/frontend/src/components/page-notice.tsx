import type { ReactNode } from "react";
import { useCallback, useState } from "react";

import type { FeedbackTone } from "@engchina/production-ready-ui";

import { Banner } from "@/components/ui/banner";

/**
 * ページ/セクション常設通知（Messaging Spec Channel 4 Banner）の正準状態。
 * tone は 4 トーン固定で "error" ではなく "danger" を使う。
 */
export type Notice = { tone: FeedbackTone; message: string } | null;

/**
 * 各ページで発散していた `{message, messageTone}` state を一元化する hook。
 * 空文字は null に正規化する（spec §9 P4: 空メッセージ面を作らない）。
 */
export function usePageNotice(initial: Notice = null) {
  const [notice, setNotice] = useState<Notice>(initial);
  const showNotice = useCallback((tone: FeedbackTone, message: string) => {
    setNotice(message ? { tone, message } : null);
  }, []);
  const clearNotice = useCallback(() => setNotice(null), []);
  return { notice, showNotice, clearNotice } as const;
}

/**
 * PageNotice（Channel 4 Banner）。PageHeader 直後・<main> 先頭の正準位置に置く。
 * notice が null / 空なら何も描画しない（P4 ガード。raw Banner は空でもアイコン箱を出す）。
 * action は JSX なので Notice state ではなく slot props で透過する。
 */
export function PageNotice({
  notice,
  onDismiss,
  action,
  className,
}: {
  notice: Notice;
  onDismiss?: () => void;
  action?: ReactNode;
  className?: string;
}) {
  if (!notice || !notice.message) return null;
  return (
    <Banner severity={notice.tone} action={action} onDismiss={onDismiss} className={className}>
      {notice.message}
    </Banner>
  );
}
