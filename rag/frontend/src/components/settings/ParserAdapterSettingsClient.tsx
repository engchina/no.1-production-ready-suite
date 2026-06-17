"use client";

import type { ReactNode } from "react";
import {
  CheckCircle2,
  CircleOff,
  PackageCheck,
  PackageX,
  Plug,
  Route,
} from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type ParserAdapterBackendName,
  type ParserAdapterSettingsData,
  type ParserAdapterStatus,
  type ParserAdapterStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useParserAdapterSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** Optional parser adapter の runtime readiness を表示する read-only 設定画面。 */
export function ParserAdapterSettingsClient() {
  const query = useParserAdapterSettings();

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-40 w-full rounded-lg" />
        <Skeleton className="h-72 w-full rounded-lg" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="p-8">
        <ErrorState
          message={
            query.error instanceof ApiError
              ? query.error.message
              : t("settings.parserAdapters.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings) return null;

  return (
    <div className="space-y-5 p-8">
      <OverviewCard settings={settings} />
      <AdapterReadinessCard adapters={settings.adapters} />
    </div>
  );
}

function OverviewCard({ settings }: { settings: ParserAdapterSettingsData }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
            <Plug size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.parserAdapters.overview.title")}</CardTitle>
            <CardDescription>
              {t("settings.parserAdapters.overview.description")}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <RuntimeFact
            label={t("settings.parserAdapters.backend")}
            value={settings.adapter_backend}
          />
          <RuntimeFact
            label={t("settings.parserAdapters.effectiveOrder")}
            value={formatEffectiveOrder(settings.effective_order)}
          />
          <RuntimeFact label={t("settings.parserAdapters.source")} value={settings.config_source} />
        </dl>
      </CardContent>
    </Card>
  );
}

function AdapterReadinessCard({ adapters }: { adapters: ParserAdapterStatusData[] }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
            <Route size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.parserAdapters.adapters.title")}</CardTitle>
            <CardDescription>
              {t("settings.parserAdapters.adapters.description")}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="hidden border-y border-border text-xs font-medium text-muted md:grid md:grid-cols-[1.1fr_0.8fr_1fr_0.8fr_0.8fr_1fr]">
          <div className="px-3 py-2">{t("settings.parserAdapters.adapter")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.flag")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.package")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.role")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.status")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.warning")}</div>
        </div>
        <ul className="divide-y divide-border">
          {adapters.map((adapter) => (
            <AdapterRow key={adapter.backend} adapter={adapter} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function AdapterRow({ adapter }: { adapter: ParserAdapterStatusData }) {
  return (
    <li className="grid grid-cols-1 gap-3 py-4 md:grid-cols-[1.1fr_0.8fr_1fr_0.8fr_0.8fr_1fr] md:gap-0 md:py-0">
      <RowCell label={t("settings.parserAdapters.adapter")}>
        <div className="font-medium text-foreground">{adapterLabel(adapter.backend)}</div>
        <div className="text-xs text-muted">{adapter.package_name}</div>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.flag")}>
        <BooleanState
          value={adapter.enabled}
          trueLabel={t("settings.parserAdapters.enabled")}
          falseLabel={t("settings.parserAdapters.disabled")}
        />
      </RowCell>
      <RowCell label={t("settings.parserAdapters.package")}>
        <PackageState adapter={adapter} />
      </RowCell>
      <RowCell label={t("settings.parserAdapters.role")}>
        <span className="text-sm text-foreground">
          {adapter.selected
            ? t("settings.parserAdapters.selected")
            : t("settings.parserAdapters.notSelected")}
        </span>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.status")}>
        <StatusPill status={adapter.status} />
      </RowCell>
      <RowCell label={t("settings.parserAdapters.warning")}>
        <span className="break-words text-sm text-foreground">
          {warningLabel(adapter.warning_code)}
        </span>
      </RowCell>
    </li>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function RowCell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 px-3 md:py-3">
      <div className="mb-1 text-xs font-medium text-muted md:hidden">{label}</div>
      {children}
    </div>
  );
}

function BooleanState({
  value,
  trueLabel,
  falseLabel,
}: {
  value: boolean;
  trueLabel: string;
  falseLabel: string;
}) {
  const Icon = value ? CheckCircle2 : CircleOff;
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center gap-1.5 rounded-md px-2 text-xs font-medium",
        value ? "bg-success-bg text-success" : "bg-muted text-foreground"
      )}
    >
      <Icon size={14} aria-hidden />
      {value ? trueLabel : falseLabel}
    </span>
  );
}

function PackageState({ adapter }: { adapter: ParserAdapterStatusData }) {
  const Icon = adapter.installed ? PackageCheck : PackageX;
  return (
    <div className="space-y-1">
      <span
        className={cn(
          "inline-flex min-h-6 items-center gap-1.5 rounded-md px-2 text-xs font-medium",
          adapter.installed ? "bg-success-bg text-success" : "bg-warning-bg text-warning"
        )}
      >
        <Icon size={14} aria-hidden />
        {adapter.installed
          ? t("settings.parserAdapters.installed")
          : t("settings.parserAdapters.notInstalled")}
      </span>
      {adapter.version ? (
        <div className="text-xs text-muted">
          {t("settings.parserAdapters.version", { version: adapter.version })}
        </div>
      ) : null}
    </div>
  );
}

function StatusPill({ status }: { status: ParserAdapterStatus }) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center rounded-md px-2 text-xs font-semibold",
        statusToneClass(status)
      )}
    >
      {t(statusLabelKey(status))}
    </span>
  );
}

function adapterLabel(adapter: ParserAdapterBackendName) {
  if (adapter === "docling") return "Docling";
  if (adapter === "marker") return "Marker";
  return "Unstructured";
}

function formatEffectiveOrder(order: ParserAdapterBackendName[]) {
  if (!order.length) return t("settings.parserAdapters.noEffectiveOrder");
  return order.map(adapterLabel).join(" -> ");
}

function statusLabelKey(status: ParserAdapterStatus): I18nKey {
  return `settings.parserAdapters.status.${status}` as I18nKey;
}

function statusToneClass(status: ParserAdapterStatus) {
  if (status === "active") return "bg-success-bg text-success";
  if (status === "missing") return "bg-danger-bg text-danger";
  if (status === "ignored") return "bg-warning-bg text-warning";
  if (status === "available") return "bg-info-bg text-info";
  return "bg-muted text-foreground";
}

function warningLabel(code: string | null) {
  if (!code) return t("settings.parserAdapters.noWarning");
  if (code === "adapter_feature_flag_disabled") {
    return t("settings.parserAdapters.warning.adapter_feature_flag_disabled");
  }
  if (code === "adapter_package_missing") {
    return t("settings.parserAdapters.warning.adapter_package_missing");
  }
  if (code === "adapter_flag_ignored_by_backend") {
    return t("settings.parserAdapters.warning.adapter_flag_ignored_by_backend");
  }
  return code;
}
