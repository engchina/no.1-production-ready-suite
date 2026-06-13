import { FileStack, Upload } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState } from "@/components/StateViews";
import { t } from "@/lib/i18n";
import { formatDateTime } from "@/lib/format";
import type { DashboardActivity } from "@/lib/api";

/** 最近のアクティビティ一覧。空状態にも対応。 */
export function RecentActivity({ activities }: { activities: DashboardActivity[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("dashboard.activity.title")}</CardTitle>
        <CardDescription>{t("dashboard.activity.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        {activities.length === 0 ? (
          <EmptyState
            title={t("dashboard.activity.empty")}
            hint={t("dashboard.activity.emptyHint")}
          />
        ) : (
          <ul className="divide-y divide-border">
            {activities.map((a) => {
              const Icon = a.type === "UPLOAD" ? Upload : FileStack;
              const typeLabel =
                a.type === "UPLOAD"
                  ? t("dashboard.activity.type.upload")
                  : t("dashboard.activity.type.registration");
              return (
                <li key={a.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-background text-muted">
                    <Icon size={15} aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground" title={a.file_name}>
                      {a.file_name}
                    </p>
                    <p className="tnum mt-0.5 text-xs text-muted">
                      {typeLabel}
                      {a.category_name ? ` ・ ${a.category_name}` : ""} ・ {formatDateTime(a.timestamp)}
                    </p>
                  </div>
                  <StatusBadge status={a.status} />
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
