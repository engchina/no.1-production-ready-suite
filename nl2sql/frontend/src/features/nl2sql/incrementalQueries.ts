import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiGet, apiGetWithMetadata, apiPost } from "@/lib/api";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import type {
  DbAdminObjectPage,
  Nl2SqlProfile,
  ProfileSummaryPage,
  SchemaCatalogHead,
  SchemaCatalog,
  SchemaObjectPage,
  SchemaObjectDetail,
  SchemaRefreshJob,
} from "./types";
import {
  filterUserVisibleCatalog,
  filterUserVisibleDbAdminObjectPage,
  filterUserVisibleSchemaObjectPage,
  isUserVisibleObjectName,
} from "./objectVisibility";
import type { ProfileOntologyViewData } from "./ontology/types";

let legacyCatalogOverride: SchemaCatalog | null = null;

const LEGACY_COMPATIBILITY_STATUSES = new Set([404, 410, 501]);

export function isLegacyCompatibilityError(error: unknown): boolean {
  return error instanceof ApiError && LEGACY_COMPATIBILITY_STATUSES.has(error.status);
}

function profileSummary(profile: Nl2SqlProfile) {
  return {
    id: profile.id,
    name: profile.name,
    category: profile.category ?? "",
    description: profile.description,
    archived: profile.archived,
    allowed_table_count: profile.allowed_tables.length,
    allowed_view_count: profile.allowed_views.length,
    glossary_count: Object.keys(profile.glossary).length,
    few_shot_count: profile.few_shot_examples.length,
    version: profile.version ?? 1,
    etag: profile.etag ?? "",
    updated_at: profile.updated_at ?? "",
  };
}

async function legacyCatalog(signal?: AbortSignal): Promise<SchemaCatalog> {
  if (legacyCatalogOverride) return filterUserVisibleCatalog(legacyCatalogOverride);
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
  profileOntologyView: (profileId: string) =>
    ["nl2sql", "profiles", "ontology-view", profileId] as const,
  schemaHead: ["schema", "catalog", "head"] as const,
  schemaObjects: (query: string, objectType: string, profileId: string, rowState: string) =>
    ["schema", "objects", query, objectType, profileId, rowState] as const,
  dbAdminObjects: (query: string, objectType: string, rowState: string) =>
    ["nl2sql", "db-admin", "objects", query, objectType, rowState] as const,
  schemaRefreshJob: (jobId: string) => ["schema", "refresh-job", jobId] as const,
};

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
          const normalizedQuery = query.trim().toLowerCase();
          const items = profiles
            .filter(
              (profile) =>
                !profile.archived &&
                (!normalizedQuery ||
                  profile.name.toLowerCase().includes(normalizedQuery) ||
                  (profile.category ?? "").toLowerCase().includes(normalizedQuery))
            )
            .map(profileSummary);
          return { items, next_cursor: null, total: items.length, change_token: 0 };
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
        if (!profile) throw new Error("指定された profile が見つかりません。");
        return { data: profile, etag: profile.etag ?? "" };
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
    retry: false,
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
  rowState = ""
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
              isUserVisibleObjectName(table.table_name) &&
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
    staleTime: 5_000,
  });
}

export function useDbAdminObjects(query: string, objectType: string, rowState: string) {
  return useInfiniteQuery({
    queryKey: nl2sqlIncrementalKeys.dbAdminObjects(query.trim(), objectType, rowState),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({
        limit: "100",
        q: query.trim(),
        type: objectType || "all",
        row_state: rowState || "all",
      });
      if (pageParam) params.set("cursor", pageParam);
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
      });
      if (cursor) params.set("cursor", cursor);
      const page = await apiGet<SchemaObjectPage>(`/api/schema/objects?${params}`, {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveList,
      });
      for (const object of page.items) {
        if (!isUserVisibleObjectName(object.object_name)) continue;
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
          isUserVisibleObjectName(table.table_name) &&
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
    if (!table) throw new Error("Schema object が見つかりません。");
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
      }).catch(async (error: unknown) => {
        if (!isLegacyCompatibilityError(error)) throw error;
        legacyCatalogOverride = filterUserVisibleCatalog(
          await apiPost<SchemaCatalog>("/api/schema/refresh", {}, {
            timeoutMs: API_TIMEOUT_MS.jobControl,
          })
        );
        return {
          job_id: "",
          status: "done" as const,
          created_at: new Date().toISOString(),
          scanned_objects: legacyCatalogOverride.tables.length,
          changed_objects: legacyCatalogOverride.tables.length,
          deleted_objects: 0,
          catalog_version: 0,
          error_code: "",
        };
      }),
  });
}

export function useSchemaRefreshJob(jobId: string) {
  const queryClient = useQueryClient();
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
      if (status === "done") {
        void queryClient.invalidateQueries({ queryKey: ["schema", "objects"] });
        void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin", "objects"] });
        void queryClient.invalidateQueries({ queryKey: nl2sqlIncrementalKeys.schemaHead });
      }
      return status === "pending" || status === "running" ? 1_000 : false;
    },
    retry: false,
  });
}

export async function waitForSchemaRefreshJob(jobId: string, signal?: AbortSignal) {
  let job = await apiGet<SchemaRefreshJob>(`/api/schema/refresh-jobs/${jobId}`, {
    signal,
    timeoutMs: API_TIMEOUT_MS.jobControl,
  });
  while (job.status === "pending" || job.status === "running") {
    await new Promise<void>((resolve, reject) => {
      const timeoutId = window.setTimeout(resolve, 1_000);
      signal?.addEventListener(
        "abort",
        () => {
          window.clearTimeout(timeoutId);
          reject(signal.reason);
        },
        { once: true }
      );
    });
    job = await apiGet<SchemaRefreshJob>(`/api/schema/refresh-jobs/${jobId}`, {
      signal,
      timeoutMs: API_TIMEOUT_MS.jobControl,
    });
  }
  if (job.status === "error") throw new Error(job.error_code || "schema_refresh_failed");
  return job;
}
