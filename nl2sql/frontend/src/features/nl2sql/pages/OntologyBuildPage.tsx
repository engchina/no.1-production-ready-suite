import { Button } from "@/components/ui/button";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, RefreshCw, Target } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  Banner,
  EmptyState,
  toast,
} from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { ErrorState } from "@/components/StateViews";
import { isTimeoutError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS, requestTimeoutSeconds } from "@/lib/requestPolicy";
import { DbManagementLoadingSkeleton, DbObjectManagementPanelShell, DbObjectPanelHeader } from "../components/DbObjectManagementShared";
import {
  nl2sqlIncrementalKeys,
  useProfileDetail,
  useProfileOntologyView,
  useProfileSummaries,
  useSchemaRefreshJob,
  useStartSchemaRefresh,
} from "../incrementalQueries";
import { classifyOntologyWorkspaceError, ontologyWorkspaceErrorPresentation } from "../ontologyWorkspaceError";
import { profileDisplayLabel } from "../profileDisplay";
import { OntologyBuildSection } from "../ontology/OntologyBuildSection";
import { OntologyQueryPlayground } from "../ontology/OntologyQueryPlayground";
import type { OntologyMarkdownState } from "../ontology/types";

function listLoadMoreErrorMessage(error: unknown, fallbackKey: Parameters<typeof t>[0]) {
  if (isTimeoutError(error)) {
    return t("objectSelector.loadMoreTimeout", {
      seconds: requestTimeoutSeconds(API_TIMEOUT_MS.interactiveList),
    });
  }
  return error instanceof Error ? error.message : t(fallbackKey);
}

/**
 * AI 構築、Markdown Draft 確認、質問の接地確認を一続きで扱う単一ページ。
 * 旧 tab URL は profile だけを残す正規 URL へ置き換える。
 */
export function OntologyBuildPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [refreshJobId, setRefreshJobId] = useState("");
  const [pageError, setPageError] = useState("");
  const [publishedMarkdownState, setPublishedMarkdownState] = useState<{
    profileId: string;
    hasPublished: boolean;
  }>({ profileId: "", hasPublished: false });
  const completedSchemaRefreshJob = useRef("");
  const queryClient = useQueryClient();

  const profilesQuery = useProfileSummaries("");
  const activeProfiles = useMemo(
    () => profilesQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [profilesQuery.data]
  );
  const profileLoadMoreError =
    profilesQuery.isFetchNextPageError && profilesQuery.error
      ? listLoadMoreErrorMessage(profilesQuery.error, "profiles.error.load")
      : "";
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
  const ontologyGraph = ontologyViewQuery.data?.ontology_graph ?? null;
  const ontologyWarnings = ontologyViewQuery.data?.warnings_ja ?? [];
  const hasPublishedOntology =
    publishedMarkdownState.profileId === selectedProfileId &&
    publishedMarkdownState.hasPublished;
  const visibleOntologyWarnings = hasPublishedOntology ? ontologyWarnings : [];
  const startSchemaRefresh = useStartSchemaRefresh();
  const schemaRefreshJobQuery = useSchemaRefreshJob(refreshJobId);
  const schemaRefreshStatus = schemaRefreshJobQuery.isError
    ? "error"
    : (schemaRefreshJobQuery.data?.status ?? "");
  const refreshing =
    startSchemaRefresh.isPending ||
    schemaRefreshStatus === "pending" ||
    schemaRefreshStatus === "running";

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

  const refreshOntologyView = useCallback(async () => {
    if (!selectedProfileId) return;
    await queryClient.invalidateQueries({
      queryKey: nl2sqlIncrementalKeys.profileOntologyView(selectedProfileId),
    });
  }, [queryClient, selectedProfileId]);

  const refreshSchema = async () => {
    setPageError("");
    try {
      const job = await startSchemaRefresh.mutateAsync();
      if (job.status === "done") {
        await Promise.all([profilesQuery.refetch(), refreshOntologyView()]);
        toast.success(t("common.action.schemaRefreshed"));
      } else {
        completedSchemaRefreshJob.current = "";
        setRefreshJobId(job.job_id);
      }
    } catch (err) {
      setPageError(err instanceof Error ? err.message : t("profiles.error.load"));
    }
  };

  useEffect(() => {
    const job = schemaRefreshJobQuery.data;
    if (job) {
      const reportKey = `${job.job_id}:${job.status}`;
      if (completedSchemaRefreshJob.current === reportKey) return;
      if (job.status === "done") {
        completedSchemaRefreshJob.current = reportKey;
        void Promise.all([profilesQuery.refetch(), refreshOntologyView()]);
        toast.success(t("common.action.schemaRefreshed"));
      } else if (job.status === "error") {
        completedSchemaRefreshJob.current = reportKey;
        setPageError(t("profiles.schemaRefresh.error"));
      }
      return;
    }
    if (schemaRefreshJobQuery.isError && refreshJobId) {
      const reportKey = `${refreshJobId}:query-error`;
      if (completedSchemaRefreshJob.current === reportKey) return;
      completedSchemaRefreshJob.current = reportKey;
      setPageError(t("profiles.schemaRefresh.error"));
    }
  }, [refreshJobId, schemaRefreshJobQuery.data, schemaRefreshJobQuery.isError]);

  const selectProfile = (id: string) => {
    const next = new URLSearchParams();
    if (id) next.set("profile", id);
    setSearchParams(next, { replace: true });
  };

  const handleMarkdownStateChange = useCallback(
    (state: OntologyMarkdownState | null) => {
      setPublishedMarkdownState({
        profileId: selectedProfileId,
        hasPublished: Boolean(state?.published_revision),
      });
    },
    [selectedProfileId]
  );
  const handleOntologyPublished = useCallback(async () => {
    await refreshOntologyView();
  }, [refreshOntologyView]);

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
        {refreshing ? (
          <ProcessingIndicator
            active
            label={t("common.processing.schemaRefreshing")}
            operationKey={refreshJobId || "ontology-build-schema-refresh"}
            placement="workspace"
            className="rounded-md border border-border bg-background px-3 py-2"
            testId="ontology-build-schema-refresh-processing"
            activityIcon="none"
          />
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
                      {profileDisplayLabel(profile)}
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
              {profileLoadMoreError ? (
                <div
                  className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger sm:col-span-2"
                  role="alert"
                >
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <span className="flex min-w-0 items-start gap-2">
                      <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
                      <span className="min-w-0 [overflow-wrap:anywhere]">{profileLoadMoreError}</span>
                    </span>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="w-full sm:w-auto"
                      loading={profilesQuery.isFetchingNextPage}
                      onClick={() => void profilesQuery.fetchNextPage()}
                    >
                      <RefreshCw size={15} aria-hidden="true" />
                      <span>{t("common.retry")}</span>
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          )}
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
              hasProfileSchemaInput={
                (selectedProfile?.allowed_tables?.length ?? 0) > 0 ||
                (selectedProfile?.allowed_views?.length ?? 0) > 0
              }
              onPublished={handleOntologyPublished}
              onMarkdownStateChange={handleMarkdownStateChange}
              onRefreshSchema={refreshSchema}
              refreshingSchema={refreshing}
            />
            <OntologyQueryPlayground
              graph={ontologyGraph}
              warningsJa={visibleOntologyWarnings}
              onRefreshSchema={refreshSchema}
              refreshingSchema={refreshing}
            />
          </>
        ) : null}
      </main>
    </>
  );
}
