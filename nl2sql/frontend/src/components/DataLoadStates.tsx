import { DatabaseSettingsLink } from "@production-ready/system-settings";
import { DataLoadFailureState, type DataLoadFailureLabels } from "@production-ready/ui";

import {
  DATABASE_GATE_ROUTES,
  databaseGateMessages,
  useDatabaseGatePermissions,
} from "@/components/system/DatabaseUnavailableNotice";
import { t } from "@/lib/i18n";

function dataLoadLabels(): DataLoadFailureLabels {
  return {
    timeout: t("common.dataLoad.timeout"),
    unavailable: t("common.dataLoad.unavailable"),
    unavailableRestricted: t("common.dataLoad.unavailableRestricted"),
    retry: t("common.retry"),
    staleTitle: (time) => t("common.dataLoad.staleTitle", { time }),
    staleOther: t("common.dataLoad.staleOther"),
  };
}

/**
 * 読み取りの失敗の表示（RAG・Agent の `DataLoadFailure` と同じ共有の部品。messaging.md §3.6.1。#4817）。
 * `layout="field"` は選択欄の候補・欄の値など、欄を支える読み取りの失敗（欄の直下の 1 行）。
 * 手書きの読み取り（文だけを持つ state）の失敗は、文を `error` に渡す（「詳細」に入る）。
 */
export function DataLoadFailure({
  error,
  fallback,
  onRetry,
  layout,
  testId,
}: {
  /** 取得の失敗（`ApiError` など）か、失敗の文。 */
  error: unknown;
  /** 何を読めなかったかの文（欄ごとの文）。 */
  fallback: string;
  onRetry: () => void;
  layout?: "region" | "field";
  testId?: string;
}) {
  const { canManageDatabase } = useDatabaseGatePermissions();
  return (
    <DataLoadFailureState
      error={typeof error === "string" ? new Error(error) : error}
      fallback={fallback}
      labels={dataLoadLabels()}
      onRetry={onRetry}
      // データベース設定へのリンクは、その画面を開ける利用者だけに出す（messaging.md §3.4.1）。
      databaseAction={
        canManageDatabase ? (
          <DatabaseSettingsLink routes={DATABASE_GATE_ROUTES} messages={databaseGateMessages()} />
        ) : undefined
      }
      layout={layout}
      testId={testId}
    />
  );
}
