import { useEffect, useMemo, useState } from "react";
import {
  BrainCircuit,
  Download,
  FileSpreadsheet,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Trash2,
  Wand2,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import {
  Banner,
  Button,
  EmptyState,
  PageHeader,
  Pagination,
  StatusBadge,
  usePagination,
} from "@engchina/production-ready-ui";

import { formatDateTime, formatNumber } from "@/lib/format";
import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { historyRerunUrl } from "../queryPrefillState";
import {
  ExecutionConfirmationField,
  FileInputControl,
} from "../components/DbAdminShared";
import {
  DbManagementSearchField,
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
} from "../components/DbObjectManagementShared";
import type {
  ClassifierImportData,
  ClassifierModelsData,
  ClassifierPredictionData,
  ClassifierStatusData,
  ClassifierTrainingDataData,
  ClassifierTrainingExample,
  ClassifierModelInfo,
  Nl2SqlProfile,
  ProfileRecommendationData,
  SimilarHistoryData,
  SimilarHistoryItem,
} from "../types";

type ActiveView = "models" | "trainingData" | "train" | "test" | "assist";
type MessageTone = "error" | "success";

const fieldClass = "grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground";
const controlClass =
  "min-h-11 w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const linkButtonClass =
  "inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring/40";
const TRAINING_DATA_PAGE_SIZE = 10;

export function QuestionClassifierModelsPage() {
  const navigate = useNavigate();
  const [activeView, setActiveView] = useState<ActiveView>("models");
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [question, setQuestion] = useState("登録済みの表から主要な列を一覧したい");
  const [recommendation, setRecommendation] = useState<ProfileRecommendationData | null>(null);
  const [similar, setSimilar] = useState<SimilarHistoryData | null>(null);
  const [classifierStatus, setClassifierStatus] = useState<ClassifierStatusData | null>(null);
  const [classifierModels, setClassifierModels] = useState<ClassifierModelsData | null>(null);
  const [classifierTrainingData, setClassifierTrainingData] = useState<ClassifierTrainingDataData | null>(null);
  const [classifierImport, setClassifierImport] = useState<ClassifierImportData | null>(null);
  const [classifierPrediction, setClassifierPrediction] = useState<ClassifierPredictionData | null>(null);
  const [classifierReplace, setClassifierReplace] = useState(false);
  const [selectedModelVersion, setSelectedModelVersion] = useState("");
  const [trainingSearch, setTrainingSearch] = useState("");
  const [trainingFilename, setTrainingFilename] = useState("");
  const [deleteTarget, setDeleteTarget] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<MessageTone>("error");

  const models = classifierModels?.models ?? [];
  const selectedModel =
    models.find((model) => model.version === selectedModelVersion) ||
    models.find((model) => model.active) ||
    models[0] ||
    null;

  const filteredExamples = useMemo(() => {
    const query = trainingSearch.trim().toLowerCase();
    const examples = classifierTrainingData?.examples ?? [];
    if (!query) return examples;
    return examples.filter(
      (example) =>
        example.category.toLowerCase().includes(query) ||
        example.text.toLowerCase().includes(query) ||
        example.source.toLowerCase().includes(query)
    );
  }, [classifierTrainingData, trainingSearch]);

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [profileData, classifierData, classifierModelData, trainingData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<ClassifierStatusData>("/api/nl2sql/classifier"),
        apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"),
        apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data"),
      ]);
      setProfiles(profileData);
      setClassifierStatus(classifierData);
      setClassifierModels(classifierModelData);
      setClassifierTrainingData(trainingData);
      setProfileId((current) => current || profileData[0]?.id || "");
      setSelectedModelVersion((current) => {
        if (classifierModelData.models.some((model) => model.version === current)) return current;
        return classifierModelData.active_version || classifierModelData.models[0]?.version || "";
      });
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("qcm.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const refreshTrainingData = async () => {
    setLoading("training-load");
    setMessage("");
    try {
      setClassifierTrainingData(await apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("qcm.error.trainingData"));
    } finally {
      setLoading("");
    }
  };

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

  const importClassifierTraining = async (file: File) => {
    setTrainingFilename(file.name);
    setLoading("classifier-import");
    setMessage("");
    try {
      const data = await uploadClassifierTrainingFile(file, classifierReplace, profileId || null);
      setClassifierImport(data);
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setClassifierModels(await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"));
      setClassifierTrainingData(await apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data"));
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
      const nextModels = await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models");
      setClassifierStatus(data);
      setClassifierModels(nextModels);
      setSelectedModelVersion(nextModels.active_version || nextModels.models[0]?.version || "");
      setMessageTone(data.ready ? "success" : "error");
      setMessage(data.ready ? t("learning.classifier.trained") : data.warnings.join(" "));
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
      const nextModels = await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models");
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setClassifierModels(nextModels);
      setSelectedModelVersion(version);
      setMessageTone("success");
      setMessage(t("learning.classifier.modelActivated"));
    } catch (err) {
      setMessageTone("error");
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
    } finally {
      setLoading("");
    }
  };

  const deleteClassifierModel = async () => {
    if (!deleteTarget || deleteConfirmation.trim() !== deleteTarget) return;
    setLoading(`classifier-model-delete-${deleteTarget}`);
    setMessage("");
    try {
      const response = await fetch(`/api/nl2sql/classifier/models/${encodeURIComponent(deleteTarget)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      const payload = (await response.json()) as { data?: ClassifierModelsData };
      const nextModels = payload.data ?? (await apiGet<ClassifierModelsData>("/api/nl2sql/classifier/models"));
      setClassifierModels(nextModels);
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setSelectedModelVersion(nextModels.active_version || nextModels.models[0]?.version || "");
      setDeleteTarget("");
      setDeleteConfirmation("");
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
      <PageHeader title={t("nav.questionClassifierModels")} subtitle={t("qcm.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        {message && (
          <Banner
            severity={messageTone === "success" ? "success" : "danger"}
            action={
              messageTone === "error" ? (
                <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
                  <RefreshCw size={15} aria-hidden="true" />
                  <span>{t("learning.action.refresh")}</span>
                </Button>
              ) : undefined
            }
          >
            {message}
          </Banner>
        )}

        <DbObjectManagementStatusBar
          ariaLabel={t("qcm.status.aria")}
          metricColumnsClass="sm:grid-cols-2 lg:grid-cols-5"
          metrics={[
            {
              label: t("qcm.metric.models"),
              value: formatNumber(models.length),
              emphasis: true,
              testId: "qcm-model-count",
            },
            { label: t("qcm.metric.activeVersion"), value: classifierModels?.active_version || "-" },
            { label: t("learning.classifier.examples"), value: formatNumber(classifierStatus?.example_count ?? 0) },
            { label: t("learning.classifier.categories"), value: formatNumber(classifierStatus?.category_count ?? 0) },
            { label: t("learning.classifier.dimension"), value: formatNumber(classifierStatus?.vector_dimension ?? 1536) },
          ]}
          actions={
            <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("learning.action.refresh")}</span>
            </Button>
          }
        />

        <DbObjectManagementTabs
          activeView={activeView}
          tabs={[
            { id: "models", label: t("qcm.tabs.models"), icon: BrainCircuit },
            { id: "trainingData", label: t("qcm.tabs.trainingData"), icon: FileSpreadsheet },
            { id: "train", label: t("qcm.tabs.train"), icon: Play },
            { id: "test", label: t("qcm.tabs.test"), icon: Search },
            { id: "assist", label: t("qcm.tabs.assist"), icon: Wand2 },
          ]}
          idPrefix="question-classifier-models"
          ariaLabel={t("qcm.tabs.label")}
          onViewChange={setActiveView}
        />

        {activeView === "models" && (
          <DbObjectManagementPanelShell
            id="question-classifier-models-panel-models"
            labelledBy="question-classifier-models-tab-models"
            idPrefix="question-classifier-models"
            ariaLabel={t("qcm.models.workspace")}
            className="xl:grid-cols-[minmax(24rem,0.9fr)_minmax(0,1.1fr)]"
          >
            <ModelRegistryList
              models={models}
              selectedVersion={selectedModel?.version || ""}
              loading={loading === "load" && !classifierModels}
              onSelect={setSelectedModelVersion}
            />
            <ModelDetailPanel
              model={selectedModel}
              activeVersion={classifierModels?.active_version || ""}
              loading={loading}
              onActivate={(version) => void activateClassifierModel(version)}
              onDelete={(version) => {
                setDeleteTarget(version);
                setDeleteConfirmation("");
              }}
            />
          </DbObjectManagementPanelShell>
        )}

        {activeView === "trainingData" && (
          <DbObjectManagementPanelShell
            id="question-classifier-models-panel-trainingData"
            labelledBy="question-classifier-models-tab-trainingData"
            idPrefix="question-classifier-models"
            ariaLabel={t("qcm.training.workspace")}
          >
            <TrainingDataPanel
              examples={filteredExamples}
              totalExamples={classifierTrainingData?.total_examples ?? 0}
              categories={classifierTrainingData?.categories ?? []}
              warnings={classifierTrainingData?.warnings ?? []}
              search={trainingSearch}
              filename={trainingFilename}
              replace={classifierReplace}
              profiles={profiles}
              profileId={profileId}
              loading={loading}
              importSummary={classifierImport}
              onSearchChange={setTrainingSearch}
              onReplaceChange={setClassifierReplace}
              onProfileChange={setProfileId}
              onRefresh={() => void refreshTrainingData()}
              onImport={(file) => void importClassifierTraining(file)}
              onClearFile={() => setTrainingFilename("")}
            />
          </DbObjectManagementPanelShell>
        )}

        {activeView === "train" && (
          <DbObjectManagementPanelShell
            id="question-classifier-models-panel-train"
            labelledBy="question-classifier-models-tab-train"
            idPrefix="question-classifier-models"
            ariaLabel={t("qcm.train.workspace")}
          >
            <ModelTrainPanel
              status={classifierStatus}
              trainingData={classifierTrainingData}
              loading={loading === "classifier-train"}
              onTrain={() => void trainClassifier()}
            />
          </DbObjectManagementPanelShell>
        )}

        {activeView === "test" && (
          <DbObjectManagementPanelShell
            id="question-classifier-models-panel-test"
            labelledBy="question-classifier-models-tab-test"
            idPrefix="question-classifier-models"
            ariaLabel={t("qcm.test.workspace")}
          >
            <ModelTestPanel
              question={question}
              prediction={classifierPrediction}
              loading={loading === "classifier-predict"}
              ready={Boolean(classifierStatus?.ready)}
              onQuestionChange={setQuestion}
              onPredict={() => void predictClassifier()}
            />
          </DbObjectManagementPanelShell>
        )}

        {activeView === "assist" && (
          <DbObjectManagementPanelShell
            id="question-classifier-models-panel-assist"
            labelledBy="question-classifier-models-tab-assist"
            idPrefix="question-classifier-models"
            ariaLabel={t("qcm.assist.workspace")}
            className="xl:grid-cols-[minmax(0,1fr)_minmax(18rem,0.9fr)]"
          >
            <QuestionAssistPanel
              profiles={profiles}
              profileId={profileId}
              question={question}
              recommendation={recommendation}
              similar={similar}
              loading={loading}
              onProfileChange={setProfileId}
              onQuestionChange={setQuestion}
              onRecommend={() => void recommend()}
              onSimilar={() => void findSimilar()}
              onRerun={(entry) => navigate(historyRerunUrl(entry.item, APP_ROUTES.query))}
            />
          </DbObjectManagementPanelShell>
        )}
      </main>

      {deleteTarget && (
        <DeleteClassifierModelDialog
          version={deleteTarget}
          confirmation={deleteConfirmation}
          loading={loading === `classifier-model-delete-${deleteTarget}`}
          onConfirmationChange={setDeleteConfirmation}
          onClose={() => setDeleteTarget("")}
          onDelete={() => void deleteClassifierModel()}
        />
      )}
    </>
  );
}

function ModelRegistryList({
  models,
  selectedVersion,
  loading,
  onSelect,
}: {
  models: ClassifierModelInfo[];
  selectedVersion: string;
  loading: boolean;
  onSelect: (version: string) => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="qcm-model-list-heading">
      <DbObjectPanelHeader
        headingId="qcm-model-list-heading"
        icon={BrainCircuit}
        title={t("qcm.models.title")}
        description={t("qcm.models.hint")}
        action={<StatusBadge variant="info" label={t("qcm.models.count", { count: models.length })} />}
      />
      {loading ? (
        <div className="grid gap-2">
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="h-14 animate-pulse rounded-md bg-muted/30" aria-hidden="true" />
          ))}
        </div>
      ) : models.length === 0 ? (
        <EmptyState title={t("qcm.models.emptyTitle")} hint={t("qcm.models.emptyHint")} />
      ) : (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className="max-h-[42rem] overflow-auto">
            <table className="w-full min-w-[32rem] table-fixed divide-y divide-border text-left text-sm" data-testid="question-classifier-models-grid">
              <colgroup>
                <col className="w-[13rem]" />
                <col className="w-[6rem]" />
                <col className="w-[6rem]" />
                <col />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
                <tr>
                  <th className="px-3 py-2">{t("qcm.models.version")}</th>
                  <th className="px-3 py-2">{t("qcm.models.state")}</th>
                  <th className="px-3 py-2">{t("learning.classifier.categories")}</th>
                  <th className="px-3 py-2">{t("qcm.models.updatedAt")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/70">
                {models.map((model) => {
                  const selected = model.version === selectedVersion;
                  return (
                    <tr key={model.version} className={selected ? "bg-primary/10" : "hover:bg-background"}>
                      <td className="px-3 py-2 align-top">
                        <button
                          type="button"
                          className="break-all font-mono text-xs font-semibold text-primary hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring/40"
                          aria-current={selected ? "true" : undefined}
                          aria-label={t("qcm.models.showModel", { version: model.version })}
                          onClick={() => onSelect(model.version)}
                        >
                          {model.version}
                        </button>
                      </td>
                      <td className="px-3 py-2 align-top">
                        <StatusBadge variant={model.active ? "success" : "neutral"} label={model.active ? "active" : model.source} />
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-foreground">{formatNumber(model.category_count)}</td>
                      <td className="px-3 py-2 text-xs text-muted">{formatDateTime(model.updated_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function ModelDetailPanel({
  model,
  activeVersion,
  loading,
  onActivate,
  onDelete,
}: {
  model: ClassifierModelInfo | null;
  activeVersion: string;
  loading: string;
  onActivate: (version: string) => void;
  onDelete: (version: string) => void;
}) {
  if (!model) {
    return (
      <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4">
        <EmptyState title={t("qcm.models.emptyTitle")} hint={t("qcm.models.emptyHint")} />
      </section>
    );
  }

  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4" aria-labelledby="qcm-model-detail-heading">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="qcm-model-detail-heading" className="break-all font-mono text-base font-semibold text-foreground">
              {model.version}
            </h2>
            <StatusBadge variant={model.active ? "success" : "neutral"} label={model.active ? "active" : model.source} />
            <StatusBadge variant="info" label={t("qcm.models.categoryCount", { count: model.category_count })} />
          </div>
          <p className="mt-2 text-sm leading-6 text-foreground">
            {t("qcm.models.detailHint", { activeVersion: activeVersion || "-" })}
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap xl:justify-end">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={model.active}
            loading={loading === `classifier-model-activate-${model.version}`}
            onClick={() => onActivate(model.version)}
          >
            <Save size={15} aria-hidden="true" />
            <span>{t("learning.classifier.activateModel")}</span>
          </Button>
          <Button type="button" variant="danger" size="sm" onClick={() => onDelete(model.version)}>
            <Trash2 size={15} aria-hidden="true" />
            <span>{t("learning.classifier.deleteModel")}</span>
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <CompactFact label={t("learning.classifier.model")} value={model.embedding_model || "-"} />
        <CompactFact label={t("learning.classifier.dimension")} value={formatNumber(model.vector_dimension)} />
        <CompactFact label={t("qcm.models.updatedAt")} value={formatDateTime(model.updated_at)} />
        <CompactFact label={t("qcm.models.source")} value={model.source || "-"} />
      </div>

      <section className="grid gap-2 rounded-md border border-border bg-card p-3">
        <h3 className="text-sm font-semibold text-foreground">{t("qcm.models.categories")}</h3>
        <div className="flex flex-wrap gap-2">
          {model.categories.length > 0 ? (
            model.categories.map((category) => <StatusBadge key={category} variant="neutral" label={category} />)
          ) : (
            <span className="text-sm text-muted">-</span>
          )}
        </div>
      </section>

      <section className="grid gap-2 rounded-md border border-border bg-card p-3">
        <h3 className="text-sm font-semibold text-foreground">{t("qcm.models.metrics")}</h3>
        {Object.keys(model.metrics).length > 0 ? (
          <dl className="grid gap-2 sm:grid-cols-3">
            {Object.entries(model.metrics).map(([key, value]) => (
              <CompactFact key={key} label={key} value={String(value)} />
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted">{t("qcm.models.noMetrics")}</p>
        )}
      </section>
    </section>
  );
}

function TrainingDataPanel({
  examples,
  totalExamples,
  categories,
  warnings,
  search,
  filename,
  replace,
  profiles,
  profileId,
  loading,
  importSummary,
  onSearchChange,
  onReplaceChange,
  onProfileChange,
  onRefresh,
  onImport,
  onClearFile,
}: {
  examples: ClassifierTrainingExample[];
  totalExamples: number;
  categories: string[];
  warnings: string[];
  search: string;
  filename: string;
  replace: boolean;
  profiles: Nl2SqlProfile[];
  profileId: string;
  loading: string;
  importSummary: ClassifierImportData | null;
  onSearchChange: (value: string) => void;
  onReplaceChange: (value: boolean) => void;
  onProfileChange: (value: string) => void;
  onRefresh: () => void;
  onImport: (file: File) => void;
  onClearFile: () => void;
}) {
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={FileSpreadsheet}
        title={t("qcm.training.title")}
        description={t("qcm.training.hint")}
        action={
          <Button type="button" variant="secondary" size="sm" loading={loading === "training-load"} onClick={onRefresh}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("qcm.training.refresh")}</span>
          </Button>
        }
      />

      <div className="grid gap-3 md:grid-cols-3">
        <CompactFact label={t("learning.classifier.examples")} value={formatNumber(totalExamples)} />
        <CompactFact label={t("learning.classifier.categories")} value={formatNumber(categories.length)} />
        <CompactFact label={t("qcm.training.filtered")} value={formatNumber(examples.length)} />
      </div>

      <div className="grid gap-3 rounded-md border border-border bg-background p-3 lg:grid-cols-[minmax(0,1fr)_minmax(13rem,0.35fr)]">
        <FileInputControl
          label={t("qcm.training.file")}
          accept=".csv,.txt,.xlsx,.xlsm"
          filename={filename}
          selectedText={filename ? t("qcm.file.selected", { filename }) : ""}
          emptyText={t("qcm.training.noFile")}
          pickText={t("learning.classifier.import")}
          replaceText={t("qcm.file.replace")}
          clearAriaLabel={t("qcm.file.clear")}
          icon="spreadsheet"
          disabled={loading === "classifier-import"}
          dataTestId="qcm-training-file-field"
          onPick={onImport}
          onClear={onClearFile}
        />
        <label className={fieldClass}>
          <span>{t("nl2sql.profile.label")}</span>
          <select value={profileId} onChange={(event) => onProfileChange(event.currentTarget.value)} className={controlClass}>
            <option value="">{t("qcm.training.profileAuto")}</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-h-11 items-start gap-3 rounded-md border border-border bg-card p-3 text-sm text-foreground lg:col-span-2">
          <input
            type="checkbox"
            checked={replace}
            onChange={(event) => onReplaceChange(event.currentTarget.checked)}
            className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
          />
          <span>{t("learning.classifier.replace")}</span>
        </label>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap lg:col-span-2">
          <a className={linkButtonClass} href="/api/nl2sql/classifier/training-data/export.xlsx">
            <Download size={15} aria-hidden="true" />
            <span>{t("learning.classifier.exportXlsx")}</span>
          </a>
          <a className={linkButtonClass} href="/api/nl2sql/classifier/training-data/export.jsonl">
            <Download size={15} aria-hidden="true" />
            <span>{t("learning.classifier.exportJsonl")}</span>
          </a>
        </div>
      </div>

      {importSummary && (
        <div className="rounded-md border border-success/30 bg-success-bg px-3 py-2 text-sm text-success">
          {t("learning.classifier.importSummary", {
            count: importSummary.imported_count,
            total: importSummary.total_examples,
          })}
        </div>
      )}

      {warnings.map((warning) => (
        <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
          {warning}
        </p>
      ))}

      <div className="grid gap-3">
        <DbManagementSearchField
          label={t("dbAdmin.search.label")}
          placeholder={t("qcm.training.searchPlaceholder")}
          value={search}
          onChange={onSearchChange}
        />
        <TrainingDataTable examples={examples} hasFilter={Boolean(search.trim())} />
      </div>
    </div>
  );
}

function TrainingDataTable({ examples, hasFilter }: { examples: ClassifierTrainingExample[]; hasFilter: boolean }) {
  const {
    page: currentPage,
    setPage,
    totalPages,
    pageItems: visibleExamples,
    range,
  } = usePagination(examples, TRAINING_DATA_PAGE_SIZE);

  if (examples.length === 0) {
    return (
      <EmptyState
        title={hasFilter ? t("qcm.training.noResultsTitle") : t("qcm.training.emptyTitle")}
        hint={hasFilter ? t("qcm.training.noResultsHint") : t("qcm.training.emptyHint")}
      />
    );
  }

  return (
    <div className="grid gap-2">
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <div className="max-h-[42rem] overflow-auto">
          <table className="w-full min-w-[44rem] table-fixed divide-y divide-border text-left text-sm" data-testid="qcm-training-data-table">
            <colgroup>
              <col className="w-[12rem]" />
              <col />
              <col className="w-[12rem]" />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
              <tr>
                <th className="px-3 py-2">CATEGORY</th>
                <th className="px-3 py-2">TEXT</th>
                <th className="px-3 py-2">{t("qcm.training.source")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {visibleExamples.map((example) => (
                <tr key={example.id} className="hover:bg-background">
                  <td className="break-words px-3 py-2 text-xs font-semibold text-foreground">{example.category}</td>
                  <td className="break-words px-3 py-2 leading-6 text-foreground">{example.text}</td>
                  <td className="break-words px-3 py-2 font-mono text-xs text-muted">{example.source || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <Pagination
        page={currentPage}
        totalPages={totalPages}
        onPageChange={setPage}
        summary={t("qcm.training.pagination.range", { start: range.start, end: range.end, total: range.total })}
        pageIndicator={t("qcm.training.pagination.page", { page: currentPage, total: totalPages })}
        prevLabel={t("qcm.training.pagination.prev")}
        nextLabel={t("qcm.training.pagination.next")}
        ariaLabel={t("qcm.training.pagination.label")}
        testId="qcm-training-data-pagination"
      />
    </div>
  );
}

function ModelTrainPanel({
  status,
  trainingData,
  loading,
  onTrain,
}: {
  status: ClassifierStatusData | null;
  trainingData: ClassifierTrainingDataData | null;
  loading: boolean;
  onTrain: () => void;
}) {
  const canTrain = (trainingData?.total_examples ?? status?.example_count ?? 0) > 0;
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Play}
        title={t("qcm.train.title")}
        description={t("qcm.train.hint")}
        action={
          <Button type="button" size="sm" loading={loading} disabled={!canTrain} onClick={onTrain}>
            <BrainCircuit size={15} aria-hidden="true" />
            <span>{t("learning.classifier.train")}</span>
          </Button>
        }
      />
      <DbObjectStepIndicator
        steps={[t("qcm.train.stepData"), t("qcm.train.stepEmbedding"), t("qcm.train.stepFit")]}
        activeIndex={status?.ready ? 2 : canTrain ? 1 : 0}
        ariaLabel={t("qcm.train.steps")}
        dataTestId="qcm-train-steps"
      />
      <div className="grid gap-3 lg:grid-cols-3">
        <CompactFact label={t("learning.classifier.examples")} value={formatNumber(trainingData?.total_examples ?? status?.example_count ?? 0)} />
        <CompactFact label={t("learning.classifier.model")} value={status?.embedding_model || "cohere.embed-v4.0"} />
        <CompactFact label={t("learning.classifier.dimension")} value={formatNumber(status?.vector_dimension ?? 1536)} />
      </div>
      <label className={fieldClass}>
        <span>{t("learning.classifier.model")}</span>
        <select value={status?.embedding_model || "cohere.embed-v4.0"} disabled className={`${controlClass} disabled:bg-muted/30 disabled:text-muted`}>
          <option value={status?.embedding_model || "cohere.embed-v4.0"}>{status?.embedding_model || "cohere.embed-v4.0"}</option>
        </select>
      </label>
      <section className="grid gap-3 rounded-md border border-border bg-background p-3">
        <div className="flex flex-wrap gap-2">
          <StatusBadge variant={status?.ready ? "success" : "warning"} label={status?.ready ? t("learning.classifier.ready") : t("learning.classifier.notReady")} />
          <StatusBadge variant="neutral" label={status?.persistence_mode ?? "memory"} />
          <StatusBadge variant="neutral" label={status?.recommendation_source ?? "deterministic"} />
          {status?.classifier_version && <StatusBadge variant="info" label={status.classifier_version} />}
        </div>
        {(status?.warnings ?? []).map((warning) => (
          <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
            {warning}
          </p>
        ))}
        {status?.metrics && Object.keys(status.metrics).length > 0 && (
          <dl className="grid gap-2 md:grid-cols-3">
            {Object.entries(status.metrics).map(([key, value]) => (
              <CompactFact key={key} label={key} value={String(value)} />
            ))}
          </dl>
        )}
      </section>
    </div>
  );
}

function ModelTestPanel({
  question,
  prediction,
  loading,
  ready,
  onQuestionChange,
  onPredict,
}: {
  question: string;
  prediction: ClassifierPredictionData | null;
  loading: boolean;
  ready: boolean;
  onQuestionChange: (value: string) => void;
  onPredict: () => void;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
      <section className="grid content-start gap-4">
        <DbObjectPanelHeader
          icon={Search}
          title={t("qcm.test.title")}
          description={t("qcm.test.hint")}
          action={
            <Button type="button" size="sm" loading={loading} disabled={!question.trim() || !ready} onClick={onPredict}>
              <Search size={15} aria-hidden="true" />
              <span>{t("learning.classifier.predict")}</span>
            </Button>
          }
        />
        <label className={fieldClass}>
          <span>{t("qcm.test.text")}</span>
          <textarea
            value={question}
            onChange={(event) => onQuestionChange(event.currentTarget.value)}
            rows={6}
            className={`${controlClass} min-h-36 leading-6`}
          />
        </label>
      </section>
      <section className="grid content-start gap-3 rounded-md border border-border bg-background p-4">
        <h3 className="text-sm font-semibold text-foreground">{t("qcm.test.result")}</h3>
        {prediction ? (
          <>
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant={prediction.recommendation_source === "classifier" ? "success" : "neutral"} label={prediction.recommendation_source} />
              <StatusBadge variant="info" label={t("learning.classifier.confidence", { confidence: Math.round(prediction.confidence * 100) })} />
            </div>
            <CompactFact label={t("qcm.test.predictedCategory")} value={prediction.predicted_category || "-"} />
            {prediction.candidates.length > 0 && (
              <div className="overflow-hidden rounded-md border border-border bg-card">
                <table className="w-full min-w-[28rem] table-fixed divide-y divide-border text-sm">
                  <colgroup>
                    <col />
                    <col className="w-[7rem]" />
                    <col className="w-[11rem]" />
                  </colgroup>
                  <thead className="bg-background text-xs text-muted">
                    <tr>
                      <th className="px-3 py-2 text-left">CATEGORY</th>
                      <th className="px-3 py-2 text-left">{t("qcm.test.probability")}</th>
                      <th className="px-3 py-2 text-left">{t("nl2sql.profile.label")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/70">
                    {prediction.candidates.map((candidate) => (
                      <tr key={candidate.category}>
                        <td className="break-words px-3 py-2 font-semibold text-foreground">{candidate.category}</td>
                        <td className="px-3 py-2 font-mono text-xs text-foreground">{Math.round(candidate.score * 100)}%</td>
                        <td className="break-words px-3 py-2 text-xs text-muted">{candidate.profile_name || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {prediction.warnings.map((warning) => (
              <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm text-warning">
                {warning}
              </p>
            ))}
          </>
        ) : (
          <EmptyState title={t("qcm.test.emptyTitle")} hint={t("qcm.test.emptyHint")} />
        )}
      </section>
    </div>
  );
}

function QuestionAssistPanel({
  profiles,
  profileId,
  question,
  recommendation,
  similar,
  loading,
  onProfileChange,
  onQuestionChange,
  onRecommend,
  onSimilar,
  onRerun,
}: {
  profiles: Nl2SqlProfile[];
  profileId: string;
  question: string;
  recommendation: ProfileRecommendationData | null;
  similar: SimilarHistoryData | null;
  loading: string;
  onProfileChange: (value: string) => void;
  onQuestionChange: (value: string) => void;
  onRecommend: () => void;
  onSimilar: () => void;
  onRerun: (entry: SimilarHistoryItem) => void;
}) {
  return (
    <>
      <section className="grid content-start gap-4">
        <DbObjectPanelHeader icon={Wand2} title={t("qcm.assist.title")} description={t("qcm.assist.hint")} />
        <label className={fieldClass}>
          <span>{t("nl2sql.profile.label")}</span>
          <select value={profileId} onChange={(event) => onProfileChange(event.currentTarget.value)} className={controlClass}>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
        <label className={fieldClass}>
          <span>{t("nl2sql.question.label")}</span>
          <textarea value={question} onChange={(event) => onQuestionChange(event.currentTarget.value)} rows={4} className={`${controlClass} min-h-28 leading-6`} />
        </label>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <Button type="button" loading={loading === "recommend"} disabled={!question.trim()} onClick={onRecommend}>
            <Wand2 size={16} aria-hidden="true" />
            <span>{t("learning.action.recommend")}</span>
          </Button>
          <Button type="button" variant="secondary" loading={loading === "similar"} disabled={!question.trim()} onClick={onSimilar}>
            <Search size={16} aria-hidden="true" />
            <span>{t("learning.action.similar")}</span>
          </Button>
        </div>
      </section>

      <section className="grid content-start gap-3 rounded-md border border-border bg-background p-4">
        <h3 className="text-sm font-semibold text-foreground">{t("learning.rewrite.title")}</h3>
        {recommendation ? (
          <div className="grid gap-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge variant="success" label={t("learning.recommend.confidence", { confidence: Math.round(recommendation.confidence * 100) })} />
              <StatusBadge variant="info" label={recommendation.recommended_profile_name} />
            </div>
            <CompactFact label={t("learning.recommend.reason")} value={recommendation.reason} />
            <CompactFact label={t("learning.recommend.rewrite")} value={recommendation.rewritten_question || "-"} />
            <CompactFact label={t("learning.recommend.allowedTables")} value={recommendation.recommended_allowed_objects.table_names.join(", ") || "-"} />
            <div className="grid gap-2">
              <p className="text-xs font-medium text-muted">{t("learning.recommend.candidates")}</p>
              {recommendation.candidates.map((candidate) => (
                <div key={candidate.profile_id} className="rounded-md border border-border bg-card p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold text-foreground">{candidate.profile_name}</p>
                    <StatusBadge variant="neutral" label={t("learning.recommend.score", { score: Math.round(candidate.score * 100) })} />
                  </div>
                  <p className="mt-2 text-xs text-muted">{candidate.matched_terms.join(", ") || "-"}</p>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <EmptyState title={t("qcm.assist.recommendEmptyTitle")} hint={t("learning.recommend.empty")} />
        )}
      </section>

      <section className="grid content-start gap-3 rounded-md border border-border bg-card p-4 xl:col-span-2">
        <DbObjectPanelHeader icon={Search} title={t("learning.similar.title")} description={t("qcm.assist.similarHint")} />
        {similar && similar.items.length > 0 ? (
          similar.items.map((entry) => <SimilarHistoryRow key={entry.item.id} entry={entry} onRerun={() => onRerun(entry)} />)
        ) : (
          <EmptyState title={t("learning.similar.emptyTitle")} hint={t("learning.similar.emptyHint")} />
        )}
      </section>
    </>
  );
}

function DeleteClassifierModelDialog({
  version,
  confirmation,
  loading,
  onConfirmationChange,
  onClose,
  onDelete,
}: {
  version: string;
  confirmation: string;
  loading: boolean;
  onConfirmationChange: (value: string) => void;
  onClose: () => void;
  onDelete: () => void;
}) {
  const confirmed = confirmation.trim() === version;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-3 sm:items-center" role="presentation">
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-classifier-model-dialog-title"
        className="max-h-[90dvh] w-full max-w-3xl overflow-auto rounded-md border border-danger/30 bg-card shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-danger/20 bg-danger-bg px-4 py-3">
          <div>
            <h2 id="delete-classifier-model-dialog-title" className="text-base font-semibold text-danger">
              {t("qcm.deleteDialog.title")}
            </h2>
            <p className="mt-1 text-sm text-danger">{t("qcm.deleteDialog.subtitle")}</p>
          </div>
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            <span>{t("qcm.deleteDialog.close")}</span>
          </Button>
        </div>
        <div className="grid gap-4 p-4">
          <div className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2">
            <p className="text-xs font-semibold text-danger">{t("qcm.models.version")}</p>
            <p className="mt-1 break-all font-mono text-sm font-semibold text-danger">{version}</p>
          </div>
          <fieldset className="grid gap-3 rounded-md border border-danger/30 bg-danger-bg/70 p-3">
            <legend className="px-1 text-sm font-semibold text-danger">{t("qcm.deleteDialog.executeTitle")}</legend>
            <ExecutionConfirmationField
              value={confirmation}
              onChange={onConfirmationChange}
              confirmed={confirmed}
              placeholder={version}
              expectedLabel={version}
              helper={t("qcm.deleteDialog.executeHint")}
              tone="danger"
              actions={
                <>
                  <Button type="button" variant="danger" size="sm" loading={loading} disabled={!confirmed} onClick={onDelete}>
                    <Trash2 size={15} aria-hidden="true" />
                    <span>{t("qcm.deleteDialog.run")}</span>
                  </Button>
                  <Button type="button" variant="secondary" size="sm" onClick={onClose}>
                    <span>{t("qcm.deleteDialog.cancel")}</span>
                  </Button>
                </>
              }
            />
          </fieldset>
        </div>
      </section>
    </div>
  );
}

function SimilarHistoryRow({ entry, onRerun }: { entry: SimilarHistoryItem; onRerun: () => void }) {
  return (
    <section className="grid gap-3 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-foreground">{entry.item.question}</p>
          <p className="mt-1 text-sm text-muted">{entry.reason}</p>
        </div>
        <StatusBadge variant="info" label={t("learning.similar.score", { score: Math.round(entry.score * 100) })} />
      </div>
      <pre className="max-h-32 overflow-auto rounded-md border border-border bg-code p-3 text-xs leading-5 text-code-fg">
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
    <div className="min-w-0 rounded-md border border-border bg-card p-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-foreground">{value}</p>
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
  form.append("replace", String(replace));
  if (profileId) form.append("profile_id", profileId);
  const response = await fetch("/api/nl2sql/classifier/training-data/import", {
    method: "POST",
    body: form,
  });
  const payload = (await response.json().catch(() => ({}))) as {
    data?: ClassifierImportData;
    detail?: string;
    error?: string;
  };
  if (!response.ok || !payload.data) {
    throw new Error(payload.error || payload.detail || t("learning.error.classifier"));
  }
  return payload.data;
}
