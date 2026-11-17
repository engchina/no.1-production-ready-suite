import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";

import { DATABASE_STATUS_QUERY_KEY } from "../database-gate/types";
import {
  SYSTEM_TABLES_QUERY_KEY,
  type SystemObjectMetadata,
  type SystemTableOperationResult,
  type SystemTableOperationStatus,
  type SystemTableRestartRequired,
  type SystemTablesApi,
  type SystemTablesDeleteOrphansRequest,
  type SystemTablesInitializeRequest,
  type SystemTablesStatusData,
} from "./types";

/** 操作中（lease が running）の最初の 1 分に状態を取り直す間隔（#5278）。 */
export const SYSTEM_TABLES_RUNNING_REFETCH_MS = 4000;
/** 操作中の取り直しを延ばす上限（1 分を過ぎたら 1 分ごとに 1.5 倍）。 */
export const SYSTEM_TABLES_RUNNING_REFETCH_MAX_MS = 30_000;
/** 平時に状態を取り直す間隔（外の操作（CLI・配備・別のタブ）の開始に気づくため）。 */
export const SYSTEM_TABLES_IDLE_REFETCH_MS = 60_000;
const RUNNING_FAST_PERIOD_MS = 60_000;
const RUNNING_BACKOFF_FACTOR = 1.5;

/**
 * 状態を取り直す間隔（#5278）。操作中は最初の 1 分は 4 秒ごと、その後は 1 分ごとに 1.5 倍に延ばし
 * 最大 30 秒。それ以外（平時・止まった可能性・失敗）は 60 秒ごと。経過は backend の開始時刻から測り、
 * 無ければ最初の間隔にする。タブが裏にある間は TanStack Query が止める（`refetchIntervalInBackground`）。
 */
export function systemTablesRefetchInterval(
  data: SystemTablesStatusData | undefined,
  now: number = Date.now(),
): number {
  if (!data || data.operation_state.status !== "running") return SYSTEM_TABLES_IDLE_REFETCH_MS;
  const startedAt = data.operation_state.started_at ? Date.parse(data.operation_state.started_at) : Number.NaN;
  if (!Number.isFinite(startedAt)) return SYSTEM_TABLES_RUNNING_REFETCH_MS;
  const elapsed = Math.max(0, now - startedAt);
  if (elapsed <= RUNNING_FAST_PERIOD_MS) return SYSTEM_TABLES_RUNNING_REFETCH_MS;
  const steps = Math.ceil((elapsed - RUNNING_FAST_PERIOD_MS) / RUNNING_FAST_PERIOD_MS);
  return Math.min(
    SYSTEM_TABLES_RUNNING_REFETCH_MAX_MS,
    Math.round(SYSTEM_TABLES_RUNNING_REFETCH_MS * RUNNING_BACKOFF_FACTOR ** steps),
  );
}

/** API の mutation か DB の lease が実行中なら、重複する操作を止める（stale は止めない）。 */
export function systemTableControlsBusy(
  mutationPending: boolean,
  operationStatus: SystemTableOperationStatus | undefined,
): boolean {
  return mutationPending || operationStatus === "running";
}

/** 画面が見ていた操作（running を見た時点の状態）。終わりを状態の差から求めるのに使う。 */
export interface WatchedSystemTableOperation {
  kind: "initialize" | "recreate";
  /** 始まる前（または running を最初に見た時点）の schema epoch。 */
  epoch: number;
  /** 始まる前（または running を最初に見た時点）の表の状態。 */
  schemaStatus: SystemTablesStatusData["status"];
}

/**
 * 見ていた操作が終わった（running でなくなった）ときの結果（#5278）。idle 以外（失敗・止まった可能性）は
 * null（案内の枠が別に出す）。同じプロセスの要約（`last_operation`）が同じ epoch なら優先し、無ければ
 * epoch の差と操作の種類から求める（epoch が増えなければ変更なし）。
 */
export function systemTableCompletedOperation(
  watched: WatchedSystemTableOperation,
  data: SystemTablesStatusData,
): { operation: SystemTableOperationResult; restartRequired: SystemTableRestartRequired | null } | null {
  if (data.operation_state.status !== "idle") return null;
  const epoch = data.operation_state.schema_epoch;
  const last = data.last_operation;
  if (last?.operation && last.schema_epoch === epoch) {
    return { operation: last.operation, restartRequired: last.restart_required ?? null };
  }
  const operation: SystemTableOperationResult =
    epoch <= watched.epoch
      ? "no_op"
      : watched.kind === "recreate"
        ? "recreated"
        : watched.schemaStatus === "missing"
          ? "initialized"
          : "migrated";
  return { operation, restartRequired: null };
}

/** 全再作成の確認語は、前後の空白を除いた完全一致だけを許す（backend は完全一致で再検証する）。 */
export function isSystemTableRecreateConfirmationValid(value: string, phrase: string): boolean {
  return value.trim() === phrase;
}

/**
 * 状態の payload が画面の描画に必要な項目を満たすか（浅い検査）。
 * 想定外の形を描画して throw すると、同じページの兄弟カードごと消えるため、描画前に弾いて
 * 取得失敗の表示へ落とす。深い階層の破綻は製品の error boundary が受け止める。
 */
export function isSystemTablesStatusData(value: unknown): value is SystemTablesStatusData {
  if (typeof value !== "object" || value === null) return false;
  const data = value as Partial<SystemTablesStatusData>;
  const operationState = data.operation_state;
  return (
    typeof data.status === "string" &&
    typeof operationState === "object" &&
    operationState !== null &&
    typeof operationState.status === "string" &&
    Array.isArray(data.missing_objects) &&
    (data.retired_objects === undefined || Array.isArray(data.retired_objects)) &&
    (data.missing_columns === undefined || Array.isArray(data.missing_columns)) &&
    (data.missing_foreign_keys === undefined || Array.isArray(data.missing_foreign_keys)) &&
    (data.orphaned_foreign_keys === undefined || Array.isArray(data.orphaned_foreign_keys)) &&
    (data.mismatched_foreign_keys === undefined || Array.isArray(data.mismatched_foreign_keys)) &&
    (data.disabled_foreign_keys === undefined || Array.isArray(data.disabled_foreign_keys)) &&
    (data.pending_destructive_migrations === undefined || Array.isArray(data.pending_destructive_migrations)) &&
    Array.isArray(data.tables)
  );
}

/** 詳細の表の行。全管理 object が無い応答（RAG・旧 NL2SQL）はテーブルだけを並べる。 */
export function systemTableObjects(data: SystemTablesStatusData): SystemObjectMetadata[] {
  if (data.objects) return data.objects;
  return data.tables.map((table) => ({ ...table, object_type: "TABLE" }));
}

/** 詳細の見出しの件数（全管理 object が無いときはテーブルの件数）。 */
export function systemTableDetailCounts(data: SystemTablesStatusData): {
  existing: number;
  expected: number;
} {
  return data.objects
    ? { existing: data.existing_object_count, expected: data.expected_object_count }
    : { existing: data.existing_table_count, expected: data.expected_table_count };
}

/**
 * 状態（DDL を実行しない）。定期的に取り直し（`systemTablesRefetchInterval`）、タブが裏にある間は止め、
 * 表に戻ったらすぐ取り直す。失敗は自動で再試行せず、失敗の間は定期の取り直しも止める。
 */
export function useSystemTablesStatus(api: SystemTablesApi) {
  return useQuery({
    queryKey: SYSTEM_TABLES_QUERY_KEY,
    queryFn: ({ signal }) => api.getSystemTablesStatus({ signal }),
    retry: false,
    refetchInterval: (query) =>
      query.state.status === "error"
        ? false
        : systemTablesRefetchInterval(
            isSystemTablesStatusData(query.state.data) ? query.state.data : undefined,
          ),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
  });
}

/**
 * 作成・更新 / 全再作成を始める（後台の操作。#5278）。応答（開始した時点の状態。running）で状態を
 * 置き換え、以後は状態の polling で終わりを見る。終わった後の cache の破棄は画面が行う。
 */
export function useInitializeSystemTables(api: SystemTablesApi) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SystemTablesInitializeRequest) => api.initializeSystemTables(payload),
    onMutate: () => qc.cancelQueries({ queryKey: SYSTEM_TABLES_QUERY_KEY }),
    onSuccess: (data) => {
      qc.setQueryData(SYSTEM_TABLES_QUERY_KEY, data);
      // 実行中の DB ゲート（setup_required）を反映する。
      void qc.invalidateQueries({ queryKey: DATABASE_STATUS_QUERY_KEY });
    },
    onError: () => {
      void qc.invalidateQueries({ queryKey: SYSTEM_TABLES_QUERY_KEY });
    },
  });
}

export interface UseInitializeSystemTablesOptions {
  /** 成功したときに捨てる製品の cache（DB の状態は常に捨てる）。 */
  invalidateQueryKeys?: readonly QueryKey[];
}

/**
 * 参照先のない行の削除（#511。API を持つ製品だけ）。成功したら結果で状態を置き換える。
 * 失敗（件数が増えた・前提が変わった等）のときは状態を取り直し、最新の件数で確認し直させる。
 */
export function useDeleteSystemTableOrphanedRows(
  api: SystemTablesApi,
  { invalidateQueryKeys = [] }: UseInitializeSystemTablesOptions = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SystemTablesDeleteOrphansRequest) => {
      if (!api.deleteSystemTableOrphanedRows) {
        return Promise.reject(new Error("参照先のない行の削除はこの製品では使えません。"));
      }
      return api.deleteSystemTableOrphanedRows(payload);
    },
    onMutate: () => qc.cancelQueries({ queryKey: SYSTEM_TABLES_QUERY_KEY }),
    onSuccess: (data) => {
      qc.setQueryData(SYSTEM_TABLES_QUERY_KEY, data);
      void qc.invalidateQueries({ queryKey: DATABASE_STATUS_QUERY_KEY });
      for (const queryKey of invalidateQueryKeys) void qc.invalidateQueries({ queryKey });
    },
    onError: () => {
      void qc.invalidateQueries({ queryKey: SYSTEM_TABLES_QUERY_KEY });
    },
  });
}
