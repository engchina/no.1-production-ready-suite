import type { ReactNode } from "react";

import {
  DEFAULT_API_ERROR_DETAIL_LABELS,
  presentApiError,
  type ApiErrorDetail,
  type ApiErrorDetailLabels,
} from "../../lib/api-error";
import { cn } from "../../lib/utils";
import { Banner } from "../ui/banner";
import { Disclosure } from "../ui/disclosure";
import { ErrorState, type ErrorStateLayout } from "./state-views";

export interface ApiErrorDetailListProps {
  details: readonly ApiErrorDetail[];
  /** 折りたたみの見出し（既定「詳細」）。 */
  label?: string;
  /**
   * 最初から開くか（既定は開く）。処理・操作の失敗の面（`ApiErrorBanner`・処理の失敗のカード）は開いて出し、
   * 読み取りの失敗（`ApiErrorState`・`DataLoadFailureState`）は false を渡して閉じ、利用者が開く
   * （messaging.md §9 P3 / §10.3。#5904）。
   */
  defaultOpen?: boolean;
}

/**
 * 失敗の技術的な詳細の項目の並び（折りたたみを持たない中身）。開閉を自分で持つ部品（欄の直下の失敗の表示）が使う。
 * 項目が無ければ描かない。
 */
export function ApiErrorDetailItems({ details }: { details: readonly ApiErrorDetail[] }) {
  if (details.length === 0) return null;
  return (
    <dl className="flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
      {details.map((item) => (
        <div key={item.label} className="flex min-w-0 max-w-full flex-wrap items-baseline gap-x-1.5">
          <dt className="break-words font-medium text-fg">{item.label}</dt>
          <dd className="min-w-0 whitespace-pre-line [overflow-wrap:anywhere]">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * 失敗の技術的な詳細（要求・待ち時間の上限・HTTP ステータス・エラーコード・エラー種別・元の文・request ID）を
 * 「詳細」の折りたたみ（`Disclosure`）に並べる（UX 契約 messaging.md §9 P3 / §10.3）。項目が無ければ描かない。
 */
export function ApiErrorDetailList({
  details,
  label = DEFAULT_API_ERROR_DETAIL_LABELS.details,
  defaultOpen = true,
}: ApiErrorDetailListProps) {
  if (details.length === 0) return null;
  return (
    <Disclosure
      className="max-w-full open:basis-full"
      summaryClassName="whitespace-nowrap"
      variant="plain"
      size="sm"
      summary={label}
      defaultOpen={defaultOpen}
    >
      <ApiErrorDetailItems details={details} />
    </Disclosure>
  );
}

export interface ApiErrorBannerProps {
  /** API の失敗（`ApiTransportError`・製品の `ApiError`・その他の例外）。 */
  error: unknown;
  /** 要約に使える文が無いときの、画面の操作に合わせた文（「〜を読み込めませんでした。」）。 */
  fallback: string;
  /** 画面の操作に合わせた文で要約を差し替える。 */
  summary?: string;
  /** 画面の操作に合わせた文で次の操作を差し替える。 */
  nextAction?: string;
  /** Banner の右の操作（再試行など）。 */
  action?: ReactNode;
  testId?: string;
  className?: string;
  labels?: ApiErrorDetailLabels;
}

/**
 * 処理・操作の失敗（送信・実行・停止・保存の前の検査など、利用者が始めた操作の失敗）を danger の `Banner` で出す
 * （UX 契約 messaging.md §10.3。#900 / #906）。
 *
 * 1 文目に何が起きたか、次に次の操作を出し、技術的な詳細は「詳細」に畳んで開いて出す
 * （`SettingsTestResultPanel` の失敗と同じ形）。ブラウザの英語の文（`Failed to fetch` など）は要約に出さない。
 * 領域の読み取りの失敗（一覧・対象の候補・設定の値を読めなかった）は、この部品ではなく `ApiErrorState` で出す。
 */
export function ApiErrorBanner({
  error,
  fallback,
  summary,
  nextAction,
  action,
  testId,
  className,
  labels = DEFAULT_API_ERROR_DETAIL_LABELS,
}: ApiErrorBannerProps) {
  const presentation = presentApiError(error, fallback, labels);
  const next = nextAction ?? presentation.nextAction;
  return (
    <div className={cn("min-w-0", className)} data-testid={testId}>
      <Banner severity="danger" title={summary ?? presentation.summary} action={action}>
        {next || presentation.details.length > 0 ? (
          <div className="min-w-0 space-y-2">
            {next ? <p>{next}</p> : null}
            <ApiErrorDetailList details={presentation.details} label={labels.details} />
          </div>
        ) : null}
      </Banner>
    </div>
  );
}

export interface ApiErrorStateProps {
  /** 取得の失敗（`ApiTransportError`・製品の `ApiError`・その他の例外）。 */
  error: unknown;
  /** 要約に使える文が無いときの文（「〜を読み込めませんでした。」）。 */
  fallback: string;
  onRetry?: () => void;
  retryLabel?: string;
  /** 押した再試行の間だけ true（再試行のボタンが `loading` になる）。 */
  retrying?: boolean;
  /**
   * 「詳細」を最初から開くか。既定は閉じる（読み取りの失敗は利用者が開く。messaging.md §10.3）。
   * 同じ場所に処理の結果の失敗（回答の生成の失敗など）を出す画面だけ true を渡す。
   */
  detailsDefaultOpen?: boolean;
  /** 形。画面全体を占める失敗だけ `page`（`ErrorState` の `layout`。既定は `region`）。 */
  layout?: ErrorStateLayout;
  testId?: string;
  labels?: ApiErrorDetailLabels;
}

/**
 * 領域の取得の失敗（`ErrorState`。再試行付き）を、API の失敗の要約・次の操作・閉じた「詳細」で出す
 * （UX 契約 messaging.md §3.6 / §10.3。#906・#5904）。ブラウザの英語の文は「詳細」にだけ出す。
 */
export function ApiErrorState({
  error,
  fallback,
  onRetry,
  retryLabel,
  retrying,
  detailsDefaultOpen = false,
  layout,
  testId,
  labels = DEFAULT_API_ERROR_DETAIL_LABELS,
}: ApiErrorStateProps) {
  const presentation = presentApiError(error, fallback, labels);
  return (
    <ErrorState
      message={[presentation.summary, presentation.nextAction].filter(Boolean).join("")}
      onRetry={onRetry}
      retryLabel={retryLabel}
      retrying={retrying}
      layout={layout}
      testId={testId}
      details={<ApiErrorDetailList details={presentation.details} label={labels.details} defaultOpen={detailsDefaultOpen} />}
    />
  );
}
