import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Code2,
  Copy,
  DatabaseZap,
  Link2,
  MessageSquareText,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Banner,
  EmptyState,
  Pagination,
  Skeleton,
  toast,
  type DataTableColumn,
} from "@engchina/production-ready-ui";

import { StatusBadge } from "@/components/ui/status-badge";
import { MasterDetailDataTable } from "@/components/MasterDetailDataTable";
import { PageHeader } from "@/components/PageHeader";
import { FormActionBar } from "@/components/FormActionBar";
import { ObjectActionBar, type EntityAction } from "@/components/ObjectActions";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";

import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { SelectField } from "@/components/ui/select-field";
import { apiDelete, apiGet, apiPatch, apiPost, isAbortError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { INFORMATION_TABLE_SCROLL_CLASS } from "@/lib/list-density";
import { APP_ROUTES } from "@/lib/routes";
import { selectedVisibleStringKey } from "@/lib/visible-selection";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { QuestionText } from "../components/QuestionText";
import { engineLabel } from "../labels";
import { profileDisplayLabel, profileRecordDisplayLabel } from "../profileDisplay";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import {
  adminFeedbackReviewBadgeLabel,
  feedbackRatingLabel,
  userFeedbackRatingBadgeLabel,
} from "../feedbackLabels";
import type {
  AdminFeedbackReviewData,
  FeedbackClearData,
  FeedbackRating,
  FeedbackListData,
  FeedbackRecord,
  FeedbackSearchConfigData,
  ProfileSummary,
  ProfileSummaryPage,
  SelectAiDbProfile,
  SelectAiDbProfilesData,
  SelectAiFeedbackEntriesData,
  SelectAiFeedbackEntry,
  SelectAiFeedbackMutationData,
} from "../types";
import { formatElapsedDuration as formatElapsed } from "@/lib/operationTiming";

type FeedbackManagementView = "entries" | "vectorIndex" | "appFeedback" | "similarityIndex";
type AppFeedbackFilter = "all" | FeedbackRating | "unrated";
type AppFeedbackFilters = {
  rating?: AppFeedbackFilter;
  profileId?: string;
  query?: string;
};
type AppFeedbackRefreshDirection = "reset" | "next" | "prev" | "current";

const APP_FEEDBACK_PAGE_SIZE = 20;
const DEFAULT_FEEDBACK_MANAGEMENT_VIEW: FeedbackManagementView = "appFeedback";

const FEEDBACK_MANAGEMENT_TABS: Array<DbObjectTab<FeedbackManagementView>> = [
  { id: "appFeedback", label: t("feedbackManagement.tabs.appFeedback"), icon: MessageSquareText },
  { id: "entries", label: t("feedbackManagement.tabs.entries"), icon: MessageSquareText },
  { id: "vectorIndex", label: t("feedbackManagement.tabs.vectorIndex"), icon: DatabaseZap },
  { id: "similarityIndex", label: t("feedbackManagement.tabs.similarityIndex"), icon: DatabaseZap },
];

function profileOptionLabel(profile: SelectAiDbProfile) {
  return profile.owner ? `${profile.name} (${profile.owner})` : profile.name;
}

function defaultSelectAiResponse(item: FeedbackRecord) {
  return item.executable_sql || item.generated_sql;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function roundThreshold(value: number) {
  return Number(clamp(value, 0.1, 0.95).toFixed(2));
}

function resolveFeedbackManagementView(tab: string | null): FeedbackManagementView {
  if (tab === "appFeedback" || tab === "entries" || tab === "vectorIndex" || tab === "similarityIndex") {
    return tab;
  }
  return DEFAULT_FEEDBACK_MANAGEMENT_VIEW;
}

function feedbackManagementPanelId(view: FeedbackManagementView) {
  return `feedback-management-panel-${view}`;
}

function feedbackManagementWorkspaceLabel(view: FeedbackManagementView) {
  switch (view) {
    case "entries":
      return t("feedbackManagement.workspace.entries");
    case "vectorIndex":
      return t("feedbackManagement.workspace.vectorIndex");
    case "appFeedback":
      return t("feedbackManagement.workspace.appFeedback");
    case "similarityIndex":
      return t("feedbackManagement.workspace.similarityIndex");
  }
}

export function FeedbackManagementPage() {
  const confirm = useConfirm();
  const [searchParams] = useSearchParams();
  const requestedView = resolveFeedbackManagementView(searchParams.get("tab"));
  const [activeView, setActiveView] = useState<FeedbackManagementView>(requestedView);
  const [dbProfiles, setDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [profileName, setProfileName] = useState("");
  const [feedback, setFeedback] = useState<SelectAiFeedbackEntriesData | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.9);
  const [matchLimit, setMatchLimit] = useState(3);
  const [history, setHistory] = useState<FeedbackRecord[]>([]);
  const [appProfiles, setAppProfiles] = useState<ProfileSummary[]>([]);
  const [selectedFeedbackId, setSelectedFeedbackId] = useState(searchParams.get("history_id") || "");
  const [adminFeedbackRating, setAdminFeedbackRating] = useState<FeedbackRating>("good");
  const [adminFeedbackContent, setAdminFeedbackContent] = useState("");
  const [registerSelectAiFeedback, setRegisterSelectAiFeedback] = useState(false);
  const [selectAiResponse, setSelectAiResponse] = useState("");
  const [feedbackFilter, setFeedbackFilter] = useState<AppFeedbackFilter>("all");
  const [feedbackSearch, setFeedbackSearch] = useState("");
  const [appProfileFilter, setAppProfileFilter] = useState("");
  const [feedbackCursor, setFeedbackCursor] = useState("");
  const [feedbackCursorStack, setFeedbackCursorStack] = useState<string[]>([]);
  const [feedbackPage, setFeedbackPage] = useState(1);
  const [feedbackTotal, setFeedbackTotal] = useState(0);
  const [feedbackNextCursor, setFeedbackNextCursor] = useState("");
  const [feedbackConfig, setFeedbackConfig] = useState<FeedbackSearchConfigData | null>(null);
  const [savedFeedbackConfig, setSavedFeedbackConfig] = useState<FeedbackSearchConfigData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const loadSequence = useRef(0);
  const syncedAppFeedbackId = useRef<string | null>(null);
  const adminFeedbackContentRef = useRef<HTMLTextAreaElement | null>(null);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const profiles = dbProfiles?.profiles ?? [];
  const selectAiFeedbackItems = feedback?.items ?? [];
  const selectedSelectAiFeedback = useMemo(
    () => selectAiFeedbackItems[selectedIndex] ?? selectAiFeedbackItems[0] ?? null,
    [selectAiFeedbackItems, selectedIndex]
  );
  const appFeedbackItems = history;
  const feedbackConfigDirty = Boolean(
    feedbackConfig &&
      savedFeedbackConfig &&
      (feedbackConfig.similarity_threshold !== savedFeedbackConfig.similarity_threshold ||
        feedbackConfig.match_limit !== savedFeedbackConfig.match_limit)
  );
  const feedbackTotalPages = Math.max(
    1,
    Math.ceil(feedbackTotal / APP_FEEDBACK_PAGE_SIZE),
    feedbackPage + (feedbackNextCursor ? 1 : 0)
  );
  const feedbackPageStart =
    history.length > 0 ? (feedbackPage - 1) * APP_FEEDBACK_PAGE_SIZE + 1 : 0;
  const feedbackPageEnd =
    history.length > 0 ? feedbackPageStart + history.length - 1 : 0;
  const visibleSelectedFeedbackId = selectedVisibleStringKey(
    appFeedbackItems,
    selectedFeedbackId,
    (item) => item.id
  );
  const selectedAppFeedback = useMemo(
    () => appFeedbackItems.find((item) => item.id === visibleSelectedFeedbackId) ?? null,
    [appFeedbackItems, visibleSelectedFeedbackId]
  );
  const feedbackHistoryOptions = useMemo(
    () =>
      appFeedbackItems.map((item) => ({
        value: item.id,
        label: item.question,
        description: `${formatDateTime(item.feedback_updated_at || item.created_at)} / ${profileRecordDisplayLabel(item)} / ${userFeedbackRatingBadgeLabel(item.feedback_rating)}`,
      })),
    [appFeedbackItems]
  );
  const adminFeedbackContentRequired = adminFeedbackRating === "bad";
  const initialEntriesLoading =
    feedback === null && (loadSequence.current === 0 || loading === "load");

  const fetchSelectAiFeedback = (name: string, signal?: AbortSignal) =>
    apiGet<SelectAiFeedbackEntriesData>(
      `/api/nl2sql/select-ai/feedback?profile_name=${encodeURIComponent(name)}&limit=50`,
      { signal }
    );

  const fetchAppFeedback = (
    cursor = "",
    signal?: AbortSignal,
    filters: AppFeedbackFilters = {}
  ) => {
    const rating = filters.rating ?? feedbackFilter;
    const profileId = filters.profileId ?? appProfileFilter;
    const query = filters.query ?? feedbackSearch;
    const params = new URLSearchParams({
      limit: String(APP_FEEDBACK_PAGE_SIZE),
      rating,
    });
    if (cursor) params.set("cursor", cursor);
    if (profileId) params.set("profile_id", profileId);
    if (query.trim()) params.set("q", query.trim());
    return apiGet<FeedbackListData>(`/api/nl2sql/feedback?${params.toString()}`, {
      signal,
    });
  };

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading("load");
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        const [
          dbProfileData,
          appProfileData,
          appFeedbackData,
          configData,
        ] = await Promise.all([
          apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL, {
            signal,
          }),
          apiGet<ProfileSummaryPage>("/api/nl2sql/profiles/search?limit=100", {
            signal,
          }),
          fetchAppFeedback("", signal),
          apiGet<FeedbackSearchConfigData>("/api/nl2sql/feedback-config", { signal }),
        ]);
        const hasCurrentProfile = dbProfileData.profiles.some(
          (profile) => profile.name === profileName
        );
        const nextProfile = !profileName
          ? dbProfileData.profiles[0]?.name || ""
          : hasCurrentProfile
            ? profileName
            : "";
        const feedbackData = nextProfile
          ? await fetchSelectAiFeedback(nextProfile, signal)
          : null;
        if (signal.aborted || sequence !== loadSequence.current) return;
        setDbProfiles(dbProfileData);
        setProfileName(nextProfile);
        setFeedback(feedbackData);
        setSelectedIndex(0);
        setAppProfiles(appProfileData.items);
        setHistory(appFeedbackData.items);
        setFeedbackTotal(appFeedbackData.total);
        setFeedbackNextCursor(appFeedbackData.next_cursor);
        setFeedbackCursor("");
        setFeedbackCursorStack([]);
        setFeedbackPage(1);
        setFeedbackConfig(configData);
        setSavedFeedbackConfig(configData);
        setSelectedFeedbackId((current) =>
          appFeedbackData.items.some((item) => item.id === current)
            ? current
            : appFeedbackData.items[0]?.id || ""
        );
        if (announce) toast.success(t("common.action.refreshed"));
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.load"));
    } finally {
      if (sequence === loadSequence.current) setLoading("");
    }
  };

  const refreshSelectAiFeedback = async (name = profileName, announce = false) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setLoading("feedback");
    setMessage("");
    try {
      setFeedback(await fetchSelectAiFeedback(trimmed));
      setSelectedIndex(0);
      if (announce) toast.success(t("common.action.refreshed"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.load"));
    } finally {
      setLoading("");
    }
  };

  const refreshAppFeedback = async (
    cursor = "",
    direction: AppFeedbackRefreshDirection = "reset",
    filters: AppFeedbackFilters = {}
  ) => {
    setLoading("app-feedback-load");
    setMessage("");
    try {
      const data = await fetchAppFeedback(cursor, undefined, filters);
      setHistory(data.items);
      setFeedbackTotal(data.total);
      setFeedbackNextCursor(data.next_cursor);
      setSelectedFeedbackId((current) =>
        data.items.some((item) => item.id === current) ? current : data.items[0]?.id || ""
      );
      if (direction === "reset") {
        setFeedbackCursor("");
        setFeedbackCursorStack([]);
        setFeedbackPage(1);
      } else if (direction === "current") {
        setFeedbackCursor(cursor);
      } else {
        setFeedbackCursor(cursor);
        setFeedbackPage((current) => Math.max(1, current + (direction === "next" ? 1 : -1)));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.load"));
    } finally {
      setLoading("");
    }
  };

  const nextAppFeedbackPage = () => {
    if (!feedbackNextCursor) return;
    setFeedbackCursorStack((current) => [...current, feedbackCursor]);
    void refreshAppFeedback(feedbackNextCursor, "next");
  };

  const previousAppFeedbackPage = () => {
    const previous = feedbackCursorStack.at(-1);
    if (previous === undefined) return;
    setFeedbackCursorStack((current) => current.slice(0, -1));
    void refreshAppFeedback(previous, "prev");
  };

  const changeProfile = (nextProfile: string) => {
    setProfileName(nextProfile);
    void refreshSelectAiFeedback(nextProfile);
  };

  const deleteSelectedFeedback = async () => {
    if (!selectedSelectAiFeedback || !profileName.trim()) return;
    const ok = await confirm({
      title: t("feedbackManagement.deleteConfirmTitle"),
      description: t("feedbackManagement.deleteConfirmDescription"),
      confirmLabel: t("feedbackManagement.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setLoading("delete");
    setMessage("");
    try {
      const data = await apiPost<SelectAiFeedbackMutationData>("/api/nl2sql/select-ai/feedback/delete", {
        profile_name: profileName,
        sql_text: selectedSelectAiFeedback.sql_text,
      });
      const resultMessage = data.warnings.join(" ") || t("feedbackManagement.deleted");
      if (data.executed) toast.success(resultMessage);
      else setMessage(resultMessage);
      setFeedback(await fetchSelectAiFeedback(profileName));
      setSelectedIndex(0);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.delete"));
    } finally {
      setLoading("");
    }
  };

  const updateVectorIndex = async () => {
    if (!profileName.trim()) return;
    setLoading("vector-index");
    setMessage("");
    try {
      const data = await apiPost<SelectAiFeedbackMutationData>("/api/nl2sql/select-ai/feedback/vector-index", {
        profile_name: profileName,
        similarity_threshold: similarityThreshold,
        match_limit: matchLimit,
      });
      const resultMessage = data.warnings.join(" ") || t("feedbackManagement.index.updated");
      if (data.executed) toast.success(resultMessage);
      else setMessage(resultMessage);
      setFeedback(await fetchSelectAiFeedback(profileName));
      setSelectedIndex(0);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.update"));
    } finally {
      setLoading("");
    }
  };

  const saveAppFeedback = async () => {
    if (!selectedAppFeedback) return;
    const trimmedAdminFeedbackContent = adminFeedbackContent.trim();
    if (adminFeedbackRating === "bad" && !trimmedAdminFeedbackContent) {
      setMessage(t("feedbackManagement.appFeedback.adminFeedbackRequired"));
      window.requestAnimationFrame(() => adminFeedbackContentRef.current?.focus());
      return;
    }
    if (registerSelectAiFeedback && !selectAiResponse.trim()) {
      setMessage(t("feedbackManagement.appFeedback.selectAiResponseRequired"));
      return;
    }
    setLoading("app-feedback");
    setMessage("");
    try {
      const data = await apiPost<AdminFeedbackReviewData>("/api/nl2sql/feedback/admin-review", {
        history_id: selectedAppFeedback.id,
        rating: adminFeedbackRating,
        feedback_content: trimmedAdminFeedbackContent,
        register_select_ai_feedback: registerSelectAiFeedback,
        select_ai_response: selectAiResponse.trim(),
        select_ai_profile_name: profileName.trim(),
      });
      await refreshAppFeedback(feedbackCursor, "current");
      const publishWarnings = data.similar_history_publish?.warnings ?? [];
      if (publishWarnings.length > 0) {
        setMessage(publishWarnings.join(" "));
      }
      const publishStatus = data.similar_history_publish?.status ?? "published";
      const publishedToSimilarHistory =
        data.rating === "good" && publishStatus !== "skipped" && publishStatus !== "unpublished";
      if (data.select_ai_feedback) {
        const selectAiMessage = data.select_ai_feedback.warnings.join(" ");
        if (data.select_ai_feedback.executed) {
          toast.success(t("feedbackManagement.appFeedback.adminSavedAndRegistered"));
          await refreshSelectAiFeedback(profileName);
        } else {
          setMessage(selectAiMessage || t("feedbackManagement.appFeedback.selectAiRegistrationFailed"));
        }
      } else {
        toast.success(
          publishedToSimilarHistory
            ? t("feedbackManagement.appFeedback.adminSavedAndPublished")
            : t("feedbackManagement.appFeedback.adminSaved")
        );
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.appFeedback"));
    } finally {
      setLoading("");
    }
  };

  const clearAppFeedback = async () => {
    if (!selectedAppFeedback) return;
    const defaultResponse = defaultSelectAiResponse(selectedAppFeedback);
    const ok = await confirm({
      title: t("feedbackManagement.appFeedback.clearTitle"),
      description: t("feedbackManagement.appFeedback.clearDescription"),
      confirmLabel: t("feedbackManagement.appFeedback.clear"),
      tone: "danger",
    });
    if (!ok) return;
    setLoading("app-feedback-clear");
    setMessage("");
    try {
      await apiDelete<FeedbackClearData>(`/api/nl2sql/feedback/${selectedAppFeedback.id}`);
      await refreshAppFeedback(feedbackCursor, "current");
      setRegisterSelectAiFeedback(false);
      setSelectAiResponse(defaultResponse);
      toast.success(t("feedbackManagement.appFeedback.cleared"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.appFeedback"));
    } finally {
      setLoading("");
    }
  };

  const saveFeedbackConfig = async () => {
    if (!feedbackConfig) return;
    setLoading("feedback-config");
    setMessage("");
    try {
      const nextConfig = await apiPatch<FeedbackSearchConfigData>("/api/nl2sql/feedback-config", feedbackConfig);
      setFeedbackConfig(nextConfig);
      setSavedFeedbackConfig(nextConfig);
      toast.success(t("feedbackManagement.similarityIndex.configSaved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.feedbackConfig"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, []);

  useEffect(() => {
    setActiveView(requestedView);
  }, [requestedView]);

  useEffect(() => {
    if (history.length === 0 && appFeedbackItems.length === 0) return;
    setSelectedFeedbackId((current) =>
      selectedVisibleStringKey(appFeedbackItems, current, (item) => item.id)
    );
  }, [appFeedbackItems, history.length]);

  useEffect(() => {
    if (!selectedAppFeedback) {
      syncedAppFeedbackId.current = null;
      setAdminFeedbackRating("good");
      setAdminFeedbackContent("");
      setRegisterSelectAiFeedback(false);
      setSelectAiResponse("");
      return;
    }
    const switchedFeedback = syncedAppFeedbackId.current !== selectedAppFeedback.id;
    syncedAppFeedbackId.current = selectedAppFeedback.id;
    setAdminFeedbackRating(selectedAppFeedback.admin_feedback_rating ?? "good");
    setAdminFeedbackContent(selectedAppFeedback.admin_feedback_content ?? "");
    if (switchedFeedback) {
      setRegisterSelectAiFeedback(false);
      setSelectAiResponse(defaultSelectAiResponse(selectedAppFeedback));
    }
  }, [selectedAppFeedback]);

  const profileSelect = (
    <ProfileSelect
      profiles={profiles}
      value={profileName}
      disabled={loading !== ""}
      onChange={changeProfile}
    />
  );
  const entriesProfileSelect = (
    <ProfileSelect
      profiles={profiles}
      value={profileName}
      disabled={loading !== ""}
      onChange={changeProfile}
      fullWidth
    />
  );

  return (
    <>
      <PageHeader
        title={t("nav.feedbackManagement")}
        subtitle={t("feedbackManagement.subtitle")}
        actionsTestId="feedback-management-actions"
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => load(true),
            loading: loading === "load",
          },
        ]}
      />

      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={message ? { tone: "danger", message } : null}
          action={
            message && activeView === "entries" ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={loading === "load"}
                onClick={() => void load()}
              >
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("feedbackManagement.action.reload")}</span>
              </Button>
            ) : undefined
          }
        />

        <DbObjectManagementTabs
          idPrefix="feedback-management"
          tabs={FEEDBACK_MANAGEMENT_TABS}
          activeView={activeView}
          ariaLabel={t("feedbackManagement.tabs.label")}
          onViewChange={setActiveView}
        />

        {loading === "load" ? (
          <DbObjectManagementPanelShell
            id={feedbackManagementPanelId(activeView)}
            labelledBy={`feedback-management-tab-${activeView}`}
            ariaLabel={feedbackManagementWorkspaceLabel(activeView)}
            idPrefix="feedback-management-refresh"
          >
            <DbManagementLoadingSkeleton
              idPrefix="feedback-management-workspace-refresh"
              ariaLabel={t("common.processing.refreshing")}
              variant="detail"
              operationKey="feedback-management-refresh"
              placement="workspace"
              testId="feedback-management-workspace-refresh-skeleton"
              activityIcon="none"
            />
          </DbObjectManagementPanelShell>
        ) : null}

        {loading !== "load" && activeView === "entries" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-entries"
            labelledBy="feedback-management-tab-entries"
            ariaLabel={t("feedbackManagement.workspace.entries")}
            idPrefix="feedback-management-entries"
          >
            <section className="grid min-w-0 gap-4" data-testid="feedback-management-entries-workspace">
              <DbObjectPanelHeader
                title={t("feedbackManagement.entries.title")}
                description={t("feedbackManagement.entries.hint")}
                icon={MessageSquareText}
                action={
                  <span data-testid="feedback-management-entry-count">
                    <StatusBadge
                      variant="info"
                      label={t("feedbackManagement.entries.count", {
                        count: feedback?.total ?? selectAiFeedbackItems.length,
                      })}
                    />
                  </span>
                }
              />

              <section
                className="grid min-w-0 gap-3 rounded-md border border-border bg-background p-3"
                aria-label={t("feedbackManagement.entries.context")}
                data-testid="feedback-management-entries-toolbar"
              >
                <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end xl:max-w-3xl">
                  {entriesProfileSelect}
                  <Button
                    type="button"
                    variant="secondary"
                    size="lg"
                    className="h-[44px] w-full whitespace-nowrap sm:w-auto"
                    loading={loading === "feedback"}
                    disabled={!profileName.trim()}
                    onClick={() => void refreshSelectAiFeedback()}
                  >
                    <RefreshCw size={15} aria-hidden="true" />
                    <span>{t("feedbackManagement.action.refresh")}</span>
                  </Button>
                </div>

                <dl
                  className="grid min-w-0 gap-2 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)]"
                  aria-label={t("feedbackManagement.entries.runtimeInfo")}
                  data-testid="feedback-management-entries-runtime-info"
                >
                  <div className="min-w-0 self-start">
                    <dt className="sr-only">{t("feedbackManagement.entries.runtime")}</dt>
                    <dd>
                      <StatusBadge
                        variant={feedback?.runtime === "oracle" ? "success" : "neutral"}
                        label={t("feedbackManagement.entries.runtimeBadge", {
                          value: feedback?.runtime || dbProfiles?.runtime || "-",
                        })}
                      />
                    </dd>
                  </div>
                  <TechnicalRuntimeFact
                    label={t("feedbackManagement.entries.vectorIndex")}
                    value={feedback?.index_name || "-"}
                  />
                  <TechnicalRuntimeFact
                    label={t("feedbackManagement.entries.vectorTable")}
                    value={feedback?.table_name || "-"}
                  />
                </dl>
              </section>

              <FeedbackWarnings warnings={feedback?.warnings ?? dbProfiles?.warnings ?? []} />
              {loading === "feedback" && feedback ? (
                <ProcessingIndicator
                  active
                  label={t("feedbackManagement.entries.refreshing")}
                  operationKey={profileName}
                  placement="workspace"
                  className="rounded-md border border-border bg-background px-3 py-2"
                  testId="feedback-management-entries-processing"
                  activityIcon="none"
                />
              ) : null}

              <FixedSplitPane
                splitId="feedback-management-entries-split"
                preferredWidePane="left"
                minLeftPaneWidthPx={560}
                minRightPaneWidthPx={400}
                left={
                  initialEntriesLoading ? (
                    <FeedbackEntriesListSkeleton />
                  ) : (
                    <FeedbackEntriesList
                      entries={selectAiFeedbackItems}
                      selectedIndex={selectedSelectAiFeedback ? selectedIndex : null}
                      onSelect={setSelectedIndex}
                    />
                  )
                }
                right={
                  initialEntriesLoading ? (
                    <FeedbackEntryDetailSkeleton />
                  ) : (
                    <FeedbackEntryDetail
                      entry={selectedSelectAiFeedback}
                      profileName={feedback?.profile_name || profileName}
                      deleting={loading === "delete"}
                      onDelete={() => void deleteSelectedFeedback()}
                    />
                  )
                }
              />
            </section>
          </DbObjectManagementPanelShell>
        )}

        {loading !== "load" && activeView === "vectorIndex" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-vectorIndex"
            labelledBy="feedback-management-tab-vectorIndex"
            ariaLabel={t("feedbackManagement.workspace.vectorIndex")}
            idPrefix="feedback-management-vector-index"
          >
            <DbObjectPanelHeader
              title={t("feedbackManagement.index.title")}
              description={t("feedbackManagement.index.hint")}
              icon={DatabaseZap}
              action={profileSelect}
            />
            <FeedbackWarnings warnings={feedback?.warnings ?? dbProfiles?.warnings ?? []} />
            <div className="grid gap-4 lg:grid-cols-2">
              <SliderNumberField
                label={t("feedbackManagement.index.threshold")}
                min={0.1}
                max={0.95}
                step={0.05}
                value={similarityThreshold}
                onChange={(value) => setSimilarityThreshold(roundThreshold(value))}
              />
              <SliderNumberField
                label={t("feedbackManagement.index.matchLimit")}
                min={1}
                max={5}
                step={1}
                value={matchLimit}
                onChange={(value) => setMatchLimit(Math.round(clamp(value, 1, 5)))}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant="neutral" label={feedback?.runtime ?? dbProfiles?.runtime ?? "-"} />
              {feedback?.index_name && <StatusBadge variant="info" label={feedback.index_name} />}
              {feedback?.table_name && <StatusBadge variant="neutral" label={feedback.table_name} />}
            </div>
            <FormActionBar
              ariaLabel={t("feedbackManagement.index.actions")}
              testId="feedback-vector-index-actions"
              primaryActions={[
                {
                  id: "update",
                  label: t("feedbackManagement.index.update"),
                  icon: Save,
                  loading: loading === "vector-index",
                  disabled: !profileName.trim(),
                  onClick: () => void updateVectorIndex(),
                },
              ]}
            />
          </DbObjectManagementPanelShell>
        )}

        {loading !== "load" && activeView === "appFeedback" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-appFeedback"
            labelledBy="feedback-management-tab-appFeedback"
            ariaLabel={t("feedbackManagement.workspace.appFeedback")}
            idPrefix="feedback-management-app-feedback"
            splitId="feedback-management-app-feedback-history-left-v2"
            preferredWidePane="left"
            minLeftPaneWidthPx={640}
            minRightPaneWidthPx={420}
          >
            <section
              className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-background p-4"
              data-testid="feedback-history-pane"
            >
              <DbObjectPanelHeader
                title={t("feedbackManagement.appFeedback.historyList")}
                icon={MessageSquareText}
                action={
                  <StatusBadge
                    variant="neutral"
                    label={`${t("feedbackManagement.metric.appFeedback")} ${feedbackTotal}`}
                  />
                }
              />
              <form
                className="grid min-w-0 gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_12rem_16rem_auto] xl:items-end"
                data-testid="feedback-app-filters"
                onSubmit={(event) => {
                  event.preventDefault();
                  void refreshAppFeedback("", "reset", { query: feedbackSearch });
                }}
              >
                <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                  <span>{t("feedbackManagement.appFeedback.search")}</span>
                  <input
                    value={feedbackSearch}
                    onChange={(event) => setFeedbackSearch(event.currentTarget.value)}
                    className="min-h-[44px] w-full min-w-0 max-w-full rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                    placeholder={t("feedbackManagement.appFeedback.searchPlaceholder")}
                  />
                </label>
                <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                  <span>{t("feedbackManagement.appFeedback.filter")}</span>
                  <select
                    aria-label={t("feedbackManagement.appFeedback.filter")}
                    value={feedbackFilter}
                    onChange={(event) => {
                      const rating = event.currentTarget.value as AppFeedbackFilter;
                      setFeedbackFilter(rating);
                      void refreshAppFeedback("", "reset", { rating });
                    }}
                    className="min-h-[44px] w-full min-w-0 max-w-full rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                  >
                    <option value="all">{t("feedbackManagement.appFeedback.filterAll")}</option>
                    <option value="good">{t("nl2sql.feedback.good")}</option>
                    <option value="bad">{t("nl2sql.feedback.bad")}</option>
                    <option value="unrated">{t("feedbackManagement.appFeedback.unrated")}</option>
                  </select>
                </label>
                <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                  <span>{t("feedbackManagement.appFeedback.profileFilter")}</span>
                  <select
                    aria-label={t("feedbackManagement.appFeedback.profileFilter")}
                    value={appProfileFilter}
                    onChange={(event) => {
                      const profileId = event.currentTarget.value;
                      setAppProfileFilter(profileId);
                      void refreshAppFeedback("", "reset", { profileId });
                    }}
                    className="min-h-[44px] w-full min-w-0 max-w-full rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                  >
                    <option value="">{t("feedbackManagement.appFeedback.profileAll")}</option>
                    {appProfiles.filter((profile) => !profile.archived).map((profile) => (
                      <option key={profile.id} value={profile.id}>{profileDisplayLabel(profile)}</option>
                    ))}
                  </select>
                </label>
                {/* 入力と同じ行の送信操作なので、Button spec の許容例に従い入力高 44px に揃える。 */}
                <Button
                  type="submit"
                  variant="secondary"
                  size="lg"
                  className="h-[44px] w-full whitespace-nowrap md:w-auto"
                  loading={loading === "app-feedback-load"}
                >
                  <RefreshCw size={16} aria-hidden="true" />
                  <span>{t("feedbackManagement.appFeedback.applyFilters")}</span>
                </Button>
              </form>
              <div className="grid min-w-0 gap-2">
                {appFeedbackItems.length > 0 ? (
                  appFeedbackItems.map((item) => (
                    <FeedbackHistoryRow
                      key={item.id}
                      item={item}
                      selected={selectedAppFeedback?.id === item.id}
                      onSelect={() => setSelectedFeedbackId(item.id)}
                    />
                  ))
                ) : (
                  <EmptyState
                    title={t("feedbackManagement.appFeedback.noMatchesTitle")}
                    hint={t("feedbackManagement.appFeedback.noMatchesHint")}
                  />
                )}
              </div>
              <Pagination
                page={feedbackPage}
                totalPages={feedbackTotalPages}
                onPageChange={(nextPage) => {
                  if (nextPage > feedbackPage && feedbackNextCursor) nextAppFeedbackPage();
                  if (nextPage < feedbackPage && feedbackCursorStack.length > 0) {
                    previousAppFeedbackPage();
                  }
                }}
                summary={t("feedbackManagement.appFeedback.pagination.range", {
                  start: feedbackPageStart,
                  end: feedbackPageEnd,
                  total: feedbackTotal,
                })}
                pageIndicator={t("feedbackManagement.appFeedback.pagination.page", {
                  page: feedbackPage,
                  total: feedbackTotalPages,
                })}
                prevLabel={t("feedbackManagement.appFeedback.pagination.prev")}
                nextLabel={t("feedbackManagement.appFeedback.pagination.next")}
                ariaLabel={t("feedbackManagement.appFeedback.pagination.label")}
                testId="app-feedback-pagination"
              />
            </section>

            <section className="grid min-w-0 content-start gap-4" data-testid="app-feedback-editor-pane">
              <DbObjectPanelHeader
                title={t("feedbackManagement.appFeedback.title")}
                description={t("feedbackManagement.appFeedback.hint")}
                icon={MessageSquareText}
              />
              {history.length > 0 && selectedAppFeedback ? (
                <>
                  <SelectField
                    id="feedback-app-history-select"
                    label={t("feedbackManagement.appFeedback.history")}
                    value={selectedAppFeedback.id}
                    options={feedbackHistoryOptions}
                    onValueChange={setSelectedFeedbackId}
                    className="min-w-0"
                    buttonClassName="h-11"
                  />
                  <section className="rounded-md border border-border bg-card p-3">
                    <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.history")}</p>
                    <QuestionText
                      value={selectedAppFeedback.question}
                      variant="detail"
                      maxLines={3}
                      expandable
                      className="mt-1"
                      testId="app-feedback-selected-question"
                    />
                  </section>
                  <div className="grid min-w-0 gap-3 sm:grid-cols-2">
                    <div className="rounded-md border border-border bg-card p-3">
                      <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.profile")}</p>
                      <p className="mt-1 break-words text-sm font-semibold text-foreground">
                        {profileRecordDisplayLabel(selectedAppFeedback)}
                      </p>
                    </div>
                    <div className="rounded-md border border-border bg-card p-3">
                      <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.createdAt")}</p>
                      <p className="mt-1 text-sm font-semibold text-foreground">{formatDateTime(selectedAppFeedback.feedback_updated_at || selectedAppFeedback.created_at)}</p>
                      {selectedAppFeedback.training_status && (
                        <div className="mt-2"><StatusBadge variant="info" label={t(`qcm.candidates.status.${selectedAppFeedback.training_status}`)} /></div>
                      )}
                    </div>
                  </div>
                  <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                    <span>{t("feedbackManagement.appFeedback.generatedSql")}</span>
                    <textarea
                      value={selectedAppFeedback.executable_sql || selectedAppFeedback.generated_sql}
                      readOnly
                      rows={5}
                      className="min-h-32 w-full min-w-0 max-w-full rounded-md border border-border bg-code px-3 py-2 font-mono text-sm leading-6 text-code-fg outline-none"
                    />
                  </label>
                  <div className="grid min-w-0 gap-3 sm:grid-cols-2">
                    <div className="rounded-md border border-border bg-card p-3">
                      <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.userRating")}</p>
                      <div className="mt-2">
                        <StatusBadge
                          variant={selectedAppFeedback.feedback_rating ? "success" : "neutral"}
                          label={feedbackRatingLabel(selectedAppFeedback.feedback_rating)}
                        />
                      </div>
                    </div>
                    <div className="rounded-md border border-border bg-card p-3">
                      <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.adminRatingStatus")}</p>
                      <div className="mt-2">
                        <StatusBadge
                          variant={selectedAppFeedback.admin_feedback_rating === "good" ? "success" : "neutral"}
                          label={feedbackRatingLabel(selectedAppFeedback.admin_feedback_rating)}
                        />
                      </div>
                    </div>
                  </div>
                  <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                    <span>{t("feedbackManagement.appFeedback.userFeedbackContent")}</span>
                    <textarea
                      aria-label={t("feedbackManagement.appFeedback.userFeedbackContent")}
                      value={selectedAppFeedback.feedback_comment}
                      readOnly
                      rows={3}
                      className="min-h-24 w-full min-w-0 max-w-full rounded-md border border-border bg-background px-3 py-2 text-sm leading-6 text-foreground outline-none"
                      placeholder={t("feedbackManagement.appFeedback.userFeedbackEmpty")}
                    />
                  </label>
                  <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                    <span>{t("feedbackManagement.appFeedback.adminRating")}</span>
                    <select
                      aria-label={t("feedbackManagement.appFeedback.adminRating")}
                      value={adminFeedbackRating}
                      onChange={(event) => setAdminFeedbackRating(event.currentTarget.value as FeedbackRating)}
                      className="min-h-11 w-full min-w-0 max-w-full rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                    >
                      <option value="good">{t("nl2sql.feedback.good")}</option>
                      <option value="bad">{t("nl2sql.feedback.bad")}</option>
                    </select>
                  </label>
                  <div className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                    <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                      <label htmlFor="app-feedback-admin-content">
                        {t("feedbackManagement.appFeedback.adminFeedbackContent")}
                      </label>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        disabled={!selectedAppFeedback.feedback_comment.trim()}
                        onClick={() => setAdminFeedbackContent(selectedAppFeedback.feedback_comment)}
                      >
                        <Copy size={15} aria-hidden="true" />
                        <span>{t("feedbackManagement.appFeedback.copyUserContent")}</span>
                      </Button>
                    </div>
                    <textarea
                      ref={adminFeedbackContentRef}
                      id="app-feedback-admin-content"
                      aria-label={t("feedbackManagement.appFeedback.adminFeedbackContent")}
                      aria-required={adminFeedbackContentRequired}
                      value={adminFeedbackContent}
                      onChange={(event) => setAdminFeedbackContent(event.currentTarget.value)}
                      required={adminFeedbackContentRequired}
                      rows={4}
                      className="min-h-28 w-full min-w-0 max-w-full rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                      placeholder={t("feedbackManagement.appFeedback.adminFeedbackPlaceholder")}
                    />
                  </div>
                  <label className="flex min-h-11 min-w-0 items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground">
                    <input
                      type="checkbox"
                      checked={registerSelectAiFeedback}
                      onChange={(event) => setRegisterSelectAiFeedback(event.currentTarget.checked)}
                      className="h-4 w-4 shrink-0 accent-primary"
                    />
                    <span>{t("feedbackManagement.appFeedback.registerSelectAi")}</span>
                  </label>
                  {registerSelectAiFeedback && (
                    <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                      <span>{t("feedbackManagement.appFeedback.selectAiResponse")}</span>
                      <textarea
                        aria-label={t("feedbackManagement.appFeedback.selectAiResponse")}
                        value={selectAiResponse}
                        onChange={(event) => setSelectAiResponse(event.currentTarget.value)}
                        rows={5}
                        className="min-h-32 w-full min-w-0 max-w-full rounded-md border border-border bg-code px-3 py-2 font-mono text-sm leading-6 text-code-fg outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                        placeholder={t("feedbackManagement.appFeedback.selectAiResponsePlaceholder")}
                      />
                    </label>
                  )}
                  <FormActionBar
                    ariaLabel={t("feedbackManagement.appFeedback.actions")}
                    testId="feedback-app-actions"
                    primaryActions={[
                      {
                        id: "save",
                        label: t("feedbackManagement.appFeedback.save"),
                        icon: Save,
                        loading: loading === "app-feedback",
                        disabled: loading === "app-feedback",
                        onClick: () => void saveAppFeedback(),
                      },
                    ]}
                    secondaryActions={
                      selectedAppFeedback.admin_feedback_rating === "good"
                        ? [
                            {
                              id: "open-candidate",
                              label: t("feedbackManagement.appFeedback.openCandidate"),
                              icon: Link2,
                              href: `${APP_ROUTES.questionClassifierModels}?tab=candidates&history_id=${encodeURIComponent(selectedAppFeedback.id)}`,
                            },
                          ]
                        : []
                    }
                    dangerActions={[
                      {
                        id: "clear",
                        label: t("feedbackManagement.appFeedback.clear"),
                        icon: Trash2,
                        loading: loading === "app-feedback-clear",
                        onClick: () => void clearAppFeedback(),
                      },
                    ]}
                  />
                </>
              ) : (
                <EmptyState
                  title={t("feedbackManagement.appFeedback.emptyTitle")}
                  hint={t("feedbackManagement.appFeedback.emptyHint")}
                />
              )}
            </section>

          </DbObjectManagementPanelShell>
        )}

        {loading !== "load" && activeView === "similarityIndex" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-similarityIndex"
            labelledBy="feedback-management-tab-similarityIndex"
            ariaLabel={t("feedbackManagement.workspace.similarityIndex")}
            idPrefix="feedback-management-similarity-index"
          >
            <DbObjectPanelHeader
              title={t("feedbackManagement.similarityIndex.title")}
              description={t("feedbackManagement.similarityIndex.hint")}
              icon={DatabaseZap}
            />
            <div className="grid gap-4 lg:grid-cols-2">
              <SimilarityConfigField
                id="feedback-similarity-threshold"
                label={t("feedbackManagement.similarityIndex.threshold")}
                hint={t("feedbackManagement.similarityIndex.thresholdHint")}
                min={0}
                max={1}
                step={0.05}
                value={feedbackConfig?.similarity_threshold ?? 0}
                onChange={(value) => {
                  setFeedbackConfig((current) => ({
                    similarity_threshold: clamp(value, 0, 1),
                    match_limit: current?.match_limit ?? 3,
                  }));
                }}
              />
              <SimilarityConfigField
                id="feedback-similarity-match-limit"
                label={t("feedbackManagement.similarityIndex.matchLimit")}
                hint={t("feedbackManagement.similarityIndex.matchLimitHint")}
                min={1}
                max={20}
                step={1}
                value={feedbackConfig?.match_limit ?? 3}
                onChange={(value) => {
                  setFeedbackConfig((current) => ({
                    similarity_threshold: current?.similarity_threshold ?? 0,
                    match_limit: Math.round(clamp(value, 1, 20)),
                  }));
                }}
              />
            </div>
            <FormActionBar
              ariaLabel={t("feedbackManagement.similarityIndex.configActions")}
              testId="feedback-similarity-index-actions"
              status={
                feedbackConfigDirty ? (
                  <p className="text-sm text-muted">
                    {t("feedbackManagement.similarityIndex.configDirty")}
                  </p>
                ) : null
              }
              primaryActions={[
                {
                  id: "save-config",
                  label: t("feedbackManagement.similarityIndex.saveConfig"),
                  icon: Save,
                  loading: loading === "feedback-config",
                  disabled: !feedbackConfig || !feedbackConfigDirty,
                  onClick: () => void saveFeedbackConfig(),
                },
              ]}
            />
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}

function ProfileSelect({
  profiles,
  value,
  disabled,
  onChange,
  fullWidth = false,
}: {
  profiles: SelectAiDbProfile[];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  fullWidth?: boolean;
}) {
  return (
    <label
      className={`grid min-w-0 gap-1 text-sm font-medium text-foreground ${
        fullWidth ? "w-full" : "sm:min-w-72"
      }`}
    >
      <span>{t("feedbackManagement.profile")}</span>
      <select
        value={value}
        disabled={disabled || profiles.length === 0}
        onChange={(event) => onChange(event.currentTarget.value)}
        className="min-h-[44px] w-full min-w-0 rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
      >
        {profiles.map((profile) => (
          <option key={profile.name} value={profile.name}>
            {profileOptionLabel(profile)}
          </option>
        ))}
      </select>
    </label>
  );
}

function FeedbackWarnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="grid gap-2">
      {warnings.map((warning) => (
        <Banner key={warning} severity="warning">
          {warning}
        </Banner>
      ))}
    </div>
  );
}

function feedbackEntryRowKey(entry: SelectAiFeedbackEntry, index: number) {
  return `${entry.sql_id || entry.sql_text || "feedback"}-${index}`;
}

function FeedbackEntriesList({
  entries,
  selectedIndex,
  onSelect,
}: {
  entries: SelectAiFeedbackEntry[];
  selectedIndex: number | null;
  onSelect: (index: number) => void;
}) {
  const columns: Array<DataTableColumn<SelectAiFeedbackEntry>> = [
    {
      key: "content",
      header: t("feedbackManagement.entries.content"),
      className: "w-[42%]",
      render: (entry, index) => (
        <button
          type="button"
          className="block w-full rounded-sm text-left text-foreground underline-offset-2 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          aria-label={t("feedbackManagement.entries.select", {
            content: entry.content || entry.sql_text || "-",
          })}
          onClick={(event) => {
            event.stopPropagation();
            onSelect(index);
          }}
        >
          <span className="line-clamp-3 break-words">{entry.content || "-"}</span>
        </button>
      ),
    },
    {
      key: "sql_text",
      header: t("feedbackManagement.entries.sqlText"),
      className: "w-[58%]",
      render: (entry) => (
        <p className="line-clamp-3 break-words font-mono text-xs leading-5 text-foreground">
          {entry.sql_text || "-"}
        </p>
      ),
    },
  ];

  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="feedback-entries-list-heading">
      <DbObjectPanelHeader
        headingId="feedback-entries-list-heading"
        title={t("feedbackManagement.entries.listTitle")}
        description={t("feedbackManagement.entries.listHint")}
        icon={MessageSquareText}
      />
      <MasterDetailDataTable
        columns={columns}
        rows={entries}
        getRowKey={feedbackEntryRowKey}
        selectedRowKey={
          selectedIndex == null || !entries[selectedIndex]
            ? null
            : feedbackEntryRowKey(entries[selectedIndex], selectedIndex)
        }
        onRowSelect={(entry) => {
          const index = entries.indexOf(entry);
          if (index >= 0) onSelect(index);
        }}
        getRowAriaLabel={(entry) =>
          t("feedbackManagement.entries.select", {
            content: entry.content || entry.sql_text || "-",
          })
        }
        dense
        empty={
          <EmptyState
            title={t("feedbackManagement.entries.emptyTitle")}
            hint={t("feedbackManagement.entries.emptyHint")}
          />
        }
        ariaLabel={t("feedbackManagement.entries.listAria")}
        testId="feedback-management-entries-table"
        scrollTestId="feedback-management-entries-scroll-region"
        scrollClassName={INFORMATION_TABLE_SCROLL_CLASS}
        className="min-w-[640px] table-fixed [&_thead]:sticky [&_thead]:top-0 [&_thead]:z-10 [&_thead]:uppercase [&_tbody_tr]:h-[3.5rem]"
      />
    </section>
  );
}

function FeedbackEntryDetail({
  entry,
  profileName,
  deleting,
  onDelete,
}: {
  entry: SelectAiFeedbackEntry | null;
  profileName: string;
  deleting: boolean;
  onDelete: () => void;
}) {
  const actions: EntityAction[] = entry
    ? [
        {
          id: "delete",
          label: t("feedbackManagement.delete"),
          icon: Trash2,
          tone: "danger",
          loading: deleting,
          onSelect: onDelete,
        },
      ]
    : [];

  return (
    <section
      className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4"
      aria-labelledby="feedback-entry-detail-heading"
      data-testid="feedback-management-entry-detail"
    >
      <DbObjectPanelHeader
        headingId="feedback-entry-detail-heading"
        title={t("feedbackManagement.entries.selectedSql")}
        description={entry ? t("feedbackManagement.entries.detailHint") : undefined}
        icon={Code2}
        action={
          actions.length > 0 ? (
            <ObjectActionBar
              ariaLabel={t("feedbackManagement.entries.selectedSqlActions")}
              testId="feedback-selected-sql-actions"
              actions={actions}
            />
          ) : undefined
        }
      />

      {entry ? (
        <>
          <div className="grid min-w-0 gap-3 sm:grid-cols-2">
            <CompactFact label={t("feedbackManagement.entries.sqlId")} value={entry.sql_id || "-"} />
            <CompactFact label={t("feedbackManagement.profile")} value={profileName || "-"} />
          </div>
          <section className="rounded-md border border-border bg-card p-3">
            <p className="text-xs font-medium text-muted">{t("feedbackManagement.entries.content")}</p>
            <p className="mt-1 break-words text-sm leading-6 text-foreground">{entry.content || "-"}</p>
          </section>
          <section className="grid min-w-0 gap-2">
            <p className="text-xs font-medium text-muted">{t("feedbackManagement.entries.sqlText")}</p>
            <pre
              aria-label={t("feedbackManagement.entries.selectedSql")}
              data-testid="feedback-management-entry-sql"
              className="max-h-[30.5rem] min-h-44 overflow-auto rounded-md border border-border bg-code p-3 font-mono text-sm leading-6 text-code-fg"
            >
              <code>{entry.sql_text || "-"}</code>
            </pre>
          </section>
        </>
      ) : (
        <div
          className="grid min-h-52 place-items-center rounded-md border border-border bg-card p-4"
          data-testid="feedback-management-entry-detail-empty"
        >
          <EmptyState
            title={t("feedbackManagement.entries.detailEmptyTitle")}
            hint={t("feedbackManagement.entries.detailEmptyHint")}
          />
        </div>
      )}
    </section>
  );
}

function FeedbackEntriesListSkeleton() {
  return (
    <section
      className="grid min-w-0 content-start gap-3"
      aria-label={t("feedbackManagement.entries.loading")}
      aria-busy="true"
      data-testid="feedback-management-entries-list-skeleton"
    >
      <span className="sr-only" role="status">{t("feedbackManagement.entries.loading")}</span>
      <Skeleton className="h-6 w-44" />
      <Skeleton className="h-5 w-72 max-w-full" />
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <Skeleton className="h-10 rounded-none" />
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-14 rounded-none border-t border-border" />
        ))}
      </div>
    </section>
  );
}

function TechnicalRuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-1 rounded-md border border-border bg-card px-2 py-1 text-xs leading-5">
      <dt className="shrink-0 font-medium text-muted">{label}:</dt>
      <dd className="min-w-0 break-all font-medium text-foreground">{value}</dd>
    </div>
  );
}

function FeedbackEntryDetailSkeleton() {
  return (
    <section
      className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4"
      aria-label={t("feedbackManagement.entries.loading")}
      aria-busy="true"
      data-testid="feedback-management-entry-detail-skeleton"
    >
      <Skeleton className="h-6 w-52 max-w-full" />
      <Skeleton className="h-5 w-64 max-w-full" />
      <div className="grid gap-3 sm:grid-cols-2">
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
      <Skeleton className="h-24" />
      <Skeleton className="h-56" />
    </section>
  );
}

function FeedbackHistoryRow({
  item,
  selected,
  onSelect,
}: {
  item: FeedbackRecord;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-testid="feedback-history-row"
      aria-current={selected ? "true" : undefined}
      className={`grid min-w-0 max-w-full gap-2 rounded-md border p-3 text-left text-sm outline-none focus:ring-2 focus:ring-ring/40 ${
        selected ? "border-primary/40 bg-primary/10" : "border-border bg-card hover:bg-background"
      }`}
      onClick={onSelect}
    >
      <span className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <span className="min-w-0 flex-1">
          <QuestionText
            value={item.question}
            variant="select"
            maxLines={1}
            testId="feedback-history-question"
          />
        </span>
        <span className="flex min-w-0 flex-wrap gap-2">
          <StatusBadge variant="neutral" label={engineLabel(item.engine)} />
          <StatusBadge
            variant={item.feedback_rating ? "success" : "neutral"}
            label={userFeedbackRatingBadgeLabel(item.feedback_rating)}
          />
          <StatusBadge
            variant={item.admin_feedback_rating === "good" ? "success" : "neutral"}
            label={adminFeedbackReviewBadgeLabel(item.admin_feedback_rating)}
          />
          {(item.profile_name || item.profile_category) && (
            <StatusBadge variant="info" label={profileRecordDisplayLabel(item)} />
          )}
          {item.training_status && <StatusBadge variant="neutral" label={t(`qcm.candidates.status.${item.training_status}`)} />}
          <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
        </span>
      </span>
      {item.feedback_comment && (
        <span className="rounded-md border border-primary/20 bg-primary/10 px-3 py-2 text-foreground">
          {item.feedback_comment}
        </span>
      )}
      {item.admin_feedback_content && (
        <span className="rounded-md border border-border bg-background px-3 py-2 text-foreground">
          {item.admin_feedback_content}
        </span>
      )}
    </button>
  );
}

function SimilarityConfigField({
  id,
  label,
  hint,
  min,
  max,
  step,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
}) {
  const hintId = `${id}-hint`;
  return (
    <fieldset className="grid gap-2 text-sm font-medium text-foreground">
      <legend className="mb-1">{label}</legend>
      <input
        type="range"
        aria-label={`${label} slider`}
        aria-describedby={hintId}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="w-full accent-primary"
      />
      <input
        id={id}
        type="number"
        min={min}
        max={max}
        step={step}
        inputMode="decimal"
        aria-label={label}
        aria-describedby={hintId}
        value={value}
        onChange={(event) => {
          const nextValue = Number(event.currentTarget.value);
          if (!Number.isNaN(nextValue)) onChange(nextValue);
        }}
        className="min-h-11 rounded-md border border-border bg-card px-3 py-2 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
      />
      <span id={hintId} className="text-xs font-normal leading-5 text-muted">
        {hint}
      </span>
    </fieldset>
  );
}

function SliderNumberField({
  label,
  min,
  max,
  step,
  value,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <fieldset className="grid gap-3 rounded-md border border-border bg-background p-4 text-sm font-medium text-foreground">
      <legend className="px-1">{label}</legend>
      <input
        type="range"
        aria-label={`${label} slider`}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="w-full accent-primary"
      />
      <input
        type="number"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="min-h-11 rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
      />
    </fieldset>
  );
}

function CompactFact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-card p-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-foreground">{value}</p>
      {hint ? <p className="mt-1 text-xs leading-5 text-muted">{hint}</p> : null}
    </div>
  );
}
