import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Target } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  Banner,
  Button,
  EmptyState,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/StateViews";
import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import {
  nl2sqlIncrementalKeys,
  useProfileDetail,
  useProfileOntologyView,
  useProfileSummaries,
  useSchemaRefreshJob,
  useStartSchemaRefresh,
} from "../incrementalQueries";
import {
  classifyOntologyWorkspaceError,
  ontologyWorkspaceErrorPresentation,
} from "../ontologyWorkspaceError";
import { OntologyBuildSection } from "../ontology/OntologyBuildSection";
import { OntologyInterchangeSection } from "../ontology/OntologyInterchangeSection";
import { OntologyMermaidPanel } from "../ontology/OntologyMermaidPanel";
import { OntologyQueryPlayground } from "../ontology/OntologyQueryPlayground";
import { ProfileOntologyEditor } from "../ontology/ProfileOntologyEditor";
import { createOntologyRevisionDraft } from "../ontology/api";
import type {
  ProfileOntologyDraftPayload,
  ProfileOntologyDraftState,
} from "../ontology/ProfileOntologyEditorCore";
import type {
  OntologyNode,
  ProfileOntologyViewData,
} from "../ontology/types";

/**
 * AI 構築、提案レビュー、業務モデル編集、技術表現を一続きで扱う単一ページ。
 * 旧 tab URL は profile だけを残す正規 URL へ置き換える。
 */
export function OntologyBuildPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [refreshJobId, setRefreshJobId] = useState("");
  const [pageError, setPageError] = useState("");
  const [materializingOntologyView, setMaterializingOntologyView] = useState(false);
  const [proposalsRefreshToken, setProposalsRefreshToken] = useState(0);
  const queryClient = useQueryClient();

  const profilesQuery = useProfileSummaries("");
  const activeProfiles = useMemo(
    () => profilesQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [profilesQuery.data]
  );
  const profileParam = searchParams.get("profile");
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
  const ontologyViewQuery = useProfileOntologyView(selectedProfileId);
  const selectedProfile = profileDetailQuery.data?.profile ?? null;
  const ontologyView = ontologyViewQuery.data?.profile_ontology_view;
  const ontologyGraph = ontologyViewQuery.data?.ontology_graph ?? null;
  const ontologyWarnings = ontologyViewQuery.data?.warnings_ja ?? [];
  const ontologyViewMaterialized = Boolean(ontologyViewQuery.data?.materialized);
  const ontologyViewStale = Boolean(ontologyViewQuery.data?.stale);
  const startSchemaRefresh = useStartSchemaRefresh();
  const schemaRefreshJobQuery = useSchemaRefreshJob(refreshJobId);
  const schemaRefreshStatus = schemaRefreshJobQuery.isError
    ? "error"
    : (schemaRefreshJobQuery.data?.status ?? "");
  const refreshing =
    startSchemaRefresh.isPending ||
    schemaRefreshStatus === "pending" ||
    schemaRefreshStatus === "running";

  const ontologyDraft = useMemo<ProfileOntologyDraftState>(
    () => ({
      nodes: Object.fromEntries([
        ...Object.entries(ontologyView?.table_usages_ja ?? {}).map(([nodeId, usage]) => [
          nodeId,
          { table_usage: usage },
        ]),
        ...(ontologyView?.draft_node_overrides ?? []).map((item) => [
          item.node_id,
          { business_name_ja: item.business_name_ja, table_usage: item.table_usage },
        ]),
      ]),
      edges: Object.fromEntries(
        (ontologyView?.draft_edge_overrides ?? []).map((item) => [
          item.edge_id,
          { cardinality: item.cardinality, allowed_path: item.allowed_path },
        ])
      ),
    }),
    [ontologyView]
  );

  useEffect(() => {
    if (!searchParams.has("tab")) return;
    const next = new URLSearchParams();
    const profileId = searchParams.get("profile");
    if (profileId) next.set("profile", profileId);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

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

  const refreshOntologyView = async () => {
    if (!selectedProfileId) return;
    await queryClient.invalidateQueries({
      queryKey: nl2sqlIncrementalKeys.profileOntologyView(selectedProfileId),
    });
  };

  const saveOntologyDraft = async (payload: ProfileOntologyDraftPayload) => {
    if (!selectedProfileId) throw new Error(t("profiles.empty.hint"));
    const path = `/api/nl2sql/profiles/${encodeURIComponent(selectedProfileId)}/ontology-view`;
    const current = await apiGet<ProfileOntologyViewData>(path);
    await apiPatch(path, {
      base_etag: current.profile_ontology_view?.etag ?? "",
      table_usages_ja: payload.table_usage,
      allowed_path_ids: payload.allowed_path_ids,
      node_overrides: payload.node_overrides,
      edge_overrides: payload.edge_overrides,
      physical_scope: payload.physical_scope,
      schema_fingerprint: payload.schema_fingerprint,
    });
    await refreshOntologyView();
  };

  const materializeOntologyView = async () => {
    if (!selectedProfileId) return;
    setMaterializingOntologyView(true);
    setPageError("");
    try {
      const data = await apiPost<ProfileOntologyViewData>(
        `/api/nl2sql/profiles/${encodeURIComponent(selectedProfileId)}/ontology-view/materialize`
      );
      queryClient.setQueryData(
        nl2sqlIncrementalKeys.profileOntologyView(selectedProfileId),
        data
      );
      toast.success(t("ontologyBuild.view.materializeSuccess"));
    } catch (err) {
      setPageError(
        err instanceof Error ? err.message : t("ontologyBuild.view.materializeError")
      );
    } finally {
      setMaterializingOntologyView(false);
    }
  };

  const saveSemanticNode = async (node: OntologyNode) => {
    const revision = ontologyGraph?.revision;
    if (!revision?.id || !revision.etag) {
      throw new Error("Ontology revision を取得できません。");
    }
    const draft = await createOntologyRevisionDraft(revision.id, revision.etag, [node]);
    queryClient.setQueryData<ProfileOntologyViewData>(
      nl2sqlIncrementalKeys.profileOntologyView(selectedProfileId),
      (current) => ({ ...current, ontology_graph: draft })
    );
    await refreshOntologyView();
  };

  const refreshSchema = async () => {
    setPageError("");
    try {
      const job = await startSchemaRefresh.mutateAsync();
      if (job.status === "done") {
        await Promise.all([profilesQuery.refetch(), refreshOntologyView()]);
      } else {
        setRefreshJobId(job.job_id);
      }
    } catch (err) {
      setPageError(err instanceof Error ? err.message : t("profiles.error.load"));
    }
  };

  useEffect(() => {
    const status = schemaRefreshJobQuery.data?.status;
    if (status === "done") {
      void Promise.all([profilesQuery.refetch(), refreshOntologyView()]);
    } else if (status === "error" || schemaRefreshJobQuery.isError) {
      setPageError(t("profiles.schemaRefresh.error"));
    }
  }, [schemaRefreshJobQuery.data?.status, schemaRefreshJobQuery.isError]);

  const selectProfile = (id: string) => {
    const next = new URLSearchParams();
    if (id) next.set("profile", id);
    setSearchParams(next, { replace: true });
  };

  const workspaceLoading =
    Boolean(selectedProfileId) && (profileDetailQuery.isLoading || ontologyViewQuery.isLoading);
  const workspaceFailure = classifyOntologyWorkspaceError(
    profileDetailQuery.error,
    ontologyViewQuery.error
  );
  const workspaceErrorPresentation = workspaceFailure
    ? ontologyWorkspaceErrorPresentation(workspaceFailure)
    : null;
  const workspaceErrorMessage = workspaceErrorPresentation
    ? t(workspaceErrorPresentation.key, workspaceErrorPresentation.params)
    : "";

  return (
    <>
      <PageHeader title={t("nav.ontologyBuild")} subtitle={t("ontologyBuild.subtitle")} />
      <main className="grid min-w-0 gap-4 p-4 lg:p-8">
        {pageError ? <Banner severity="danger">{pageError}</Banner> : null}
        {schemaRefreshStatus ? (
          <p className="text-sm text-muted" aria-live="polite" aria-atomic="true">
            {t(`profiles.schemaRefresh.status.${schemaRefreshStatus}`)}
          </p>
        ) : null}

        <DbObjectManagementPanelShell
          id="ontology-profile-panel"
          role="region"
          ariaLabel={t("ontologyBuild.profile.label")}
          idPrefix="ontology-profile"
        >
          <DbObjectPanelHeader
            icon={Target}
            title={t("ontologyBuild.profile.label")}
            description={t("ontologyBuild.profile.hint")}
          />
          {profilesQuery.isLoading ? (
            <DbManagementLoadingSkeleton
              idPrefix="ontology-profile"
              ariaLabel={t("ontologyBuild.profile.loading")}
              variant="compact"
            />
          ) : profilesQuery.isError ? (
            <ErrorState
              message={t("profiles.error.load")}
              onRetry={() => void profilesQuery.refetch()}
            />
          ) : activeProfiles.length === 0 ? (
            <EmptyState title={t("ontologyBuild.empty.title")} hint={t("ontologyBuild.empty.hint")} />
          ) : (
            <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,24rem)_auto]">
              <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                <span>{t("ontologyBuild.profile.selectLabel")}</span>
                <select
                  value={selectedProfileId}
                  onChange={(event) => selectProfile(event.currentTarget.value)}
                  className="min-h-11 min-w-0 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
                  data-testid="ontology-build-profile-select"
                >
                  {activeProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>
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
            </div>
          )}
          {selectedProfileId && !profilesQuery.isLoading && !profilesQuery.isError ? (
            <div
              className="grid gap-3 border-t border-border pt-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
              aria-live="polite"
              aria-atomic="true"
              data-testid="ontology-view-lifecycle"
            >
              <div className="grid gap-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-foreground">
                    {t("ontologyBuild.view.title")}
                  </h3>
                  <StatusBadge
                    variant={
                      ontologyViewStale
                        ? "warning"
                        : ontologyViewMaterialized
                          ? "success"
                          : "neutral"
                    }
                    label={
                      ontologyViewStale
                        ? t("ontologyBuild.view.status.stale")
                        : ontologyViewMaterialized
                          ? t("ontologyBuild.view.status.current")
                          : t("ontologyBuild.view.status.missing")
                    }
                  />
                </div>
                <p className="text-sm text-muted">
                  {ontologyViewStale
                    ? t("ontologyBuild.view.staleHint")
                    : t("ontologyBuild.view.hint")}
                </p>
              </div>
              <Button
                type="button"
                variant="secondary"
                size="md"
                loading={materializingOntologyView}
                onClick={() => void materializeOntologyView()}
              >
                {ontologyViewMaterialized
                  ? t("ontologyBuild.view.rebuild")
                  : t("ontologyBuild.view.build")}
              </Button>
            </div>
          ) : null}
        </DbObjectManagementPanelShell>

        {workspaceLoading ? (
          <DbManagementLoadingSkeleton
            idPrefix="ontology-workspace"
            ariaLabel={t("ontologyBuild.workspace.loading")}
            variant="detail"
          />
        ) : workspaceFailure ? (
          <ErrorState
            message={workspaceErrorMessage}
            onRetry={() => {
              void profileDetailQuery.refetch();
              void ontologyViewQuery.refetch();
            }}
          />
        ) : selectedProfileId ? (
          <>
            <OntologyBuildSection
              profileId={selectedProfileId}
              onPublished={() => void refreshOntologyView()}
              proposalsRefreshToken={proposalsRefreshToken}
            />
            <OntologyInterchangeSection
              profileId={selectedProfileId}
              onProposalsRegistered={() => setProposalsRefreshToken((token) => token + 1)}
            />
            <ProfileOntologyEditor
              graph={ontologyGraph}
              profileId={selectedProfileId}
              selectedTables={selectedProfile?.allowed_tables ?? []}
              selectedViews={selectedProfile?.allowed_views ?? []}
              warnings={ontologyWarnings}
              onRefreshSchema={refreshSchema}
              refreshingSchema={refreshing}
              initialDraft={ontologyDraft}
              onSaveDraft={saveOntologyDraft}
              onSaveSemanticNode={saveSemanticNode}
              labels={{
                inspectorTitle: t("profiles.ontology.inspector"),
                tableUsage: t("profiles.ontology.usage"),
                cardinality: t("profiles.ontology.cardinality"),
                allowedPath: t("profiles.ontology.allowedPath"),
                graphUnavailableTitle: t("profiles.ontology.emptyTitle"),
                graphUnavailable: t("profiles.ontology.emptyHint"),
              }}
            />
            <OntologyQueryPlayground graph={ontologyGraph} />
            <OntologyMermaidPanel profileId={selectedProfileId} />
          </>
        ) : null}
      </main>
    </>
  );
}
