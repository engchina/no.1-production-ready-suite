import { AlertTriangle, DatabaseZap, RefreshCw, RotateCcw } from "lucide-react";
import { Banner, StatusBadge, toast } from "@engchina/production-ready-ui";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { DatabaseUnavailableNotice } from "@/components/system/DatabaseUnavailableNotice";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/features/security/AuthProvider";
import {
  ApiError,
  type SystemTableSchemaStatus,
  type SystemTablesOperationData,
  type SystemTablesStatusData,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useInitializeSystemTables,
  useSystemTablesStatus,
} from "@/lib/queries";
import {
  systemTableControlsBusy,
  systemTableOperationMessageKey,
  systemTableStatusLabelKey,
} from "@/lib/system-tables";

const RECREATE_CONFIRMATION = "RECREATE_NL2SQL_SYSTEM_TABLES";

const STATUS_VARIANTS = {
  ready: "success",
  missing: "neutral",
  partial: "warning",
  outdated: "info",
} as const;

function statusLabel(status: SystemTableSchemaStatus): string {
  return t(systemTableStatusLabelKey(status));
}

function operationSuccessMessage(result: SystemTablesOperationData): string {
  return t(systemTableOperationMessageKey(result.operation));
}

/** Versioned NL2SQL system table の状態と明示 DDL 操作。 */
export function SystemTablesCard() {
  const confirm = useConfirm();
  const { hasPermission } = useAuth();
  const statusQuery = useSystemTablesStatus();
  const operation = useInitializeSystemTables();
  const [operationError, setOperationError] = useState("");

  const data = statusQuery.data;
  const mayExecute = hasPermission("settings.database.sql_execute");
  const schemaOperationRunning = data?.operation_state.status === "running";
  const busy = systemTableControlsBusy(
    operation.isPending,
    data?.operation_state.status
  );

  const execute = (recreate: boolean) => {
    if (busy) return;
    setOperationError("");
    operation.mutate(
      {
        recreate,
        confirmation: recreate ? RECREATE_CONFIRMATION : undefined,
      },
      {
        onSuccess: (result) => {
          toast.success(operationSuccessMessage(result));
        },
        onError: (cause) => {
          const message =
            cause instanceof ApiError
              ? cause.message
              : t("settings.database.systemTables.error.operation");
          setOperationError(
            `${message} ${t("settings.database.systemTables.error.recovery")}`
          );
        },
      }
    );
  };

  const recreate = async () => {
    if (busy) return;
    const accepted = await confirm({
      title: t("settings.database.systemTables.recreate.title"),
      description: t("settings.database.systemTables.recreate.description"),
      confirmLabel: t("settings.database.systemTables.action.recreate"),
      cancelLabel: t("common.cancel"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (accepted) execute(true);
  };

  return (
    <Card
      id="system-tables"
      className="min-w-0 max-w-full scroll-mt-24 rounded-md"
      aria-busy={busy}
    >
      <CardHeader className="p-6 pb-0">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <DatabaseZap size={18} aria-hidden />
              <CardTitle className="text-lg">
                {t("settings.database.systemTables.title")}
              </CardTitle>
            </div>
            <CardDescription className="mt-2 leading-relaxed">
              {t("settings.database.systemTables.description")}
            </CardDescription>
          </div>
          {data ? (
            <div className="flex flex-wrap items-center gap-2" aria-live="polite">
              <StatusBadge
                variant={STATUS_VARIANTS[data.status]}
                label={statusLabel(data.status)}
              />
              {schemaOperationRunning ? (
                <StatusBadge
                  variant="pending"
                  label={t("settings.database.systemTables.operation.running")}
                />
              ) : null}
            </div>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="min-w-0 space-y-5 p-6">
        {statusQuery.isPending ? <SystemTablesSkeleton /> : null}

        {statusQuery.isError ? (
          <DatabaseUnavailableNotice
            mode="banner"
            onRetry={() => void statusQuery.refetch()}
            isRetrying={statusQuery.isFetching}
          />
        ) : null}

        {data ? (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <SummaryItem
                label={t("settings.database.systemTables.summary.objects")}
                value={`${formatNumber(data.existing_object_count)} / ${formatNumber(data.expected_object_count)}`}
              />
              <SummaryItem
                label={t("settings.database.systemTables.summary.head")}
                value={`v${data.schema_head}`}
              />
              <SummaryItem
                label={t("settings.database.systemTables.summary.epoch")}
                value={formatNumber(data.operation_state.schema_epoch)}
              />
            </div>

            {data.status !== "ready" ? (
              <Banner
                severity={data.status === "missing" ? "info" : "warning"}
                title={statusLabel(data.status)}
              >
                {t(`settings.database.systemTables.statusHint.${data.status}`, {
                  count: data.missing_objects.length,
                })}
              </Banner>
            ) : null}

            {data.operation_state.status === "failed" ? (
              <Banner
                severity="danger"
                title={t("settings.database.systemTables.previousFailure")}
              >
                {t("settings.database.systemTables.previousFailureDetail", {
                  code: data.operation_state.last_error_code ?? "-",
                })}
              </Banner>
            ) : null}

            {!mayExecute ? (
              <Banner severity="info">
                {t("settings.database.systemTables.readOnly")}
              </Banner>
            ) : null}

            {operationError ? (
              <div>
                <Banner
                  severity="danger"
                  title={t("settings.database.systemTables.error.operationTitle")}
                >
                  {operationError}
                </Banner>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
              {mayExecute ? (
                <Button
                  size="md"
                  onClick={() => execute(false)}
                  loading={operation.isPending && operation.variables?.recreate === false}
                  disabled={busy}
                >
                  <DatabaseZap size={16} aria-hidden />
                  {t("settings.database.systemTables.action.initialize")}
                </Button>
              ) : null}
              <Button
                size="md"
                variant="secondary"
                onClick={() => {
                  setOperationError("");
                  void statusQuery.refetch();
                }}
                loading={statusQuery.isFetching}
                disabled={operation.isPending}
              >
                <RefreshCw size={16} aria-hidden />
                {t("settings.database.systemTables.action.refresh")}
              </Button>
            </div>

            <SystemTablesDetails data={data} />

            {mayExecute ? (
              <section className="space-y-3 border-t border-border pt-5" aria-labelledby="recreate-system-tables-title">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 shrink-0 text-danger" size={17} aria-hidden />
                  <div>
                    <h3 id="recreate-system-tables-title" className="text-sm font-semibold text-foreground">
                      {t("settings.database.systemTables.recreate.sectionTitle")}
                    </h3>
                    <p className="mt-1 text-xs leading-relaxed text-muted">
                      {t("settings.database.systemTables.recreate.sectionDescription")}
                    </p>
                  </div>
                </div>
                <Button
                  size="md"
                  variant="danger"
                  onClick={() => void recreate()}
                  loading={operation.isPending && operation.variables?.recreate === true}
                  disabled={busy}
                >
                  <RotateCcw size={16} aria-hidden />
                  {t("settings.database.systemTables.action.recreate")}
                </Button>
              </section>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold text-foreground">{value}</p>
    </div>
  );
}

function SystemTablesSkeleton() {
  return (
    <div className="space-y-3" role="status" aria-label={t("settings.database.systemTables.loading")}>
      <Skeleton className="h-16 w-full rounded-md" />
      <Skeleton className="h-10 w-full rounded-md" />
    </div>
  );
}

function SystemTablesDetails({ data }: { data: SystemTablesStatusData }) {
  return (
    <details className="min-w-0 rounded-md border border-border">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {t("settings.database.systemTables.details.title")}
      </summary>
      <div className="min-w-0 border-t border-border p-4">
        <p className="mb-3 text-xs leading-relaxed text-muted">
          {t("settings.database.systemTables.details.versions", {
            applied: data.applied_versions.join(", ") || "-",
            pending: data.pending_versions.join(", ") || "-",
          })}
        </p>
        <div
          role="region"
          tabIndex={0}
          aria-label={t("settings.database.systemTables.table.scrollLabel")}
          data-testid="system-tables-scroll-region"
          className="max-h-[27.25rem] max-w-full overflow-auto rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <table className="min-w-[720px] w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-background">
              <tr className="h-9 border-b border-border text-xs text-muted">
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.name")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.status")}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">{t("settings.database.systemTables.table.rows")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.created")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.analyzed")}</th>
              </tr>
            </thead>
            <tbody>
              {data.tables.map((table) => (
                <tr key={table.name} className="h-10 border-b border-border last:border-b-0">
                  <th scope="row" className="whitespace-nowrap px-3 py-2 font-mono text-xs font-medium text-foreground">{table.name}</th>
                  <td className="px-3 py-2">
                    <StatusBadge
                      variant={table.exists ? "success" : "neutral"}
                      label={t(table.exists ? "settings.database.systemTables.table.exists" : "settings.database.systemTables.table.missing")}
                    />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-foreground">{table.estimated_rows == null ? "—" : formatNumber(table.estimated_rows)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{formatDateTime(table.created_at)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{formatDateTime(table.last_analyzed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}
