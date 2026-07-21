import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type AdbSettingsUpdate,
  type DatabaseSettingsUpdate,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type SystemTablesInitializeRequest,
  type UploadStorageSettingsUpdate,
} from "@/lib/api";

export const queryKeys = {
  databaseStatus: ["ready", "database"] as const,
  persistenceStatus: ["nl2sql", "persistence"] as const,
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
  systemTables: ["settings", "database", "system-tables"] as const,
  schemaOwners: ["schema", "owners"] as const,
  adbInfo: ["settings", "database", "adb"] as const,
  uploadStorageSettings: ["settings", "upload-storage"] as const,
};

const ACTIVE_REFETCH_INTERVAL_MS = 4000;

const ADB_TRANSITIONAL_STATES: ReadonlySet<string> = new Set([
  "STARTING",
  "STOPPING",
  "PROVISIONING",
  "TERMINATING",
  "UPDATING",
  "RESTORING",
  "BACKUP_IN_PROGRESS",
  "MAINTENANCE_IN_PROGRESS",
  "ROLE_CHANGE_IN_PROGRESS",
]);

function adbIsTransitioning(state: string | null | undefined): boolean {
  return state != null && ADB_TRANSITIONAL_STATES.has(state);
}

export function useDatabaseStatus({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.databaseStatus,
    queryFn: ({ signal }) => api.getDatabaseStatus({ signal }),
    enabled,
    staleTime: 15_000,
    retry: false,
  });
}

export function usePersistenceStatus({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.persistenceStatus,
    queryFn: ({ signal }) => api.getPersistenceStatus({ signal }),
    enabled,
    retry: false,
  });
}

export function useRecoverPersistence() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.recoverPersistence,
    onSuccess: (data) => qc.setQueryData(queryKeys.persistenceStatus, data),
  });
}

export function useModelSettings() {
  return useQuery({
    queryKey: queryKeys.modelSettings,
    queryFn: ({ signal }) => api.getModelSettings({ signal }),
  });
}

export function useUpdateModelSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ModelSettingsPayload) => api.updateModelSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelSettings });
    },
  });
}

export function useTestModelSettings() {
  return useMutation({
    mutationFn: (payload: ModelSettingsTestRequest) => api.testModelSettings(payload),
  });
}

export function useDatabaseSettings() {
  return useQuery({
    queryKey: queryKeys.databaseSettings,
    queryFn: ({ signal }) => api.getDatabaseSettings({ signal }),
  });
}

export function useSystemTablesStatus() {
  return useQuery({
    queryKey: queryKeys.systemTables,
    queryFn: ({ signal }) => api.getSystemTablesStatus({ signal }),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.operation_state.status === "running"
        ? ACTIVE_REFETCH_INTERVAL_MS
        : false,
  });
}

export function useInitializeSystemTables() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SystemTablesInitializeRequest) =>
      api.initializeSystemTables(payload),
    onMutate: () => qc.cancelQueries({ queryKey: queryKeys.systemTables }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.systemTables, data);
      qc.invalidateQueries({ queryKey: queryKeys.databaseStatus });
      qc.invalidateQueries({ queryKey: queryKeys.persistenceStatus });
      qc.invalidateQueries({ queryKey: queryKeys.schemaOwners });
      qc.invalidateQueries({ queryKey: ["schema"] });
      qc.invalidateQueries({ queryKey: ["nl2sql"] });
    },
    onError: () => {
      qc.invalidateQueries({ queryKey: queryKeys.systemTables });
    },
  });
}

export function useSchemaOwners() {
  return useQuery({
    queryKey: queryKeys.schemaOwners,
    queryFn: ({ signal }) => api.getSchemaOwners({ signal }),
    staleTime: 30_000,
    retry: false,
  });
}

export function useUpdateDatabaseSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: DatabaseSettingsUpdate) => api.updateDatabaseSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.databaseSettings });
    },
  });
}

export function useUploadDatabaseWallet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadDatabaseWallet(file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.databaseSettings });
    },
  });
}

export function useDownloadDatabaseWallet() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.downloadDatabaseWallet,
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.databaseSettings, data.settings);
      qc.invalidateQueries({ queryKey: queryKeys.databaseStatus });
    },
  });
}

export function useTestDatabaseSettings() {
  return useMutation({
    mutationFn: (payload: DatabaseSettingsUpdate) => api.testDatabaseSettings(payload),
  });
}

export function useAdbInfo() {
  return useQuery({
    queryKey: queryKeys.adbInfo,
    queryFn: ({ signal }) => api.getAdbInfo({ signal }),
    refetchInterval: (query) =>
      adbIsTransitioning(query.state.data?.lifecycle_state)
        ? ACTIVE_REFETCH_INTERVAL_MS
        : false,
  });
}

export function useUpdateAdbSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AdbSettingsUpdate) => api.updateAdbSettings(payload),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.adbInfo, data);
      qc.invalidateQueries({ queryKey: queryKeys.databaseSettings });
    },
  });
}

export function useStartAdb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.startAdb(),
    // 操作開始前の ADB 情報リクエストが遅れて STOPPED を書き戻すと、
    // STARTING のポーリングが開始されないため in-flight query を無効化する。
    onMutate: () => qc.cancelQueries({ queryKey: queryKeys.adbInfo }),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.adbInfo, data);
      qc.invalidateQueries({ queryKey: queryKeys.databaseStatus });
    },
  });
}

export function useStopAdb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.stopAdb(),
    onMutate: () => qc.cancelQueries({ queryKey: queryKeys.adbInfo }),
    onSuccess: (data) => qc.setQueryData(queryKeys.adbInfo, data),
  });
}

export function useUploadStorageSettings() {
  return useQuery({
    queryKey: queryKeys.uploadStorageSettings,
    queryFn: ({ signal }) => api.getUploadStorageSettings({ signal }),
  });
}

export function useUpdateUploadStorageSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: UploadStorageSettingsUpdate) =>
      api.updateUploadStorageSettings(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.uploadStorageSettings });
    },
  });
}
