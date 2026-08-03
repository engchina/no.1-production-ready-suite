import type { SystemTableOperationStatus } from "./api";

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
