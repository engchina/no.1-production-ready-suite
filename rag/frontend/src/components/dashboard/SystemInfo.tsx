import { Link } from "react-router-dom";
import { ArrowRight, Settings } from "lucide-react";

import {
  Banner,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@engchina/production-ready-ui";
import { t, type I18nKey } from "@/lib/i18n";
import { formatNumber } from "@/lib/format";
import { APP_ROUTES } from "@/lib/routes";
import type { DashboardSystemInfo } from "@/lib/api";

const STATUS_PRESENTATION: Record<
  DashboardSystemInfo["status"],
  { labelKey: Parameters<typeof t>[0]; color: string }
> = {
  online: { labelKey: "dashboard.system.online", color: "text-success-fg" },
  degraded: { labelKey: "dashboard.system.degraded", color: "text-warning-fg" },
  offline: { labelKey: "dashboard.system.offline", color: "text-danger-fg" },
};

const DOT_COLOR: Record<DashboardSystemInfo["status"], string> = {
  online: "bg-success-emphasis",
  degraded: "bg-warning-emphasis",
  offline: "bg-danger-emphasis",
};

const CHECK_STATUS_LABELS: Record<string, I18nKey> = {
  ok: "settings.database.readiness.ok",
  missing: "settings.database.readiness.missing",
  missing_credentials: "settings.database.readiness.missingCredentials",
  invalid: "settings.database.readiness.invalid",
  wallet_not_found: "settings.database.readiness.walletNotFound",
  error: "settings.database.readiness.error",
  timeout: "dashboard.system.check.timeout",
};

/** システム情報パネル。 */
export function SystemInfo({ info }: { info: DashboardSystemInfo }) {
  const presentation = STATUS_PRESENTATION[info.status];
  const databaseCheckStatus = info.checks.dashboard_data ?? info.checks.oracle;
  const hasDatabaseIssue = Boolean(databaseCheckStatus && databaseCheckStatus !== "ok");
  const databaseCheckPresentation = databaseCheckStatus
    ? checkStatusPresentation(databaseCheckStatus)
    : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("dashboard.system.title")}</CardTitle>
        <CardDescription>{t("dashboard.system.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        {hasDatabaseIssue ? (
          <Banner
            severity="warning"
            title={t("dashboard.system.databaseDegraded.title")}
            action={
              <Link
                to={APP_ROUTES.settingsDatabase}
                className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-3 text-sm font-medium text-fg transition-colors hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
              >
                <Settings size={14} aria-hidden />
                {t("dashboard.system.openDatabaseSettings")}
                <ArrowRight size={14} aria-hidden />
              </Link>
            }
          >
            {t("dashboard.system.databaseDegraded.message")}
          </Banner>
        ) : null}
        <dl className={`space-y-3 text-sm ${hasDatabaseIssue ? "mt-4" : ""}`}>
          <div className="flex items-center justify-between">
            <dt className="text-fg-muted">{t("dashboard.system.serviceStatus")}</dt>
            <dd className={`flex items-center gap-1.5 font-medium ${presentation.color}`}>
              <span className={`size-2 rounded-full ${DOT_COLOR[info.status]}`} aria-hidden />
              {t(presentation.labelKey)}
            </dd>
          </div>
          {databaseCheckPresentation ? (
            <div className="flex items-center justify-between">
              <dt className="text-fg-muted">{t("dashboard.system.databaseStatus")}</dt>
              <dd
                className={`flex items-center gap-1.5 font-medium ${databaseCheckPresentation.color}`}
              >
                <span
                  className={`size-2 rounded-full ${databaseCheckPresentation.dotColor}`}
                  aria-hidden
                />
                {databaseCheckPresentation.label}
              </dd>
            </div>
          ) : null}
          <div className="flex items-center justify-between">
            <dt className="text-fg-muted">{t("dashboard.system.version")}</dt>
            <dd className="tnum font-medium text-fg">v{info.version}</dd>
          </div>
          <div className="flex items-center justify-between">
            <dt className="text-fg-muted">{t("dashboard.system.indexedRows")}</dt>
            <dd className="tnum font-medium text-fg">
              {formatNumber(info.searchable_rows)}
            </dd>
          </div>
        </dl>
        <p className="mt-4 border-t border-border pt-3 text-xs leading-relaxed text-fg-muted">
          {t("dashboard.system.hint")}
        </p>
      </CardContent>
    </Card>
  );
}

function checkStatusPresentation(status: string): {
  label: string;
  color: string;
  dotColor: string;
} {
  if (status === "ok") {
    return {
      label: t(CHECK_STATUS_LABELS.ok),
      color: "text-success-fg",
      dotColor: "bg-success-emphasis",
    };
  }
  if (status === "timeout" || status === "error") {
    return {
      label: t(CHECK_STATUS_LABELS[status]),
      color: "text-danger-fg",
      dotColor: "bg-danger-emphasis",
    };
  }
  const labelKey = CHECK_STATUS_LABELS[status];
  return {
    label: labelKey ? t(labelKey) : status,
    color: "text-warning-fg",
    dotColor: "bg-warning-emphasis",
  };
}
