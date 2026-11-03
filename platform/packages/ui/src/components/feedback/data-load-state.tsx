import { RefreshCw } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import {
  DEFAULT_API_ERROR_DETAIL_LABELS,
  presentApiError,
  type ApiErrorDetailLabels,
} from "../../lib/api-error";
import { dataLoadFailureKind, focusDataLoadRecovery } from "../../lib/data-load";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";
import { ApiErrorDetailList } from "./api-error-banner";
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
  /** 時間の上限・接続の失敗以外で、要約に使える文が無いときの文（「〜を読み込めませんでした。」）。 */
  fallback: string;
  labels: DataLoadFailureLabels;
  onRetry: () => void;
  /**
   * 接続の失敗のときだけ再試行の前に出す導線（データベース設定へのリンク）。
   * 開ける利用者のときだけ渡す（押しても開けない導線を出さない。messaging.md §3.4.1）。
   */
  databaseAction?: ReactNode;
  detailLabels?: ApiErrorDetailLabels;
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
  testId,
}: DataLoadFailureStateProps) {
  const kind = dataLoadFailureKind(error);
  const presentation = presentApiError(error, fallback, detailLabels);
  const action = kind === "unavailable" ? databaseAction : undefined;
  const message =
    failureMessage(error, labels, Boolean(action)) ??
    [presentation.summary, presentation.nextAction].filter(Boolean).join("");
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
