import { useEffect, useMemo, useRef, useState } from "react";
import { Code2, DatabaseZap, Link2, MessageSquareText, RefreshCw, Save, Trash2 } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { Button, EmptyState, PageHeader, StatusBadge, toast } from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { apiDelete, apiGet, apiPatch, apiPost, isAbortError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { engineLabel } from "../labels";
import { BUSINESS_SELECT_AI_DB_PROFILES_URL } from "../selectAiProfileUrls";
import type {
  FeedbackData,
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
import { formatElapsed } from "../useOperationTimer";

type FeedbackManagementView = "entries" | "vectorIndex" | "appFeedback" | "similarityIndex";

const FEEDBACK_MANAGEMENT_TABS: Array<DbObjectTab<FeedbackManagementView>> = [
  { id: "entries", label: t("feedbackManagement.tabs.entries"), icon: MessageSquareText },
  { id: "vectorIndex", label: t("feedbackManagement.tabs.vectorIndex"), icon: DatabaseZap },
  { id: "appFeedback", label: t("feedbackManagement.tabs.appFeedback"), icon: MessageSquareText },
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
  const [feedbackRating, setFeedbackRating] = useState<FeedbackRating>("good");
  const [feedbackComment, setFeedbackComment] = useState("");
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
          item.feedback_comment.toLowerCase().includes(q)
        );
      })
      .slice(0, 20);
  }, [feedbackFilter, feedbackSearch, history]);
  const selectedAppFeedback = useMemo(
    () => history.find((item) => item.id === selectedFeedbackId) ?? history[0] ?? null,
    [history, selectedFeedbackId]
  );

  const fetchSelectAiFeedback = (name: string, signal?: AbortSignal) =>
    apiGet<SelectAiFeedbackEntriesData>(
      `/api/nl2sql/select-ai/feedback?profile_name=${encodeURIComponent(name)}&limit=50`,
      { signal }
    );

  const fetchAppFeedback = (cursor = "", signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: "20", rating: feedbackFilter });
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
    setLoading("app-feedback");
    setMessage("");
    try {
      await apiPost<FeedbackData>("/api/nl2sql/feedback", {
        history_id: selectedAppFeedback.id,
        rating: feedbackRating,
        comment: feedbackComment.trim(),
      });
      await refreshAppFeedback();
      toast.success(t("feedbackManagement.appFeedback.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("feedbackManagement.error.appFeedback"));
    } finally {
      setLoading("");
    }
  };

  const clearAppFeedback = async () => {
    if (!selectedAppFeedback) return;
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
      setFeedbackRating("good");
      setFeedbackComment("");
      return;
    }
    setFeedbackRating(selectedAppFeedback.feedback_rating ?? "good");
    setFeedbackComment(selectedAppFeedback.feedback_comment ?? "");
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
        actions={
          <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={() => void load(true)}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("feedbackManagement.action.reload")}</span>
          </Button>
        }
      />

      <main className="grid gap-4 p-4 lg:p-8">
        <DbObjectManagementStatusBar
          ariaLabel={t("feedbackManagement.status.aria")}
          metricColumnsClass="sm:grid-cols-4"
          metrics={[
            {
              label: t("feedbackManagement.metric.entries"),
              value: String(feedback?.total ?? selectAiFeedbackItems.length),
              emphasis: true,
              testId: "feedback-management-entry-count",
            },
            { label: t("feedbackManagement.metric.appFeedback"), value: String(feedbackTotal) },
            { label: t("feedbackManagement.metric.indexed"), value: String(feedbackEntries?.indexed_count ?? 0) },
            { label: t("feedbackManagement.metric.profile"), value: profileName || "-" },
          ]}
          actions={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading === "feedback"}
              disabled={!profileName.trim()}
              onClick={() => void refreshSelectAiFeedback(profileName, true)}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("feedbackManagement.action.refresh")}</span>
            </Button>
          }
        />

        <PageNotice notice={message ? { tone: "danger", message } : null} />

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
                    {profileSelect}
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      className="min-h-11"
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
              <div className="overflow-x-auto rounded-md border border-border">
                <table className="min-w-[860px] w-full table-fixed divide-y divide-border text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-background text-xs font-semibold uppercase tracking-normal text-muted">
                    <tr>
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
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    loading={loading === "delete"}
                    disabled={!selectedSelectAiFeedback}
                    onClick={() => void deleteSelectedFeedback()}
                  >
                    <Trash2 size={15} aria-hidden="true" />
                    <span>{t("feedbackManagement.delete")}</span>
                  </Button>
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
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={feedback?.runtime ?? dbProfiles?.runtime ?? "-"} />
                {feedback?.index_name && <StatusBadge variant="info" label={feedback.index_name} />}
                {feedback?.table_name && <StatusBadge variant="neutral" label={feedback.table_name} />}
              </div>
              <Button
                type="button"
                variant="primary"
                size="sm"
                loading={loading === "vector-index"}
                disabled={!profileName.trim()}
                onClick={() => void updateVectorIndex()}
              >
                <Save size={15} aria-hidden="true" />
                <span>{t("feedbackManagement.index.update")}</span>
              </Button>
            </div>
          </DbObjectManagementPanelShell>
        )}

        {activeView === "appFeedback" && (
          <DbObjectManagementPanelShell
            id="feedback-management-panel-appFeedback"
            labelledBy="feedback-management-tab-appFeedback"
            ariaLabel={t("feedbackManagement.workspace.appFeedback")}
            idPrefix="feedback-management-app-feedback"
            splitId="feedback-management-app-feedback-split"
            preferredWidePane="right"
          >
            <section className="grid min-w-0 content-start gap-4">
              <DbObjectPanelHeader
                title={t("feedbackManagement.appFeedback.title")}
                description={t("feedbackManagement.appFeedback.hint")}
                icon={MessageSquareText}
              />
              {history.length > 0 && selectedAppFeedback ? (
                <>
                  <label className="grid gap-1 text-sm font-medium text-foreground">
                    <span>{t("feedbackManagement.appFeedback.history")}</span>
                    <select
                      aria-label={t("feedbackManagement.appFeedback.history")}
                      value={selectedAppFeedback.id}
                      onChange={(event) => setSelectedFeedbackId(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                    >
                      {history.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.question}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-md border border-border bg-card p-3">
                      <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.profile")}</p>
                      <p className="mt-1 break-words text-sm font-semibold text-foreground">{selectedAppFeedback.profile_name || selectedAppFeedback.profile_id || "-"}</p>
                      <p className="mt-1 break-words font-mono text-xs text-muted">{selectedAppFeedback.profile_id || "-"}</p>
                    </div>
                    <div className="rounded-md border border-border bg-card p-3">
                      <p className="text-xs font-medium text-muted">{t("feedbackManagement.appFeedback.createdAt")}</p>
                      <p className="mt-1 text-sm font-semibold text-foreground">{formatDateTime(selectedAppFeedback.feedback_updated_at || selectedAppFeedback.created_at)}</p>
                      {selectedAppFeedback.training_status && (
                        <div className="mt-2"><StatusBadge variant="info" label={t(`qcm.candidates.status.${selectedAppFeedback.training_status}`)} /></div>
                      )}
                    </div>
                  </div>
                  <label className="grid gap-1 text-sm font-medium text-foreground">
                    <span>{t("feedbackManagement.appFeedback.generatedSql")}</span>
                    <textarea
                      value={selectedAppFeedback.executable_sql || selectedAppFeedback.generated_sql}
                      readOnly
                      rows={5}
                      className="min-h-32 rounded-md border border-border bg-code px-3 py-2 font-mono text-sm leading-6 text-code-fg outline-none"
                    />
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-foreground">
                    <span>{t("feedbackManagement.appFeedback.rating")}</span>
                    <select
                      aria-label={t("feedbackManagement.appFeedback.rating")}
                      value={feedbackRating}
                      onChange={(event) => setFeedbackRating(event.currentTarget.value as FeedbackRating)}
                      className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                    >
                      <option value="good">{t("nl2sql.feedback.good")}</option>
                      <option value="bad">{t("nl2sql.feedback.bad")}</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-foreground">
                    <span>{t("nl2sql.feedback.comment")}</span>
                    <textarea
                      value={feedbackComment}
                      onChange={(event) => setFeedbackComment(event.currentTarget.value)}
                      rows={4}
                      className="min-h-28 rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
                      placeholder={t("nl2sql.feedback.commentPlaceholder")}
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      size="sm"
                      loading={loading === "app-feedback"}
                      disabled={loading === "app-feedback"}
                      onClick={() => void saveAppFeedback()}
                    >
                      <Save size={15} aria-hidden="true" />
                      <span>{t("feedbackManagement.appFeedback.save")}</span>
                    </Button>
                    <Button type="button" variant="danger" size="sm" loading={loading === "app-feedback-clear"} onClick={() => void clearAppFeedback()}>
                      <Trash2 size={15} aria-hidden="true" />
                      <span>{t("feedbackManagement.appFeedback.clear")}</span>
                    </Button>
                    {selectedAppFeedback.feedback_rating === "good" && (
                      <a
                        href={`${APP_ROUTES.questionClassifierModels}?tab=candidates&history_id=${encodeURIComponent(selectedAppFeedback.id)}`}
                        className="inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring/40"
                      >
                        <Link2 size={15} aria-hidden="true" />
                        <span>{t("feedbackManagement.appFeedback.openCandidate")}</span>
                      </a>
                    )}
                  </div>
                </>
              ) : (
                <EmptyState
                  title={t("feedbackManagement.appFeedback.emptyTitle")}
                  hint={t("feedbackManagement.appFeedback.emptyHint")}
                />
              )}
            </section>

            <section className="grid min-w-0 content-start gap-4 rounded-md border border-border bg-background p-4">
              <DbObjectPanelHeader title={t("feedbackManagement.appFeedback.historyList")} icon={MessageSquareText} />
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_12rem_16rem_auto] xl:items-end">
                <label className="grid gap-1 text-sm font-medium text-foreground">
                  <span>{t("feedbackManagement.appFeedback.search")}</span>
                  <input
                    value={feedbackSearch}
                    onChange={(event) => setFeedbackSearch(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                    placeholder={t("feedbackManagement.appFeedback.searchPlaceholder")}
                  />
                </label>
                <label className="grid gap-1 text-sm font-medium text-foreground">
                  <span>{t("feedbackManagement.appFeedback.filter")}</span>
                  <select
                    aria-label={t("feedbackManagement.appFeedback.filter")}
                    value={feedbackFilter}
                    onChange={(event) =>
                      setFeedbackFilter(event.currentTarget.value as "all" | FeedbackRating | "unrated")
                    }
                    className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                  >
                    <option value="all">{t("feedbackManagement.appFeedback.filterAll")}</option>
                    <option value="good">{t("nl2sql.feedback.good")}</option>
                    <option value="bad">{t("nl2sql.feedback.bad")}</option>
                    <option value="unrated">{t("feedbackManagement.appFeedback.unrated")}</option>
                  </select>
                </label>
                <label className="grid gap-1 text-sm font-medium text-foreground">
                  <span>{t("feedbackManagement.appFeedback.profileFilter")}</span>
                  <select
                    aria-label={t("feedbackManagement.appFeedback.profileFilter")}
                    value={appProfileFilter}
                    onChange={(event) => setAppProfileFilter(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                  >
                    <option value="">{t("feedbackManagement.appFeedback.profileAll")}</option>
                    {appProfiles.filter((profile) => !profile.archived).map((profile) => (
                      <option key={profile.id} value={profile.id}>{profile.name}</option>
                    ))}
                  </select>
                </label>
                <Button type="button" variant="secondary" size="sm" loading={loading === "app-feedback-load"} onClick={() => void refreshAppFeedback()}>
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>{t("feedbackManagement.appFeedback.applyFilters")}</span>
                </Button>
              </div>
              <div className="grid gap-2">
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
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background p-3">
                <span className="text-sm text-muted">{t("feedbackManagement.appFeedback.page", { page: feedbackPage, total: feedbackTotal })}</span>
                <div className="flex gap-2">
                  <Button type="button" variant="secondary" size="sm" disabled={feedbackCursorStack.length === 0} onClick={previousAppFeedbackPage}>{t("qcm.training.pagination.prev")}</Button>
                  <Button type="button" variant="secondary" size="sm" disabled={!feedbackNextCursor} onClick={nextAppFeedbackPage}>{t("qcm.training.pagination.next")}</Button>
                </div>
              </div>
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
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "feedback-config"}
                    disabled={!feedbackConfig}
                    onClick={() => void saveFeedbackConfig()}
                  >
                    <Save size={15} aria-hidden="true" />
                    <span>{t("feedbackManagement.similarityIndex.saveConfig")}</span>
                  </Button>
                </section>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    loading={loading === "feedback-index"}
                    onClick={() => void rebuildFeedbackIndex()}
                  >
                    <RefreshCw size={15} aria-hidden="true" />
                    <span>{t("feedbackManagement.similarityIndex.rebuild")}</span>
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger"
                    loading={loading === "feedback-index-clear"}
                    onClick={() => void clearFeedbackIndex()}
                  >
                    <Trash2 size={15} aria-hidden="true" />
                    <span>{t("feedbackManagement.similarityIndex.clear")}</span>
                  </Button>
                </div>
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
                <label className="grid gap-1 text-sm font-medium text-foreground">
                  <span>{t("feedbackManagement.similarityIndex.ddl")}</span>
                  <textarea
                    aria-label={t("feedbackManagement.similarityIndex.ddl")}
                    readOnly
                    value={feedbackIndex?.ddl.join("\n") ?? ""}
                    rows={6}
                    className="min-h-40 rounded-md border border-border bg-background px-3 py-2 font-mono text-sm leading-6 text-foreground outline-none"
                  />
                </label>
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
        className="min-h-11 rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
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
    <tr className={selected ? "bg-primary/10" : "hover:bg-background"} onClick={onSelect}>
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
      className={`grid gap-2 rounded-md border p-3 text-left text-sm outline-none focus:ring-2 focus:ring-ring/40 ${
        selected ? "border-primary/40 bg-primary/10" : "border-border bg-card hover:bg-background"
      }`}
      onClick={onSelect}
    >
      <span className="flex flex-wrap items-start justify-between gap-2">
        <span className="font-semibold text-foreground">{item.question}</span>
        <span className="flex flex-wrap gap-2">
          <StatusBadge variant="neutral" label={engineLabel(item.engine)} />
          <StatusBadge variant={item.feedback_rating ? "success" : "neutral"} label={feedbackLabel(item)} />
          {item.profile_name && <StatusBadge variant="info" label={item.profile_name} />}
          {item.training_status && <StatusBadge variant="neutral" label={t(`qcm.candidates.status.${item.training_status}`)} />}
          <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
        </span>
      </span>
      {item.feedback_comment && (
        <span className="rounded-md border border-primary/20 bg-primary/10 px-3 py-2 text-foreground">
          {item.feedback_comment}
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
  return (
    <section className="grid gap-2 rounded-md border border-border bg-card p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="break-words font-medium text-foreground">{entry.question}</p>
          <p className="mt-1 break-all font-mono text-xs text-muted">{entry.generated_sql}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge variant={entry.indexed ? "success" : "neutral"} label={entry.indexed ? "indexed" : "pending"} />
          <StatusBadge
            variant={entry.feedback_rating ? "info" : "neutral"}
            label={entry.feedback_rating ?? t("feedbackManagement.appFeedback.unrated")}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted">{entry.profile_name || entry.profile_id || "-"}</span>
        <Button type="button" variant="danger" size="sm" loading={deleting} onClick={onDelete}>
          <Trash2 size={15} aria-hidden="true" />
          <span>{t("feedbackManagement.similarityIndex.deleteEntry")}</span>
        </Button>
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
