import { type ChangeEvent, useEffect, useMemo, useState } from "react";
import {
  BrainCircuit,
  DatabaseZap,
  Download,
  MessageSquareText,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Trash2,
  Upload,
  Wand2,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { engineLabel } from "../labels";
import { historyRerunUrl } from "../queryPrefillState";
import type {
  DemoLearningData,
  FeedbackData,
  FeedbackEntriesData,
  FeedbackIndexData,
  FeedbackRating,
  FeedbackSearchConfigData,
  FeedbackVectorEntry,
  HistoryData,
  HistoryItem,
  ClassifierImportData,
  ClassifierModelImportData,
  ClassifierModelsData,
  ClassifierPredictionData,
  ClassifierStatusData,
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
  const [question, setQuestion] = useState("登録済みの表から主要な列を一覧したい");
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [similar, setSimilar] = useState<SimilarHistoryData | null>(null);
  const [selectedFeedbackId, setSelectedFeedbackId] = useState("");
  const [feedbackRating, setFeedbackRating] = useState<FeedbackRating>("needs_review");
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackFilter, setFeedbackFilter] = useState<"all" | FeedbackRating | "unrated">("all");
  const [feedbackSearch, setFeedbackSearch] = useState("");
  const [feedbackIndex, setFeedbackIndex] = useState<FeedbackIndexData | null>(null);
  const [feedbackEntries, setFeedbackEntries] = useState<FeedbackEntriesData | null>(null);
  const [feedbackConfig, setFeedbackConfig] = useState<FeedbackSearchConfigData | null>(null);
  const [feedbackIndexExecute, setFeedbackIndexExecute] = useState(false);
  const [classifierStatus, setClassifierStatus] = useState<ClassifierStatusData | null>(null);
  const [classifierModels, setClassifierModels] = useState<ClassifierModelsData | null>(null);
  const [classifierModelImport, setClassifierModelImport] = useState<ClassifierModelImportData | null>(null);
  const [classifierImport, setClassifierImport] = useState<ClassifierImportData | null>(null);
  const [classifierPrediction, setClassifierPrediction] = useState<ClassifierPredictionData | null>(null);
  const [classifierReplace, setClassifierReplace] = useState(false);
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
      const [indexData, classifierData, classifierModelData, entriesData, configData] = await Promise.all([
        apiGet<FeedbackIndexData>("/api/nl2sql/feedback-index"),
        apiGet<ClassifierStatusData>("/api/nl2sql/classifier"),
        apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"),
        apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"),
        apiGet<FeedbackSearchConfigData>("/api/nl2sql/feedback-config"),
      ]);
      setProfiles(profileData);
      setHistory(historyData.items);
      setFeedbackIndex(indexData);
      setFeedbackEntries(entriesData);
      setFeedbackConfig(configData);
      setClassifierStatus(classifierData);
      setClassifierModels(classifierModelData);
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

  const importClassifierTraining = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setLoading("classifier-import");
    setMessage("");
    try {
      const data = await uploadClassifierTrainingFile(file, classifierReplace, profileId || null);
      setClassifierImport(data);
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setClassifierModels(await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"));
      setMessageTone("success");
      setMessage(t("learning.classifier.imported", { count: data.imported_count }));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
    } finally {
      setLoading("");
    }
  };

  const trainClassifier = async () => {
    setLoading("classifier-train");
    setMessage("");
    try {
      const data = await apiPost<ClassifierStatusData>("/api/nl2sql/classifier/train", {
        min_examples_per_category: 1,
      });
      setClassifierStatus(data);
      setClassifierModels(await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"));
      setMessageTone(data.ready ? "success" : "error");
      setMessage(data.ready ? t("learning.classifier.trained") : data.warnings.join(" "));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
    } finally {
      setLoading("");
    }
  };

  const importClassifierModelArtifact = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setLoading("classifier-model-import");
    setMessage("");
    try {
      const data = await uploadClassifierModelArtifact(file);
      setClassifierModelImport(data);
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setClassifierModels(await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"));
      setMessageTone(data.imported ? "success" : "error");
      setMessage(data.imported ? t("learning.classifier.modelImported") : data.warnings.join(" "));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
    } finally {
      setLoading("");
    }
  };

  const activateClassifierModel = async (version: string) => {
    setLoading(`classifier-model-activate-${version}`);
    setMessage("");
    try {
      await apiPost(`/api/nl2sql/classifier/models/${encodeURIComponent(version)}/activate`, {});
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setClassifierModels(await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"));
      setMessageTone("success");
      setMessage(t("learning.classifier.modelActivated"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
    } finally {
      setLoading("");
    }
  };

  const deleteClassifierModel = async (version: string) => {
    setLoading(`classifier-model-delete-${version}`);
    setMessage("");
    try {
      const response = await fetch(`/api/nl2sql/classifier/models/${encodeURIComponent(version)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = (await response.json()) as { data?: ClassifierModelsData };
      setClassifierModels(payload.data ?? (await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models")));
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setMessageTone("success");
      setMessage(t("learning.classifier.modelDeleted"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
    } finally {
      setLoading("");
    }
  };

  const predictClassifier = async () => {
    const text = question.trim();
    if (!text) return;
    setLoading("classifier-predict");
    setMessage("");
    try {
      setClassifierPrediction(
        await apiPost<ClassifierPredictionData>("/api/nl2sql/classifier/predict", {
          question: text,
          top_k: 3,
        })
      );
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
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
      setFeedbackEntries(await apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"));
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
      setFeedbackEntries(await apiGet<FeedbackEntriesData>("/api/nl2sql/feedback-entries"));
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
              <BrainCircuit size={18} aria-hidden="true" />
              {t("learning.classifier.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="grid content-start gap-3">
              <div className="grid gap-3 sm:grid-cols-3">
                <CompactFact
                  label={t("learning.classifier.examples")}
                  value={String(classifierStatus?.example_count ?? 0)}
                />
                <CompactFact
                  label={t("learning.classifier.categories")}
                  value={String(classifierStatus?.category_count ?? 0)}
                />
                <CompactFact
                  label={t("learning.classifier.dimension")}
                  value={String(classifierStatus?.vector_dimension ?? 1536)}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <StatusBadge
                  variant={classifierStatus?.ready ? "success" : "warning"}
                  label={classifierStatus?.ready ? t("learning.classifier.ready") : t("learning.classifier.notReady")}
                />
                <StatusBadge variant="neutral" label={classifierStatus?.persistence_mode ?? "memory"} />
                <StatusBadge variant="neutral" label={classifierStatus?.recommendation_source ?? "deterministic"} />
              </div>
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={classifierReplace}
                  onChange={(event) => setClassifierReplace(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                />
                <span>{t("learning.classifier.replace")}</span>
              </label>
              <div className="flex flex-wrap gap-2">
                <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 focus-within:ring-2 focus-within:ring-sky-200">
                  <Upload size={15} aria-hidden="true" />
                  <span>{t("learning.classifier.import")}</span>
                  <input
                    className="sr-only"
                    type="file"
                    accept=".csv,.txt,.xlsx,.xlsm"
                    onChange={(event) => void importClassifierTraining(event)}
                  />
                </label>
                <Button
                  type="button"
                  size="sm"
                  loading={loading === "classifier-train"}
                  disabled={(classifierStatus?.example_count ?? 0) === 0}
                  onClick={() => void trainClassifier()}
                >
                  <BrainCircuit size={15} aria-hidden="true" />
                  <span>{t("learning.classifier.train")}</span>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  loading={loading === "classifier-predict"}
                  disabled={!question.trim()}
                  onClick={() => void predictClassifier()}
                >
                  <Search size={15} aria-hidden="true" />
                  <span>{t("learning.classifier.predict")}</span>
                </Button>
              </div>
              <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3">
                <a
                  className="inline-flex min-h-9 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
                  href="/api/nl2sql/classifier/training-data/export.xlsx"
                >
                  <Download size={15} aria-hidden="true" />
                  <span>{t("learning.classifier.exportXlsx")}</span>
                </a>
                <a
                  className="inline-flex min-h-9 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
                  href="/api/nl2sql/classifier/training-data/export.jsonl"
                >
                  <Download size={15} aria-hidden="true" />
                  <span>{t("learning.classifier.exportJsonl")}</span>
                </a>
                <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 focus-within:ring-2 focus-within:ring-sky-200">
                  <Upload size={15} aria-hidden="true" />
                  <span>{t("learning.classifier.importModel")}</span>
                  <input
                    className="sr-only"
                    type="file"
                    accept=".joblib,.json"
                    onChange={(event) => void importClassifierModelArtifact(event)}
                  />
                </label>
              </div>
            </div>
            <div className="grid content-start gap-3">
              <CompactFact
                label={t("learning.classifier.model")}
                value={classifierStatus?.embedding_model || "-"}
              />
              <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-semibold text-slate-900">{t("learning.classifier.modelRegistry")}</p>
                  <StatusBadge variant="neutral" label={classifierModels?.active_version || "-"} />
                </div>
                {(classifierModels?.models ?? []).slice(0, 6).map((model) => (
                  <div key={model.version} className="rounded-md border border-slate-200 bg-white p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="break-all font-mono text-xs font-semibold text-slate-900">{model.version}</p>
                        <p className="mt-1 text-xs text-slate-600">{model.categories.join(", ") || "-"}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <StatusBadge variant={model.active ? "success" : "neutral"} label={model.active ? "active" : model.source} />
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          loading={loading === `classifier-model-activate-${model.version}`}
                          disabled={model.active}
                          onClick={() => void activateClassifierModel(model.version)}
                        >
                          <Save size={15} aria-hidden="true" />
                          <span>{t("learning.classifier.activateModel")}</span>
                        </Button>
                        <Button
                          type="button"
                          variant="danger"
                          size="sm"
                          loading={loading === `classifier-model-delete-${model.version}`}
                          onClick={() => void deleteClassifierModel(model.version)}
                        >
                          <Trash2 size={15} aria-hidden="true" />
                          <span>{t("learning.classifier.deleteModel")}</span>
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
                {(classifierModels?.models.length ?? 0) === 0 && (
                  <p className="text-sm text-slate-500">{t("learning.classifier.noModels")}</p>
                )}
                {classifierModelImport?.warnings.map((warning) => (
                  <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                    {warning}
                  </p>
                ))}
              </div>
              {classifierImport && (
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <p className="font-semibold text-slate-900">
                    {t("learning.classifier.importSummary", {
                      count: classifierImport.imported_count,
                      total: classifierImport.total_examples,
                    })}
                  </p>
                  <p className="mt-1 text-slate-600">{classifierImport.categories.join(", ") || "-"}</p>
                </div>
              )}
              {classifierPrediction && (
                <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge
                      variant={classifierPrediction.recommendation_source === "classifier" ? "success" : "neutral"}
                      label={classifierPrediction.recommendation_source}
                    />
                    <StatusBadge
                      variant="info"
                      label={t("learning.classifier.confidence", {
                        confidence: Math.round(classifierPrediction.confidence * 100),
                      })}
                    />
                  </div>
                  {classifierPrediction.candidates.map((candidate) => (
                    <div key={candidate.category} className="rounded-md bg-white p-2">
                      <p className="font-semibold text-slate-900">{candidate.category}</p>
                      <p className="text-xs text-slate-600">
                        {candidate.profile_name || "-"} / {Math.round(candidate.score * 100)}%
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {(classifierStatus?.warnings ?? []).map((warning) => (
                <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {warning}
                </p>
              ))}
            </div>
          </CardContent>
        </Card>

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

async function uploadClassifierTrainingFile(
  file: File,
  replace: boolean,
  profileId: string | null
): Promise<ClassifierImportData> {
  const form = new FormData();
  form.append("file", file);
  form.append("replace", replace ? "true" : "false");
  if (profileId) form.append("profile_id", profileId);
  const response = await fetch("/api/nl2sql/classifier/training-data/import", {
    method: "POST",
    body: form,
    headers: { Accept: "application/json" },
  });
  const payload = (await response.json()) as {
    data?: ClassifierImportData;
    error?: string;
    detail?: string;
  };
  if (!response.ok || !payload.data) {
    throw new Error(payload.error || payload.detail || t("learning.error.classifier"));
  }
  return payload.data;
}

async function uploadClassifierModelArtifact(file: File): Promise<ClassifierModelImportData> {
  const form = new FormData();
  form.append("file", file);
  form.append("activate", "true");
  const response = await fetch("/api/nl2sql/classifier/models/import", {
    method: "POST",
    body: form,
    headers: { Accept: "application/json" },
  });
  const payload = (await response.json()) as {
    data?: ClassifierModelImportData;
    error?: string;
    detail?: string;
  };
  if (!response.ok || !payload.data) {
    throw new Error(payload.error || payload.detail || t("learning.error.classifier"));
  }
  return payload.data;
}
