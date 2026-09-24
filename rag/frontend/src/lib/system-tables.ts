import type { SystemTablesStatusData, SystemTableOperationStatus } from "./api";

export const RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION =
  "RECREATE_RAG_SYSTEM_TABLES";

/** API mutation または DB lease が実行中なら重複操作を止める。 */
export function systemTableControlsBusy(
  mutationPending: boolean,
  operationStatus: SystemTableOperationStatus | undefined
): boolean {
  return mutationPending || operationStatus === "running";
}

/** 破壊的操作は前後空白を除いた完全一致だけを許可する。 */
export function isSystemTableRecreateConfirmationValid(value: string): boolean {
  return value.trim() === RECREATE_RAG_SYSTEM_TABLES_CONFIRMATION;
}

/**
 * status payload が UI の必須フィールドを満たすかを判定する。
 *
 * 1 枚のカードが想定外 payload で throw すると、error boundary が無い設定ページでは
 * 兄弟カードごと unmount されページが空になる。描画前にここで弾き、
 * 通常のエラー表示へ落とす。
 */
export function isSystemTablesStatusData(
  value: unknown
): value is SystemTablesStatusData {
  if (typeof value !== "object" || value === null) return false;
  const data = value as Partial<SystemTablesStatusData>;
  const operationState = data.operation_state;
  return (
    typeof data.status === "string" &&
    typeof operationState === "object" &&
    operationState !== null &&
    typeof operationState.status === "string" &&
    Array.isArray(data.missing_objects) &&
    Array.isArray(data.retired_objects) &&
    Array.isArray(data.tables)
  );
}
