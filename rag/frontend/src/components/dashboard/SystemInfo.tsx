import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { t } from "@/lib/i18n";
import { formatNumber } from "@/lib/format";
import type { DashboardSystemInfo } from "@/lib/api";

const STATUS_PRESENTATION: Record<
  DashboardSystemInfo["status"],
  { labelKey: Parameters<typeof t>[0]; color: string }
> = {
  online: { labelKey: "dashboard.system.online", color: "text-success" },
  degraded: { labelKey: "dashboard.system.degraded", color: "text-warning" },
  offline: { labelKey: "dashboard.system.offline", color: "text-danger" },
};

const DOT_COLOR: Record<DashboardSystemInfo["status"], string> = {
  online: "bg-success",
  degraded: "bg-warning",
  offline: "bg-danger",
};

/** システム情報パネル。 */
export function SystemInfo({ info }: { info: DashboardSystemInfo }) {
  const presentation = STATUS_PRESENTATION[info.status];
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("dashboard.system.title")}</CardTitle>
        <CardDescription>{t("dashboard.system.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <dt className="text-muted">{t("dashboard.system.serviceStatus")}</dt>
            <dd className={`flex items-center gap-1.5 font-medium ${presentation.color}`}>
              <span className={`size-2 rounded-full ${DOT_COLOR[info.status]}`} aria-hidden />
              {t(presentation.labelKey)}
            </dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-muted">{t("dashboard.system.version")}</dt>
            <dd className="tnum font-medium text-foreground">v{info.version}</dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-muted">{t("dashboard.system.indexedRows")}</dt>
            <dd className="tnum font-medium text-foreground">
              {formatNumber(info.searchable_rows)}
            </dd>
          </div>
        </dl>
        <p className="mt-4 border-t border-border pt-3 text-xs leading-relaxed text-muted">
          {t("dashboard.system.hint")}
        </p>
      </CardContent>
    </Card>
  );
}
