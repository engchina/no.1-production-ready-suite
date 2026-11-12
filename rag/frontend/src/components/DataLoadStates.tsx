import { DatabaseSettingsLink } from "@production-ready/system-settings";
import { DataLoadFailureState, StaleDataNotice, type DataLoadFailureLabels } from "@production-ready/ui";

import {
  DATABASE_GATE_ROUTES,
  databaseGateMessages,
  useDatabaseGatePermissions,
} from "@/components/system/DatabaseGate";
import { formatDateTime } from "@/lib/format";
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

/** データベース設定へのリンク。その画面を開ける利用者だけに出す（messaging.md §3.4.1）。 */
function useDatabaseAction() {
  const { canManageDatabase } = useDatabaseGatePermissions();
  return canManageDatabase ? (
    <DatabaseSettingsLink routes={DATABASE_GATE_ROUTES} messages={databaseGateMessages()} />
  ) : undefined;
}

/** 内容が無いまま読み取りに失敗したときの表示（空の状態と同じ分岐で出さない。#4217）。 */
export function DataLoadFailure({
  error,
  fallback,
  onRetry,
  layout,
  testId,
  retrying,
}: {
  error: unknown;
  fallback: string;
  onRetry: () => void;
  /**
   * `field` は選択欄の候補・欄の値など、欄を支える読み取りの失敗（欄の直下の 1 行。#4222・#4817）。
   * `fallback` が見出しになるので、欄ごとの文（何を読めなかったか）を渡す。
   */
  layout?: "region" | "field";
  testId?: string;
  retrying?: boolean;
}) {
  const databaseAction = useDatabaseAction();
  return (
    <DataLoadFailureState
      error={error}
      fallback={fallback}
      labels={dataLoadLabels()}
      onRetry={onRetry}
      databaseAction={databaseAction}
      layout={layout}
      testId={testId}
      retrying={retrying}
    />
  );
}

/** 前回の内容を残したまま、いつの内容かと失敗の原因を示す（#4217）。 */
export function DataLoadStaleNotice({
  error,
  dataUpdatedAt,
  onRetry,
  retrying,
  testId,
}: {
  error: unknown;
  /** TanStack Query の `dataUpdatedAt`（ミリ秒）。 */
  dataUpdatedAt: number;
  onRetry: () => void;
  retrying?: boolean;
  testId?: string;
}) {
  const databaseAction = useDatabaseAction();
  return (
    <StaleDataNotice
      error={error}
      updatedAt={formatDateTime(new Date(dataUpdatedAt).toISOString())}
      labels={dataLoadLabels()}
      onRetry={onRetry}
      retrying={retrying}
      databaseAction={databaseAction}
      testId={testId}
    />
  );
}
