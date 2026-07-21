import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Banner, Button, EmptyState, PageHeader } from "@engchina/production-ready-ui";

import { apiGet, apiPatch, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  useProfileDetail,
  useProfileSummaries,
  useSchemaRefreshJob,
  useStartSchemaRefresh,
} from "../incrementalQueries";
import { OntologyBuildSection } from "../ontology/OntologyBuildSection";
import { OntologyUsagePanel } from "../ontology/OntologyUsagePanel";
import { ProfileOntologyEditor } from "../ontology/ProfileOntologyEditor";
import { createOntologyRevisionDraft } from "../ontology/api";
import {
  EMPTY_PROFILE_ONTOLOGY_DRAFT,
  type ProfileOntologyDraftPayload,
  type ProfileOntologyDraftState,
} from "../ontology/ProfileOntologyEditorCore";
import type { OntologyGraph, OntologyNode, ProfileOntologyView } from "../ontology/types";

/**
 * オントロジー構築（旧: 業務プロファイル編集の末尾セクション）を独立ページ化。
 * 上部でプロファイルを選び（`?profile=` 同期）、AI 構築→承認→公開と物理・業務モデル編集を行う。
 * データ供給（ontology-view 取得 / draft 保存 / スキーマ更新）は旧 ProfileManagementPage から移設。
 */
export function OntologyBuildPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [ontologyDraft, setOntologyDraft] = useState<ProfileOntologyDraftState>({ nodes: {}, edges: {} });
  const [ontologyGraph, setOntologyGraph] = useState<OntologyGraph | null>(null);
  const [profileOntologyView, setProfileOntologyView] = useState<ProfileOntologyView | null>(null);
  const [ontologyWarnings, setOntologyWarnings] = useState<string[]>([]);
  const [ontologyViewNonce, setOntologyViewNonce] = useState(0);
  const [refreshJobId, setRefreshJobId] = useState("");
  const [error, setError] = useState("");

  const profilesQuery = useProfileSummaries("");
  const activeProfiles = useMemo(
    () => profilesQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [profilesQuery.data]
  );
  const profileParam = searchParams.get("profile");
  const tabParam = searchParams.get("tab");
  const activeTab = tabParam === "model" || tabParam === "usage" ? tabParam : "build";
  const selectedProfileSummary = useMemo(() => {
    if (activeProfiles.length === 0) return null;
    if (!profileParam) return activeProfiles[0];
    return (
      activeProfiles.find((profile) => profile.id === profileParam) ??
      (profilesQuery.hasNextPage ? null : activeProfiles[0])
    );
  }, [activeProfiles, profileParam, profilesQuery.hasNextPage]);
  const selectedProfileId = selectedProfileSummary?.id ?? "";
  const profileDetailQuery = useProfileDetail(selectedProfileId);
  const selectedProfile = profileDetailQuery.data?.profile ?? null;
  const startSchemaRefresh = useStartSchemaRefresh();
  const schemaRefreshJobQuery = useSchemaRefreshJob(refreshJobId);
  const schemaRefreshStatus = schemaRefreshJobQuery.isError
    ? "error"
    : (schemaRefreshJobQuery.data?.status ?? "");
  const refreshing =
    startSchemaRefresh.isPending || schemaRefreshStatus === "pending" || schemaRefreshStatus === "running";

  useEffect(() => {
    if (profilesQuery.isError) setError(t("profiles.error.load"));
  }, [profilesQuery.isError]);

  useEffect(() => {
    if (
      profileParam &&
      !activeProfiles.some((profile) => profile.id === profileParam) &&
      profilesQuery.hasNextPage &&
      !profilesQuery.isFetchingNextPage
    ) {
      void profilesQuery.fetchNextPage();
    }
  }, [activeProfiles, profileParam, profilesQuery.hasNextPage, profilesQuery.isFetchingNextPage]);

  useEffect(() => {
    const targetId = selectedProfileId || null;
    if (!targetId) {
      setOntologyDraft({ nodes: {}, edges: {} });
      setOntologyGraph(null);
      setProfileOntologyView(null);
      setOntologyWarnings([]);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    void apiGet<{
      profile_ontology_view?: {
        id?: string;
        profile_id?: string;
        ontology_revision_id?: string;
        etag?: string;
        activation_scenarios_ja?: string[];
        activation_keywords?: string[];
        scenario_version?: number;
        table_usages_ja?: Record<string, string>;
        draft_node_overrides?: Array<{
          node_id: string;
          business_name_ja?: string;
          table_usage?: string;
        }>;
        draft_edge_overrides?: Array<{
          edge_id: string;
          cardinality?: ProfileOntologyDraftState["edges"][string]["cardinality"];
          allowed_path?: boolean;
        }>;
      };
      ontology_graph?: OntologyGraph;
      warnings_ja?: string[];
    }>(`/api/nl2sql/profiles/${encodeURIComponent(targetId)}/ontology-view`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (cancelled) return;
        const view = data.profile_ontology_view;
        setProfileOntologyView((view ?? null) as ProfileOntologyView | null);
        setOntologyGraph(data.ontology_graph ?? null);
        setOntologyWarnings(data.warnings_ja ?? []);
        setOntologyDraft({
          nodes: Object.fromEntries([
            ...Object.entries(view?.table_usages_ja ?? {}).map(([nodeId, usage]) => [
              nodeId,
              { table_usage: usage },
            ]),
            ...(view?.draft_node_overrides ?? []).map((item) => [
              item.node_id,
              { business_name_ja: item.business_name_ja, table_usage: item.table_usage },
            ]),
          ]),
          edges: Object.fromEntries(
            (view?.draft_edge_overrides ?? []).map((item) => [
              item.edge_id,
              { cardinality: item.cardinality, allowed_path: item.allowed_path },
            ])
          ),
        });
      })
      .catch((err) => {
        if (cancelled || isAbortError(err)) return;
        setOntologyGraph(null);
        setProfileOntologyView(null);
        setOntologyWarnings([]);
        setOntologyDraft({ ...EMPTY_PROFILE_ONTOLOGY_DRAFT, nodes: {}, edges: {} });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selectedProfileId, ontologyViewNonce]);

  const saveOntologyDraft = async (payload: ProfileOntologyDraftPayload) => {
    if (!selectedProfileId) throw new Error(t("profiles.empty.hint"));
    const path = `/api/nl2sql/profiles/${encodeURIComponent(selectedProfileId)}/ontology-view`;
    const current = await apiGet<{ etag?: string; profile_ontology_view?: { etag?: string } }>(path);
    await apiPatch(path, {
      base_etag: current.profile_ontology_view?.etag ?? current.etag ?? "",
      table_usages_ja: payload.table_usage,
      allowed_path_ids: payload.allowed_path_ids,
      node_overrides: payload.node_overrides,
      edge_overrides: payload.edge_overrides,
      physical_scope: payload.physical_scope,
      schema_fingerprint: payload.schema_fingerprint,
    });
  };

  const saveSemanticNode = async (node: OntologyNode) => {
    const revision = ontologyGraph?.revision;
    if (!revision?.id || !revision.etag) throw new Error("Ontology revision を取得できません。");
    const draft = await createOntologyRevisionDraft(revision.id, revision.etag, [node]);
    setOntologyGraph(draft);
  };

  const refreshSchema = async () => {
    setError("");
    try {
      const job = await startSchemaRefresh.mutateAsync();
      if (job.status === "done") {
        await profilesQuery.refetch();
        setOntologyViewNonce((nonce) => nonce + 1);
      } else {
        setRefreshJobId(job.job_id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("profiles.error.load"));
    }
  };

  useEffect(() => {
    const job = schemaRefreshJobQuery.data;
    if (job?.status === "done") {
      void profilesQuery.refetch();
      setOntologyViewNonce((nonce) => nonce + 1);
    } else if (job?.status === "error" || schemaRefreshJobQuery.isError) {
      setError(t("profiles.schemaRefresh.error"));
    }
  }, [schemaRefreshJobQuery.data?.status, schemaRefreshJobQuery.isError]);

  const selectProfile = (id: string) => {
    const next = new URLSearchParams(searchParams);
    if (id) next.set("profile", id);
    else next.delete("profile");
    setSearchParams(next, { replace: true });
  };

  const selectTab = (tab: "build" | "model" | "usage") => {
    const next = new URLSearchParams(searchParams);
    next.set("tab", tab);
    setSearchParams(next, { replace: true });
  };

  const saveScenarios = async (scenarios: string[], keywords: string[]) => {
    if (!selectedProfileId) return;
    const path = `/api/nl2sql/profiles/${encodeURIComponent(selectedProfileId)}/ontology-view`;
    const current = await apiGet<{ profile_ontology_view?: { etag?: string } }>(path);
    await apiPatch(path, {
      base_etag: current.profile_ontology_view?.etag ?? "",
      activation_scenarios_ja: scenarios,
      activation_keywords: keywords,
    });
    setOntologyViewNonce((nonce) => nonce + 1);
  };

  return (
    <>
      <PageHeader title={t("nav.ontologyBuild")} subtitle={t("ontologyBuild.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        {error ? <Banner severity="danger">{error}</Banner> : null}
        <p className="text-sm text-muted" aria-live="polite" aria-atomic="true">
          {schemaRefreshStatus ? t(`profiles.schemaRefresh.status.${schemaRefreshStatus}`) : null}
        </p>

        <section className="rounded-md border border-border bg-card p-4 shadow-sm">
          <label className="grid gap-1 text-sm font-medium text-foreground sm:max-w-md">
            <span>{t("ontologyBuild.profile.label")}</span>
            <select
              value={selectedProfileId}
              onChange={(event) => selectProfile(event.currentTarget.value)}
              disabled={activeProfiles.length === 0}
              className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
              data-testid="ontology-build-profile-select"
            >
              {activeProfiles.length === 0 ? (
                <option value="">{t("ontologyBuild.profile.empty")}</option>
              ) : (
                activeProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))
              )}
            </select>
            {profilesQuery.hasNextPage ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={profilesQuery.isFetchingNextPage}
                onClick={() => void profilesQuery.fetchNextPage()}
              >
                {t("profiles.action.loadMore")}
              </Button>
            ) : null}
          </label>
        </section>

        {!profilesQuery.isLoading && activeProfiles.length === 0 ? (
          <EmptyState title={t("ontologyBuild.empty.title")} hint={t("ontologyBuild.empty.hint")} />
        ) : (
          <>
            <nav
              className="flex flex-wrap gap-2 rounded-md border border-border bg-card p-2"
              role="tablist"
              aria-label={t("ontologyBuild.tabs.label")}
            >
              {(["build", "model", "usage"] as const).map((tab) => (
                <Button
                  key={tab}
                  type="button"
                  variant={activeTab === tab ? "primary" : "secondary"}
                  size="sm"
                  role="tab"
                  aria-selected={activeTab === tab}
                  onClick={() => selectTab(tab)}
                >
                  {t(`ontologyBuild.tabs.${tab}`)}
                </Button>
              ))}
            </nav>

            {activeTab === "build" ? (
              <OntologyBuildSection
                profileId={selectedProfileId || null}
                onPublished={() => setOntologyViewNonce((nonce) => nonce + 1)}
              />
            ) : null}
            {activeTab === "model" ? (
              <ProfileOntologyEditor
                graph={ontologyGraph}
                profileId={selectedProfileId || "new-profile"}
                selectedTables={selectedProfile?.allowed_tables ?? []}
                selectedViews={selectedProfile?.allowed_views ?? []}
                warnings={ontologyWarnings}
                onRefreshSchema={selectedProfileId ? refreshSchema : undefined}
                refreshingSchema={refreshing}
                initialDraft={ontologyDraft}
                onSaveDraft={selectedProfileId ? saveOntologyDraft : undefined}
                onSaveSemanticNode={selectedProfileId ? saveSemanticNode : undefined}
                labels={{
                  inspectorTitle: t("profiles.ontology.inspector"),
                  tableUsage: t("profiles.ontology.usage"),
                  cardinality: t("profiles.ontology.cardinality"),
                  allowedPath: t("profiles.ontology.allowedPath"),
                  graphUnavailableTitle: t("profiles.ontology.emptyTitle"),
                  graphUnavailable: t("profiles.ontology.emptyHint"),
                }}
              />
            ) : null}
            {activeTab === "usage" && selectedProfileId ? (
              <OntologyUsagePanel
                key={`${selectedProfileId}:${profileOntologyView?.scenario_version ?? 0}`}
                profileId={selectedProfileId}
                view={profileOntologyView}
                onSaveScenarios={saveScenarios}
              />
            ) : null}
          </>
        )}
      </main>
    </>
  );
}
