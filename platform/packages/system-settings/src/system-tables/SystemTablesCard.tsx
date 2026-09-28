import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { QueryKey } from "@tanstack/react-query";
import { AlertTriangle, DatabaseZap, RefreshCw, RotateCcw } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  DisclosureChevron,
  RequiredBadge,
  Skeleton,
  StatusBadge,
  TimedLoadingState,
  cn,
  toast,
} from "@engchina/production-ready-ui";

import { formatMessage } from "../auth/messages";
import { DatabaseUnavailableNotice } from "../database-gate/DatabaseUnavailableNotice";
import type { DatabaseGateMessages } from "../database-gate/messages";
import type { DatabaseGateRoutes } from "../database-gate/types";
import { SECURITY_TABLE_ROW_CLASS, SECURITY_TABLE_VISIBLE_ROWS } from "../users-roles/shared";
import {
  SYSTEM_TABLES_MESSAGES,
  systemObjectTypeMessageKey,
  type SystemTablesMessageKey,
  type SystemTablesMessages,
} from "./messages";
import {
  isSystemTableRecreateConfirmationValid,
  isSystemTablesStatusData,
  systemTableControlsBusy,
  systemTableDetailCounts,
  systemTableObjects,
  useInitializeSystemTables,
  useSystemTablesStatus,
} from "./systemTables";
import type {
  SystemObjectMetadata,
  SystemTableSchemaStatus,
  SystemTablesApi,
  SystemTablesStatusData,
} from "./types";

const STATUS_VARIANTS = {
  ready: "success",
  missing: "neutral",
  partial: "warning",
  outdated: "info",
} as const satisfies Record<SystemTableSchemaStatus, string>;

const numberFormat = new Intl.NumberFormat("ja-JP");
const dateTimeFormat = new Intl.DateTimeFormat("ja-JP", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function formatNumber(value: number): string {
  return numberFormat.format(value);
}

/** ISO 文字列を「MM/DD HH:mm」へ。未設定・無効値はダッシュ。 */
function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormat.format(date);
}

/** Migration head の表示（番号は `v17`、名前はそのまま）。 */
function formatSchemaHead(head: string | number): string {
  return typeof head === "number" ? `v${head}` : head;
}

export interface SystemTablesCardProps {
  api: SystemTablesApi;
  /** 作成・更新と全再作成ができるか（状態の確認と再取得は誰でもできる）。 */
  canManage: boolean;
  /** 全再作成の確認語（製品ごと。例: `RECREATE_RAG_SYSTEM_TABLES`）。backend も完全一致で検証する。 */
  recreateConfirmation: string;
  /** 状態を取得できないときの DB の案内（banner）の導線と文言。 */
  databaseRoutes: DatabaseGateRoutes;
  databaseMessages?: Partial<DatabaseGateMessages>;
  /** 製品の辞書の値（製品名が入る文言など）。 */
  messages?: Partial<SystemTablesMessages>;
  /** 操作が成功したときに捨てる製品の cache。 */
  invalidateQueryKeys?: readonly QueryKey[];
  /** 操作の失敗を画面に出す文言にする（API の業務エラーの本文など）。null は既定の文言。 */
  describeOperationError?: (cause: unknown) => string | null;
  /** 確認語の一致に加えて、全再作成の前に確認ダイアログを出す（製品が `useConfirm` などで渡す）。 */
  confirmRecreate?: () => Promise<boolean>;
  /** 詳細の表の object 名（NL2SQL は所有者付きの識別子の表示）。既定は所有者付きの名前を等幅で出す。 */
  renderObjectName?: (object: SystemObjectMetadata) => ReactNode;
}

/**
 * システムテーブルの状態と明示の DDL 操作（3 製品共通。NL2SQL の見た目が基準。#325）。
 * 状態の要約・作成 / 更新・確認語付きの全再作成・台帳（適用済み migration と管理 object）の詳細を出す。
 */
export function SystemTablesCard({
  api,
  canManage,
  recreateConfirmation: phrase,
  databaseRoutes,
  databaseMessages,
  messages,
  invalidateQueryKeys,
  describeOperationError,
  confirmRecreate,
  renderObjectName,
}: SystemTablesCardProps) {
  const m: SystemTablesMessages = { ...SYSTEM_TABLES_MESSAGES, ...messages };
  const text = (key: SystemTablesMessageKey, params?: Record<string, string | number>) =>
    formatMessage(m[key], params);
  const statusQuery = useSystemTablesStatus(api);
  const operation = useInitializeSystemTables(api, { invalidateQueryKeys });
  const [operationError, setOperationError] = useState("");
  const operationErrorRef = useRef<HTMLDivElement>(null);
  const [recreateInput, setRecreateInput] = useState("");
  const recreateConfirmed = isSystemTableRecreateConfirmationValid(recreateInput, phrase);

  // 想定外の形の payload は描画せず、取得失敗として扱う（兄弟カードごと消さない）。
  const data = isSystemTablesStatusData(statusQuery.data) ? statusQuery.data : undefined;
  const statusUnavailable = statusQuery.isError || (statusQuery.data !== undefined && !data);
  const schemaOperationRunning = data?.operation_state.status === "running";
  const busy =
    statusQuery.isFetching ||
    statusUnavailable ||
    !data ||
    systemTableControlsBusy(operation.isPending, data.operation_state.status);

  useEffect(() => {
    if (operationError) operationErrorRef.current?.focus();
  }, [operationError]);

  // 再取得が始まったレンダーで確認語を消す（取り直した状態を見てから入力し直させる）。
  const [wasFetching, setWasFetching] = useState(statusQuery.isFetching);
  if (wasFetching !== statusQuery.isFetching) {
    setWasFetching(statusQuery.isFetching);
    if (statusQuery.isFetching) setRecreateInput("");
  }

  const execute = (recreate: boolean) => {
    if (busy || !canManage || (recreate && !recreateConfirmed)) return;
    setRecreateInput("");
    setOperationError("");
    operation.mutate(
      { recreate, confirmation: recreate ? phrase : undefined },
      {
        onSuccess: (result) => {
          toast.success(text(`settings.database.systemTables.operation.${result.operation}`));
        },
        onError: (cause) => {
          setOperationError(
            describeOperationError?.(cause) ??
              `${text("settings.database.systemTables.error.operation")} ${text("settings.database.systemTables.error.recovery")}`,
          );
        },
      },
    );
  };

  const requestRecreate = async () => {
    if (busy || !canManage || !recreateConfirmed) return;
    if (confirmRecreate && !(await confirmRecreate())) return;
    execute(true);
  };

  const refreshStatus = async () => {
    setRecreateInput("");
    setOperationError("");
    const result = await statusQuery.refetch();
    if (!result.error) toast.success(text("settings.database.systemTables.refreshed"));
  };

  const statusLabel = (status: SystemTableSchemaStatus) =>
    text(`settings.database.systemTables.status.${status}`);

  return (
    <Card id="system-tables" className="min-w-0 max-w-full scroll-mt-24 rounded-md" aria-busy={busy}>
      <CardHeader className="p-6 pb-0">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <DatabaseZap size={20} aria-hidden />
              <CardTitle className="text-base">{text("settings.database.systemTables.title")}</CardTitle>
            </div>
            <CardDescription className="mt-2 leading-relaxed">
              {text("settings.database.systemTables.description")}
            </CardDescription>
          </div>
          {data ? (
            <div className="flex flex-wrap items-center gap-2" aria-live="polite">
              <StatusBadge variant={STATUS_VARIANTS[data.status]} label={statusLabel(data.status)} />
              {schemaOperationRunning ? (
                <StatusBadge variant="info" label={text("settings.database.systemTables.operation.running")} />
              ) : null}
            </div>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="min-w-0 space-y-5 p-6">
        {statusQuery.isPending ? (
          <TimedLoadingState
            label={text("settings.database.systemTables.loading")}
            operationKey="system-tables-status"
            placement="panel"
            testId="system-tables-loading"
          >
            <Skeleton className="h-16 w-full rounded-md" />
            <Skeleton className="h-10 w-full rounded-md" />
          </TimedLoadingState>
        ) : null}

        {statusUnavailable ? (
          <DatabaseUnavailableNotice
            mode="banner"
            routes={databaseRoutes}
            messages={databaseMessages}
            onRetry={() => void refreshStatus()}
            isRetrying={statusQuery.isFetching}
          />
        ) : null}

        {data ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <SummaryItem
                label={text("settings.database.systemTables.summary.tables")}
                value={`${formatNumber(data.existing_table_count)} / ${formatNumber(data.expected_table_count)}`}
              />
              <SummaryItem
                label={text("settings.database.systemTables.summary.objects")}
                value={`${formatNumber(data.existing_object_count)} / ${formatNumber(data.expected_object_count)}`}
                description={text("settings.database.systemTables.summary.objectsHint")}
              />
              <SummaryItem
                label={text("settings.database.systemTables.summary.head")}
                value={formatSchemaHead(data.schema_head)}
              />
              <SummaryItem
                label={text("settings.database.systemTables.summary.epoch")}
                value={formatNumber(data.operation_state.schema_epoch)}
              />
            </div>

            {data.status !== "ready" ? (
              <Banner severity={data.status === "missing" ? "info" : "warning"} title={statusLabel(data.status)}>
                {text(`settings.database.systemTables.statusHint.${data.status}`, {
                  count: data.missing_objects.length,
                })}
              </Banner>
            ) : null}

            {operationError ? (
              <div
                ref={operationErrorRef}
                tabIndex={-1}
                data-testid="system-tables-operation-error"
                className="rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
              >
                <Banner severity="danger" title={text("settings.database.systemTables.error.operationTitle")}>
                  {operationError}
                </Banner>
              </div>
            ) : data.operation_state.status === "failed" ? (
              <Banner severity="danger" title={text("settings.database.systemTables.previousFailure")}>
                {data.operation_state.last_error_code === "ORA-00054"
                  ? text("settings.database.systemTables.previousFailureLockDetail")
                  : text("settings.database.systemTables.previousFailureDetail", {
                      code: data.operation_state.last_error_code ?? "-",
                    })}
              </Banner>
            ) : null}

            {!canManage ? <Banner severity="info">{text("settings.database.systemTables.readOnly")}</Banner> : null}

            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
              {canManage ? (
                <Button
                  type="button"
                  size="md"
                  onClick={() => execute(false)}
                  loading={operation.isPending && operation.variables?.recreate === false}
                  disabled={busy}
                  icon={DatabaseZap}
                >
                  {text("settings.database.systemTables.action.initialize")}
                </Button>
              ) : null}
              <Button
                type="button"
                size="md"
                variant="secondary"
                onClick={() => void refreshStatus()}
                loading={statusQuery.isFetching}
                disabled={operation.isPending}
                icon={RefreshCw}
              >
                {text("settings.database.systemTables.action.refresh")}
              </Button>
            </div>

            <SystemTablesDetails data={data} text={text} renderObjectName={renderObjectName} />

            {canManage ? (
              <section className="border-t border-border pt-5" aria-labelledby="recreate-system-tables-title">
                {/* 危険な操作区画は幅を止めず、広い画面では「影響の説明」と「確認語・実行」を左右に分けてカード幅を使う。 */}
                <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] xl:items-start xl:gap-6">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 shrink-0 text-danger-fg" size={16} aria-hidden />
                    <div>
                      <h3 id="recreate-system-tables-title" className="text-sm font-semibold text-fg">
                        {text("settings.database.systemTables.recreate.sectionTitle")}
                      </h3>
                      <p className="mt-1 text-xs leading-relaxed text-fg-muted">
                        {text("settings.database.systemTables.recreate.sectionDescription")}
                      </p>
                    </div>
                  </div>
                  <RecreateConfirmationField
                    value={recreateInput}
                    onChange={setRecreateInput}
                    confirmed={recreateConfirmed}
                    phrase={phrase}
                    disabled={busy}
                    text={text}
                    action={
                      <Button
                        type="button"
                        size="lg"
                        variant="danger"
                        className="w-full sm:w-auto"
                        onClick={() => void requestRecreate()}
                        loading={operation.isPending && operation.variables?.recreate === true}
                        disabled={busy || !recreateConfirmed}
                        icon={RotateCcw}
                      >
                        {text("settings.database.systemTables.action.recreate")}
                      </Button>
                    }
                  />
                </div>
              </section>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

type Text = (key: SystemTablesMessageKey, params?: Record<string, string | number>) => string;

function SummaryItem({ label, value, description }: { label: string; value: string; description?: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-surface-hover p-3">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className="mt-1 break-words font-sans text-sm font-semibold text-fg">{value}</p>
      {description ? <p className="mt-1 text-xs leading-relaxed text-fg-muted">{description}</p> : null}
    </div>
  );
}

/**
 * 全再作成の確認語の入力（NL2SQL の実行確認語の見た目）。
 * 入力前から danger 色にせず、状態（未入力 / 不一致 / 確認済み）は文字のバッジでも示す。
 */
function RecreateConfirmationField({
  value,
  onChange,
  confirmed,
  phrase,
  disabled,
  text,
  action,
}: {
  value: string;
  onChange: (value: string) => void;
  confirmed: boolean;
  phrase: string;
  disabled: boolean;
  text: Text;
  action: ReactNode;
}) {
  const id = useId();
  const helperId = `${id}-helper`;
  const mismatch = Boolean(value.trim()) && !confirmed;
  const statusLabel = confirmed
    ? text("settings.database.systemTables.confirmation.status.confirmed")
    : mismatch
      ? text("settings.database.systemTables.confirmation.status.mismatch")
      : text("settings.database.systemTables.confirmation.status.pending");
  // 「入力条件: {phrase}」の確認語の部分だけを等幅で出すため、前後に分ける。
  const [expectedPrefix, expectedSuffix = ""] = text("settings.database.systemTables.confirmation.expected", {
    phrase: PHRASE_PLACEHOLDER,
  }).split(PHRASE_PLACEHOLDER);

  return (
    <div
      className="grid min-w-0 gap-2 rounded-md border border-border bg-surface-sunken p-3"
      data-testid="execution-confirmation-field"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <label htmlFor={id} className="text-sm font-semibold text-fg">
          {text("settings.database.systemTables.confirmation.label")}
          <RequiredBadge
            label={text("settings.database.systemTables.confirmation.required")}
            aria-hidden
            className="ml-2 align-middle"
          />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <span className="max-w-full rounded-md bg-surface px-2 py-1 font-sans text-xs text-fg">
            {expectedPrefix}
            {/* 確認語は識別子なので、入力する文字を読み違えないよう等幅で示す。 */}
            <span className="font-mono font-semibold [overflow-wrap:anywhere]">{phrase}</span>
            {expectedSuffix}
          </span>
          <span
            className={cn(
              "inline-flex min-h-6 items-center rounded-full border px-2 py-0.5 text-xs font-semibold",
              confirmed
                ? "border-success-border bg-success-subtle text-success-fg"
                : mismatch
                  ? "border-danger-border bg-danger-subtle text-danger-fg"
                  : "border-border bg-surface text-fg-muted",
            )}
            aria-live="polite"
          >
            {statusLabel}
          </span>
        </div>
      </div>
      <input
        id={id}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        className="h-[44px] w-full rounded-md border border-border-control bg-surface px-3 py-2 text-sm outline-none transition-colors placeholder:text-fg-muted focus:border-danger-fg disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-fg-disabled"
        placeholder={phrase}
        disabled={disabled}
        required
        aria-required="true"
        aria-describedby={helperId}
        aria-invalid={mismatch ? "true" : undefined}
        autoCapitalize="off"
        autoCorrect="off"
        autoComplete="off"
        spellCheck={false}
      />
      <p id={helperId} className={cn("break-words text-xs leading-5", mismatch ? "text-danger-fg" : "text-fg-muted")}>
        {text("settings.database.systemTables.confirmation.helper", { phrase })}
      </p>
      <div className="flex min-w-0 flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:flex-wrap sm:items-center">
        {action}
      </div>
    </div>
  );
}

const PHRASE_PLACEHOLDER = "\u0000";

function SystemTablesDetails({
  data,
  text,
  renderObjectName,
}: {
  data: SystemTablesStatusData;
  text: Text;
  renderObjectName?: (object: SystemObjectMetadata) => ReactNode;
}) {
  const objects = systemTableObjects(data);
  const counts = systemTableDetailCounts(data);
  const objectTypeLabel = (objectType: string) => {
    const key = systemObjectTypeMessageKey(objectType);
    return key ? text(key) : objectType;
  };
  const notApplicable = text("settings.database.systemTables.table.notApplicable");

  return (
    <details className="group/disclosure min-w-0 rounded-md border border-border">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring [&::-webkit-details-marker]:hidden">
        <span>{text("settings.database.systemTables.details.title", counts)}</span>
        <DisclosureChevron expanded="group" size={16} className="text-fg-muted" />
      </summary>
      <div className="min-w-0 border-t border-border p-4">
        <p className="mb-3 break-words text-xs leading-relaxed text-fg-muted">
          {text("settings.database.systemTables.details.versions", {
            applied: data.applied_versions.join(", ") || "-",
            pending: data.pending_versions.join(", ") || "-",
          })}
        </p>
        <DataTable<SystemObjectMetadata>
          columns={[
            {
              key: "name",
              header: text("settings.database.systemTables.table.name"),
              rowHeader: true,
              render: (object) =>
                renderObjectName ? (
                  renderObjectName(object)
                ) : (
                  <span className="font-mono text-xs font-semibold text-fg [overflow-wrap:anywhere]">
                    {object.qualified_name || object.name}
                  </span>
                ),
            },
            {
              key: "type",
              header: text("settings.database.systemTables.table.type"),
              className: "whitespace-nowrap",
              render: (object) => objectTypeLabel(object.object_type),
            },
            {
              key: "status",
              header: text("settings.database.systemTables.table.status"),
              render: (object) => (
                <StatusBadge
                  variant={object.exists ? "success" : "neutral"}
                  label={text(
                    object.exists
                      ? "settings.database.systemTables.table.exists"
                      : "settings.database.systemTables.table.missing",
                  )}
                />
              ),
            },
            {
              key: "rows",
              header: text("settings.database.systemTables.table.rows"),
              align: "right",
              className: "tabular-nums",
              render: (object) =>
                object.object_type !== "TABLE"
                  ? notApplicable
                  : object.estimated_rows == null
                    ? "—"
                    : formatNumber(object.estimated_rows),
            },
            {
              key: "created",
              header: text("settings.database.systemTables.table.created"),
              className: "whitespace-nowrap text-fg-muted",
              render: (object) => formatDateTime(object.created_at),
            },
            {
              key: "analyzed",
              header: text("settings.database.systemTables.table.analyzed"),
              className: "whitespace-nowrap text-fg-muted",
              render: (object) =>
                object.object_type === "TABLE" ? formatDateTime(object.last_analyzed_at) : notApplicable,
            },
          ]}
          rows={objects}
          getRowKey={(object) => `${object.object_type}:${object.name}`}
          rowProps={() => ({ className: SECURITY_TABLE_ROW_CLASS })}
          tableClassName="w-full min-w-[60rem]"
          scrollAriaLabel={text("settings.database.systemTables.table.scrollLabel", counts)}
          scrollTestId="system-tables-scroll-region"
          stickyHeader
          visibleRows={SECURITY_TABLE_VISIBLE_ROWS}
        />
      </div>
    </details>
  );
}
