import { useEffect, useRef } from "react";

import { Banner } from "../ui/banner";

export interface SaveErrorBannerProps {
  /** 保存の失敗の文言（原因 + 次の行動。i18n 済みの文字列か `ApiError.message`）。空なら何も描かない。 */
  message?: string | null;
  /**
   * 保存を試みるたびに変わる値（TanStack Query の `mutation.submittedAt` など）。同じ文言の失敗が
   * 続いたときも、Banner を画面に入れ直す。
   */
  attemptKey?: string | number;
  title?: string;
  testId?: string;
  className?: string;
}

/**
 * ヘッダー（`PageHeader`）に保存がある全画面のエディタの、欄に結び付かない保存の失敗（#585）。
 * UX 契約 messaging.md §3.3.1: `PageBody` の先頭（ヘッダーの直下）に 1 つだけ置き、同じ失敗を
 * Toast やフォームの下の `FormStatus` に重ねない。欄に結び付く失敗は欄の直下（`FieldError`）に出す。
 *
 * - danger の `Banner`（`role="alert"`）なので、出たときに読み上げる。フォーカスは動かさない。
 * - 長いフォームを下までスクロールして保存したときも気づけるよう、失敗が出たら Banner を画面に入れる。
 *   本文の先頭にあるため、ページは先頭（sticky のヘッダーの直下）まで戻る。
 * - 次の保存を始めるまで残す（呼び出し側は mutation の `error` をそのまま渡す）。
 */
export function SaveErrorBanner({ message, attemptKey, title, testId, className }: SaveErrorBannerProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!message) return;
    const element = ref.current;
    // jsdom / happy-dom には scrollIntoView が無いことがある。
    if (typeof element?.scrollIntoView !== "function") return;
    // 中央へ寄せると、先頭にある Banner のページは先頭まで戻り、sticky のヘッダーに隠れない。
    element.scrollIntoView({ block: "center", inline: "nearest" });
  }, [message, attemptKey]);

  if (!message) return null;
  return (
    <div ref={ref} data-testid={testId} className={className}>
      <Banner severity="danger" title={title}>
        {message}
      </Banner>
    </div>
  );
}
