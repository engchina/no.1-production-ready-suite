import { AlertCircle, Inbox, RefreshCw } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { focusDataLoadRecovery } from "../../lib/data-load";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { MessageText } from "../ui/message-text";
import { Skeleton } from "../ui/skeleton";
import { TimedLoadingState } from "./processing-state";

/**
 * 読込状態。ほかの読み込み中と同じく、領域の先頭に処理の名前と経過時間の行（`TimedLoadingState`）を出し、
 * その下を行の形の Skeleton で覆う（#2510）。1 秒超の取得はブロッキングスピナーでなくこれを使う
 * （progressive-loading）。領域寸法を予約して CLS を防ぐため、行数を rows で指定する。
 */
export function LoadingState({
  label,
  rows = 3,
  operationKey,
  testId,
}: {
  label: string;
  rows?: number;
  operationKey?: string | number | null;
  testId?: string;
}) {
  return (
    <TimedLoadingState label={label} operationKey={operationKey} testId={testId}>
      <div className="flex flex-col gap-2" aria-hidden="true">
        {Array.from({ length: rows }).map((_, index) => (
          <Skeleton key={index} className="h-5 w-full" />
        ))}
      </div>
    </TimedLoadingState>
  );
}

/**
 * `ErrorState` の形（messaging.md §3.6）。
 * - `region`（既定）: カード・一覧・パネル・`PageBody` の中の読み取りの失敗。左揃えの帯で、本文の隣に回復操作と
 *   「詳細」を並べ、狭い幅では折り返す（データ領域の面積を減らさない。#5904）。
 * - `page`: 画面全体を占める失敗（`AppShell` の外で、画面にその失敗しか無いとき。ログインの状態の確認の失敗）。
 *   中央寄せの大きい形（24px のアイコン・広い余白）で、本文・回復操作・「詳細」を縦に並べる（#5929）。
 */
export type ErrorStateLayout = "region" | "page";

/**
 * エラー状態（再試行ボタン付き）。
 * 再試行ラベルは i18n をパッケージに持ち込まないため `retryLabel` で注入（既定「再試行」）。
 * 並びは形（`layout`）にかかわらず本文 → 回復操作 → 「詳細」（Tab 順も同じ）。role・再試行へのフォーカスも同じ。
 */
export function ErrorState({
  message,
  onRetry,
  retryLabel = "再試行",
  details,
  action,
  focusRetry = false,
  retrying = false,
  layout = "region",
  testId,
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  /**
   * 技術的な詳細（`ApiErrorDetailList`）。回復操作の後に出す。読み取りの失敗では閉じて渡し、利用者が開く
   * （messaging.md §10.3。#906・#5904）。開くと枠の全幅に左揃えで並ぶ。
   */
  details?: ReactNode;
  /** 再試行の前に置く導線（データベース設定へのリンクなど。messaging.md §3.4.1 の Tab 順）。 */
  action?: ReactNode;
  /** 出たときに再試行へフォーカスを移す（入力中の欄からは奪わない）。データ領域の読み取りの失敗で使う。 */
  focusRetry?: boolean;
  retrying?: boolean;
  /** 形。画面全体を占める失敗だけ `page`（既定は `region`）。 */
  layout?: ErrorStateLayout;
  testId?: string;
}) {
  const retryRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (focusRetry) focusDataLoadRecovery(retryRef.current);
  }, [focusRetry]);
  const page = layout === "page";
  return (
    <div
      role="alert"
      data-surface-tint="danger"
      data-failure-layout={layout}
      data-testid={testId}
      className={cn(
        "flex min-w-0 rounded-lg border border-danger-border bg-danger-subtle",
        page ? "flex-col items-center gap-3 p-8 text-center" : "flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left",
      )}
    >
      <div className={cn("flex min-w-0", page ? "max-w-full flex-col items-center gap-3" : "items-start gap-2")}>
        <AlertCircle size={page ? 24 : 16} className={cn("shrink-0 text-danger-fg", !page && "mt-0.5")} aria-hidden />
        <p className="min-w-0 max-w-full text-sm leading-relaxed text-fg [overflow-wrap:anywhere]">
          <MessageText text={message} />
        </p>
      </div>
      {action || onRetry ? (
        <div className={cn("flex flex-wrap items-center gap-2", page && "justify-center")}>
          {action}
          {onRetry ? (
            <Button ref={retryRef} type="button" variant="secondary" size="sm" icon={RefreshCw} loading={retrying} onClick={onRetry}>
              <span>{retryLabel}</span>
            </Button>
          ) : null}
        </div>
      ) : null}
      {/* page は縦の並びなので、閉じた「詳細」を中央に、開いた「詳細」を全幅の左揃えにする行で包む。 */}
      {page && details ? <div className="flex w-full min-w-0 flex-wrap justify-center text-left">{details}</div> : details}
    </div>
  );
}

/** 空状態。任意で操作（作成導線など）を添える。 */
export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-1 py-10 text-center">
      <Inbox size={24} className="text-fg-muted" aria-hidden />
      <p className="mt-1 text-sm leading-relaxed text-fg">
        <MessageText text={title} />
      </p>
      {hint ? (
        <p className="max-w-md text-xs leading-relaxed text-fg-muted">
          <MessageText text={hint} />
        </p>
      ) : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
