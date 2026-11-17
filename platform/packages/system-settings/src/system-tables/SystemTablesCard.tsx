import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { AlertTriangle, DatabaseZap, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import {
  displayDateTimeFormat,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  cn,
  CombinedBanner,
  combinedBannerItems,
  DataTable,
  Disclosure,
  ExecutionConfirmationField,
  ListScrollRegion,
  ListToolbar,
  Skeleton,
  StatusBadge,
  TimedLoadingState,
  toast,
  useActionPending,
  type CombinedBannerItem,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
} from "@production-ready/ui";

import { formatMessage } from "../auth/messages";
import { DATABASE_STATUS_QUERY_KEY } from "../database-gate/types";
import { useDatabaseUnavailableBannerItem } from "../database-gate/DatabaseUnavailableNotice";
import type { DatabaseGateMessages } from "../database-gate/messages";
import type { DatabaseGateRoutes } from "../database-gate/types";
import {
  SYSTEM_TABLES_MESSAGES,
  systemObjectTypeMessageKey,
  type SystemTablesMessageKey,
  type SystemTablesMessages,
} from "./messages";
import {
  isSystemTableRecreateConfirmationValid,
  isSystemTablesStatusData,
  systemTableCompletedOperation,
  systemTableControlsBusy,
  systemTableDetailCounts,
  systemTableObjects,
  useDeleteSystemTableOrphanedRows,
  useInitializeSystemTables,
  useSystemTablesStatus,
  type WatchedSystemTableOperation,
} from "./systemTables";
import type {
  SystemObjectMetadata,
  SystemTableDestructiveMigration,
  SystemTableForeignKey,
  SystemTableOperationResult,
  SystemTableOperationState,
  SystemTableRestartRequired,
  SystemTableSchemaStatus,
  SystemTablesApi,
  SystemTablesStatusData,
  SystemTableVectorMemory,
} from "./types";

const STATUS_VARIANTS = {
  ready: "success",
  missing: "neutral",
  partial: "warning",
  outdated: "info",
} as const satisfies Record<SystemTableSchemaStatus, string>;

const numberFormat = new Intl.NumberFormat("ja-JP");
// システムの設定の時間帯で書く（#3265）。
const dateTimeFormat = displayDateTimeFormat({
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

// 操作の開始時刻（「13:45」。#5278）。
const timeFormat = displayDateTimeFormat({ hour: "2-digit", minute: "2-digit" });
// 経過時間の表示を進める間隔（分の単位で出すため、秒ごとには描き直さない）。
const ELAPSED_TICK_MS = 15_000;
const megabyteFormat = new Intl.NumberFormat("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function formatNumber(value: number): string {
  return numberFormat.format(value);
}

/** バイト数を「57.1 MB」の形へ（1 MB = 1024 × 1024 バイト）。 */
function formatMegabytes(bytes: number): string {
  return `${megabyteFormat.format(bytes / 1048576)} MB`;
}

/** ISO 文字列を「MM/DD HH:mm」へ。未設定・無効値はダッシュ。 */
function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return dateTimeFormat.format(iso);
}

/** Migration head の表示（番号は `v17`、名前はそのまま）。 */
/**
 * migration の表示（3 製品で「v + 番号」にそろえる。NL2SQL が基準。#658）。
 * NL2SQL は番号そのもの。RAG は名前（`20260930_009_…`）が台帳の識別子のため、並び順の番号
 * （適用済みと未適用・不一致は全 migration を分けたもの）を出し、名前は補足に回す。
 */
function schemaHeadDisplay(data: SystemTablesStatusData): { value: string; name?: string } {
  if (typeof data.schema_head === "number") return { value: `v${data.schema_head}` };
  const ordinal = data.applied_versions.length + data.pending_versions.length;
  return ordinal > 0 ? { value: `v${ordinal}`, name: data.schema_head } : { value: data.schema_head };
}

function formatVersion(version: string | number): string {
  return typeof version === "number" ? `v${version}` : version;
}

/** 確認ダイアログに渡す文言（製品が `useConfirm` などで、danger のトーンで開く）。 */
export interface SystemTablesConfirmRequest {
  title: string;
  description: string;
  confirmLabel: string;
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
  /**
   * 参照先のない行の削除の前に出す確認ダイアログ（#511。表・外部キー・件数を示す）。
   * 破壊的な操作のため、これと API（`deleteSystemTableOrphanedRows`）の両方があるときだけ削除の操作を出す。
   */
  confirmDeleteOrphans?: (request: SystemTablesConfirmRequest) => Promise<boolean>;
  /**
   * データを消す未適用の migration（状態の `pending_destructive_migrations`。#619）があるときに、
   * 「作成・更新」の前に出す確認ダイアログ。承認したときだけ `allow_destructive` を送る。
   * 無ければ承認を送らず、backend が 409 で止める（その文言を出す）。
   */
  confirmDestructiveMigrations?: (request: SystemTablesConfirmRequest) => Promise<boolean>;
  /**
   * 製品の案内（Agent の保存先の案内など）。カードの案内と同じ 1 つの枠に項目として並べる
   * （同じ区画の案内の枠は 1 つ。messaging.md §3.4.3。#5183）。
   */
  notices?: ReadonlyArray<CombinedBannerItem | null | false | undefined>;
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
  confirmDeleteOrphans,
  confirmDestructiveMigrations,
  notices = [],
}: SystemTablesCardProps) {
  const m: SystemTablesMessages = { ...SYSTEM_TABLES_MESSAGES, ...messages };
  const text = (key: SystemTablesMessageKey, params?: Record<string, string | number>) =>
    formatMessage(m[key], params);
  const queryClient = useQueryClient();
  const statusQuery = useSystemTablesStatus(api);
  const operation = useInitializeSystemTables(api);
  const orphanDeletion = useDeleteSystemTableOrphanedRows(api, { invalidateQueryKeys });
  const mutationPending = operation.isPending || orphanDeletion.isPending;
  const [operationError, setOperationError] = useState("");
  const operationErrorRef = useRef<HTMLDivElement>(null);
  // 作成・更新の結果で、再起動が要るプロセス（#1844）。次の操作・状態の取り直しまで残す（messaging.md §10.4）。
  const [restartNotice, setRestartNotice] = useState<{
    operation: SystemTableOperationResult;
    restart: SystemTableRestartRequired;
  } | null>(null);
  const [recreateInput, setRecreateInput] = useState("");
  // 「最新の状態を確認」を押した取り直しの間だけ、そのボタンを回す。実行中の定期の取り直しや、
  // 親の画面の invalidate（ADB の保存などは ["settings", "database"] の下を取り直す）では回さない（#819）。
  const manualRefresh = useActionPending();
  const recreateConfirmed = isSystemTableRecreateConfirmationValid(recreateInput, phrase);

  // 想定外の形の payload は描画せず、取得失敗として扱う（兄弟カードごと消さない）。
  const data = isSystemTablesStatusData(statusQuery.data) ? statusQuery.data : undefined;
  const statusUnavailable = statusQuery.isError || (statusQuery.data !== undefined && !data);
  const operationState = data?.operation_state;
  // 操作中（誰が始めた操作でも。lease が running）と、止まった可能性（lease の期限切れ。#5278）。
  const schemaOperationRunning = operationState?.status === "running";
  const schemaOperationStale = operationState?.status === "stale";
  const runningKind = schemaOperationRunning ? (operationState?.operation_kind ?? "initialize") : null;
  // ボタンが表す操作そのものが進行中なら、誰が始めたかに関わらず loading にする（buttons.md §8。#5278）。
  const initializeInProgress =
    (operation.isPending && operation.variables?.recreate === false) || runningKind === "initialize";
  const recreateInProgress =
    (operation.isPending && operation.variables?.recreate === true) || runningKind === "recreate";
  // 既存の表に無い正本の列（#2450 / #2458。3 製品が返す。無ければ出さない）。
  const missingColumns = data?.missing_columns ?? [];
  // 外部キーの差分（#505。RAG と Agent が返す。無ければ出さない）。
  const missingForeignKeys = data?.missing_foreign_keys ?? [];
  const orphanedForeignKeys = data?.orphaned_foreign_keys ?? [];
  // 削除規則の違い・無効化（#511。RAG と Agent が返す）。
  const mismatchedForeignKeys = data?.mismatched_foreign_keys ?? [];
  const disabledForeignKeys = data?.disabled_foreign_keys ?? [];
  // データを消す未適用の migration（#619。RAG と Agent が返す。partial のときも返る）。
  const destructiveMigrations = data?.pending_destructive_migrations ?? [];
  const schemaHead = data ? schemaHeadDisplay(data) : null;
  // 参照先のない行の削除は、権限・API・確認ダイアログがそろったときだけ出す（#511）。
  const canDeleteOrphans =
    canManage && Boolean(api.deleteSystemTableOrphanedRows) && Boolean(confirmDeleteOrphans);
  // 操作できない状態と、現在処理が動いている状態を分ける。取得失敗は処理中ではない。定期の取り直し
  // （平時 60 秒・操作中 4〜30 秒。#5278）は処理中にも操作できない状態にもしない（押した取り直しだけ）。
  const activityBusy = statusQuery.isPending || manualRefresh.pending || mutationPending || schemaOperationRunning;
  const busy =
    manualRefresh.pending ||
    statusUnavailable ||
    !data ||
    systemTableControlsBusy(mutationPending, data.operation_state.status);

  useEffect(() => {
    if (operationError) operationErrorRef.current?.focus();
  }, [operationError]);

  // 見ている操作（画面で始めた操作と、running を見た外の操作）。終わったら結果を知らせ、cache を捨てる（#5278）。
  const watchedRef = useRef<WatchedSystemTableOperation | null>(null);
  useEffect(() => {
    if (!data || operation.isPending) return;
    const state = data.operation_state;
    if (state.status === "running") {
      watchedRef.current ??= {
        kind: state.operation_kind ?? "initialize",
        epoch: state.schema_epoch,
        schemaStatus: data.status,
      };
      return;
    }
    const watched = watchedRef.current;
    if (!watched) return;
    watchedRef.current = null;
    // 終わった（成功・失敗・止まった可能性）。DB ゲートと製品の cache を取り直す。
    void queryClient.invalidateQueries({ queryKey: DATABASE_STATUS_QUERY_KEY });
    for (const queryKey of invalidateQueryKeys ?? []) void queryClient.invalidateQueries({ queryKey });
    const completed = systemTableCompletedOperation(watched, data);
    if (!completed) return;
    // 再起動が要るときは、結果を操作の直下の Banner 1 か所に出す（Toast と重ねない。messaging.md §10.1）。
    if (completed.restartRequired?.processes.length) {
      setRestartNotice({ operation: completed.operation, restart: completed.restartRequired });
      return;
    }
    toast.success(text(`settings.database.systemTables.operation.${completed.operation}`));
    // 結果の文言は製品の辞書で変わらないので、data の変化だけで判定する。
  }, [data, operation.isPending]);

  const describeError = (cause: unknown) =>
    describeOperationError?.(cause) ??
    `${text("settings.database.systemTables.error.operation")} ${text("settings.database.systemTables.error.recovery")}`;

  const execute = (recreate: boolean, allowDestructive = false) => {
    if (busy || !canManage || (recreate && !recreateConfirmed)) return;
    setRecreateInput("");
    setOperationError("");
    setRestartNotice(null);
    // 始まる前の epoch と表の状態から、終わったときの結果を求める（#5278）。
    watchedRef.current = {
      kind: recreate ? "recreate" : "initialize",
      epoch: data?.operation_state.schema_epoch ?? 0,
      schemaStatus: data?.status ?? "missing",
    };
    operation.mutate(
      {
        recreate,
        confirmation: recreate ? phrase : undefined,
        ...(allowDestructive ? { allow_destructive: true } : {}),
      },
      {
        onError: (cause) => {
          watchedRef.current = null;
          setOperationError(describeError(cause));
        },
      },
    );
  };

  // データを消す未適用の migration があれば、確認ダイアログで承認させてから送る（#619）。
  const requestInitialize = async () => {
    if (busy || !canManage) return;
    if (destructiveMigrations.length === 0 || !confirmDestructiveMigrations) {
      execute(false);
      return;
    }
    const params = {
      count: formatNumber(destructiveMigrations.length),
      names: destructiveMigrations.map((migration) => migration.name).join(", "),
    };
    const confirmed = await confirmDestructiveMigrations({
      title: text("settings.database.systemTables.destructive.confirmTitle", params),
      description: text("settings.database.systemTables.destructive.confirmDescription", params),
      confirmLabel: text("settings.database.systemTables.destructive.confirmLabel"),
    });
    if (confirmed) execute(false, true);
  };

  const requestDeleteOrphans = async (foreignKey: SystemTableForeignKey) => {
    const count = foreignKey.orphan_rows;
    if (busy || !canDeleteOrphans || !confirmDeleteOrphans || count == null) return;
    const params = {
      table: foreignKey.table_name,
      name: foreignKey.name,
      columns: foreignKey.columns.join(", "),
      referenced: foreignKey.referenced_table_name,
      count: formatNumber(count),
    };
    const confirmed = await confirmDeleteOrphans({
      title: text("settings.database.systemTables.deleteOrphans.confirmTitle", params),
      description: text("settings.database.systemTables.deleteOrphans.confirmDescription", params),
      confirmLabel: text("settings.database.systemTables.action.deleteOrphans"),
    });
    if (!confirmed) return;
    setRecreateInput("");
    setOperationError("");
    orphanDeletion.mutate(
      // 確認した件数を送る（backend は数え直し、増えていたら削除しない）。
      { constraint_name: foreignKey.name, expected_orphan_rows: count },
      {
        onSuccess: (result) => {
          toast.success(
            result.operation === "no_op"
              ? text("settings.database.systemTables.deleteOrphans.noOp", { name: foreignKey.name })
              : text("settings.database.systemTables.deleteOrphans.done", {
                  ...params,
                  count: formatNumber(result.deleted_row_count),
                }),
          );
        },
        onError: (cause) => setOperationError(describeError(cause)),
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
    setRestartNotice(null);
    const result = await manualRefresh.track(() => statusQuery.refetch());
    if (!result.error) toast.success(text("settings.database.systemTables.refreshed"));
  };

  const statusLabel = (status: SystemTableSchemaStatus) =>
    text(`settings.database.systemTables.status.${status}`);

  // 状態を取得できないときの DB の案内（再試行つき）。製品の案内と同じ枠に並べる。
  const databaseItem = useDatabaseUnavailableBannerItem({
    routes: databaseRoutes,
    messages: databaseMessages,
    onRetry: () => void refreshStatus(),
    isRetrying: manualRefresh.pending,
  });

  // カードの案内は 1 つの枠にまとめる（messaging.md §3.4.3。#5183）。色と見出しは最も重い事柄
  // （前回・今回の失敗は danger）。見出しは見出しの StatusBadge の文字を繰り返さない。
  // 取り直しに失敗しても前回の状態（data）は残るので、DB の案内とカードの案内を同じ枠に並べる。
  const cardItems: Array<CombinedBannerItem | null | false | undefined> = data
      ? [
          operationError
            ? {
                id: "operation-error",
                severity: "danger",
                title: text("settings.database.systemTables.error.operationTitle"),
                children: operationError,
                testId: "system-tables-operation-error",
              }
            : schemaOperationStale
              ? {
                  id: "stale-operation",
                  severity: "warning",
                  title: text("settings.database.systemTables.stale.title"),
                  testId: "system-tables-stale-operation",
                  children: (
                    <p className="[overflow-wrap:anywhere]">
                      {text("settings.database.systemTables.stale.detail", {
                        operation: operationKindLabel(data.operation_state, text),
                        startedAt: formatTime(data.operation_state.started_at),
                        expiresAt: formatTime(data.operation_state.lease_expires_at),
                      })}
                    </p>
                  ),
                }
              : data.operation_state.status === "failed"
              ? {
                  id: "previous-failure",
                  severity: "danger",
                  title: text("settings.database.systemTables.previousFailure"),
                  testId: "system-tables-previous-failure",
                  // 本文は原因と対処だけにし、エラーコードは「詳細」に分ける（失敗なので開いて出す。messaging.md §10.3。#722）。
                  children: (
                    <div className="space-y-2">
                      {/* 製品が失敗の原因と対処を返したときはそれを出す（vector memory の不足など。#5261）。
                          長い識別子（索引の名前・文書のパス）は 375px で折り返す。 */}
                      <p className="[overflow-wrap:anywhere]">
                        {data.operation_state.last_error_detail ||
                          (data.operation_state.last_error_code === "ORA-00054"
                            ? text("settings.database.systemTables.previousFailureLockDetail")
                            : text("settings.database.systemTables.previousFailureDetail"))}
                      </p>
                      {data.operation_state.last_error_code ? (
                        <Disclosure
                          variant="plain"
                          size="sm"
                          summary={text("settings.database.systemTables.previousFailureDetails")}
                          defaultOpen
                        >
                          <p className="text-xs text-fg-muted">
                            {text("settings.database.systemTables.previousFailureErrorCode", {
                              code: data.operation_state.last_error_code,
                            })}
                          </p>
                        </Disclosure>
                      ) : null}
                    </div>
                  ),
                }
              : null,
          schemaNoticeItem({
            data,
            destructiveMigrations,
            missingColumns,
            missingForeignKeys,
            mismatchedForeignKeys,
            disabledForeignKeys,
            showApprovalHint: canManage && Boolean(confirmDestructiveMigrations),
            text,
          }),
          orphanedForeignKeys.length > 0
            ? {
                id: "orphaned-foreign-keys",
                severity: "warning",
                title: text("settings.database.systemTables.foreignKeys.orphanedTitle"),
                testId: "system-tables-orphaned-foreign-keys",
                children: (
                  <>
                    <p>{text("settings.database.systemTables.foreignKeys.orphaned")}</p>
                    {canDeleteOrphans ? (
                      <p className="mt-1">{text("settings.database.systemTables.foreignKeys.orphanedDeleteHint")}</p>
                    ) : null}
                    <ForeignKeyList
                      foreignKeys={orphanedForeignKeys}
                      text={text}
                      label={text("settings.database.systemTables.foreignKeys.orphanedTitle")}
                      renderAction={
                        canDeleteOrphans
                          ? (foreignKey) =>
                              foreignKey.orphan_rows ? (
                                // 確認ダイアログを開く起点なので赤塗りにしない（secondary + 赤文字。buttons.md §3）。
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="secondary"
                                  tone="danger"
                                  icon={Trash2}
                                  onClick={() => void requestDeleteOrphans(foreignKey)}
                                  loading={
                                    orphanDeletion.isPending &&
                                    orphanDeletion.variables?.constraint_name === foreignKey.name
                                  }
                                  disabled={
                                    busy &&
                                    !(
                                      orphanDeletion.isPending &&
                                      orphanDeletion.variables?.constraint_name === foreignKey.name
                                    )
                                  }
                                  aria-label={text("settings.database.systemTables.action.deleteOrphansLabel", {
                                    name: foreignKey.name,
                                  })}
                                >
                                  {text("settings.database.systemTables.action.deleteOrphans")}
                                </Button>
                              ) : null
                          : undefined
                      }
                    />
                  </>
                ),
              }
            : null,
          // 作成・更新の結果で再起動が要るプロセス（#1844。Toast と重ねない）。
          restartNotice
            ? {
                id: "restart-required",
                severity: "warning",
                title: text("settings.database.systemTables.restartRequired.title", {
                  operation: text(`settings.database.systemTables.operation.${restartNotice.operation}`),
                }),
                testId: "system-tables-restart-required",
                children: (
                  <div className="space-y-1.5">
                    <p>{restartNotice.restart.message}</p>
                    <p className="font-medium">{text("settings.database.systemTables.restartRequired.processes")}</p>
                    <ul className="list-disc space-y-1 pl-5">
                      {restartNotice.restart.processes.map((process) => (
                        <li key={process} className="[overflow-wrap:anywhere]">
                          {process}
                        </li>
                      ))}
                    </ul>
                  </div>
                ),
              }
            : null,
          !canManage
            ? {
                id: "read-only",
                severity: "info",
                children: text("settings.database.systemTables.readOnly"),
                testId: "system-tables-read-only",
              }
            : null,
        ]
      : [];
  const noticeItems = [statusUnavailable ? databaseItem : null, ...cardItems, ...notices];
  const hasNotice = combinedBannerItems(noticeItems).length > 0;
  // 操作の失敗は案内の枠へフォーカスを移す（枠は 1 つなので、失敗の項目も同じ枠の中）。
  const noticeBlock = hasNotice ? (
    <div
      ref={operationErrorRef}
      tabIndex={-1}
      data-testid="system-tables-notice"
      className="rounded-lg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
    >
      <CombinedBanner items={noticeItems} />
    </div>
  ) : null;

  return (
    <Card id="system-tables" className="min-w-0 max-w-full scroll-mt-24 rounded-md" aria-busy={activityBusy}>
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
              {schemaOperationStale ? (
                <StatusBadge variant="warning" label={text("settings.database.systemTables.operation.stale")} />
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

        {statusUnavailable ? noticeBlock : null}

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
                value={schemaHead?.value ?? ""}
                description={schemaHead?.name}
                descriptionClassName="font-mono [overflow-wrap:anywhere]"
              />
              <SummaryItem
                label={text("settings.database.systemTables.summary.epoch")}
                value={formatNumber(data.operation_state.schema_epoch)}
              />
            </div>

            {statusUnavailable ? null : noticeBlock}

            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
              {canManage ? (
                <Button
                  type="button"
                  size="md"
                  onClick={() => void requestInitialize()}
                  loading={initializeInProgress}
                  // loading のボタンに disabled を重ねない（フォーカスを外さない。buttons.md §8）。
                  disabled={busy && !initializeInProgress}
                  icon={DatabaseZap}
                >
                  {text("settings.database.systemTables.action.initialize")}
                </Button>
              ) : null}
              {runningKind === "initialize" && operationState ? (
                <OperationProgress state={operationState} text={text} />
              ) : null}
              <Button
                type="button"
                size="md"
                variant="secondary"
                onClick={() => void refreshStatus()}
                loading={manualRefresh.pending}
                disabled={mutationPending}
                icon={RefreshCw}
              >
                {text("settings.database.systemTables.action.refresh")}
              </Button>
            </div>

            {data.vector_memory ? <VectorMemorySummary vectorMemory={data.vector_memory} text={text} /> : null}

            <SystemTablesDetails data={data} text={text} renderObjectName={renderObjectName} />

            {canManage ? (
              <section className="border-t border-border pt-5" aria-labelledby="recreate-system-tables-title">
                {/* 影響の説明を読み、直下の確認語を入力して実行する。 */}
                <div className="grid gap-3">
                  <div className="flex items-start gap-2">
                    {/* 見出しの 1 行目の中心にそろえる（見出しと同じ文字の大きさの行の高さの箱。#2371）。 */}
                    <span className="pr-icon-line text-sm text-danger-fg">
                      <AlertTriangle size={16} aria-hidden />
                    </span>
                    <div>
                      <h3 id="recreate-system-tables-title" className="text-sm font-semibold text-fg">
                        {text("settings.database.systemTables.recreate.sectionTitle")}
                      </h3>
                      <p className="mt-1 text-xs leading-relaxed text-fg-muted">
                        {text("settings.database.systemTables.recreate.sectionDescription")}
                      </p>
                    </div>
                  </div>
                  <ExecutionConfirmationField
                    value={recreateInput}
                    onChange={setRecreateInput}
                    confirmed={recreateConfirmed}
                    expectedLabel={phrase}
                    helper={text("settings.database.systemTables.confirmation.helper", { phrase })}
                    disabled={busy}
                    labels={{
                      label: text("settings.database.systemTables.confirmation.label"),
                      required: text("settings.database.systemTables.confirmation.required"),
                      // `{phrase}` の位置に確認語を差し込むのは共有の確認語欄が行う。
                      expected: text("settings.database.systemTables.confirmation.expected", { phrase: "{phrase}" }),
                      pending: text("settings.database.systemTables.confirmation.status.pending"),
                      mismatch: text("settings.database.systemTables.confirmation.status.mismatch"),
                      confirmed: text("settings.database.systemTables.confirmation.status.confirmed"),
                    }}
                    actions={
                      <Button
                        type="button"
                        size="lg"
                        variant="danger"
                        className="w-full sm:w-auto"
                        onClick={() => void requestRecreate()}
                        loading={recreateInProgress}
                        disabled={!recreateInProgress && (busy || !recreateConfirmed)}
                        icon={RotateCcw}
                      >
                        {text("settings.database.systemTables.action.recreate")}
                      </Button>
                    }
                  />
                  {runningKind === "recreate" && operationState ? (
                    <OperationProgress state={operationState} text={text} />
                  ) : null}
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

/** ISO 文字列を「HH:mm」へ。未設定・無効値はダッシュ。 */
function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return timeFormat.format(iso);
}

function operationKindLabel(state: SystemTableOperationState, text: Text): string {
  return text(
    state.operation_kind === "recreate"
      ? "settings.database.systemTables.operation.kind.recreate"
      : "settings.database.systemTables.operation.kind.initialize",
  );
}

/**
 * 操作中のボタンの隣の「〈操作〉を実行中（開始 hh:mm・経過 n 分）」（#5278）。ボタンのラベルは変えない
 * （buttons.md §8）ため、状態の文言はここに出す。経過は backend の開始時刻から分の単位で数え、15 秒ごとに
 * 描き直す（1 秒ごとに読み上げない timer）。
 */
function OperationProgress({ state, text }: { state: SystemTableOperationState; text: Text }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), ELAPSED_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  const startedAt = state.started_at ? Date.parse(state.started_at) : Number.NaN;
  const minutes = Number.isFinite(startedAt) ? Math.floor(Math.max(0, now - startedAt) / 60_000) : null;
  const elapsed =
    minutes == null || minutes < 1
      ? text("settings.database.systemTables.operation.elapsedUnderMinute")
      : text("settings.database.systemTables.operation.elapsedMinutes", { minutes: formatNumber(minutes) });
  return (
    <p
      role="timer"
      aria-live="off"
      data-testid="system-tables-operation-progress"
      className="min-w-0 text-xs leading-relaxed text-fg-muted tabular-nums [overflow-wrap:anywhere]"
    >
      {text("settings.database.systemTables.operation.progress", {
        operation: operationKindLabel(state, text),
        startedAt: formatTime(state.started_at),
        elapsed,
      })}
    </p>
  );
}

/**
 * HNSW 索引ごとの vector memory の「見込みの必要量 / 今の空き」（#5278）。読めない値は「取得できません」。
 * 見込みが空きを超え、索引がまだ vector memory に無いときは、アイコンと文で知らせる（色だけにしない）。
 */
function VectorMemorySummary({ vectorMemory, text }: { vectorMemory: SystemTableVectorMemory; text: Text }) {
  const unavailable = vectorMemory.error_code
    ? text("settings.database.systemTables.vectorMemory.unavailableWithCode", { code: vectorMemory.error_code })
    : text("settings.database.systemTables.vectorMemory.unavailable");
  const free = vectorMemory.available && vectorMemory.pool_free_bytes != null ? vectorMemory.pool_free_bytes : null;
  return (
    <section
      aria-labelledby="system-tables-vector-memory-title"
      data-testid="system-tables-vector-memory"
      data-surface-tint="neutral"
      className="min-w-0 rounded-md border border-border bg-surface-hover p-3"
    >
      <h3 id="system-tables-vector-memory-title" className="text-sm font-semibold text-fg">
        {text("settings.database.systemTables.vectorMemory.title")}
      </h3>
      <p className="mt-1 text-xs leading-relaxed text-fg-muted">
        {text("settings.database.systemTables.vectorMemory.description")}
      </p>
      <ul className="mt-2 space-y-2">
        {vectorMemory.indexes.map((index) => {
          const shortage =
            free != null && index.estimated_bytes != null && index.loaded !== true && index.estimated_bytes > free;
          return (
            <li key={index.name} className="min-w-0" data-testid="system-tables-vector-memory-index">
              <span className="block font-mono text-xs font-semibold text-fg [overflow-wrap:anywhere]">
                {index.name}
              </span>
              <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg">
                <div className="flex gap-1">
                  <dt className="text-fg-muted">{text("settings.database.systemTables.vectorMemory.estimated")}</dt>
                  <dd className="tabular-nums">
                    {index.estimated_bytes != null
                      ? formatMegabytes(index.estimated_bytes)
                      : index.error_code
                        ? text("settings.database.systemTables.vectorMemory.unavailableWithCode", {
                            code: index.error_code,
                          })
                        : text("settings.database.systemTables.vectorMemory.unavailable")}
                  </dd>
                </div>
                <div className="flex gap-1">
                  <dt className="text-fg-muted">{text("settings.database.systemTables.vectorMemory.free")}</dt>
                  <dd className="tabular-nums">{free != null ? formatMegabytes(free) : unavailable}</dd>
                </div>
                {index.loaded && index.used_bytes != null ? (
                  <div className="flex gap-1">
                    <dt className="text-fg-muted">{text("settings.database.systemTables.vectorMemory.used")}</dt>
                    <dd className="tabular-nums">{formatMegabytes(index.used_bytes)}</dd>
                  </div>
                ) : null}
              </dl>
              {shortage ? (
                <p className="mt-1 flex items-start gap-1.5 text-xs text-warning-fg">
                  <span className="pr-icon-line">
                    <AlertTriangle size={14} aria-hidden />
                  </span>
                  <span>{text("settings.database.systemTables.vectorMemory.shortage")}</span>
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * 表の状態（missing / partial / outdated）とデータを消す未適用の migration を、案内の枠の 1 項目にする（#5183）。
 * 見出しは最も重い事柄（削除の migration があれば「データを削除する更新があります」、無ければ状態の事柄）。
 * 本文は「作成・更新」ですること → 削除の migration の明細 → 列・外部キーの差分 → 削除の確認の案内の順。
 * partial は outdated を上書きして返る（backend）ので、削除の migration は状態に関わらず扱う。
 */
function schemaNoticeItem({
  data,
  destructiveMigrations,
  missingColumns,
  missingForeignKeys,
  mismatchedForeignKeys,
  disabledForeignKeys,
  showApprovalHint,
  text,
}: {
  data: SystemTablesStatusData;
  destructiveMigrations: SystemTableDestructiveMigration[];
  missingColumns: NonNullable<SystemTablesStatusData["missing_columns"]>;
  missingForeignKeys: SystemTableForeignKey[];
  mismatchedForeignKeys: SystemTableForeignKey[];
  disabledForeignKeys: SystemTableForeignKey[];
  showApprovalHint: boolean;
  text: Text;
}): CombinedBannerItem | null {
  const destructive = destructiveMigrations.length > 0;
  if (data.status === "ready" && !destructive) return null;
  const count = data.missing_objects.length;
  // ready のまま削除の migration だけが残ることは無いが、残れば outdated と同じ文にする。
  const status = data.status === "ready" ? "outdated" : data.status;
  const differences = (
    <>
      {missingColumns.length > 0 ? (
        <div className="mt-2" data-testid="system-tables-missing-columns">
          <p>{text("settings.database.systemTables.columns.missing", { count: missingColumns.length })}</p>
          {/* スキーマが古いと件数が増えるので、行リストの高さで止める（#4636）。 */}
          <ListScrollRegion
            className="mt-1.5"
            label={text("settings.database.systemTables.columns.missing", { count: missingColumns.length })}
          >
            <ul className="space-y-1">
              {missingColumns.map((column) => (
                <li
                  key={`${column.table_name}.${column.column_name}`}
                  className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]"
                >
                  {column.table_name}.{column.column_name}
                </li>
              ))}
            </ul>
          </ListScrollRegion>
        </div>
      ) : null}
      {missingForeignKeys.length > 0 ? (
        <div className="mt-2" data-testid="system-tables-missing-foreign-keys">
          <p>{text("settings.database.systemTables.foreignKeys.missing", { count: missingForeignKeys.length })}</p>
          <ForeignKeyList
            foreignKeys={missingForeignKeys}
            text={text}
            label={text("settings.database.systemTables.foreignKeys.missing", { count: missingForeignKeys.length })}
          />
        </div>
      ) : null}
      {mismatchedForeignKeys.length > 0 ? (
        <div className="mt-2" data-testid="system-tables-mismatched-foreign-keys">
          <p>
            {text("settings.database.systemTables.foreignKeys.mismatched", { count: mismatchedForeignKeys.length })}
          </p>
          <ForeignKeyList
            foreignKeys={mismatchedForeignKeys}
            text={text}
            label={text("settings.database.systemTables.foreignKeys.mismatched", {
              count: mismatchedForeignKeys.length,
            })}
            showDeleteRule
          />
        </div>
      ) : null}
      {disabledForeignKeys.length > 0 ? (
        <div className="mt-2" data-testid="system-tables-disabled-foreign-keys">
          <p>{text("settings.database.systemTables.foreignKeys.disabled", { count: disabledForeignKeys.length })}</p>
          <ForeignKeyList
            foreignKeys={disabledForeignKeys}
            text={text}
            label={text("settings.database.systemTables.foreignKeys.disabled", { count: disabledForeignKeys.length })}
          />
        </div>
      ) : null}
    </>
  );

  if (destructive) {
    return {
      id: "schema",
      severity: "warning",
      title: text("settings.database.systemTables.destructive.title"),
      testId: "system-tables-destructive-migrations",
      children: (
        <>
          <p>{text(`settings.database.systemTables.destructive.plan.${status}`, { count })}</p>
          <DestructiveMigrationList migrations={destructiveMigrations} />
          {differences}
          <p className="mt-2">
            {text("settings.database.systemTables.destructive.irreversible")}
            {showApprovalHint ? <> {text("settings.database.systemTables.destructive.approvalHint")}</> : null}
          </p>
        </>
      ),
    };
  }
  return {
    id: "schema",
    severity: status === "missing" ? "info" : "warning",
    title: text(`settings.database.systemTables.notice.${status}`, { count }),
    testId: "system-tables-schema-notice",
    children: (
      <>
        <p>{text(`settings.database.systemTables.statusHint.${status}`)}</p>
        {differences}
      </>
    ),
  };
}

/**
 * 外部キーの一覧（子の表と列 → 参照先の表。参照先のない行があれば件数を添える）。
 * 削除規則の違い（#511）は「削除規則 現在 → 正本」を添え、行ごとの操作（参照先のない行の削除）は右に置く
 * （狭い幅では折り返して下に出す）。
 * 件数・削除規則は Banner の地の上に置くため、文字色は Banner の本文色を継ぎ、太さで区別する
 * （`text-fg-muted` はライトの warning の地で 4.34:1 になり、4.5:1 に届かない。#511）。
 */
function ForeignKeyList({
  foreignKeys,
  text,
  showDeleteRule = false,
  renderAction,
  label,
}: {
  foreignKeys: SystemTableForeignKey[];
  text: Text;
  showDeleteRule?: boolean;
  renderAction?: (foreignKey: SystemTableForeignKey) => ReactNode;
  /** 一覧のスクロール領域の読み上げ名。 */
  label: string;
}) {
  // 件数に上限が無いので、行リストの高さで止める（#4636）。
  return (
    <ListScrollRegion className="mt-1.5" label={label}>
      <ul className={renderAction ? "space-y-2" : "space-y-1"}>
        {foreignKeys.map((foreignKey) => {
          const action = renderAction?.(foreignKey);
          return (
            <li
              key={foreignKey.name}
              className={cn(
                "min-w-0",
                action ? "flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5" : undefined,
              )}
            >
              <span className="min-w-0">
                <span className="font-mono text-xs [overflow-wrap:anywhere]">
                  {text("settings.database.systemTables.foreignKeys.item", {
                    table: foreignKey.table_name,
                    columns: foreignKey.columns.join(", "),
                    referenced: foreignKey.referenced_table_name,
                  })}
                </span>
                {showDeleteRule && foreignKey.current_delete_rule ? (
                  <span className="ml-2 text-xs font-medium">
                    {text("settings.database.systemTables.foreignKeys.deleteRule", {
                      current: foreignKey.current_delete_rule,
                      expected: foreignKey.delete_rule,
                    })}
                  </span>
                ) : null}
                {foreignKey.orphan_rows ? (
                  <span className="ml-2 text-xs font-medium">
                    {text("settings.database.systemTables.foreignKeys.orphanRows", {
                      count: formatNumber(foreignKey.orphan_rows),
                    })}
                  </span>
                ) : null}
              </span>
              {action}
            </li>
          );
        })}
      </ul>
    </ListScrollRegion>
  );
}

/**
 * データを消す未適用の migration の一覧（名前は等幅、説明は Banner の本文色のまま。#619）。
 * 説明は backend が返す（消えるデータと、適用の前にすること）。
 */
function DestructiveMigrationList({ migrations }: { migrations: SystemTableDestructiveMigration[] }) {
  return (
    <ul className="mt-1.5 space-y-1.5">
      {migrations.map((migration) => (
        <li key={migration.name} className="min-w-0">
          <span className="block font-mono text-xs font-medium [overflow-wrap:anywhere]">{migration.name}</span>
          <span className="block text-xs leading-relaxed">{migration.description}</span>
        </li>
      ))}
    </ul>
  );
}

function SummaryItem({
  label,
  value,
  description,
  descriptionClassName,
}: {
  label: string;
  value: string;
  description?: string;
  descriptionClassName?: string;
}) {
  return (
    <div data-surface-tint="neutral" className="min-w-0 rounded-md border border-border bg-surface-hover p-3">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className="mt-1 break-words font-sans text-sm font-semibold text-fg">{value}</p>
      {description ? (
        <p className={cn("mt-1 text-xs leading-relaxed text-fg-muted", descriptionClassName)}>{description}</p>
      ) : null}
    </div>
  );
}


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
    <Disclosure
      summary={text("settings.database.systemTables.details.title", counts)}
      summaryClassName="px-4 py-3 font-medium"
      contentClassName="p-4"
    >
        {/* 適用済みは件数だけにする（migration は増え続け、全件を並べても判断に使えない）。
            未適用・不一致は「作成・更新」で直す対象なので名前を出す（#658）。 */}
        <p className="mb-3 break-words text-xs leading-relaxed text-fg-muted" data-testid="system-tables-versions">
          {text("settings.database.systemTables.details.versions", {
            applied: formatNumber(data.applied_versions.length),
            pending: formatNumber(data.pending_versions.length),
          })}
          {data.pending_versions.length > 0 ? (
            <span className="block font-mono [overflow-wrap:anywhere]">
              {text("settings.database.systemTables.details.pendingVersions", {
                versions: data.pending_versions.map(formatVersion).join(", "),
              })}
            </span>
          ) : null}
        </p>
        {/* 一覧の件数はツールバーの右の ListCount（#1891）。見出しの「存在 / 必須」は状態の数で、一覧の件数とは別。
            ページングはしない: 製品ごとに決まった数十件の管理オブジェクトを、種類・状態を見比べて点検する一覧で、
            ページに分けると不足しているオブジェクトが別のページに隠れる（表の中は 8 行で縦スクロール）。 */}
        <ListToolbar
          className="mb-3"
          count={{ total: objects.length, live: false, testId: "system-tables-count" }}
          testId="system-tables-toolbar"
        />
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
          rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
          tableClassName="w-full min-w-[60rem]"
          scrollAriaLabel={text("settings.database.systemTables.table.scrollLabel", counts)}
          scrollTestId="system-tables-scroll-region"
          stickyHeader
          visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
        />
    </Disclosure>
  );
}
