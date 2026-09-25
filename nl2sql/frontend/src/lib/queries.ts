import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  type SelectAiCredentialCreateRequest,
  type SystemTablesInitializeRequest,
} from "@/lib/api";

export const queryKeys = {
  databaseStatus: ["ready", "database"] as const,
  persistenceStatus: ["nl2sql", "persistence"] as const,
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
  selectAiCredential: ["settings", "database", "select-ai-credential"] as const,
  systemTables: ["settings", "database", "system-tables"] as const,
  schemaOwners: ["schema", "owners"] as const,
  adbInfo: ["settings", "database", "adb"] as const,
  uploadStorageSettings: ["settings", "upload-storage"] as const,
};

const ACTIVE_REFETCH_INTERVAL_MS = 4000;

export function useDatabaseStatus({
  enabled = true,
}: { enabled?: boolean } = {}) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: queryKeys.databaseStatus,
    queryFn: async ({ signal }) => {
      const next = await api.getDatabaseStatus({ signal });
      const previous = qc.getQueryData<import("@/lib/api").DatabaseStatusData>(
        queryKeys.databaseStatus,
      );
      if (
        previous?.context_id &&
        next.context_id &&
        previous.context_id !== next.context_id
      ) {
        const business = {
          predicate: (query: { queryKey: readonly unknown[] }) =>
            ["nl2sql", "schema"].includes(String(query.queryKey[0])),
        };
        await qc.cancelQueries(business);
        qc.removeQueries(business);
      }
      return next;
    },
    enabled,
    staleTime: 15_000,
    retry: false,
  });
}

export function usePersistenceStatus({
  enabled = true,
}: { enabled?: boolean } = {}) {
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

export function useSelectAiCredential() {
  return useQuery({
    queryKey: queryKeys.selectAiCredential,
    queryFn: ({ signal }) => api.getSelectAiCredential({ signal }),
    retry: false,
  });
}

export function useCreateSelectAiCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SelectAiCredentialCreateRequest) =>
      api.createSelectAiCredential(payload),
    onSuccess: (data) => qc.setQueryData(queryKeys.selectAiCredential, data),
    onError: () =>
      qc.invalidateQueries({ queryKey: queryKeys.selectAiCredential }),
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
