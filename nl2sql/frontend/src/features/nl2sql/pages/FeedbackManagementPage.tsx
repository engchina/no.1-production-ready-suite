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
  EmptyState,
  Pagination,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { FormActionBar } from "@/components/FormActionBar";
import { ObjectActionBar, RowActionMenu, type EntityAction } from "@/components/ObjectActions";
import { ProcessingIndicator } from "@/components/ProcessingState";

import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { SelectField } from "@/components/ui/select-field";
import { apiDelete, apiGet, apiPatch, apiPost, isAbortError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_SCROLL_CLASS,
} from "@/lib/list-density";
import { APP_ROUTES } from "@/lib/routes";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { QuestionText } from "../components/QuestionText";
import { engineLabel } from "../labels";
import { profileDisplayLabel, profileRecordDisplayLabel } from "../profileDisplay";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import type {
  AdminFeedbackReviewData,
  FeedbackClearData,
  FeedbackEntriesData,
  FeedbackIndexData,
  FeedbackRating,
  FeedbackListData,
  FeedbackRecord,
  FeedbackSearchConfigData,
  FeedbackVectorEntry,
  Nl2SqlProfile,
  SelectAiDbProfile,
  SelectAiDbProfilesData,
  SelectAiFeedbackEntriesData,
  SelectAiFeedbackEntry,
  SelectAiFeedbackMutationData,
} from "../types";
import { formatElapsedDuration as formatElapsed } from "@/lib/operationTiming";

type FeedbackManagementView = "entries" | "vectorIndex" | "appFeedback" | "similarityIndex";

const APP_FEEDBACK_PAGE_SIZE = 20;

const FEEDBACK_MANAGEMENT_TABS: Array<DbObjectTab<FeedbackManagementView>> = [
  { id: "appFeedback", label: t("feedbackManagement.tabs.appFeedback"), icon: MessageSquareText },
  { id: "entries", label: t("feedbackManagement.tabs.entries"), icon: MessageSquareText },
  { id: "vectorIndex", label: t("feedbackManagement.tabs.vectorIndex"), icon: DatabaseZap },
  { id: "similarityIndex", label: t("feedbackManagement.tabs.similarityIndex"), icon: DatabaseZap },
];

function formatAttributes(entry: SelectAiFeedbackEntry) {
  if (entry.raw_attributes) return entry.raw_attributes;
  return JSON.stringify(entry.attributes ?? {});
}

function profileOptionLabel(profile: SelectAiDbProfile) {
  return profile.owner ? `${profile.name} (${profile.owner})` : profile.name;
}

function feedbackLabel(item: FeedbackRecord) {
  if (item.feedback_rating === "good") return t("nl2sql.feedback.good");
  if (item.feedback_rating === "bad") return t("nl2sql.feedback.bad");
  return t("feedbackManagement.appFeedback.unrated");
}

function adminFeedbackLabel(item: { admin_feedback_rating?: FeedbackRating | null }) {
  if (item.admin_feedback_rating === "good") return t("nl2sql.feedback.good");
  if (item.admin_feedback_rating === "bad") return t("nl2sql.feedback.bad");
  return t("feedbackManagement.appFeedback.unrated");
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

export function FeedbackManagementPage() {
  const confirm = useConfirm();
  const [searchParams] = useSearchParams();
  const [activeView, setActiveView] = useState<FeedbackManagementView>(
    searchParams.get("tab") === "appFeedback" ? "appFeedback" : "entries"
  );
  const [dbProfiles, setDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [profileName, setProfileName] = useState("");
  const [feedback, setFeedback] = useState<SelectAiFeedbackEntriesData | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.9);
  const [matchLimit, setMatchLimit] = useState(3);
  const [history, setHistory] = useState<FeedbackRecord[]>([]);
  const [appProfiles, setAppProfiles] = useState<Nl2SqlProfile[]>([]);
  const [selectedFeedbackId, setSelectedFeedbackId] = useState(searchParams.get("history_id") || "");
  const [adminFeedbackRating, setAdminFeedbackRating] = useState<FeedbackRating>("good");
  const [adminFeedbackContent, setAdminFeedbackContent] = useState("");
  const [registerSelectAiFeedback, setRegisterSelectAiFeedback] = useState(false);
  const [selectAiResponse, setSelectAiResponse] = useState("");
  const [feedbackFilter, setFeedbackFilter] = useState<"all" | FeedbackRating | "unrated">("all");
  const [feedbackSearch, setFeedbackSearch] = useState("");
  const [appProfileFilter, setAppProfileFilter] = useState("");
  const [feedbackCursor, setFeedbackCursor] = useState("");
  const [feedbackCursorStack, setFeedbackCursorStack] = useState<string[]>([]);
  const [feedbackPage, setFeedbackPage] = useState(1);
  const [feedbackTotal, setFeedbackTotal] = useState(0);
  const [feedbackNextCursor, setFeedbackNextCursor] = useState("");
  const [feedbackIndex, setFeedbackIndex] = useState<FeedbackIndexData | null>(null);
  const [feedbackEntries, setFeedbackEntries] = useState<FeedbackEntriesData | null>(null);
  const [feedbackConfig, setFeedbackConfig] = useState<FeedbackSearchConfigData | null>(null);
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
  const appFeedbackItems = useMemo(() => {
    const q = feedbackSearch.trim().toLowerCase();
    return history
      .filter((item) => {
        if (feedbackFilter === "unrated" && item.feedback_rating) return false;
        if (feedbackFilter !== "all" && feedbackFilter !== "unrated" && item.feedback_rating !== feedbackFilter) {
          return false;
        }
        if (!q) return true;
        return (
          item.question.toLowerCase().includes(q) ||
          item.generated_sql.toLowerCase().includes(q) ||
          item.feedback_comment.toLowerCase().includes(q) ||
          (item.admin_feedback_content ?? "").toLowerCase().includes(q)
        );
      })
      .slice(0, APP_FEEDBACK_PAGE_SIZE);
  }, [feedbackFilter, feedbackSearch, history]);
  const feedbackTotalPages = Math.max(
    1,
    Math.ceil(feedbackTotal / APP_FEEDBACK_PAGE_SIZE),
    feedbackPage + (feedbackNextCursor ? 1 : 0)
  );
  const feedbackPageStart =
    history.length > 0 ? (feedbackPage - 1) * APP_FEEDBACK_PAGE_SIZE + 1 : 0;
  const feedbackPageEnd =
    history.length > 0 ? feedbackPageStart + history.length - 1 : 0;
  const selectedAppFeedback = useMemo(
    () => history.find((item) => item.id === selectedFeedbackId) ?? history[0] ?? null,
    [history, selectedFeedbackId]
  );
  const feedbackHistoryOptions = useMemo(
    () =>
      history.map((item) => ({
        value: item.id,
        label: item.question,
        description: `${formatDateTime(item.feedback_updated_at || item.created_at)} / ${profileRecordDisplayLabel(item)} / ${feedbackLabel(item)}`,
      })),
    [history]
  );
  const adminFeedbackContentRequired = adminFeedbackRating === "bad";

  const fetchSelectAiFeedback = (name: string, signal?: AbortSignal) =>
    apiGet<SelectAiFeedbackEntriesData>(
      `/api/nl2sql/select-ai/feedback?profile_name=${encodeURIComponent(name)}&limit=50`,
      { signal }
    );

  const fetchAppFeedback = (cursor = "", signal?: AbortSignal) => {
    const params = new URLSearchParams({
      limit: String(APP_FEEDBACK_PAGE_SIZE),
      rating: feedbackFilter,
    });
    if (cursor) params.set("cursor", cursor);
    if (appProfileFilter) params.set("profile_id", appProfileFilter);
    if (feedbackSearch.trim()) params.set("q", feedbackSearch.trim());
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
          indexData,
          entriesData,
          configData,
        ] = await Promise.all([
          apiGet<SelectAiDbProfilesData>(BUSINESS_SELECT_AI_DB_PROFILES_URL, {
            signal,
          }),
          apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", { signal }),
          fetchAppFeedback("", signal),
          apiGet<FeedbackIndexData>("/api/nl2sql/feedback-index", { signal }),
          apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries", { signal }),
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
        setAppProfiles(appProfileData);
        setHistory(appFeedbackData.items);
        setFeedbackTotal(appFeedbackData.total);
        setFeedbackNextCursor(appFeedbackData.next_cursor);
        setFeedbackCursor("");
        setFeedbackCursorStack([]);
        setFeedbackPage(1);
        setFeedbackIndex(indexData);
        setFeedbackEntries(entriesData);
        setFeedbackConfig(configData);
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
    direction: "reset" | "next" | "prev" = "reset"
  ) => {
    setLoading("app-feedback-load");
    setMessage("");
    try {
      const data = await fetchAppFeedback(cursor);
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
      setMessage(t("nl2sql.selectAiFeedbackAdd.requiresContent"));
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
      await refreshAppFeedback();
      if (data.select_ai_feedback) {
        const selectAiMessage = data.select_ai_feedback.warnings.join(" ");
        if (data.select_ai_feedback.executed) {
          toast.success(t("feedbackManagement.appFeedback.adminSavedAndRegistered"));
          await refreshSelectAiFeedback(profileName);
        } else {
          setMessage(selectAiMessage || t("feedbackManagement.appFeedback.selectAiRegistrationFailed"));
        }
      } else {
        toast.success(t("feedbackManagement.appFeedback.adminSaved"));
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
      await refreshAppFeedback();
      setRegisterSelectAiFeedback(false);
      setSelectAiResponse(defaultResponse);
      toast.success(t("feedbackManagement.appFeedback.cleared"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.appFeedback"));
    } finally {
      setLoading("");
    }
  };

  const rebuildFeedbackIndex = async () => {
    const ok = await confirm({
      title: t("feedbackManagement.similarityIndex.rebuildConfirmTitle"),
      description: t("feedbackManagement.similarityIndex.rebuildConfirmDescription"),
      confirmLabel: t("feedbackManagement.similarityIndex.rebuild"),
      tone: "info",
    });
    if (!ok) return;
    setLoading("feedback-index");
    setMessage("");
    try {
      setFeedbackIndex(await apiPost<FeedbackIndexData>("/api/nl2sql/feedback-index/rebuild", {}));
      setFeedbackEntries(await apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"));
      toast.success(t("feedbackManagement.similarityIndex.rebuilt"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.feedbackIndex"));
    } finally {
      setLoading("");
    }
  };

  const clearFeedbackIndex = async () => {
    const ok = await confirm({
      title: t("feedbackManagement.similarityIndex.clearConfirmTitle"),
      description: t("feedbackManagement.similarityIndex.clearConfirmDescription"),
      confirmLabel: t("feedbackManagement.similarityIndex.clear"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setLoading("feedback-index-clear");
    setMessage("");
    try {
      setFeedbackIndex(await apiPost<FeedbackIndexData>("/api/nl2sql/feedback-index/clear", {}));
      setFeedbackEntries(await apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"));
      toast.success(t("feedbackManagement.similarityIndex.cleared"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.feedbackIndex"));
    } finally {
      setLoading("");
    }
  };

  const saveFeedbackConfig = async () => {
    if (!feedbackConfig) return;
    setLoading("feedback-config");
    setMessage("");
    try {
      setFeedbackConfig(await apiPatch<FeedbackSearchConfigData>("/api/nl2sql/feedback-config", feedbackConfig));
      toast.success(t("feedbackManagement.similarityIndex.configSaved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.feedbackConfig"));
    } finally {
      setLoading("");
    }
  };

  const deleteFeedbackEntry = async (historyId: string) => {
    const ok = await confirm({
      title: t("feedbackManagement.similarityIndex.deleteEntryConfirmTitle"),
      description: t("feedbackManagement.similarityIndex.deleteEntryConfirmDescription"),
      confirmLabel: t("feedbackManagement.similarityIndex.deleteEntry"),
      tone: "danger",
    });
    if (!ok) return;

    setLoading(`feedback-entry-${historyId}`);
    setMessage("");
    try {
      setFeedbackEntries(
        await apiPost<FeedbackEntriesData>("/api/nl2sql/feedback-entries/delete", {
          history_ids: [historyId],
        })
      );
      setHistory((current) => current.filter((item) => item.id !== historyId));
      toast.success(t("feedbackManagement.similarityIndex.entryDeleted"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.feedbackEntries"));
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

  return (
    <>
      <PageHeader
        title={t("nav.feedbackManagement")}
        subtitle={t("feedbackManagement.subtitle")}
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
        <PageNotice notice={message ? { tone: "danger", message } : null} />
        {loading === "load" ? (
          <ProcessingIndicator
            active
            label={t("common.processing.refreshing")}
            operationKey="feedback-management-refresh"
            placement="workspace"
            className="rounded-md border border-border bg-card px-3 py-2 shadow-sm"
            testId="feedback-management-workspace-processing"
            activityIcon="none"
          />
        ) : null}

        <DbObjectManagementTabs
          idPrefix="feedback-management"
          tabs={FEEDBACK_MANAGEMENT_TABS}
          activeView={activeView}
          ariaLabel={t("feedbackManagement.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "entries" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-entries"
            labelledBy="feedback-management-tab-entries"
            ariaLabel={t("feedbackManagement.workspace.entries")}
            idPrefix="feedback-management-entries"
            splitId="feedback-management-entries-split"
            preferredWidePane="left"
          >
            <section className="grid min-w-0 gap-4">
              <DbObjectPanelHeader
                title={t("feedbackManagement.entries.title")}
                description={t("feedbackManagement.entries.hint")}
                icon={MessageSquareText}
                action={
                  <>
                    <span data-testid="feedback-management-entry-count">
                      <StatusBadge
                        variant="neutral"
                        label={`${t("feedbackManagement.metric.entries")} ${feedback?.total ?? selectAiFeedbackItems.length}`}
                      />
                    </span>
                    {profileSelect}
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
                  </>
                }
              />
              <FeedbackWarnings warnings={feedback?.warnings ?? dbProfiles?.warnings ?? []} />
              <div
                className={`rounded-md border border-border ${INFORMATION_TABLE_SCROLL_CLASS}`}
                data-testid="feedback-management-entries-scroll-region"
              >
                <table className="min-w-[860px] w-full table-fixed divide-y divide-border text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-background text-xs font-semibold uppercase tracking-normal text-muted">
                    <tr className="h-10">
                      <th scope="col" className="w-[28%] px-3 py-2">
                        {t("feedbackManagement.entries.content")}
                      </th>
                      <th scope="col" className="w-[18%] px-3 py-2">
                        {t("feedbackManagement.entries.sqlId")}
                      </th>
                      <th scope="col" className="w-[34%] px-3 py-2">
                        {t("feedbackManagement.entries.sqlText")}
                      </th>
                      <th scope="col" className="w-[20%] px-3 py-2">
                        {t("feedbackManagement.entries.attributes")}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/70 bg-card">
                    {selectAiFeedbackItems.map((entry, index) => (
                      <FeedbackEntryRow
                        key={`${entry.sql_id}-${index}`}
                        entry={entry}
                        selected={selectedIndex === index}
                        onSelect={() => setSelectedIndex(index)}
                      />
                    ))}
                    {selectAiFeedbackItems.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-3 py-8">
                          <EmptyState
                            title={t("feedbackManagement.entries.emptyTitle")}
                            hint={t("feedbackManagement.entries.emptyHint")}
                          />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="grid min-w-0 gap-4 rounded-md border border-border bg-background p-4">
              <DbObjectPanelHeader
                title={t("feedbackManagement.entries.selectedSql")}
                icon={Code2}
                action={
                  <ObjectActionBar
                    ariaLabel={t("feedbackManagement.entries.selectedSqlActions")}
                    testId="feedback-selected-sql-actions"
                    actions={[
                      {
                        id: "delete",
                        label: t("feedbackManagement.delete"),
                        icon: Trash2,
                        tone: "danger",
                        loading: loading === "delete",
                        disabled: !selectedSelectAiFeedback,
                        onSelect: () => void deleteSelectedFeedback(),
                      },
                    ]}
                  />
                }
              />
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={selectedSelectAiFeedback?.sql_id || "-"} />
                {feedback?.index_name && <StatusBadge variant="info" label={feedback.index_name} />}
                {feedback?.table_name && <StatusBadge variant="neutral" label={feedback.table_name} />}
              </div>
              <textarea
                aria-label={t("feedbackManagement.entries.selectedSql")}
                value={selectedSelectAiFeedback?.sql_text ?? ""}
                readOnly
                rows={16}
                className="min-h-80 rounded-md border border-border bg-code px-3 py-2 font-mono text-sm leading-6 text-code-fg outline-none"
              />
            </section>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "vectorIndex" && (
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

        {activeView === "appFeedback" && (
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
                  void refreshAppFeedback();
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
                    onChange={(event) =>
                      setFeedbackFilter(event.currentTarget.value as "all" | FeedbackRating | "unrated")
                    }
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
                    onChange={(event) => setAppProfileFilter(event.currentTarget.value)}
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
                          label={feedbackLabel(selectedAppFeedback)}
                        />
                      </div>
                    </div>
                    <div className="rounded-md border border-border bg-card p-3">
                      <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.adminRatingStatus")}</p>
                      <div className="mt-2">
                        <StatusBadge
                          variant={selectedAppFeedback.admin_feedback_rating === "good" ? "success" : "neutral"}
                          label={
                            selectedAppFeedback.admin_feedback_rating
                              ? adminFeedbackLabel(selectedAppFeedback)
                              : t("feedbackManagement.appFeedback.unrated")
                          }
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

        {activeView === "similarityIndex" && (
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
            <div className="grid gap-4 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1fr)]">
              <section className="grid content-start gap-3">
                <div className="grid gap-3 sm:grid-cols-3">
                  <CompactFact
                    label={t("feedbackManagement.similarityIndex.indexed")}
                    value={String(feedbackIndex?.indexed_count ?? 0)}
                  />
                  <CompactFact
                    label={t("feedbackManagement.similarityIndex.indexable")}
                    value={String(feedbackIndex?.indexable_count ?? 0)}
                  />
                  <CompactFact
                    label={t("feedbackManagement.similarityIndex.dimension")}
                    value={String(feedbackIndex?.vector_dimension ?? 1536)}
                  />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <CompactFact
                    label={t("feedbackManagement.similarityIndex.embeddingModel")}
                    value={feedbackIndex?.embedding_model || "-"}
                  />
                  <CompactFact
                    label={t("feedbackManagement.similarityIndex.embeddingConfigured")}
                    value={
                      feedbackIndex?.embedding_configured
                        ? t("feedbackManagement.similarityIndex.configured")
                        : t("feedbackManagement.similarityIndex.notConfigured")
                    }
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge variant="neutral" label={feedbackIndex?.status ?? "empty"} />
                  <StatusBadge variant="neutral" label={feedbackIndex?.vector_backend ?? "oracle_26ai"} />
                  <StatusBadge variant="neutral" label={feedbackIndex?.runtime ?? "deterministic"} />
                  <StatusBadge
                    variant="neutral"
                    label={t("feedbackManagement.similarityIndex.entryTotal", { count: feedbackEntries?.total ?? 0 })}
                  />
                </div>
                <section className="grid gap-3 rounded-md border border-border bg-background p-3">
                  <p className="text-sm font-semibold text-foreground">
                    {t("feedbackManagement.similarityIndex.configTitle")}
                  </p>
                  <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem]">
                    <label className="grid gap-1 text-sm font-medium text-foreground">
                      <span>{t("feedbackManagement.similarityIndex.threshold")}</span>
                      <input
                        type="number"
                        min={0}
                        max={1}
                        step={0.05}
                        value={feedbackConfig?.similarity_threshold ?? 0}
                        onChange={(event) => {
                          const nextThreshold = Number(event.currentTarget.value) || 0;
                          setFeedbackConfig((current) => ({
                            similarity_threshold: nextThreshold,
                            match_limit: current?.match_limit ?? 3,
                          }));
                        }}
                        className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                      />
                    </label>
                    <label className="grid gap-1 text-sm font-medium text-foreground">
                      <span>{t("feedbackManagement.similarityIndex.matchLimit")}</span>
                      <input
                        type="number"
                        min={1}
                        max={20}
                        value={feedbackConfig?.match_limit ?? 3}
                        onChange={(event) => {
                          const nextMatchLimit = Number(event.currentTarget.value) || 1;
                          setFeedbackConfig((current) => ({
                            similarity_threshold: current?.similarity_threshold ?? 0,
                            match_limit: nextMatchLimit,
                          }));
                        }}
                        className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                      />
                    </label>
                  </div>
                  <FormActionBar
                    ariaLabel={t("feedbackManagement.similarityIndex.configActions")}
                    primaryActions={[
                      {
                        id: "save-config",
                        label: t("feedbackManagement.similarityIndex.saveConfig"),
                        icon: Save,
                        loading: loading === "feedback-config",
                        disabled: !feedbackConfig,
                        onClick: () => void saveFeedbackConfig(),
                      },
                    ]}
                  />
                </section>
                <FormActionBar
                  ariaLabel={t("feedbackManagement.similarityIndex.actions")}
                  testId="feedback-similarity-index-actions"
                  secondaryActions={[
                    {
                      id: "rebuild",
                      label: t("feedbackManagement.similarityIndex.rebuild"),
                      icon: RefreshCw,
                      loading: loading === "feedback-index",
                      onClick: () => void rebuildFeedbackIndex(),
                    },
                  ]}
                  dangerActions={[
                    {
                      id: "clear",
                      label: t("feedbackManagement.similarityIndex.clear"),
                      icon: Trash2,
                      loading: loading === "feedback-index-clear",
                      onClick: () => void clearFeedbackIndex(),
                    },
                  ]}
                />
              </section>

              <section className="grid content-start gap-3">
                <FeedbackWarnings warnings={feedbackIndex?.warnings ?? []} />
                <section className="grid gap-2 rounded-md border border-border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-foreground">
                      {t("feedbackManagement.similarityIndex.entries")}
                    </p>
                    <StatusBadge
                      variant="neutral"
                      label={t("feedbackManagement.similarityIndex.indexedTotal", {
                        count: feedbackEntries?.indexed_count ?? 0,
                      })}
                    />
                  </div>
                  {(feedbackEntries?.items ?? []).slice(0, 5).map((entry) => (
                    <FeedbackVectorEntryRow
                      key={entry.history_id}
                      entry={entry}
                      deleting={loading === `feedback-entry-${entry.history_id}`}
                      onDelete={() => void deleteFeedbackEntry(entry.history_id)}
                    />
                  ))}
                  {(!feedbackEntries || feedbackEntries.items.length === 0) && (
                    <EmptyState
                      title={t("feedbackManagement.similarityIndex.entriesEmptyTitle")}
                      hint={t("feedbackManagement.similarityIndex.entriesEmptyHint")}
                    />
                  )}
                </section>
              </section>
            </div>
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
}: {
  profiles: SelectAiDbProfile[];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground sm:min-w-72">
      <span>{t("feedbackManagement.profile")}</span>
      <select
        value={value}
        disabled={disabled || profiles.length === 0}
        onChange={(event) => onChange(event.currentTarget.value)}
        className="min-h-[44px] rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
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
        <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
          {warning}
        </p>
      ))}
    </div>
  );
}

function FeedbackEntryRow({
  entry,
  selected,
  onSelect,
}: {
  entry: SelectAiFeedbackEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr className={`${INFORMATION_TABLE_ROW_CLASS} ${selected ? "bg-primary/10" : "hover:bg-background"}`} onClick={onSelect}>
      <td className="px-3 py-2 align-top">
        <p className="line-clamp-3 break-words text-foreground">{entry.content || "-"}</p>
      </td>
      <td className="px-3 py-2 align-top">
        <button
          type="button"
          className="text-left font-mono text-xs font-semibold text-primary underline-offset-2 hover:underline focus:outline-none focus:ring-2 focus:ring-ring/40"
          aria-label={t("feedbackManagement.entries.show", { id: entry.sql_id || "-" })}
          onClick={(event) => {
            event.stopPropagation();
            onSelect();
          }}
        >
          {entry.sql_id || "-"}
        </button>
      </td>
      <td className="px-3 py-2 align-top">
        <p className="line-clamp-3 break-words font-mono text-xs leading-5 text-foreground">{entry.sql_text || "-"}</p>
      </td>
      <td className="px-3 py-2 align-top">
        <p className="line-clamp-3 break-words font-mono text-xs leading-5 text-muted">
          {formatAttributes(entry)}
        </p>
      </td>
    </tr>
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
            label={`${t("feedbackManagement.appFeedback.userRatingShort")}: ${feedbackLabel(item)}`}
          />
          <StatusBadge
            variant={item.admin_feedback_rating === "good" ? "success" : "neutral"}
            label={`${t("feedbackManagement.appFeedback.adminRatingShort")}: ${adminFeedbackLabel(item)}`}
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

function FeedbackVectorEntryRow({
  entry,
  deleting,
  onDelete,
}: {
  entry: FeedbackVectorEntry;
  deleting: boolean;
  onDelete: () => void;
}) {
  const actions: EntityAction[] = [
    {
      id: "delete",
      label: t("feedbackManagement.similarityIndex.deleteEntry"),
      icon: Trash2,
      tone: "danger",
      loading: deleting,
      onSelect: onDelete,
    },
  ];

  return (
    <section className="grid gap-2 rounded-md border border-border bg-card p-3 text-sm" data-testid="feedback-vector-entry">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <QuestionText
            value={entry.question}
            variant="select"
            maxLines={1}
            testId="feedback-vector-question"
          />
          <p className="mt-1 break-all font-mono text-xs text-muted">{entry.generated_sql}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge variant={entry.indexed ? "success" : "neutral"} label={entry.indexed ? "indexed" : "pending"} />
          <StatusBadge
            variant={entry.admin_feedback_rating === "good" ? "info" : "neutral"}
            label={adminFeedbackLabel(entry)}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted">{profileRecordDisplayLabel(entry)}</span>
        <RowActionMenu
          actions={actions}
          ariaLabel={t("feedbackManagement.similarityIndex.entryActions")}
          loading={deleting}
          testId={`feedback-vector-entry-actions-${entry.history_id}`}
        />
      </div>
    </section>
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

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-card p-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-foreground">{value}</p>
    </div>
  );
}
