"use client";

import { RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
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
    <header className="border-b border-border bg-card px-8 py-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">{t("dashboard.title")}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">{t("dashboard.subtitle")}</p>
        </div>
        <div className="flex items-center gap-3">
          {updatedAt ? (
            <span className="tnum text-xs text-muted">
              {t("dashboard.lastUpdated")} {formatDateTime(updatedAt)}
            </span>
          ) : null}
          <button
            type="button"
            onClick={onRefresh}
            disabled={isRefreshing}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-background disabled:opacity-50"
          >
            <RefreshCw size={14} className={cn(isRefreshing && "animate-spin")} aria-hidden />
            {t("dashboard.refresh")}
          </button>
        </div>
      </div>
    </header>
  );
}
