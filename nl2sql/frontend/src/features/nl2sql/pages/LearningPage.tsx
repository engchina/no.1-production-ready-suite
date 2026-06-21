import { useEffect, useMemo, useState } from "react";
import { DatabaseZap, MessageSquareText, RefreshCw, RotateCcw, Save, Search, Trash2, Wand2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { engineLabel } from "../labels";
import { historyRerunUrl } from "../queryPrefillState";
import type {
  DemoLearningData,
  FeedbackData,
  FeedbackIndexData,
  FeedbackRating,
  HistoryData,
  HistoryItem,
  Nl2SqlProfile,
  ProfileRecommendationData,
  SimilarHistoryData,
  SimilarHistoryItem,
} from "../types";
import { formatElapsed } from "../useOperationTimer";

function feedbackLabel(item: HistoryItem) {
  if (item.feedback_rating === "good") return t("nl2sql.feedback.good");
  if (item.feedback_rating === "bad") return t("nl2sql.feedback.bad");
  if (item.feedback_rating === "needs_review") return t("nl2sql.feedback.review");
  return t("history.feedback.none");
}

export function LearningPage() {
  const navigate = useNavigate();
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [question, setQuestion] = useState("請求金額が大きい取引先を見たい");
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [similar, setSimilar] = useState<SimilarHistoryData | null>(null);
  const [selectedFeedbackId, setSelectedFeedbackId] = useState("");
  const [feedbackRating, setFeedbackRating] = useState<FeedbackRating>("needs_review");
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackFilter, setFeedbackFilter] = useState<"all" | FeedbackRating | "unrated">("all");
  const [feedbackSearch, setFeedbackSearch] = useState("");
  const [feedbackIndex, setFeedbackIndex] = useState<FeedbackIndexData | null>(null);
  const [feedbackIndexExecute, setFeedbackIndexExecute] = useState(false);
  const [demoSeed, setDemoSeed] = useState<DemoLearningData | null>(null);
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

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [profileData, historyData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<HistoryData>("/api/nl2sql/history"),
      ]);
      const indexData = await apiGet<FeedbackIndexData>("/api/nl2sql/feedback-index");
      setProfiles(profileData);
      setHistory(historyData.items);
      setFeedbackIndex(indexData);
      setProfileId((current) => current || profileData[0]?.id || "");
      setSelectedFeedbackId((current) => current || historyData.items[0]?.id || "");
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.load"));
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

  const recommend = async () => {
    const text = question.trim();
    if (!text) return;
    setLoading("recommend");
    setMessage("");
    try {
      setRecommendation(
        await apiPost<ProfileRecommendationData>("/api/nl2sql/recommend-profile", {
          question: text,
          current_profile_id: profileId || null,
        })
      );
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.recommend"));
    } finally {
      setLoading("");
    }
  };

  const findSimilar = async () => {
    const text = question.trim();
    if (!text) return;
    setLoading("similar");
    setMessage("");
    try {
      setSimilar(
        await apiPost<SimilarHistoryData>("/api/nl2sql/similar-history", {
          question: text,
          profile_id: profileId || null,
          limit: 5,
        })
      );
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.similar"));
    } finally {
      setLoading("");
    }
  };

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
    setLoading("feedback-index");
    setMessage("");
    try {
      setFeedbackIndex(
        await apiPost<FeedbackIndexData>("/api/nl2sql/feedback-index/rebuild", {
          execute: feedbackIndexExecute,
        })
      );
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.feedbackIndex"));
    } finally {
      setLoading("");
    }
  };

  const clearFeedbackIndex = async () => {
    setLoading("feedback-index-clear");
    setMessage("");
    try {
      setFeedbackIndex(
        await apiPost<FeedbackIndexData>("/api/nl2sql/feedback-index/clear", {
          execute: feedbackIndexExecute,
        })
      );
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.feedbackIndex"));
    } finally {
      setLoading("");
    }
  };

  const seedDemoLearning = async () => {
    setLoading("demo");
    setMessage("");
    try {
      const data = await apiPost<DemoLearningData>("/api/nl2sql/demo/learning", {});
      const [historyData, indexData] = await Promise.all([
        apiGet<HistoryData>("/api/nl2sql/history"),
        apiGet<FeedbackIndexData>("/api/nl2sql/feedback-index"),
      ]);
      setHistory(historyData.items);
      setFeedbackIndex(indexData);
      setSelectedFeedbackId((current) => current || historyData.items[0]?.id || "");
      setDemoSeed(data);
      setMessageTone("success");
      setMessage(data.message);
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.demoSeed"));
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
              loading={loading === "demo"}
              disabled={loading === "demo"}
              onClick={() => void seedDemoLearning()}
            >
              <DatabaseZap size={15} aria-hidden="true" />
              <span>{t("learning.action.demoSeed")}</span>
            </Button>
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
        {demoSeed && (
          <section className="grid gap-3 rounded-md border border-sky-200 bg-sky-50 p-4 text-sm text-slate-800 md:grid-cols-[1fr_1fr_1fr]">
            <CompactFact
              label={t("learning.demo.historyCount")}
              value={String(demoSeed.seeded_history_count)}
            />
            <CompactFact
              label={t("learning.demo.feedbackCount")}
              value={String(demoSeed.seeded_feedback_count)}
            />
            <CompactFact
              label={t("learning.demo.profiles")}
              value={demoSeed.profile_ids.join(", ") || "-"}
            />
          </section>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Wand2 size={18} aria-hidden="true" />
              {t("learning.rewrite.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.9fr)]">
            <div className="grid content-start gap-4">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("nl2sql.profile.label")}</span>
                <select
                  value={profileId}
                  onChange={(event) => setProfileId(event.currentTarget.value)}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                >
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("nl2sql.question.label")}</span>
                <textarea
                  value={question}
                  onChange={(event) => setQuestion(event.currentTarget.value)}
                  rows={4}
                  className="min-h-28 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  loading={loading === "recommend"}
                  disabled={!question.trim()}
                  onClick={() => void recommend()}
                >
                  <Wand2 size={16} aria-hidden="true" />
                  <span>{t("learning.action.recommend")}</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  loading={loading === "similar"}
                  disabled={!question.trim()}
                  onClick={() => void findSimilar()}
                >
                  <Search size={16} aria-hidden="true" />
                  <span>{t("learning.action.similar")}</span>
                </Button>
              </div>
            </div>

            {recommendation ? (
              <section className="grid content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    variant="success"
                    label={t("learning.recommend.confidence", {
                      confidence: Math.round(recommendation.confidence * 100),
                    })}
                  />
                  <StatusBadge variant="info" label={recommendation.recommended_profile_name} />
                </div>
                <CompactFact label={t("learning.recommend.reason")} value={recommendation.reason} />
                <CompactFact label={t("learning.recommend.rewrite")} value={recommendation.rewritten_question || "-"} />
                <CompactFact
                  label={t("learning.recommend.allowedTables")}
                  value={recommendation.recommended_allowed_objects.table_names.join(", ") || "-"}
                />
                <div className="grid gap-2">
                  <p className="text-xs font-medium text-slate-500">{t("learning.recommend.candidates")}</p>
                  {recommendation.candidates.map((candidate) => (
                    <div key={candidate.profile_id} className="rounded-md border border-slate-200 bg-white p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-semibold text-slate-900">{candidate.profile_name}</p>
                        <StatusBadge
                          variant="neutral"
                          label={t("learning.recommend.score", { score: Math.round(candidate.score * 100) })}
                        />
                      </div>
                      <p className="mt-2 text-xs text-slate-600">{candidate.matched_terms.join(", ") || "-"}</p>
                    </div>
                  ))}
                </div>
              </section>
            ) : (
              <section className="grid min-h-48 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                {t("learning.recommend.empty")}
              </section>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Search size={18} aria-hidden="true" />
                {t("learning.similar.title")}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              {similar && similar.items.length > 0 ? (
                similar.items.map((entry) => (
                  <SimilarHistoryRow
                    key={entry.item.id}
                    entry={entry}
                    onRerun={() => navigate(historyRerunUrl(entry.item, APP_ROUTES.query))}
                  />
                ))
              ) : (
                <EmptyState title={t("learning.similar.emptyTitle")} hint={t("learning.similar.emptyHint")} />
              )}
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
        </div>

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
              </div>
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={feedbackIndexExecute}
                  onChange={(event) => setFeedbackIndexExecute(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                />
                <span>{t("learning.index.execute")}</span>
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  loading={loading === "feedback-index"}
                  onClick={() => void rebuildFeedbackIndex()}
                >
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>
                    {feedbackIndexExecute ? t("learning.index.rebuild") : t("learning.index.rebuildDryRun")}
                  </span>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={feedbackIndexExecute ? "danger" : "secondary"}
                  loading={loading === "feedback-index-clear"}
                  onClick={() => void clearFeedbackIndex()}
                >
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{feedbackIndexExecute ? t("learning.index.clear") : t("learning.index.clearDryRun")}</span>
                </Button>
              </div>
            </div>
            <div className="grid content-start gap-3">
              {feedbackIndex?.warnings.map((warning) => (
                <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {warning}
                </p>
              ))}
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

function SimilarHistoryRow({ entry, onRerun }: { entry: SimilarHistoryItem; onRerun: () => void }) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900">{entry.item.question}</p>
          <p className="mt-1 text-sm text-slate-600">{entry.reason}</p>
        </div>
        <StatusBadge variant="info" label={t("learning.similar.score", { score: Math.round(entry.score * 100) })} />
      </div>
      <pre className="max-h-32 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
        <code>{entry.item.executable_sql || entry.item.generated_sql}</code>
      </pre>
      <Button type="button" variant="secondary" size="sm" onClick={onRerun}>
        <RotateCcw size={15} aria-hidden="true" />
        <span>{t("history.action.rerun")}</span>
      </Button>
    </section>
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

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 bg-white p-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-slate-900">{value}</p>
    </div>
  );
}
