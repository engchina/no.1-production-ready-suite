import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiGetWithMetadata, apiPost } from "@/lib/api";
import type {
  Nl2SqlProfile,
  ProfileSummaryPage,
  SchemaCatalogHead,
  SchemaCatalog,
  SchemaObjectPage,
  SchemaObjectDetail,
  SchemaRefreshJob,
} from "./types";

let legacyCatalogOverride: SchemaCatalog | null = null;

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
  return legacyCatalogOverride ?? apiGet<SchemaCatalog>("/api/schema/catalog", { signal });
}

export const nl2sqlIncrementalKeys = {
  profiles: (query: string) => ["nl2sql", "profiles", "search", query] as const,
  profile: (profileId: string) => ["nl2sql", "profiles", "detail", profileId] as const,
  schemaHead: ["schema", "catalog", "head"] as const,
  schemaObjects: (query: string, objectType: string, profileId: string) =>
    ["schema", "objects", query, objectType, profileId] as const,
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
      }).catch(
        async () => {
          const profiles = await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", {
            signal,
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
        { signal }
      ).catch(async () => {
        const profiles = await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", {
          signal,
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

export function useSchemaCatalogHead() {
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.schemaHead,
    queryFn: ({ signal }) =>
      apiGet<SchemaCatalogHead>("/api/schema/catalog/head", { signal }).catch(async () => {
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

export function useSchemaObjects(query: string, objectType: string, profileId = "") {
  return useInfiniteQuery({
    queryKey: nl2sqlIncrementalKeys.schemaObjects(query.trim(), objectType, profileId),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({
        limit: "50",
        q: query.trim(),
        type: objectType,
      });
      if (pageParam) params.set("cursor", pageParam);
      if (profileId) params.set("profile_id", profileId);
      return apiGet<SchemaObjectPage>(`/api/schema/objects?${params}`, { signal }).catch(async () => {
        const catalog = await legacyCatalog(signal);
        const normalizedQuery = query.trim().toLowerCase();
        const items = catalog.tables
          .filter(
            (table) =>
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
      });
      for (const object of page.items) {
        names.add(`${object.owner}.${object.object_name}`.toUpperCase());
      }
      const next = page.next_cursor ?? "";
      if (!next || seenCursors.has(next)) break;
      seenCursors.add(next);
      cursor = next;
    } while (cursor);
    return [...names].sort();
  } catch {
    const catalog = await legacyCatalog(signal);
    return catalog.tables
      .filter(
        (table) =>
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
    { signal }
  ).catch(async () => {
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
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiPost<SchemaRefreshJob>("/api/schema/refresh-jobs").catch(async () => {
        legacyCatalogOverride = await apiPost<SchemaCatalog>("/api/schema/refresh", {});
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
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["schema", "objects"] });
      void queryClient.invalidateQueries({ queryKey: nl2sqlIncrementalKeys.schemaHead });
    },
  });
}

export function useSchemaRefreshJob(jobId: string) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: nl2sqlIncrementalKeys.schemaRefreshJob(jobId),
    queryFn: ({ signal }) =>
      apiGet<SchemaRefreshJob>(`/api/schema/refresh-jobs/${jobId}`, { signal }),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "done") {
        void queryClient.invalidateQueries({ queryKey: ["schema", "objects"] });
        void queryClient.invalidateQueries({ queryKey: nl2sqlIncrementalKeys.schemaHead });
      }
      return status === "pending" || status === "running" ? 1_000 : false;
    },
    retry: false,
  });
}
