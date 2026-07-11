import { useEffect, useMemo, useState } from "react";
import {
  DatabaseZap,
  MessageSquareText,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { t } from "@/lib/i18n";
import { engineLabel } from "../labels";
import type {
  FeedbackData,
  FeedbackEntriesData,
  FeedbackIndexData,
  FeedbackRating,
  FeedbackSearchConfigData,
  FeedbackVectorEntry,
  HistoryData,
  HistoryItem,
  SelectAiDbProfilesData,
  SelectAiFeedbackEntriesData,
  SelectAiFeedbackEntry,
  SelectAiFeedbackMutationData,
} from "../types";
import { formatElapsed } from "../useOperationTimer";

function feedbackLabel(item: HistoryItem) {
  if (item.feedback_rating === "good") return t("nl2sql.feedback.good");
  if (item.feedback_rating === "bad") return t("nl2sql.feedback.bad");
  if (item.feedback_rating === "needs_review") return t("nl2sql.feedback.review");
  return t("history.feedback.none");
}

export function LearningPage() {
  const confirm = useConfirm();
  const [selectAiDbProfiles, setSelectAiDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [selectAiProfileName, setSelectAiProfileName] = useState("");
  const [selectAiFeedback, setSelectAiFeedback] = useState<SelectAiFeedbackEntriesData | null>(null);
  const [selectedSelectAiFeedbackIndex, setSelectedSelectAiFeedbackIndex] = useState(0);
  const [selectAiSimilarityThreshold, setSelectAiSimilarityThreshold] = useState(0.9);
  const [selectAiMatchLimit, setSelectAiMatchLimit] = useState(3);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [selectedFeedbackId, setSelectedFeedbackId] = useState("");
  const [feedbackRating, setFeedbackRating] = useState<FeedbackRating>("needs_review");
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackFilter, setFeedbackFilter] = useState<"all" | FeedbackRating | "unrated">("all");
  const [feedbackSearch, setFeedbackSearch] = useState("");
  const [feedbackIndex, setFeedbackIndex] = useState<FeedbackIndexData | null>(null);
  const [feedbackEntries, setFeedbackEntries] = useState<FeedbackEntriesData | null>(null);
  const [feedbackConfig, setFeedbackConfig] = useState<FeedbackSearchConfigData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"error" | "success">("error");

  const feedbackItems = useMemo(() => {
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
  const selectedFeedbackItem = useMemo(
    () => history.find((item) => item.id === selectedFeedbackId) ?? history[0] ?? null,
    [history, selectedFeedbackId]
  );
  const selectedSelectAiFeedback = useMemo(() => {
    const items = selectAiFeedback?.items ?? [];
    return items[selectedSelectAiFeedbackIndex] ?? items[0] ?? null;
  }, [selectAiFeedback, selectedSelectAiFeedbackIndex]);

  const fetchSelectAiFeedback = (profileName: string) =>
    apiGet<SelectAiFeedbackEntriesData>(
      `/api/nl2sql/select-ai/feedback?profile_name=${encodeURIComponent(profileName)}&limit=50`
    );

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [historyData, dbProfileData] = await Promise.all([
        apiGet<HistoryData>("/api/nl2sql/history"),
        apiGet<SelectAiDbProfilesData>("/api/nl2sql/select-ai/db-profiles"),
      ]);
      const [indexData, entriesData, configData] = await Promise.all([
        apiGet<FeedbackIndexData>("/api/nl2sql/feedback-index"),
        apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"),
        apiGet<FeedbackSearchConfigData>("/api/nl2sql/feedback-config"),
      ]);
      const nextSelectAiProfile = selectAiProfileName || dbProfileData.profiles[0]?.name || "";
      const selectAiFeedbackData = nextSelectAiProfile
        ? await fetchSelectAiFeedback(nextSelectAiProfile)
        : null;
      setSelectAiDbProfiles(dbProfileData);
      setSelectAiProfileName(nextSelectAiProfile);
      setSelectAiFeedback(selectAiFeedbackData);
      setSelectedSelectAiFeedbackIndex(0);
      setHistory(historyData.items);
      setFeedbackIndex(indexData);
      setFeedbackEntries(entriesData);
      setFeedbackConfig(configData);
      setSelectedFeedbackId((current) => current || historyData.items[0]?.id || "");
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.load"));
    } finally {
      setLoading("");
    }
  };

  const refreshSelectAiFeedback = async (profileName = selectAiProfileName) => {
    const name = profileName.trim();
    if (!name) return;
    setLoading("select-ai-feedback");
    setMessage("");
    try {
      setSelectAiFeedback(await fetchSelectAiFeedback(name));
      setSelectedSelectAiFeedbackIndex(0);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.selectAiFeedback"));
    } finally {
      setLoading("");
    }
  };

  const deleteSelectAiFeedback = async () => {
    if (!selectedSelectAiFeedback || !selectAiProfileName.trim()) return;
    const ok = await confirm({
      title: t("learning.selectAiFeedback.deleteConfirmTitle"),
      description: t("learning.selectAiFeedback.deleteConfirmDescription"),
      confirmLabel: t("learning.selectAiFeedback.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setLoading("select-ai-feedback-delete");
    setMessage("");
    try {
      const data = await apiPost<SelectAiFeedbackMutationData>("/api/nl2sql/select-ai/feedback/delete", {
        profile_name: selectAiProfileName,
        sql_text: selectedSelectAiFeedback.sql_text,
      });
      setMessageTone(data.executed ? "success" : "error");
      setMessage(data.warnings.join(" ") || t("learning.selectAiFeedback.deleted"));
      setSelectAiFeedback(await fetchSelectAiFeedback(selectAiProfileName));
      setSelectedSelectAiFeedbackIndex(0);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.selectAiFeedbackDelete"));
    } finally {
      setLoading("");
    }
  };

  const updateSelectAiFeedbackVectorIndex = async () => {
    if (!selectAiProfileName.trim()) return;
    setLoading("select-ai-feedback-update");
    setMessage("");
    try {
      const data = await apiPost<SelectAiFeedbackMutationData>(
        "/api/nl2sql/select-ai/feedback/vector-index",
        {
          profile_name: selectAiProfileName,
          similarity_threshold: selectAiSimilarityThreshold,
          match_limit: selectAiMatchLimit,
        }
      );
      setMessageTone(data.executed ? "success" : "error");
      setMessage(data.warnings.join(" ") || t("learning.selectAiFeedback.indexUpdated"));
      setSelectAiFeedback(await fetchSelectAiFeedback(selectAiProfileName));
      setSelectedSelectAiFeedbackIndex(0);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.selectAiFeedbackUpdate"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!selectedFeedbackItem) {
      setFeedbackRating("needs_review");
      setFeedbackComment("");
      return;
    }
    setFeedbackRating(selectedFeedbackItem.feedback_rating ?? "needs_review");
    setFeedbackComment(selectedFeedbackItem.feedback_comment ?? "");
  }, [selectedFeedbackItem]);

  const saveFeedback = async () => {
    if (!selectedFeedbackItem) return;
    setLoading("feedback");
    setMessage("");
    try {
      await apiPost<FeedbackData>("/api/nl2sql/feedback", {
        history_id: selectedFeedbackItem.id,
        rating: feedbackRating,
        comment: feedbackComment.trim(),
      });
      await load();
      setMessageTone("success");
      setMessage(t("learning.feedback.saved"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.feedback"));
    } finally {
      setLoading("");
    }
  };

  const rebuildFeedbackIndex = async () => {
    const ok = await confirm({
      title: t("learning.index.rebuildConfirmTitle"),
      description: t("learning.index.rebuildConfirmDescription"),
      confirmLabel: t("learning.index.rebuild"),
      tone: "info",
    });
    if (!ok) return;
    setLoading("feedback-index");
    setMessage("");
    try {
      setFeedbackIndex(
        await apiPost<FeedbackIndexData>("/api/nl2sql/feedback-index/rebuild", {})
      );
      setFeedbackEntries(await apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.feedbackIndex"));
    } finally {
      setLoading("");
    }
  };

  const clearFeedbackIndex = async () => {
    const ok = await confirm({
      title: t("learning.index.clearConfirmTitle"),
      description: t("learning.index.clearConfirmDescription"),
      confirmLabel: t("learning.index.clear"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    setLoading("feedback-index-clear");
    setMessage("");
    try {
      setFeedbackIndex(
        await apiPost<FeedbackIndexData>("/api/nl2sql/feedback-index/clear", {})
      );
      setFeedbackEntries(await apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.feedbackIndex"));
    } finally {
      setLoading("");
    }
  };

  const saveFeedbackConfig = async () => {
    if (!feedbackConfig) return;
    setLoading("feedback-config");
    setMessage("");
    try {
      setFeedbackConfig(
        await apiPatch<FeedbackSearchConfigData>("/api/nl2sql/feedback-config", feedbackConfig)
      );
      setMessageTone("success");
      setMessage(t("learning.index.configSaved"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.feedbackConfig"));
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
      setMessageTone("success");
      setMessage(t("learning.index.entryDeleted"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.feedbackEntries"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.learning")}
        subtitle={t("learning.subtitle")}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading === "load"}
              onClick={() => void load()}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("learning.action.refresh")}</span>
            </Button>
          </div>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div
            className={
              messageTone === "success"
                ? "rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
                : "rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
            }
            role={messageTone === "success" ? "status" : "alert"}
          >
            {message}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MessageSquareText size={18} aria-hidden="true" />
              {t("learning.selectAiFeedback.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(18rem,0.85fr)]">
            <div className="grid content-start gap-3">
              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("learning.selectAiFeedback.profile")}</span>
                  <select
                    value={selectAiProfileName}
                    onChange={(event) => {
                      const nextProfileName = event.currentTarget.value;
                      setSelectAiProfileName(nextProfileName);
                      void refreshSelectAiFeedback(nextProfileName);
                    }}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  >
                    {(selectAiDbProfiles?.profiles ?? []).map((profile) => (
                      <option key={profile.name} value={profile.name}>
                        {profile.name}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="flex items-end">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "select-ai-feedback"}
                    disabled={!selectAiProfileName.trim()}
                    onClick={() => void refreshSelectAiFeedback()}
                  >
                    <RefreshCw size={15} aria-hidden="true" />
                    <span>{t("learning.selectAiFeedback.refresh")}</span>
                  </Button>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge variant="neutral" label={selectAiFeedback?.runtime ?? "deterministic"} />
                <StatusBadge
                  variant="neutral"
                  label={t("learning.selectAiFeedback.entryTotal", {
                    count: selectAiFeedback?.total ?? 0,
                  })}
                />
                {selectAiFeedback?.index_name && (
                  <code className="break-all rounded-md border border-sky-200 bg-sky-50 px-2 py-1 text-xs font-semibold text-sky-900">
                    {selectAiFeedback.index_name}
                  </code>
                )}
              </div>
              {(selectAiFeedback?.warnings ?? []).map((warning) => (
                <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {warning}
                </p>
              ))}
              <div className="overflow-x-auto rounded-md border border-slate-200">
                <table className="min-w-full divide-y divide-slate-200 text-sm">
                  <thead className="bg-slate-50 text-left text-xs font-semibold text-slate-600">
                    <tr>
                      <th scope="col" className="px-3 py-2">{t("learning.selectAiFeedback.sqlId")}</th>
                      <th scope="col" className="px-3 py-2">{t("learning.selectAiFeedback.content")}</th>
                      <th scope="col" className="px-3 py-2">{t("learning.selectAiFeedback.sqlText")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 bg-white">
                    {(selectAiFeedback?.items ?? []).map((entry, index) => (
                      <SelectAiFeedbackRow
                        key={`${entry.sql_id}-${index}`}
                        entry={entry}
                        selected={selectedSelectAiFeedbackIndex === index}
                        onSelect={() => setSelectedSelectAiFeedbackIndex(index)}
                      />
                    ))}
                    {(!selectAiFeedback || selectAiFeedback.items.length === 0) && (
                      <tr>
                        <td colSpan={3} className="px-3 py-6">
                          <EmptyState
                            title={t("learning.selectAiFeedback.emptyTitle")}
                            hint={t("learning.selectAiFeedback.emptyHint")}
                          />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="grid content-start gap-3">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("learning.selectAiFeedback.selectedSql")}</span>
                <textarea
                  readOnly
                  value={selectedSelectAiFeedback?.sql_text ?? ""}
                  rows={7}
                  className="min-h-44 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
                />
              </label>
              <Button
                type="button"
                variant="danger"
                size="sm"
                loading={loading === "select-ai-feedback-delete"}
                disabled={!selectedSelectAiFeedback}
                onClick={() => void deleteSelectAiFeedback()}
              >
                <Trash2 size={15} aria-hidden="true" />
                <span>{t("learning.selectAiFeedback.delete")}</span>
              </Button>
              <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
                <p className="text-sm font-semibold text-slate-900">{t("learning.selectAiFeedback.indexTitle")}</p>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("learning.index.threshold")}</span>
                    <input
                      type="number"
                      min={0.1}
                      max={0.95}
                      step={0.05}
                      value={selectAiSimilarityThreshold}
                      onChange={(event) => setSelectAiSimilarityThreshold(Number(event.currentTarget.value) || 0.9)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    />
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("learning.index.matchLimit")}</span>
                    <input
                      type="number"
                      min={1}
                      max={5}
                      value={selectAiMatchLimit}
                      onChange={(event) => setSelectAiMatchLimit(Number(event.currentTarget.value) || 3)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    />
                  </label>
                </div>
                <Button
                  type="button"
                  size="sm"
                  loading={loading === "select-ai-feedback-update"}
                  disabled={!selectAiProfileName.trim()}
                  onClick={() => void updateSelectAiFeedbackVectorIndex()}
                >
                  <Save size={15} aria-hidden="true" />
                  <span>{t("learning.selectAiFeedback.updateIndex")}</span>
                </Button>
              </section>
            </div>
          </CardContent>
        </Card>

        <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MessageSquareText size={18} aria-hidden="true" />
                {t("learning.feedback.title")}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              {history.length > 0 && selectedFeedbackItem ? (
                <>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("learning.feedback.history")}</span>
                    <select
                      aria-label={t("learning.feedback.history")}
                      value={selectedFeedbackItem.id}
                      onChange={(event) => setSelectedFeedbackId(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    >
                      {history.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.question}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("learning.feedback.rating")}</span>
                    <select
                      aria-label={t("learning.feedback.rating")}
                      value={feedbackRating}
                      onChange={(event) => setFeedbackRating(event.currentTarget.value as FeedbackRating)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    >
                      <option value="good">{t("nl2sql.feedback.good")}</option>
                      <option value="needs_review">{t("nl2sql.feedback.review")}</option>
                      <option value="bad">{t("nl2sql.feedback.bad")}</option>
                    </select>
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("nl2sql.feedback.comment")}</span>
                    <textarea
                      value={feedbackComment}
                      onChange={(event) => setFeedbackComment(event.currentTarget.value)}
                      rows={4}
                      className="min-h-28 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      placeholder={t("nl2sql.feedback.commentPlaceholder")}
                    />
                  </label>
                  <Button
                    type="button"
                    size="sm"
                    loading={loading === "feedback"}
                    disabled={loading === "feedback"}
                    onClick={() => void saveFeedback()}
                  >
                    <Save size={15} aria-hidden="true" />
                    <span>{t("learning.feedback.save")}</span>
                  </Button>
                  <div className="grid gap-3 border-t border-slate-200 pt-3 md:grid-cols-[1fr_12rem]">
                    <label className="grid gap-1 text-sm font-medium text-slate-800">
                      <span>{t("learning.feedback.search")}</span>
                      <input
                        value={feedbackSearch}
                        onChange={(event) => setFeedbackSearch(event.currentTarget.value)}
                        className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                        placeholder={t("learning.feedback.searchPlaceholder")}
                      />
                    </label>
                    <label className="grid gap-1 text-sm font-medium text-slate-800">
                      <span>{t("learning.feedback.filter")}</span>
                      <select
                        aria-label={t("learning.feedback.filter")}
                        value={feedbackFilter}
                        onChange={(event) =>
                          setFeedbackFilter(event.currentTarget.value as "all" | FeedbackRating | "unrated")
                        }
                        className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                      >
                        <option value="all">{t("learning.feedback.filterAll")}</option>
                        <option value="good">{t("nl2sql.feedback.good")}</option>
                        <option value="needs_review">{t("nl2sql.feedback.review")}</option>
                        <option value="bad">{t("nl2sql.feedback.bad")}</option>
                        <option value="unrated">{t("learning.feedback.unrated")}</option>
                      </select>
                    </label>
                  </div>
                  <div className="grid gap-2 border-t border-slate-200 pt-3">
                    {feedbackItems.length > 0 ? (
                      feedbackItems.map((item) => (
                        <FeedbackHistoryRow key={item.id} item={item} />
                      ))
                    ) : (
                      <EmptyState title={t("learning.feedback.noMatchesTitle")} hint={t("learning.feedback.noMatchesHint")} />
                    )}
                  </div>
                </>
              ) : (
                <EmptyState title={t("learning.feedback.emptyTitle")} hint={t("learning.feedback.emptyHint")} />
              )}
            </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <DatabaseZap size={18} aria-hidden="true" />
              {t("learning.index.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1fr)]">
            <div className="grid content-start gap-3">
              <div className="grid gap-3 sm:grid-cols-3">
                <CompactFact
                  label={t("learning.index.indexed")}
                  value={String(feedbackIndex?.indexed_count ?? 0)}
                />
                <CompactFact
                  label={t("learning.index.indexable")}
                  value={String(feedbackIndex?.indexable_count ?? 0)}
                />
                <CompactFact
                  label={t("learning.index.dimension")}
                  value={String(feedbackIndex?.vector_dimension ?? 1536)}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <CompactFact
                  label={t("learning.index.embeddingModel")}
                  value={feedbackIndex?.embedding_model || "-"}
                />
                <CompactFact
                  label={t("learning.index.embeddingConfigured")}
                  value={
                    feedbackIndex?.embedding_configured
                      ? t("learning.index.configured")
                      : t("learning.index.notConfigured")
                  }
                />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge variant="neutral" label={feedbackIndex?.status ?? "empty"} />
                <StatusBadge variant="neutral" label={feedbackIndex?.vector_backend ?? "oracle_26ai"} />
                <StatusBadge variant="neutral" label={feedbackIndex?.runtime ?? "deterministic"} />
                <StatusBadge
                  variant="neutral"
                  label={t("learning.index.entryTotal", { count: feedbackEntries?.total ?? 0 })}
                />
              </div>
              <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
                <p className="text-sm font-semibold text-slate-900">{t("learning.index.configTitle")}</p>
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem]">
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("learning.index.threshold")}</span>
                    <input
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={feedbackConfig?.similarity_threshold ?? 0}
                      onChange={(event) =>
                        setFeedbackConfig((current) => ({
                          similarity_threshold: Number(event.currentTarget.value) || 0,
                          match_limit: current?.match_limit ?? 3,
                        }))
                      }
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    />
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("learning.index.matchLimit")}</span>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={feedbackConfig?.match_limit ?? 3}
                      onChange={(event) =>
                        setFeedbackConfig((current) => ({
                          similarity_threshold: current?.similarity_threshold ?? 0,
                          match_limit: Number(event.currentTarget.value) || 1,
                        }))
                      }
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
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
                  <span>{t("learning.index.saveConfig")}</span>
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
                  <span>{t("learning.index.rebuild")}</span>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="danger"
                  loading={loading === "feedback-index-clear"}
                  onClick={() => void clearFeedbackIndex()}
                >
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{t("learning.index.clear")}</span>
                </Button>
              </div>
            </div>
            <div className="grid content-start gap-3">
              {feedbackIndex?.warnings.map((warning) => (
                <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {warning}
                </p>
              ))}
              <section className="grid gap-2 rounded-md border border-slate-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{t("learning.index.entries")}</p>
                  <StatusBadge
                    variant="neutral"
                    label={t("learning.index.indexedTotal", {
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
                  <EmptyState title={t("learning.index.entriesEmptyTitle")} hint={t("learning.index.entriesEmptyHint")} />
                )}
              </section>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("learning.index.ddl")}</span>
                <textarea
                  readOnly
                  value={feedbackIndex?.ddl.join("\n") ?? ""}
                  rows={6}
                  className="min-h-40 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-800 outline-none"
                />
              </label>
            </div>
          </CardContent>
        </Card>
      </main>
    </>
  );
}

function FeedbackHistoryRow({ item }: { item: HistoryItem }) {
  return (
    <section data-testid="feedback-history-row" className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="font-semibold text-slate-900">{item.question}</p>
        <div className="flex flex-wrap gap-2">
          <StatusBadge variant="neutral" label={engineLabel(item.engine)} />
          <StatusBadge variant={item.feedback_rating ? "success" : "neutral"} label={feedbackLabel(item)} />
          <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
        </div>
      </div>
      {item.feedback_comment && (
        <p className="rounded-md border border-sky-100 bg-sky-50 px-3 py-2 text-slate-800">
          {item.feedback_comment}
        </p>
      )}
    </section>
  );
}

function SelectAiFeedbackRow({
  entry,
  selected,
  onSelect,
}: {
  entry: SelectAiFeedbackEntry;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr className={selected ? "bg-sky-50" : "hover:bg-slate-50"}>
      <td className="max-w-36 px-3 py-2 align-top">
        <button
          type="button"
          className="min-h-11 w-full rounded-md px-2 py-1 text-left font-mono text-xs font-semibold text-sky-800 outline-none hover:bg-sky-100 focus:ring-2 focus:ring-sky-200"
          aria-pressed={selected}
          onClick={onSelect}
        >
          {entry.sql_id || "-"}
        </button>
      </td>
      <td className="max-w-80 px-3 py-2 align-top text-xs leading-5 text-slate-700">
        <p className="line-clamp-3 break-words">{entry.content || "-"}</p>
      </td>
      <td className="max-w-96 px-3 py-2 align-top">
        <pre className="max-h-20 overflow-hidden whitespace-pre-wrap break-words font-mono text-xs leading-5 text-slate-800">
          {entry.sql_text || "-"}
        </pre>
      </td>
    </tr>
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
    <section className="grid gap-2 rounded-md border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="break-words font-medium text-slate-900">{entry.question}</p>
          <p className="mt-1 break-all font-mono text-xs text-slate-500">{entry.generated_sql}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusBadge variant={entry.indexed ? "success" : "neutral"} label={entry.indexed ? "indexed" : "pending"} />
          <StatusBadge variant={entry.feedback_rating ? "info" : "neutral"} label={entry.feedback_rating ?? "unrated"} />
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-slate-500">{entry.profile_name || entry.profile_id || "-"}</span>
        <Button type="button" variant="danger" size="sm" loading={deleting} onClick={onDelete}>
          <Trash2 size={15} aria-hidden="true" />
          <span>{t("learning.index.deleteEntry")}</span>
        </Button>
      </div>
    </section>
  );
}

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 bg-white p-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-slate-900">{value}</p>
    </div>
  );
}
