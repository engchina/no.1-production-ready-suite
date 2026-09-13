import { Button, PageHeader } from "@engchina/production-ready-ui";
import { RefreshCw } from "lucide-react";

import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/format";

/** ダッシュボードのヘッダー（タイトル + 最終更新 + 更新ボタン）。 */
export function DashboardHeader({
  onRefresh,
  isRefreshing,
  updatedAt,
}: {
  onRefresh: () => void;
  isRefreshing: boolean;
  updatedAt: string | null;
}) {
  return (
    <PageHeader
      title={t("dashboard.title")}
      subtitle={t("dashboard.subtitle")}
      actions={
        <>
          {updatedAt ? (
            <span className="tnum text-xs text-fg-muted">
              {t("dashboard.lastUpdated")} {formatDateTime(updatedAt)}
            </span>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={RefreshCw}
            loading={isRefreshing}
            onClick={onRefresh}
          >
            {t("dashboard.refresh")}
          </Button>
        </>
      }
    />
  );
}
