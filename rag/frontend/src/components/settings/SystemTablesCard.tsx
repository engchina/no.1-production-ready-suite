import {
  AlertTriangle,
  DatabaseZap,
  RefreshCw,
  RotateCcw,
} from "lucide-react";
import { StatusBadge } from "@engchina/production-ready-ui";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type SystemTableSchemaStatus,
  type SystemTablesOperationData,
  type SystemTablesStatusData,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useInitializeSystemTables,
  useSystemTablesStatus,
} from "@/lib/queries";
import {
  RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION,
  isSystemTableRecreateConfirmationValid,
  systemTableControlsBusy,
} from "@/lib/system-tables";
import { toast } from "@/lib/toast";

const STATUS_VARIANTS = {
  ready: "success",
  missing: "neutral",
  partial: "warning",
  outdated: "info",
} as const;

const STATUS_KEYS: Record<SystemTableSchemaStatus, I18nKey> = {
  ready: "settings.database.systemTables.status.ready",
  missing: "settings.database.systemTables.status.missing",
  partial: "settings.database.systemTables.status.partial",
  outdated: "settings.database.systemTables.status.outdated",
};

const STATUS_HINT_KEYS: Record<Exclude<SystemTableSchemaStatus, "ready">, I18nKey> = {
  missing: "settings.database.systemTables.hint.missing",
  partial: "settings.database.systemTables.hint.partial",
  outdated: "settings.database.systemTables.hint.outdated",
};

const OPERATION_KEYS: Record<SystemTablesOperationData["operation"], I18nKey> = {
  no_op: "settings.database.systemTables.success.noOp",
  initialized: "settings.database.systemTables.success.initialized",
  migrated: "settings.database.systemTables.success.migrated",
  recreated: "settings.database.systemTables.success.recreated",
};

/** Versioned RAG system table の状態と明示 DDL 操作。 */
export function SystemTablesCard() {
  const statusQuery = useSystemTablesStatus();
  const operation = useInitializeSystemTables();
  const confirm = useConfirm();
  const [operationError, setOperationError] = useState("");
  const [recreateConfirmation, setRecreateConfirmation] = useState("");
  const operationErrorRef = useRef<HTMLDivElement>(null);

  const data = statusQuery.data;
  const schemaOperationRunning = data?.operation_state.status === "running";
  const busy = systemTableControlsBusy(
    operation.isPending,
    data?.operation_state.status
  );
  const recreateConfirmed =
    isSystemTableRecreateConfirmationValid(recreateConfirmation);

  useEffect(() => {
    if (operationError) operationErrorRef.current?.focus();
  }, [operationError]);

  function execute(recreate: boolean) {
    if (busy) return;
    setOperationError("");
    operation.mutate(
      {
        recreate,
        confirmation: recreate
          ? RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION
          : undefined,
      },
      {
        onSuccess: (result) => {
          toast.success(t(OPERATION_KEYS[result.operation]));
          if (recreate) setRecreateConfirmation("");
        },
        onError: (cause) => {
          setOperationError(
            cause instanceof ApiError
              ? cause.message
              : t("settings.database.systemTables.error.operation")
          );
        },
      }
    );
  }

  async function requestRecreate() {
    if (!recreateConfirmed || busy) return;
    const accepted = await confirm({
      title: t("settings.database.systemTables.confirm.title"),
      description: t("settings.database.systemTables.confirm.description"),
      confirmLabel: t("settings.database.systemTables.action.recreate"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (accepted) execute(true);
  }

  async function refreshStatus() {
    setOperationError("");
    const result = await statusQuery.refetch();
    if (!result.error) toast.success(t("settings.database.systemTables.success.refreshed"));
  }

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
                label={t(STATUS_KEYS[data.status])}
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
          <Banner
            severity="danger"
            title={t("settings.database.systemTables.error.statusTitle")}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span>{t("settings.database.systemTables.error.status")}</span>
              <Button
                type="button"
                variant="secondary"
                className="min-h-[44px]"
                loading={statusQuery.isFetching}
                onClick={() => void refreshStatus()}
              >
                <RefreshCw size={16} aria-hidden />
                {t("settings.database.systemTables.action.retry")}
              </Button>
            </div>
          </Banner>
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
                value={data.schema_head}
              />
              <SummaryItem
                label={t("settings.database.systemTables.summary.epoch")}
                value={formatNumber(data.operation_state.schema_epoch)}
              />
            </div>

            {data.status !== "ready" ? (
              <Banner
                severity={data.status === "missing" ? "info" : "warning"}
                title={t(STATUS_KEYS[data.status])}
              >
                {t(STATUS_HINT_KEYS[data.status], {
                  count: data.missing_objects.length,
                })}
              </Banner>
            ) : (
              <FormStatus
                tone="success"
                message={t("settings.database.systemTables.ready")}
              />
            )}

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
                  {operationError}{" "}
                  {t("settings.database.systemTables.error.recovery")}
                </Banner>
              </div>
            ) : data.operation_state.status === "failed" ? (
              <Banner
                severity="danger"
                title={t("settings.database.systemTables.previousFailure")}
              >
                {t("settings.database.systemTables.previousFailureDetail", {
                  code: data.operation_state.last_error_code ?? "-",
                })}
              </Banner>
            ) : null}

            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
              <Button
                type="button"
                size="lg"
                className="min-h-[44px]"
                loading={operation.isPending && operation.variables?.recreate === false}
                disabled={busy}
                onClick={() => execute(false)}
              >
                <DatabaseZap size={16} aria-hidden />
                {t("settings.database.systemTables.action.initialize")}
              </Button>
              <Button
                type="button"
                size="lg"
                variant="secondary"
                className="min-h-[44px]"
                loading={statusQuery.isFetching}
                disabled={operation.isPending}
                onClick={() => void refreshStatus()}
              >
                <RefreshCw size={16} aria-hidden />
                {t("settings.database.systemTables.action.refresh")}
              </Button>
            </div>

            <SystemTablesDetails data={data} />

            <section
              className="space-y-4 border-t border-danger/30 pt-5"
              aria-labelledby="recreate-system-tables-title"
            >
              <div className="flex items-start gap-2">
                <AlertTriangle
                  className="mt-0.5 shrink-0 text-danger"
                  size={18}
                  aria-hidden
                />
                <div>
                  <h3
                    id="recreate-system-tables-title"
                    className="text-sm font-semibold text-danger"
                  >
                    {t("settings.database.systemTables.recreate.title")}
                  </h3>
                  <p className="mt-1 text-xs leading-relaxed text-muted">
                    {t("settings.database.systemTables.recreate.description")}
                  </p>
                </div>
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="system-tables-recreate-confirmation"
                  className="text-sm font-medium text-foreground"
                >
                  {t("settings.database.systemTables.recreate.confirmationLabel")}
                </label>
                <input
                  id="system-tables-recreate-confirmation"
                  value={recreateConfirmation}
                  disabled={busy}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => setRecreateConfirmation(event.target.value)}
                  placeholder={RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION}
                  aria-describedby="system-tables-recreate-helper"
                  className="min-h-[44px] w-full rounded-md border border-border bg-background px-3 font-mono text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50"
                />
                <p
                  id="system-tables-recreate-helper"
                  className="text-xs leading-relaxed text-muted"
                >
                  {t("settings.database.systemTables.recreate.helper", {
                    phrase: RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION,
                  })}
                </p>
              </div>

              <Button
                type="button"
                size="lg"
                variant="danger"
                className="min-h-[44px] w-full sm:w-auto"
                loading={operation.isPending && operation.variables?.recreate === true}
                disabled={busy || !recreateConfirmed}
                onClick={() => void requestRecreate()}
              >
                <RotateCcw size={16} aria-hidden />
                {t("settings.database.systemTables.action.recreate")}
              </Button>
            </section>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-muted/20 p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 break-words font-mono text-sm font-semibold text-foreground">
        {value}
      </p>
    </div>
  );
}

function SystemTablesSkeleton() {
  return (
    <div
      className="space-y-3"
      role="status"
      aria-label={t("settings.database.systemTables.loading")}
    >
      <Skeleton className="h-16 w-full rounded-md" />
      <Skeleton className="h-11 w-full rounded-md" />
    </div>
  );
}

function SystemTablesDetails({ data }: { data: SystemTablesStatusData }) {
  return (
    <details className="min-w-0 rounded-md border border-border">
      <summary className="min-h-[44px] cursor-pointer px-4 py-3 text-sm font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        {t("settings.database.systemTables.details.title")}
      </summary>
      <div className="min-w-0 border-t border-border p-4">
        <p className="mb-3 break-words text-xs leading-relaxed text-muted">
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
          className="max-h-[27rem] max-w-full overflow-auto rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          <table className="w-full min-w-[680px] border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-background">
              <tr className="border-b border-border text-xs text-muted">
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.database.systemTables.table.name")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.database.systemTables.table.status")}
                </th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  {t("settings.database.systemTables.table.rows")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.database.systemTables.table.created")}
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  {t("settings.database.systemTables.table.analyzed")}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.tables.map((table) => (
                <tr
                  key={table.name}
                  className="border-b border-border last:border-b-0"
                >
                  <th
                    scope="row"
                    className="whitespace-nowrap px-3 py-2 font-mono text-xs font-medium text-foreground"
                  >
                    {table.name}
                  </th>
                  <td className="px-3 py-2">
                    <StatusBadge
                      variant={table.exists ? "success" : "neutral"}
                      label={t(
                        table.exists
                          ? "settings.database.systemTables.table.exists"
                          : "settings.database.systemTables.table.missing"
                      )}
                    />
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-foreground">
                    {table.estimated_rows == null
                      ? "—"
                      : formatNumber(table.estimated_rows)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-muted">
                    {formatDateTime(table.created_at)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-muted">
                    {formatDateTime(table.last_analyzed_at)}
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
