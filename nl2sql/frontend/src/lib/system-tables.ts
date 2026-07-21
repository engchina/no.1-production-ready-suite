import type {
  SystemTableOperationResult,
  SystemTableOperationStatus,
  SystemTableSchemaStatus,
} from "@/lib/api";

export function systemTableStatusLabelKey(status: SystemTableSchemaStatus): string {
  return `settings.database.systemTables.status.${status}`;
}

export function systemTableOperationMessageKey(
  operation: SystemTableOperationResult
): string {
  return `settings.database.systemTables.operation.${operation}`;
}

export function systemTableControlsBusy(
  mutationPending: boolean,
  operationStatus: SystemTableOperationStatus | undefined
): boolean {
  return mutationPending || operationStatus === "running";
}
