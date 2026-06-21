import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type AdbSettingsUpdate,
  type DatabaseSettingsUpdate,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type UploadStorageSettingsUpdate,
} from "@/lib/api";

export const queryKeys = {
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
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

export function useModelSettings() {
  return useQuery({
    queryKey: queryKeys.modelSettings,
    queryFn: api.getModelSettings,
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
    queryFn: api.getDatabaseSettings,
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

export function useTestDatabaseSettings() {
  return useMutation({
    mutationFn: (payload: DatabaseSettingsUpdate) => api.testDatabaseSettings(payload),
  });
}

export function useAdbInfo() {
  return useQuery({
    queryKey: queryKeys.adbInfo,
    queryFn: api.getAdbInfo,
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
    onSuccess: (data) => qc.setQueryData(queryKeys.adbInfo, data),
  });
}

export function useStopAdb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.stopAdb(),
    onSuccess: (data) => qc.setQueryData(queryKeys.adbInfo, data),
  });
}

export function useUploadStorageSettings() {
  return useQuery({
    queryKey: queryKeys.uploadStorageSettings,
    queryFn: api.getUploadStorageSettings,
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
