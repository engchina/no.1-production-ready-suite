import { FileStack, Search, Upload, type LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { t, type I18nKey } from "@/lib/i18n";
import { formatNumber } from "@/lib/format";
import type { DashboardStats } from "@/lib/api";

interface Metric {
  labelKey: I18nKey;
  value: number;
  icon: LucideIcon;
  sub?: string;
}

/** メトリクスカード群。数値は等幅数字（tnum）で表示。 */
export function MetricCards({ stats }: { stats: DashboardStats }) {
  const metrics: Metric[] = [
    {
      labelKey: "dashboard.metric.totalUploads",
      value: stats.total_uploads,
      icon: Upload,
      sub: t("dashboard.metric.thisMonth", { count: stats.uploads_this_month }),
    },
    {
      labelKey: "dashboard.metric.totalIndexed",
      value: stats.total_indexed,
      icon: FileStack,
      sub: t("dashboard.metric.thisMonth", { count: stats.indexed_this_month }),
    },
    {
      labelKey: "dashboard.metric.searchableRows",
      value: stats.searchable_rows,
      icon: Search,
    },
  ];

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {metrics.map((m) => {
        const Icon = m.icon;
        return (
          <Card key={m.labelKey} className="p-5">
            <div className="flex items-start justify-between">
              <span className="text-xs font-medium text-muted">{t(m.labelKey)}</span>
              <span className="flex size-8 items-center justify-center rounded-md bg-info-bg text-info">
                <Icon size={16} aria-hidden />
              </span>
            </div>
            <div className="tnum mt-3 text-2xl font-bold leading-none text-foreground">
              {formatNumber(m.value)}
            </div>
            {m.sub ? <div className="mt-2 text-xs text-muted">{m.sub}</div> : null}
          </Card>
        );
      })}
    </div>
  );
}
