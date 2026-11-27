import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ErrorState,
  FormGrid,
  FormStatus,
  Skeleton,
  StatusBadge,
  TextField,
  TimedLoadingState,
} from "@production-ready/ui";
import { Layers, RefreshCw } from "lucide-react";

import { useAdbInfo, useAidpIntegration } from "./hooks";
import { t, type DatabaseMessageKey } from "./messages";
import type {
  AidpDbUserStatus,
  AidpIntegrationData,
  DatabaseSettingsApi,
} from "./types";

const DB_USER_STATUS_VARIANT = {
  exists: "success",
  missing: "warning",
  unknown: "neutral",
} as const satisfies Record<AidpDbUserStatus, "success" | "warning" | "neutral">;

const DB_USER_STATUS_LABEL_KEYS: Record<AidpDbUserStatus, DatabaseMessageKey> =
  {
    exists: "settings.aidp.dbUser.status.exists",
    missing: "settings.aidp.dbUser.status.missing",
    unknown: "settings.aidp.dbUser.status.unknown",
  };

/** リンクにするのは https の URL だけ（API も https 以外を返さない）。 */
function httpsUrl(value: string): string | null {
  return /^https:\/\//i.test(value) ? value : null;
}

/** DB ユーザーが在ると確かめられなかったときの案内（在るときは出さない）。 */
function dbUserNotice(
  data: AidpIntegrationData,
): { tone: "warning" | "info"; message: string } | null {
  if (data.db_user_status === "exists") return null;
  if (data.db_user_status === "missing") {
    return {
      tone: "warning",
      message: t("settings.aidp.dbUser.missing", { user: data.db_user }),
    };
  }
  return {
    tone: "info",
    message: data.db_user
      ? t("settings.aidp.dbUser.checkFailed")
      : t("settings.aidp.dbUser.notSet"),
  };
}

/**
 * Oracle AI Data Platform の連携の情報（3 製品共通。読み取り専用。#5931）。
 *
 * - AIDP の URL（新しいタブで開くリンク）・OCID・リージョンと、External Catalog の登録に使う値（ADB の DB 名・
 *   サービス名・AIDP 用の DB ユーザーの名前）、そのユーザーが ADB に在るかを出す。
 * - 登録・編集の操作は持たない（External Catalog の登録は AIDP の画面で行う）。パスワードは API にも画面にも無い。
 * - 呼び出し側は、データベース設定の `aidp_configured` が true のときだけ描く。
 */
export function AidpIntegrationCard({
  api,
  errorMessage,
}: {
  api: DatabaseSettingsApi;
  errorMessage?: (error: unknown) => string | undefined;
}) {
  const query = useAidpIntegration(api);
  // ADB の DB 名は、上の「Autonomous Database 管理」が読んでいる ADB の情報（同じ query）を先に使い、
  // 取れなければ接続のサービス名から読んだ値にする。この区画のために OCI の API は呼ばない。
  const adbInfo = useAdbInfo(api);
  const data = query.data;
  // 設定の読み直しの間に未設定へ変わったら、区画ごと出さない。
  if (data && !data.configured) return null;

  const url = data ? httpsUrl(data.url) : null;
  const dbName = adbInfo.data?.db_name?.trim() || data?.adb_db_name || "";
  const notice = data ? dbUserNotice(data) : null;

  return (
    <Card
      id="aidp-integration"
      className="scroll-mt-4"
      data-testid="settings-aidp-card"
    >
      <CardHeader>
        {/* 見出しの行の右端に、カード全体を対象にする「情報を再取得」を置く（ADB の区画と同じ）。 */}
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <CardTitle className="flex flex-wrap items-center gap-2">
              <Layers size={16} className="text-accent-fg" aria-hidden />
              {t("settings.aidp.title")}
              {/* 対象の状態は見出しの StatusBadge（アイコン + 文字）で出す（messaging.md §10）。 */}
              {data ? (
                <StatusBadge
                  variant={DB_USER_STATUS_VARIANT[data.db_user_status]}
                  label={`${t("settings.aidp.dbUser.statusLabel")}: ${t(DB_USER_STATUS_LABEL_KEYS[data.db_user_status])}`}
                />
              ) : null}
            </CardTitle>
            <CardDescription>{t("settings.aidp.description")}</CardDescription>
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={query.isRefetching}
            disabled={query.isPending}
            onClick={() => void query.refetch()}
            icon={RefreshCw}
          >
            {t("settings.aidp.action.refresh")}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5">
        {query.isPending ? (
          // 最初の読み込みは経過時間付きで示し、下を内容の形の Skeleton で覆う（docs/agents/ui.md「読み込み中」）。
          <TimedLoadingState
            label={t("settings.aidp.loading")}
            operationKey="settings-aidp-info"
            testId="settings-aidp-loading"
          >
            <Skeleton className="h-24 w-full" />
          </TimedLoadingState>
        ) : null}

        {query.isError && !data ? (
          <ErrorState
            message={
              errorMessage?.(query.error) ?? t("settings.aidp.loadError")
            }
            onRetry={() => void query.refetch()}
            retrying={query.isRefetching}
          />
        ) : null}

        {query.isError && data ? (
          // 再取得の失敗は、前の内容を出したまま知らせる。
          <FormStatus
            tone="danger"
            className="text-xs"
            message={errorMessage?.(query.error) ?? t("settings.aidp.loadError")}
          />
        ) : null}

        {data ? (
          <>
            {/* URL・OCID は長い値なので全幅、リージョンは短い値（sm）。すべて読み取り専用（#1891）。 */}
            <FormGrid>
              <TextField
                id="aidp-url"
                label={t("settings.aidp.field.url")}
                value={data.url}
                readOnly
                width="full"
                className="col-span-full"
                placeholder={t("settings.aidp.placeholder.notSet")}
                helper={
                  url ? (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex min-h-11 items-center rounded-sm text-accent-fg underline underline-offset-4 hover:text-accent-fg"
                      data-testid="settings-aidp-open-link"
                    >
                      {t("settings.aidp.openLink")}
                    </a>
                  ) : (
                    // 環境変数名は長い 1 語なので、375px でも折り返せるようにする。
                    <span className="pr-break-anywhere">
                      {t("settings.aidp.urlNotSet")}
                    </span>
                  )
                }
              />
              <TextField
                id="aidp-ocid"
                label={t("settings.aidp.field.ocid")}
                value={data.ocid}
                readOnly
                width="full"
                className="col-span-full"
                helper={
                  // 環境変数名は長い 1 語なので、375px でも折り返せるようにする。
                  <span className="pr-break-anywhere">
                    {t("settings.aidp.helper.ocidReadonly")}
                  </span>
                }
              />
              <TextField
                id="aidp-region"
                label={t("settings.aidp.field.region")}
                value={data.region}
                readOnly
                width="sm"
                placeholder={t("settings.aidp.placeholder.notSet")}
              />
            </FormGrid>

            <div className="space-y-2">
              <span className="block text-sm font-medium text-fg">
                {t("settings.aidp.catalog.title")}
              </span>
              <p className="text-xs leading-relaxed text-fg-muted">
                {t("settings.aidp.catalog.description")}
              </p>
            </div>

            <FormGrid>
              <TextField
                id="aidp-catalog-db-name"
                label={t("settings.aidp.field.dbName")}
                value={dbName}
                readOnly
                width="md"
                placeholder={t("settings.aidp.placeholder.notSet")}
                helper={
                  dbName ? undefined : t("settings.aidp.helper.dbNameUnknown")
                }
              />
              <TextField
                id="aidp-catalog-service-name"
                label={t("settings.aidp.field.serviceName")}
                value={data.adb_service_name}
                readOnly
                width="lg"
                placeholder={t("settings.aidp.placeholder.notSet")}
              />
              <TextField
                id="aidp-catalog-db-user"
                label={t("settings.aidp.field.dbUser")}
                value={data.db_user}
                readOnly
                width="md"
                placeholder={t("settings.aidp.placeholder.notSet")}
              />
            </FormGrid>

            {notice ? (
              <FormStatus
                tone={notice.tone}
                className="text-xs"
                message={notice.message}
              />
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
