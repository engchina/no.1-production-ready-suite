import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";

import { ApiError, apiGet, apiGetWithMetadata, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import type {
  DbAdminObjectPage,
  Nl2SqlProfile,
  ProfileSummaryPage,
  ProfileUsageContext,
  SchemaCatalogHead,
  SchemaCatalog,
  SchemaObjectPage,
  SchemaObjectDetail,
  SchemaRefreshActiveJobData,
  SchemaRefreshJob,
  SelectAiDbProfileRefreshJobData,
} from "./types";
import {
  filterUserVisibleCatalog,
  filterUserVisibleDbAdminObjectPage,
  filterUserVisibleSchemaObjectPage,
  isUserVisibleSchemaObject,
} from "./objectVisibility";
import { profileSummaryPageFromLegacyList } from "./profileListState";
import type { ProfileOntologyViewData } from "./ontology/types";

const LEGACY_COMPATIBILITY_STATUSES = new Set([404, 410, 501]);

export type DbAdminObjectQueryScope = "all" | "name_comment";

export function isLegacyCompatibilityError(error: unknown): boolean {
  return error instanceof ApiError && LEGACY_COMPATIBILITY_STATUSES.has(error.status);
}

async function legacyCatalog(signal?: AbortSignal): Promise<SchemaCatalog> {
  return filterUserVisibleCatalog(
    await apiGet<SchemaCatalog>("/api/schema/catalog", {
      signal,
      timeoutMs: API_TIMEOUT_MS.interactiveList,
    })
  );
}

export const nl2sqlIncrementalKeys = {
  profiles: (query: string) => ["nl2sql", "profiles", "search", query] as const,
  profile: (profileId: string) => ["nl2sql", "profiles", "detail", profileId] as const,
  profileUsageContext: (profileId: string) =>
    ["nl2sql", "profiles", "usage-context", profileId] as const,
  profileOntologyView: (profileId: string) =>
    ["nl2sql", "profiles", "ontology-view", profileId] as const,
  schemaHead: ["schema", "catalog", "head"] as const,
  schemaObjects: (query: string, objectType: string, profileId: string, rowState: string) =>
    ["schema", "objects", query, objectType, profileId, rowState] as const,
  dbAdminObjects: (
    query: string,
    objectType: string,
    rowState: string,
    ownerPrefix = "",
    queryScope: DbAdminObjectQueryScope = "all"
  ) =>
    [
      "nl2sql",
      "db-admin",
      "objects",
      query,
      objectType,
      rowState,
      ownerPrefix,
      queryScope,
    ] as const,
  schemaRefreshJob: (jobId: string) => ["schema", "refresh-job", jobId] as const,
  activeSchemaRefreshJob: ["schema", "refresh-job-discovery", "active"] as const,
  selectAiDbProfileRefreshJob: (jobId: string) =>
    ["nl2sql", "select-ai", "db-profile-refresh-job", jobId] as const,
};

/**
 * 一時的な失敗(ネットワーク断・タイムアウト・認証 cookie 反映前の 401・408/429)のみ
 * 最大 2 回再試行する。HTTP ステータスを持つそれ以外の応答(404/5xx 等)は再試行せず、
 * 既存の ErrorState + 手動「再試行」導線に委ねる。
 * ログイン直後の遷移でワークスペースが ErrorState のまま残るレースの回復用。
 */
function retryTransientOnly(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (error instanceof ApiError) {
    return error.status === 401 || error.status === 408 || error.status === 429;
  }
  return true;
}

export function useProfileSummaries(query: string) {
  return useInfiniteQuery({
    queryKey: nl2sqlIncrementalKeys.profiles(query.trim()),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: "50", q: query.trim() });
      if (pageParam) params.set("cursor", pageParam);
      return apiGet<ProfileSummaryPage>(`/api/nl2sql/profiles/search?${params}`, {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveList,
      }).catch(
        async (error: unknown) => {
          if (!isLegacyCompatibilityError(error)) throw error;
          const profiles = await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", {
            signal,
            timeoutMs: API_TIMEOUT_MS.interactiveList,
          });
          return profileSummaryPageFromLegacyList(profiles, query);
        }
      );
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    staleTime: 5_000,
  });
}

export function useProfileDetail(profileId: string) {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.profile(profileId),
    queryFn: async ({ signal }) => {
      const response = await apiGetWithMetadata<Nl2SqlProfile>(
        `/api/nl2sql/profiles/${encodeURIComponent(profileId)}`,
        { signal, timeoutMs: API_TIMEOUT_MS.interactiveList }
      ).catch(async (error: unknown) => {
        if (!isLegacyCompatibilityError(error)) throw error;
        const profiles = await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", {
          signal,
          timeoutMs: API_TIMEOUT_MS.interactiveList,
        });
        const profile = profiles.find((item) => item.id === profileId);
        if (!profile) throw new Error(t("profiles.error.notFound"));
        return { data: profile, etag: profile.etag ?? "" };
      });
      return { profile: response.data, etag: response.etag || response.data.etag };
    },
    enabled: Boolean(profileId),
    staleTime: 5_000,
    retry: retryTransientOnly,
  });
}

export function useProfileUsageContext(profileId: string) {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.profileUsageContext(profileId),
    queryFn: async ({ signal }) => {
      const response = await apiGetWithMetadata<ProfileUsageContext>(
        `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/usage-context`,
        { signal, timeoutMs: API_TIMEOUT_MS.interactiveList }
      ).catch(async (error: unknown) => {
        if (!isLegacyCompatibilityError(error)) throw error;
        const profiles = await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", {
          signal,
          timeoutMs: API_TIMEOUT_MS.interactiveList,
        });
        const profile = profiles.find((item) => item.id === profileId && !item.archived);
        if (!profile) throw new Error(t("profiles.error.notFound"));
        return {
          data: {
            id: profile.id,
            name: profile.name,
            category: profile.category ?? "",
            description: profile.description,
            allowed_tables: profile.allowed_tables,
            allowed_views: profile.allowed_views,
            archived: profile.archived,
            object_scope_version: profile.object_scope_version ?? 1,
            version: profile.version ?? 1,
            etag: profile.etag ?? "",
            updated_at: profile.updated_at ?? "",
          },
          etag: profile.etag ?? "",
        };
      });
      return { profile: response.data, etag: response.etag || response.data.etag };
    },
    enabled: Boolean(profileId),
    staleTime: 5_000,
    retry: false,
  });
}

export function useProfileOntologyView(profileId: string) {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.profileOntologyView(profileId),
    queryFn: ({ signal }) =>
      apiGet<ProfileOntologyViewData>(
        `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-view`,
        { signal, timeoutMs: API_TIMEOUT_MS.interactiveDetail }
      ),
    enabled: Boolean(profileId),
    staleTime: 5_000,
    retry: retryTransientOnly,
  });
}

export function useSchemaCatalogHead() {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.schemaHead,
    queryFn: ({ signal }) =>
      apiGet<SchemaCatalogHead>("/api/schema/catalog/head", {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveList,
      }).catch(async (error: unknown) => {
        if (!isLegacyCompatibilityError(error)) throw error;
        const catalog = await legacyCatalog(signal);
        return {
          catalog_version: 0,
          schema_fingerprint: catalog.schema_fingerprint ?? "",
          refreshed_at: catalog.refreshed_at,
          object_count: catalog.tables.length,
          column_count: catalog.tables.reduce((total, table) => total + table.columns.length, 0),
          change_token: 0,
          etag: catalog.schema_fingerprint ?? "",
        };
      }),
    staleTime: 5_000,
  });
}

export function useSchemaObjects(
  query: string,
  objectType: string,
  profileId = "",
  rowState = "",
  enabled = true
) {
  return useInfiniteQuery({
    queryKey: nl2sqlIncrementalKeys.schemaObjects(query.trim(), objectType, profileId, rowState),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({
        limit: "50",
        q: query.trim(),
        type: objectType,
      });
      if (pageParam) params.set("cursor", pageParam);
      if (pageParam) params.set("include_counts", "false");
      if (profileId) params.set("profile_id", profileId);
      if (rowState) params.set("row_state", rowState);
      return apiGet<SchemaObjectPage>(`/api/schema/objects?${params}`, {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveList,
      }).then(filterUserVisibleSchemaObjectPage).catch(async (error: unknown) => {
        if (!isLegacyCompatibilityError(error)) throw error;
        const catalog = await legacyCatalog(signal);
        const normalizedQuery = query.trim().toLowerCase();
        const items = catalog.tables
          .filter(
            (table) =>
              isUserVisibleSchemaObject(table.owner, table.table_name) &&
              (!objectType || table.table_type.toUpperCase() === objectType.toUpperCase()) &&
              (!normalizedQuery ||
                `${table.owner} ${table.table_name} ${table.logical_name} ${table.comment}`
                  .toLowerCase()
                  .includes(normalizedQuery))
          )
          .map((table) => ({
            owner: table.owner,
            object_name: table.table_name,
            object_type: table.table_type,
            logical_name: table.logical_name,
            comment: table.comment,
            row_count: table.row_count,
            column_count: table.columns.length,
            last_ddl_at: "",
          }));
        return {
          items,
          next_cursor: null,
          total: items.length,
          catalog_version: 0,
        };
      });
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled,
    staleTime: 5_000,
  });
}

export function useDbAdminObjects(
  query: string,
  objectType: string,
  rowState: string,
  ownerPrefix = "",
  queryScope: DbAdminObjectQueryScope = "all"
) {
  return useInfiniteQuery({
    queryKey: nl2sqlIncrementalKeys.dbAdminObjects(
      query.trim(),
      objectType,
      rowState,
      ownerPrefix.trim(),
      queryScope
    ),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({
        limit: "100",
        q: query.trim(),
        type: objectType || "all",
        row_state: rowState || "all",
      });
      if (pageParam) params.set("cursor", pageParam);
      if (pageParam) params.set("include_counts", "false");
      if (ownerPrefix.trim()) params.set("owner_prefix", ownerPrefix.trim());
      if (queryScope !== "all") params.set("query_scope", queryScope);
      return apiGet<DbAdminObjectPage>(`/api/nl2sql/db-admin/objects?${params}`, {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveList,
      }).then(filterUserVisibleDbAdminObjectPage);
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    staleTime: 5_000,
    placeholderData: (previous) => previous,
    retry: false,
  });
}

export async function getSchemaObjectSnapshot(
  owner: string,
  objectType: string,
  signal?: AbortSignal
) {
  const names = new Set<string>();
  let cursor = "";
  const seenCursors = new Set<string>();
  try {
    do {
      const params = new URLSearchParams({
        limit: "100",
        owner: owner.trim().toUpperCase(),
        type: objectType.trim().toUpperCase(),
        include_counts: "false",
      });
      if (cursor) params.set("cursor", cursor);
      const page = await apiGet<SchemaObjectPage>(`/api/schema/objects?${params}`, {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveList,
      });
      for (const object of page.items) {
        if (!isUserVisibleSchemaObject(object.owner, object.object_name)) continue;
        names.add(`${object.owner}.${object.object_name}`.toUpperCase());
      }
      const next = page.next_cursor ?? "";
      if (!next || seenCursors.has(next)) break;
      seenCursors.add(next);
      cursor = next;
    } while (cursor);
    return [...names].sort();
  } catch (error) {
    if (!isLegacyCompatibilityError(error)) throw error;
    const catalog = await legacyCatalog(signal);
    return catalog.tables
      .filter(
        (table) =>
          isUserVisibleSchemaObject(table.owner, table.table_name) &&
          table.owner.toUpperCase() === owner.trim().toUpperCase() &&
          (objectType.toUpperCase() === "VIEW"
            ? ["VIEW", "MATERIALIZED VIEW"].includes(table.table_type.toUpperCase())
            : table.table_type.toUpperCase() === objectType.toUpperCase())
      )
      .map((table) => `${table.owner}.${table.table_name}`.toUpperCase())
      .sort();
  }
}

export async function getSchemaObjectDetail(
  owner: string,
  objectName: string,
  signal?: AbortSignal
) {
  return apiGet<SchemaObjectDetail>(
    `/api/schema/objects/${encodeURIComponent(owner)}/${encodeURIComponent(objectName)}`,
    { signal, timeoutMs: API_TIMEOUT_MS.interactiveDetail }
  ).catch(async (error: unknown) => {
    if (!isLegacyCompatibilityError(error)) throw error;
    const catalog = await legacyCatalog(signal);
    const table = catalog.tables.find(
      (item) =>
        item.owner.toUpperCase() === owner.toUpperCase() &&
        item.table_name.toUpperCase() === objectName.toUpperCase()
    );
    if (!table) throw new Error(t("nl2sql.schema.objectNotFound"));
    return {
      table,
      dependencies: (catalog.view_dependencies ?? []).filter(
        (item) =>
          (item.owner ?? "").toUpperCase() === owner.toUpperCase() &&
          item.view_name.toUpperCase() === objectName.toUpperCase()
      ),
      catalog_version: 0,
      etag: catalog.schema_fingerprint ?? "",
    };
  });
}

export function useStartSchemaRefresh() {
  return useMutation({
    mutationFn: () =>
      apiPost<SchemaRefreshJob>("/api/schema/refresh-jobs", undefined, {
        timeoutMs: API_TIMEOUT_MS.jobControl,
      }),
  });
}

export function useSchemaRefreshJob(jobId: string) {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.schemaRefreshJob(jobId),
    queryFn: ({ signal }) =>
      apiGet<SchemaRefreshJob>(`/api/schema/refresh-jobs/${jobId}`, {
        signal,
        timeoutMs: API_TIMEOUT_MS.jobControl,
      }),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "done" || status === "error" ? false : 1_000;
    },
    retry: false,
  });
}

export function useActiveSchemaRefreshJob(enabled = true) {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.activeSchemaRefreshJob,
    queryFn: ({ signal }) =>
      apiGet<SchemaRefreshActiveJobData>("/api/schema/refresh-jobs/active", {
        signal,
        timeoutMs: API_TIMEOUT_MS.jobControl,
      }),
    staleTime: 0,
    enabled,
    refetchOnMount: "always",
    refetchOnReconnect: "always",
    refetchOnWindowFocus: "always",
    retry: false,
  });
}

export function useStartSelectAiDbProfileRefresh() {
  return useMutation({
    mutationFn: () =>
      apiPost<SelectAiDbProfileRefreshJobData>(
        "/api/nl2sql/select-ai/db-profiles/refresh-jobs",
        undefined,
        {
          timeoutMs: API_TIMEOUT_MS.jobControl,
        }
      ),
  });
}

export function useSelectAiDbProfileRefreshJob(jobId: string) {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.selectAiDbProfileRefreshJob(jobId),
    queryFn: ({ signal }) =>
      apiGet<SelectAiDbProfileRefreshJobData>(
        `/api/nl2sql/select-ai/db-profile-refresh-jobs/${jobId}`,
        {
          signal,
          timeoutMs: API_TIMEOUT_MS.jobControl,
        }
      ),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "running" ? 1_000 : false;
    },
    retry: false,
  });
}
