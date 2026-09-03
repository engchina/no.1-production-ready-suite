import { AlertTriangle, DatabaseZap, RefreshCw, RotateCcw } from "lucide-react";
import { Banner, toast } from "@engchina/production-ready-ui";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { StatusBadge } from "@/components/ui/status-badge";
import { TimedLoadingState } from "@/components/ProcessingState";
import { DatabaseUnavailableNotice } from "@/components/system/DatabaseUnavailableNotice";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ExecutionConfirmationField } from "@/features/nl2sql/components/DbAdminShared";
import { useAuth } from "@/features/security/AuthProvider";
import { MENU_PERMISSIONS } from "@/features/security/menu-permissions";
import {
  ApiError,
  type SystemObjectMetadata,
  type SystemObjectType,
  type SystemTableSchemaStatus,
  type SystemTablesOperationData,
  type SystemTablesStatusData,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  INFORMATION_TABLE_FOCUS_CLASS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_SCROLL_CLASS,
} from "@/lib/list-density";
import { useInitializeSystemTables, useSystemTablesStatus } from "@/lib/queries";
import { systemTableControlsBusy, systemTableOperationMessageKey, systemTableStatusLabelKey } from "@/lib/system-tables";

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

function previousFailureDetail(errorCode: string | null): string {
  if (errorCode === "ORA-00054") {
    return t("settings.database.systemTables.previousFailureLockDetail");
  }
  return t("settings.database.systemTables.previousFailureDetail", {
    code: errorCode ?? "-",
  });
}

function systemObjects(data: SystemTablesStatusData): SystemObjectMetadata[] {
  if (data.objects) return data.objects;
  return data.tables.map((table) => ({ ...table, object_type: "TABLE" }));
}

function objectTypeLabel(objectType: SystemObjectType): string {
  return t(
    `settings.database.systemTables.table.objectType.${objectType.toLowerCase()}`
  );
}

/** Versioned NL2SQL system table の状態と明示 DDL 操作。 */
export function SystemTablesCard() {
  const { hasPermission } = useAuth();
  const statusQuery = useSystemTablesStatus();
  const operation = useInitializeSystemTables();
  const [operationError, setOperationError] = useState("");
  const operationErrorRef = useRef<HTMLDivElement>(null);
  const [recreateConfirmation, setRecreateConfirmation] = useState("");
  const recreateConfirmed = recreateConfirmation.trim() === RECREATE_CONFIRMATION;

  const data = statusQuery.data;
  const mayExecute = hasPermission(MENU_PERMISSIONS.settingsSystemTables);
  const schemaOperationRunning = data?.operation_state.status === "running";
  const busy = systemTableControlsBusy(
    operation.isPending,
    data?.operation_state.status
  );

  useEffect(() => {
    if (operationError) {
      operationErrorRef.current?.focus();
    }
  }, [operationError]);

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
          if (recreate) setRecreateConfirmation("");
        },
        onError: (cause) => {
          if (cause instanceof ApiError) {
            setOperationError(cause.message);
            return;
          }
          setOperationError(
            `${t("settings.database.systemTables.error.operation")} ${t("settings.database.systemTables.error.recovery")}`
          );
        },
      }
    );
  };

  const refreshStatus = async () => {
    setOperationError("");
    const result = await statusQuery.refetch();
    if (!result.error) {
      toast.success(t("common.action.refreshed"));
    }
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
            onRetry={() => void refreshStatus()}
            isRetrying={statusQuery.isFetching}
          />
        ) : null}

        {data ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <SummaryItem
                label={t("settings.database.systemTables.summary.tables")}
                value={`${formatNumber(data.existing_table_count)} / ${formatNumber(data.expected_table_count)}`}
              />
              <SummaryItem
                label={t("settings.database.systemTables.summary.objects")}
                value={`${formatNumber(data.existing_object_count)} / ${formatNumber(data.expected_object_count)}`}
                description={t("settings.database.systemTables.summary.objectsHint")}
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

            {operationError ? (
              <div
                ref={operationErrorRef}
                tabIndex={-1}
                data-testid="system-tables-operation-error"
                className="rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                <Banner
                  severity="danger"
                  title={t("settings.database.systemTables.error.operationTitle")}
                >
                  {operationError}
                </Banner>
              </div>
            ) : data.operation_state.status === "failed" ? (
              <Banner
                severity="danger"
                title={t("settings.database.systemTables.previousFailure")}
              >
                {previousFailureDetail(data.operation_state.last_error_code)}
              </Banner>
            ) : null}

            {!mayExecute ? (
              <Banner severity="info">
                {t("settings.database.systemTables.readOnly")}
              </Banner>
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
                onClick={() => void refreshStatus()}
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
                <ExecutionConfirmationField
                  value={recreateConfirmation}
                  onChange={setRecreateConfirmation}
                  confirmed={recreateConfirmed}
                  placeholder={RECREATE_CONFIRMATION}
                  expectedLabel={RECREATE_CONFIRMATION}
                  helper={t("dbAdmin.confirmation.helper.danger", {
                    phrase: RECREATE_CONFIRMATION,
                  })}
                  tone="danger"
                  disabled={busy}
                  actions={
                    <Button
                      size="sm"
                      variant="danger"
                      className="w-full sm:w-auto"
                      onClick={() => execute(true)}
                      loading={operation.isPending && operation.variables?.recreate === true}
                      disabled={busy || !recreateConfirmed}
                    >
                      <RotateCcw size={15} aria-hidden />
                      {t("settings.database.systemTables.action.recreate")}
                    </Button>
                  }
                />
              </section>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SummaryItem({
  label,
  value,
  description,
}: {
  label: string;
  value: string;
  description?: string;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold text-foreground">{value}</p>
      {description ? (
        <p className="mt-1 text-xs leading-relaxed text-muted">{description}</p>
      ) : null}
    </div>
  );
}

function SystemTablesSkeleton() {
  return (
    <TimedLoadingState
      label={t("settings.database.systemTables.loading")}
      operationKey="system-tables-status"
      placement="panel"
      testId="system-tables-loading"
    >
      <Skeleton className="h-16 w-full rounded-md" />
      <Skeleton className="h-10 w-full rounded-md" />
    </TimedLoadingState>
  );
}

function SystemTablesDetails({ data }: { data: SystemTablesStatusData }) {
  const objects = systemObjects(data);

  return (
    <details className="group/disclosure min-w-0 rounded-md border border-border">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <span>
          {t("settings.database.systemTables.details.title", {
            existing: data.existing_object_count,
            expected: data.expected_object_count,
          })}
        </span>
        <DisclosureChevron expanded="group" size={16} className="text-muted" />
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
          aria-label={t("settings.database.systemTables.table.scrollLabel", {
            existing: data.existing_object_count,
            expected: data.expected_object_count,
          })}
          data-testid="system-tables-scroll-region"
          className={`rounded-sm ${INFORMATION_TABLE_SCROLL_CLASS} ${INFORMATION_TABLE_FOCUS_CLASS}`}
        >
          <table className="min-w-[840px] w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-background">
              <tr className="h-10 border-b border-border text-xs text-muted">
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.name")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.type")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.status")}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">{t("settings.database.systemTables.table.rows")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.created")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("settings.database.systemTables.table.analyzed")}</th>
              </tr>
            </thead>
            <tbody>
              {objects.map((object) => (
                <tr key={`${object.object_type}:${object.name}`} className={`${INFORMATION_TABLE_ROW_CLASS} border-b border-border last:border-b-0`}>
                  <th scope="row" className="whitespace-nowrap px-3 py-2 font-mono text-xs font-medium text-foreground">{object.name}</th>
                  <td className="whitespace-nowrap px-3 py-2 text-foreground">{objectTypeLabel(object.object_type)}</td>
                  <td className="px-3 py-2">
                    <StatusBadge
                      variant={object.exists ? "success" : "neutral"}
                      label={t(object.exists ? "settings.database.systemTables.table.exists" : "settings.database.systemTables.table.missing")}
                    />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-foreground">
                    {object.object_type !== "TABLE"
                      ? t("settings.database.systemTables.table.notApplicable")
                      : object.estimated_rows == null
                        ? "—"
                        : formatNumber(object.estimated_rows)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{formatDateTime(object.created_at)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-muted">
                    {object.object_type === "TABLE"
                      ? formatDateTime(object.last_analyzed_at)
                      : t("settings.database.systemTables.table.notApplicable")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </details>
  );
}
