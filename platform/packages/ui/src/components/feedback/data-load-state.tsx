import { AlertCircle, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import {
  DEFAULT_API_ERROR_DETAIL_LABELS,
  presentApiError,
  type ApiErrorDetail,
  type ApiErrorDetailLabels,
} from "../../lib/api-error";
import { dataLoadFailureKind, focusDataLoadRecovery, type DataLoadFailureKind } from "../../lib/data-load";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";
import { MessageText } from "../ui/message-text";
import { ApiErrorDetailItems, ApiErrorDetailList } from "./api-error-banner";
import { ErrorState } from "./state-views";

/** 読み取りの失敗の文言（製品の i18n を渡す）。 */
export interface DataLoadFailureLabels {
  /** 時間の上限。接続の問題と言い切らず、データが失われていないことと次の操作を示す。 */
  timeout: string;
  /** 接続の失敗（データベース設定を開ける利用者向け）。 */
  unavailable: string;
  /** 接続の失敗（データベース設定を開けない利用者向け。管理者への連絡を案内する）。 */
  unavailableRestricted: string;
  retry: string;
  /** 前回の内容を出しているときの見出し（`time` は整形済みの日時）。 */
  staleTitle: (time: string) => string;
  /** 前回の内容を出しているときの、時間の上限と接続の失敗以外の本文。 */
  staleOther: string;
}

export interface DataLoadFailureStateProps {
  /** 取得の失敗（製品の `ApiError` など）。 */
  error: unknown;
  /**
   * 何を読めなかったかの文（「〜を読み込めませんでした。」）。`region` では、時間の上限・接続の失敗以外で要約に
   * 使える文が無いときに出す。`field` では常に見出しになる（欄ごとの文を必ず渡す。#4817）。
   */
  fallback: string;
  labels: DataLoadFailureLabels;
  onRetry: () => void;
  /**
   * 接続の失敗のときだけ再試行の前に出す導線（データベース設定へのリンク）。
   * 開ける利用者のときだけ渡す（押しても開けない導線を出さない。messaging.md §3.4.1）。
   */
  databaseAction?: ReactNode;
  detailLabels?: ApiErrorDetailLabels;
  /**
   * 出し方。`region`（既定）はデータ領域を占める `ErrorState`。`field` は選択欄の候補・欄の値など、画面の一部の
   * 欄を支える読み取りの失敗で、欄の直下に収まる 1 行の表示（#4222・#4817）: danger のアイコンと `fallback`
   * （欄ごとの文）・「詳細」・導線・再試行。原因の文（backend の文・時間の上限・接続の失敗）と技術的な情報は
   * 畳んだ「詳細」に入れる。`field` はフォーカスを動かさない。
   */
  layout?: "region" | "field";
  testId?: string;
}

function failureMessage(
  error: unknown,
  labels: DataLoadFailureLabels,
  hasDatabaseAction: boolean,
): string | null {
  const kind = dataLoadFailureKind(error);
  if (kind === "timeout") return labels.timeout;
  if (kind === "unavailable") return hasDatabaseAction ? labels.unavailable : labels.unavailableRestricted;
  return null;
}

/**
 * データ領域の読み取りの失敗（messaging.md §3.6.1。#4217）。領域だけを占め、再試行へフォーカスを移す。
 * 0 件の空の状態と同じ分岐で出さない。原因は `error_code` で分ける（`dataLoadFailureKind`）。
 */
export function DataLoadFailureState({
  error,
  fallback,
  labels,
  onRetry,
  databaseAction,
  detailLabels = DEFAULT_API_ERROR_DETAIL_LABELS,
  layout = "region",
  testId,
}: DataLoadFailureStateProps) {
  const kind = dataLoadFailureKind(error);
  const presentation = presentApiError(error, fallback, detailLabels);
  const action = kind === "unavailable" ? databaseAction : undefined;
  const causeMessage = failureMessage(error, labels, Boolean(action));
  if (layout === "field") {
    // 見出しは欄ごとの文。原因の文は「詳細」に入れる（見出しと同じ文は重ねない）。
    const cause =
      causeMessage ??
      [presentation.summary === fallback ? "" : presentation.summary, presentation.nextAction].filter(Boolean).join("");
    return (
      <FieldLoadFailure
        message={fallback}
        cause={cause}
        details={presentation.details}
        detailsLabel={detailLabels.details}
        retryLabel={labels.retry}
        onRetry={onRetry}
        action={action}
        kind={kind}
        testId={testId}
      />
    );
  }
  const message = causeMessage ?? [presentation.summary, presentation.nextAction].filter(Boolean).join("");
  return (
    <div data-testid={testId} data-failure-kind={kind}>
      <ErrorState
        message={message}
        onRetry={onRetry}
        retryLabel={labels.retry}
        focusRetry
        action={action}
        details={<ApiErrorDetailList details={presentation.details} label={detailLabels.details} />}
      />
    </div>
  );
}

/**
 * 欄を支える読み取りの失敗（`layout="field"`。#4817）。欄の直下の 1 行: danger のアイコンと欄ごとの文、
 * 「詳細」の開閉、導線、再試行。狭い幅では操作の組が文の下へ折り返す。
 *
 * - 読み上げるのは見出しの文だけ（`role="alert"`）。操作は見出しの文を説明として持つ（複数の欄が同時に失敗しても
 *   どの欄の再試行かが分かる）。
 * - フォーカスは動かさない（欄の操作から奪わない。離れた欄へ画面を動かさない）。
 * - 「詳細」は button + region（`aria-expanded` / `aria-controls`）。開いても見出しの行の操作の位置は変わらない。
 */
function FieldLoadFailure({
  message,
  cause,
  details,
  detailsLabel,
  retryLabel,
  onRetry,
  action,
  kind,
  testId,
}: {
  message: string;
  cause: string;
  details: readonly ApiErrorDetail[];
  detailsLabel: string;
  retryLabel: string;
  onRetry: () => void;
  action?: ReactNode;
  kind: DataLoadFailureKind;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const messageId = `${id}-message`;
  const regionId = `${id}-details`;
  const hasDetails = Boolean(cause) || details.length > 0;
  return (
    <div
      className="min-w-0 text-sm"
      data-testid={testId}
      data-failure-kind={kind}
      data-failure-layout="field"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <p
          id={messageId}
          role="alert"
          className="pr-message-text flex min-w-0 items-start gap-1.5 font-medium leading-relaxed text-danger-fg"
        >
          <span className="pr-icon-line">
            <AlertCircle size={16} aria-hidden />
          </span>
          <MessageText text={message} />
        </p>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {hasDetails ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              trailingIcon={open ? ChevronDown : ChevronRight}
              aria-expanded={open}
              aria-controls={regionId}
              aria-describedby={messageId}
              onClick={() => setOpen((current) => !current)}
            >
              {detailsLabel}
            </Button>
          ) : null}
          {action}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={RefreshCw}
            aria-describedby={messageId}
            onClick={onRetry}
          >
            {retryLabel}
          </Button>
        </div>
      </div>
      {hasDetails ? (
        <div
          id={regionId}
          hidden={!open}
          className="mt-1 grid min-w-0 gap-2 rounded-md border border-border bg-surface-sunken p-3"
        >
          {cause ? (
            <p className="leading-relaxed text-fg">
              <MessageText text={cause} />
            </p>
          ) : null}
          <ApiErrorDetailItems details={details} />
        </div>
      ) : null}
    </div>
  );
}

export interface StaleDataNoticeProps {
  /** 最新の取得の失敗。 */
  error: unknown;
  /** 表示中の内容を読み取った日時（整形済み）。 */
  updatedAt: string;
  labels: DataLoadFailureLabels;
  onRetry: () => void;
  /** 押した再試行の間だけ true。 */
  retrying?: boolean;
  /** 接続の失敗のときだけ再試行の前に出す導線（開ける利用者のときだけ渡す）。 */
  databaseAction?: ReactNode;
  testId?: string;
}

/**
 * 前回の内容を残したまま、最新の読み取りに失敗したことを示す（messaging.md §3.6.1。#4217）。
 * 内容は読める（warning）ので、読みかけや操作中のフォーカスは動かさない。
 */
export function StaleDataNotice({
  error,
  updatedAt,
  labels,
  onRetry,
  retrying = false,
  databaseAction,
  testId,
}: StaleDataNoticeProps) {
  const retryRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    focusDataLoadRecovery(retryRef.current, { onlyIfUnfocused: true });
  }, []);
  const kind = dataLoadFailureKind(error);
  const action = kind === "unavailable" ? databaseAction : undefined;
  return (
    <div data-testid={testId} data-failure-kind={kind}>
      <Banner
        severity="warning"
        title={labels.staleTitle(updatedAt)}
        action={
          <>
            {action}
            <Button
              ref={retryRef}
              type="button"
              variant="secondary"
              size="sm"
              icon={RefreshCw}
              loading={retrying}
              onClick={onRetry}
            >
              {labels.retry}
            </Button>
          </>
        }
      >
        {failureMessage(error, labels, Boolean(action)) ?? presentApiError(error, labels.staleOther).summary}
      </Banner>
    </div>
  );
}
