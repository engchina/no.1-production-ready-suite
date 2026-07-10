import { type ChangeEvent, useEffect, useState } from "react";
import {
  BrainCircuit,
  Download,
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

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { historyRerunUrl } from "../queryPrefillState";
import type {
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

export function QuestionLearningPage() {
  const navigate = useNavigate();
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [question, setQuestion] = useState("登録済みの表から主要な列を一覧したい");
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [similar, setSimilar] = useState<SimilarHistoryData | null>(null);
  const [classifierStatus, setClassifierStatus] = useState<ClassifierStatusData | null>(null);
  const [classifierModels, setClassifierModels] = useState<ClassifierModelsData | null>(null);
  const [classifierModelImport, setClassifierModelImport] = useState<ClassifierModelImportData | null>(null);
  const [classifierImport, setClassifierImport] = useState<ClassifierImportData | null>(null);
  const [classifierPrediction, setClassifierPrediction] = useState<ClassifierPredictionData | null>(null);
  const [classifierReplace, setClassifierReplace] = useState(false);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"error" | "success">("error");

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [profileData, classifierData, classifierModelData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<ClassifierStatusData>("/api/nl2sql/classifier"),
        apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"),
      ]);
      setProfiles(profileData);
      setClassifierStatus(classifierData);
      setClassifierModels(classifierModelData);
      setProfileId((current) => current || profileData[0]?.id || "");
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

  return (
    <>
      <PageHeader
        title={t("nav.questionLearning")}
        subtitle={t("questionLearning.subtitle")}
        actions={
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
