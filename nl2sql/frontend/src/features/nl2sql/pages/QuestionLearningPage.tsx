import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckSquare,
  BrainCircuit,
  Download,
  FileSpreadsheet,
  Link2,
  ListChecks,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  Button,
  EmptyState,
  PageHeader,
  Pagination,
  StatusBadge,
  toast,
  usePagination,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { formatDateTime, formatNumber } from "@/lib/format";
import { apiDelete, apiFetch, apiGet, apiPatch, apiPost, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useRequestScope } from "@/lib/useRequestScope";
import { FileInputControl } from "../components/DbAdminShared";
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
  ClassifierFeedbackImportData,
  ClassifierPredictionData,
  ClassifierStatusData,
  ClassifierTrainingDataData,
  ClassifierTrainingCandidate,
  ClassifierTrainingCandidatesData,
  ClassifierTrainingExample,
  Nl2SqlProfile,
} from "../types";

type ActiveView = "trainingData" | "train" | "test" | "candidates";

const fieldClass = "grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground";
const controlClass =
  "min-h-11 w-full rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const linkButtonClass =
  "inline-flex min-h-9 items-center justify-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring/40";
const TRAINING_DATA_PAGE_SIZE = 10;

export function QuestionClassifierModelsPage() {
  const confirm = useConfirm();
  const [searchParams] = useSearchParams();
  const [activeView, setActiveView] = useState<ActiveView>(
    searchParams.get("tab") === "candidates" ? "candidates" : "trainingData"
  );
  const focusedCandidateHistoryId = searchParams.get("history_id")?.trim() ?? "";
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [question, setQuestion] = useState("登録済みの表から主要な列を一覧したい");
  const [classifierStatus, setClassifierStatus] = useState<ClassifierStatusData | null>(null);
  const [classifierTrainingData, setClassifierTrainingData] = useState<ClassifierTrainingDataData | null>(null);
  const [candidates, setCandidates] = useState<ClassifierTrainingCandidatesData | null>(null);
  const [candidateSearch, setCandidateSearch] = useState("");
  const [candidateStatus, setCandidateStatus] = useState("all");
  const [candidateProfileId, setCandidateProfileId] = useState("");
  const [candidateCursor, setCandidateCursor] = useState("");
  const [candidateCursorStack, setCandidateCursorStack] = useState<string[]>([]);
  const [candidatePage, setCandidatePage] = useState(1);
  const [selectedCandidates, setSelectedCandidates] = useState<Set<string>>(new Set());
  const [candidateProfileOverrides, setCandidateProfileOverrides] = useState<Record<string, string>>({});
  const [classifierImport, setClassifierImport] = useState<ClassifierImportData | null>(null);
  const [classifierPrediction, setClassifierPrediction] = useState<ClassifierPredictionData | null>(null);
  const [classifierReplace, setClassifierReplace] = useState(false);
  const [trainingSearch, setTrainingSearch] = useState("");
  const [trainingFilename, setTrainingFilename] = useState("");
  const [editingExampleId, setEditingExampleId] = useState("");
  const [editingText, setEditingText] = useState("");
  const [editingProfileId, setEditingProfileId] = useState("");
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const filteredExamples = useMemo(() => {
    const query = trainingSearch.trim().toLowerCase();
    const examples = classifierTrainingData?.examples ?? [];
    if (!query) return examples;
    return examples.filter(
      (example) =>
        example.category.toLowerCase().includes(query) ||
        (example.profile_name ?? "").toLowerCase().includes(query) ||
        example.text.toLowerCase().includes(query) ||
        (example.source ?? "").toLowerCase().includes(query)
    );
  }, [classifierTrainingData, trainingSearch]);

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading("load");
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        const [profileData, classifierData, trainingData, candidateData] = await Promise.all([
          apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles", { signal }),
          apiGet<ClassifierStatusData>("/api/nl2sql/classifier", { signal }),
          apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data", {
            signal,
          }),
          apiGet<ClassifierTrainingCandidatesData>(
            `/api/nl2sql/classifier/training-candidates?limit=20${focusedCandidateHistoryId ? `&history_id=${encodeURIComponent(focusedCandidateHistoryId)}` : ""}`,
            { signal }
          ),
        ]);
        if (signal.aborted || sequence !== loadSequence.current) return;
        setProfiles(profileData);
        setClassifierStatus(classifierData);
        setClassifierTrainingData(trainingData);
        setCandidates(candidateData);
        if (announce) toast.success(t("common.action.refreshed"));
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("qcm.error.load"));
    } finally {
      if (sequence === loadSequence.current) setLoading("");
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, []);

  const refreshTrainingData = async (announce = false) => {
    setLoading("training-load");
    setMessage("");
    try {
      setClassifierTrainingData(await apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data"));
      if (announce) toast.success(t("common.action.refreshed"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("qcm.error.trainingData"));
    } finally {
      setLoading("");
    }
  };

  const loadCandidates = async (cursor = "", direction: "reset" | "next" | "prev" = "reset") => {
    setLoading("candidates-load");
    setMessage("");
    try {
      const params = new URLSearchParams({
        limit: "20",
        status: candidateStatus,
      });
      if (cursor) params.set("cursor", cursor);
      if (candidateProfileId) params.set("profile_id", candidateProfileId);
      if (candidateSearch.trim()) params.set("q", candidateSearch.trim());
      if (focusedCandidateHistoryId) params.set("history_id", focusedCandidateHistoryId);
      const data = await apiGet<ClassifierTrainingCandidatesData>(
        `/api/nl2sql/classifier/training-candidates?${params.toString()}`
      );
      setCandidates(data);
      setSelectedCandidates(new Set());
      if (direction === "reset") {
        setCandidateCursor("");
        setCandidateCursorStack([]);
        setCandidatePage(1);
      } else {
        setCandidateCursor(cursor);
        setCandidatePage((current) => Math.max(1, current + (direction === "next" ? 1 : -1)));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("qcm.candidates.error.load"));
    } finally {
      setLoading("");
    }
  };

  const goToNextCandidatePage = () => {
    if (!candidates?.next_cursor) return;
    setCandidateCursorStack((current) => [...current, candidateCursor]);
    void loadCandidates(candidates.next_cursor, "next");
  };

  const goToPreviousCandidatePage = () => {
    const previous = candidateCursorStack.at(-1);
    if (previous === undefined) return;
    setCandidateCursorStack((current) => current.slice(0, -1));
    void loadCandidates(previous, "prev");
  };

  const importSelectedCandidates = async () => {
    const selected = candidates?.items.filter((item) => selectedCandidates.has(item.history_id)) ?? [];
    if (selected.length === 0) return;
    const ok = await confirm({
      title: t("qcm.candidates.confirmTitle"),
      description: t("qcm.candidates.confirmDescription", { count: selected.length }),
      confirmLabel: t("qcm.candidates.addSelected"),
      tone: "info",
    });
    if (!ok) return;
    setLoading("candidates-import");
    setMessage("");
    try {
      const data = await apiPost<ClassifierFeedbackImportData>(
        "/api/nl2sql/classifier/training-data/from-feedback",
        {
          items: selected.map((item) => ({
            history_id: item.history_id,
            profile_id: candidateProfileOverrides[item.history_id] || item.profile_id,
          })),
        }
      );
      const [statusData, trainingData] = await Promise.all([
        apiGet<ClassifierStatusData>("/api/nl2sql/classifier"),
        apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data"),
      ]);
      setClassifierStatus(statusData);
      setClassifierTrainingData(trainingData);
      await loadCandidates();
      toast.success(t("qcm.candidates.added", { count: data.imported_count }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("qcm.candidates.error.add"));
    } finally {
      setLoading("");
    }
  };

  const importClassifierTraining = async (file: File) => {
    setTrainingFilename(file.name);
    setLoading("classifier-import");
    setMessage("");
    try {
      const data = await uploadClassifierTrainingFile(file, classifierReplace);
      setClassifierImport(data);
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      setClassifierTrainingData(await apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data"));
      toast.success(t("learning.classifier.imported", { count: data.imported_count }));
    } catch (err) {
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
      if (data.ready) toast.success(t("learning.classifier.trained"));
      else setMessage(data.warnings.join(" ") || t("learning.error.classifier"));
    } catch (err) {
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
      setMessage(err instanceof Error ? err.message : t("learning.error.classifier"));
    } finally {
      setLoading("");
    }
  };

  const startEditingExample = (example: ClassifierTrainingExample) => {
    setEditingExampleId(example.id);
    setEditingText(example.text);
    setEditingProfileId(example.profile_id);
  };

  const saveTrainingExample = async () => {
    if (!editingExampleId || !editingText.trim() || !editingProfileId) return;
    setLoading(`training-save-${editingExampleId}`);
    setMessage("");
    try {
      await apiPatch(`/api/nl2sql/classifier/training-data/${editingExampleId}`, {
        text: editingText.trim(),
        profile_id: editingProfileId,
      });
      const [statusData, trainingData] = await Promise.all([
        apiGet<ClassifierStatusData>("/api/nl2sql/classifier"),
        apiGet<ClassifierTrainingDataData>("/api/nl2sql/classifier/training-data"),
      ]);
      setClassifierStatus(statusData);
      setClassifierTrainingData(trainingData);
      setEditingExampleId("");
      toast.success(t("qcm.training.updated"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("qcm.training.error.update"));
    } finally {
      setLoading("");
    }
  };

  const deleteTrainingExample = async (example: ClassifierTrainingExample) => {
    const ok = await confirm({
      title: t("qcm.training.deleteTitle"),
      description: t("qcm.training.deleteDescription"),
      confirmLabel: t("qcm.training.delete"),
      tone: "danger",
    });
    if (!ok) return;
    setLoading(`training-delete-${example.id}`);
    setMessage("");
    try {
      const trainingData = await apiDelete<ClassifierTrainingDataData>(
        `/api/nl2sql/classifier/training-data/${example.id}`
      );
      setClassifierTrainingData(trainingData);
      setClassifierStatus(await apiGet<ClassifierStatusData>("/api/nl2sql/classifier"));
      toast.success(t("qcm.training.deleted"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("qcm.training.error.delete"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader title={t("nav.questionClassifierModels")} subtitle={t("qcm.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={message ? { tone: "danger", message } : null}
          action={
            message ? (
              <Button type="button" variant="secondary" size="sm" onClick={() => void load(true)}>
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("learning.action.refresh")}</span>
              </Button>
            ) : undefined
          }
        />

        <DbObjectManagementStatusBar
          ariaLabel={t("qcm.status.aria")}
          metricColumnsClass="sm:grid-cols-2 lg:grid-cols-5"
          metrics={[
            {
              label: t("qcm.metric.modelStatus"),
              value: classifierStatus
                ? classifierStatus.ready
                  ? classifierStatus.stale
                    ? t("learning.classifier.stale")
                    : t("learning.classifier.ready")
                  : t("learning.classifier.notReady")
                : "—",
              emphasis: true,
              testId: "qcm-model-status",
            },
            { label: t("qcm.metric.updatedAt"), value: formatDateTime(classifierStatus?.updated_at) },
            { label: t("learning.classifier.examples"), value: formatNumber(classifierStatus?.example_count ?? 0) },
            { label: t("learning.classifier.categories"), value: formatNumber(classifierStatus?.category_count ?? 0) },
            { label: t("learning.classifier.dimension"), value: formatNumber(classifierStatus?.vector_dimension ?? 1536) },
          ]}
          actions={
            <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={() => void load(true)}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("learning.action.refresh")}</span>
            </Button>
          }
        />

        <DbObjectManagementTabs
          activeView={activeView}
          tabs={[
            { id: "trainingData", label: t("qcm.tabs.trainingData"), icon: FileSpreadsheet },
            { id: "train", label: t("qcm.tabs.train"), icon: Play },
            { id: "test", label: t("qcm.tabs.test"), icon: Search },
            { id: "candidates", label: t("qcm.tabs.candidates"), icon: ListChecks },
          ]}
          idPrefix="question-classifier-models"
          ariaLabel={t("qcm.tabs.label")}
          onViewChange={setActiveView}
        />

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
              loading={loading}
              importSummary={classifierImport}
              onSearchChange={setTrainingSearch}
              onReplaceChange={setClassifierReplace}
              onRefresh={() => void refreshTrainingData(true)}
              onImport={(file) => void importClassifierTraining(file)}
              onClearFile={() => setTrainingFilename("")}
              profiles={profiles}
              editingExampleId={editingExampleId}
              editingText={editingText}
              editingProfileId={editingProfileId}
              onStartEdit={startEditingExample}
              onEditTextChange={setEditingText}
              onEditProfileChange={setEditingProfileId}
              onCancelEdit={() => setEditingExampleId("")}
              onSaveEdit={() => void saveTrainingExample()}
              onDelete={(example) => void deleteTrainingExample(example)}
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

        {activeView === "candidates" && (
          <DbObjectManagementPanelShell
            id="question-classifier-models-panel-candidates"
            labelledBy="question-classifier-models-tab-candidates"
            idPrefix="question-classifier-models"
            ariaLabel={t("qcm.candidates.workspace")}
          >
            <TrainingCandidatesPanel
              profiles={profiles}
              data={candidates}
              search={candidateSearch}
              status={candidateStatus}
              profileId={candidateProfileId}
              selected={selectedCandidates}
              profileOverrides={candidateProfileOverrides}
              page={candidatePage}
              canGoPrevious={candidateCursorStack.length > 0}
              loading={loading}
              onSearchChange={setCandidateSearch}
              onStatusChange={setCandidateStatus}
              onProfileFilterChange={setCandidateProfileId}
              onApplyFilters={() => void loadCandidates()}
              onSelectionChange={setSelectedCandidates}
              onProfileOverrideChange={(historyId, value) =>
                setCandidateProfileOverrides((current) => ({ ...current, [historyId]: value }))
              }
              onAddSelected={() => void importSelectedCandidates()}
              onPrevious={goToPreviousCandidatePage}
              onNext={goToNextCandidatePage}
            />
          </DbObjectManagementPanelShell>
        )}
      </main>

    </>
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
  loading,
  importSummary,
  onSearchChange,
  onReplaceChange,
  onRefresh,
  onImport,
  onClearFile,
  profiles,
  editingExampleId,
  editingText,
  editingProfileId,
  onStartEdit,
  onEditTextChange,
  onEditProfileChange,
  onCancelEdit,
  onSaveEdit,
  onDelete,
}: {
  examples: ClassifierTrainingExample[];
  totalExamples: number;
  categories: string[];
  warnings: string[];
  search: string;
  filename: string;
  replace: boolean;
  loading: string;
  importSummary: ClassifierImportData | null;
  onSearchChange: (value: string) => void;
  onReplaceChange: (value: boolean) => void;
  onRefresh: () => void;
  onImport: (file: File) => void;
  onClearFile: () => void;
  profiles: Nl2SqlProfile[];
  editingExampleId: string;
  editingText: string;
  editingProfileId: string;
  onStartEdit: (example: ClassifierTrainingExample) => void;
  onEditTextChange: (value: string) => void;
  onEditProfileChange: (value: string) => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onDelete: (example: ClassifierTrainingExample) => void;
}) {
  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={FileSpreadsheet}
        title={t("qcm.training.title")}
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

      <div className="grid gap-3 rounded-md border border-border bg-background p-3">
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
        <label className="flex min-h-11 items-start gap-3 rounded-md border border-border bg-card p-3 text-sm text-foreground">
          <input
            type="checkbox"
            checked={replace}
            onChange={(event) => onReplaceChange(event.currentTarget.checked)}
            className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
          />
          <span>{t("learning.classifier.replace")}</span>
        </label>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <a className={linkButtonClass} href="/api/nl2sql/classifier/training-data/export.xlsx">
            <Download size={15} aria-hidden="true" />
            <span>{t("learning.classifier.exportXlsx")}</span>
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
        <TrainingDataTable
          examples={examples}
          hasFilter={Boolean(search.trim())}
          profiles={profiles}
          loading={loading}
          editingExampleId={editingExampleId}
          editingText={editingText}
          editingProfileId={editingProfileId}
          onStartEdit={onStartEdit}
          onEditTextChange={onEditTextChange}
          onEditProfileChange={onEditProfileChange}
          onCancelEdit={onCancelEdit}
          onSaveEdit={onSaveEdit}
          onDelete={onDelete}
        />
      </div>
    </div>
  );
}

function TrainingDataTable({
  examples,
  hasFilter,
  profiles,
  loading,
  editingExampleId,
  editingText,
  editingProfileId,
  onStartEdit,
  onEditTextChange,
  onEditProfileChange,
  onCancelEdit,
  onSaveEdit,
  onDelete,
}: {
  examples: ClassifierTrainingExample[];
  hasFilter: boolean;
  profiles: Nl2SqlProfile[];
  loading: string;
  editingExampleId: string;
  editingText: string;
  editingProfileId: string;
  onStartEdit: (example: ClassifierTrainingExample) => void;
  onEditTextChange: (value: string) => void;
  onEditProfileChange: (value: string) => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onDelete: (example: ClassifierTrainingExample) => void;
}) {
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
          <table className="w-full min-w-[62rem] table-fixed divide-y divide-border text-left text-sm" data-testid="qcm-training-data-table">
            <colgroup>
              <col className="w-[14rem]" />
              <col />
              <col className="w-[10rem]" />
              <col className="w-[11rem]" />
              <col className="w-[12rem]" />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-background text-xs text-muted">
              <tr>
                <th className="px-3 py-2">{t("qcm.training.profile")}</th>
                <th className="px-3 py-2">{t("qcm.training.question")}</th>
                <th className="px-3 py-2">{t("qcm.training.category")}</th>
                <th className="px-3 py-2">{t("qcm.training.source")}</th>
                <th className="px-3 py-2">{t("qcm.training.actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {visibleExamples.map((example) => {
                const editing = editingExampleId === example.id;
                return (
                  <tr key={example.id} className="hover:bg-background">
                    <td className="break-words px-3 py-2 align-top text-xs font-semibold text-foreground">
                      {editing ? (
                        <select
                          aria-label={t("qcm.training.editProfile")}
                          value={editingProfileId}
                          onChange={(event) => onEditProfileChange(event.currentTarget.value)}
                          className={controlClass}
                        >
                          {profiles.filter((profile) => !profile.archived).map((profile) => (
                            <option key={profile.id} value={profile.id}>{profile.name}</option>
                          ))}
                        </select>
                      ) : (
                        <>
                          <span className="block">{example.profile_name || example.profile_id || "-"}</span>
                          <span className="mt-1 block font-mono font-normal text-muted">{example.profile_id || "-"}</span>
                        </>
                      )}
                    </td>
                    <td className="break-words px-3 py-2 align-top leading-6 text-foreground">
                      {editing ? (
                        <textarea
                          aria-label={t("qcm.training.editQuestion")}
                          value={editingText}
                          onChange={(event) => onEditTextChange(event.currentTarget.value)}
                          rows={3}
                          className={`${controlClass} min-h-24`}
                        />
                      ) : example.text}
                    </td>
                    <td className="break-words px-3 py-2 align-top text-xs text-foreground">{example.category}</td>
                    <td className="break-words px-3 py-2 align-top text-xs text-muted">
                      <StatusBadge
                        variant={example.source_type === "feedback" ? "info" : "neutral"}
                        label={example.source_type === "feedback" ? t("qcm.training.sourceFeedback") : t("qcm.training.sourceFile")}
                      />
                      <span className="mt-1 block font-mono">{example.source || "-"}</span>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <div className="flex flex-wrap gap-2">
                        {editing ? (
                          <>
                            <Button
                              type="button"
                              size="sm"
                              loading={loading === `training-save-${example.id}`}
                              disabled={!editingText.trim() || !editingProfileId}
                              onClick={onSaveEdit}
                            >
                              {t("qcm.training.save")}
                            </Button>
                            <Button type="button" variant="secondary" size="sm" onClick={onCancelEdit}>
                              {t("qcm.training.cancel")}
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button type="button" variant="secondary" size="sm" onClick={() => onStartEdit(example)}>
                              <Pencil size={14} aria-hidden="true" />
                              <span>{t("qcm.training.edit")}</span>
                            </Button>
                            <Button
                              type="button"
                              variant="danger"
                              size="sm"
                              loading={loading === `training-delete-${example.id}`}
                              onClick={() => onDelete(example)}
                            >
                              <Trash2 size={14} aria-hidden="true" />
                              <span>{t("qcm.training.delete")}</span>
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
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
        activeIndex={status?.ready ? 3 : canTrain ? 1 : 0}
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
          {status?.stale && <StatusBadge variant="warning" label={t("learning.classifier.stale")} />}
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
                      <th className="px-3 py-2 text-left">{t("qcm.test.category")}</th>
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

function candidateStatusLabel(status: ClassifierTrainingCandidate["status"]) {
  return t(`qcm.candidates.status.${status}`);
}

function TrainingCandidatesPanel({
  profiles,
  data,
  search,
  status,
  profileId,
  selected,
  profileOverrides,
  page,
  canGoPrevious,
  loading,
  onSearchChange,
  onStatusChange,
  onProfileFilterChange,
  onApplyFilters,
  onSelectionChange,
  onProfileOverrideChange,
  onAddSelected,
  onPrevious,
  onNext,
}: {
  profiles: Nl2SqlProfile[];
  data: ClassifierTrainingCandidatesData | null;
  search: string;
  status: string;
  profileId: string;
  selected: Set<string>;
  profileOverrides: Record<string, string>;
  page: number;
  canGoPrevious: boolean;
  loading: string;
  onSearchChange: (value: string) => void;
  onStatusChange: (value: string) => void;
  onProfileFilterChange: (value: string) => void;
  onApplyFilters: () => void;
  onSelectionChange: (value: Set<string>) => void;
  onProfileOverrideChange: (historyId: string, value: string) => void;
  onAddSelected: () => void;
  onPrevious: () => void;
  onNext: () => void;
}) {
  const activeProfiles = profiles.filter((profile) => !profile.archived);
  const selectable = (data?.items ?? []).filter(
    (item) => item.status === "pending" || (item.status === "profile_missing" && profileOverrides[item.history_id])
  );
  const allSelected = selectable.length > 0 && selectable.every((item) => selected.has(item.history_id));
  const toggleAll = () => {
    const next = new Set(selected);
    if (allSelected) selectable.forEach((item) => next.delete(item.history_id));
    else selectable.forEach((item) => next.add(item.history_id));
    onSelectionChange(next);
  };

  return (
    <div className="grid min-w-0 gap-4">
      <DbObjectPanelHeader
        icon={ListChecks}
        title={t("qcm.candidates.title")}
        description={t("qcm.candidates.hint")}
        action={
          <Button
            type="button"
            size="sm"
            loading={loading === "candidates-import"}
            disabled={selected.size === 0}
            onClick={onAddSelected}
          >
            <CheckSquare size={15} aria-hidden="true" />
            <span>{t("qcm.candidates.addSelectedWithCount", { count: selected.size })}</span>
          </Button>
        }
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <CompactFact label={t("qcm.candidates.pending")} value={formatNumber(data?.pending_count ?? 0)} />
        <CompactFact label={t("qcm.candidates.addedMetric")} value={formatNumber(data?.added_count ?? 0)} />
        <CompactFact label={t("qcm.candidates.attention")} value={formatNumber(data?.attention_count ?? 0)} />
      </div>

      <section className="grid gap-3 rounded-md border border-border bg-background p-3 lg:grid-cols-[minmax(0,1fr)_13rem_16rem_auto] lg:items-end">
        <label className={fieldClass}>
          <span>{t("qcm.candidates.search")}</span>
          <input value={search} onChange={(event) => onSearchChange(event.currentTarget.value)} className={controlClass} />
        </label>
        <label className={fieldClass}>
          <span>{t("qcm.candidates.statusFilter")}</span>
          <select value={status} onChange={(event) => onStatusChange(event.currentTarget.value)} className={controlClass}>
            {[
              "all",
              "pending",
              "added",
              "already_covered",
              "conflict",
              "profile_missing",
              "source_changed",
            ].map((value) => <option key={value} value={value}>{t(`qcm.candidates.status.${value}`)}</option>)}
          </select>
        </label>
        <label className={fieldClass}>
          <span>{t("nl2sql.profile.label")}</span>
          <select value={profileId} onChange={(event) => onProfileFilterChange(event.currentTarget.value)} className={controlClass}>
            <option value="">{t("qcm.candidates.allProfiles")}</option>
            {activeProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
          </select>
        </label>
        <Button type="button" variant="secondary" size="sm" loading={loading === "candidates-load"} onClick={onApplyFilters}>
          <RefreshCw size={15} aria-hidden="true" />
          <span>{t("qcm.candidates.applyFilters")}</span>
        </Button>
      </section>

      {(data?.items.length ?? 0) > 0 ? (
        <div className="grid gap-3">
          <label className="flex min-h-11 items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground">
            <input type="checkbox" checked={allSelected} onChange={toggleAll} className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40" />
            <span>{t("qcm.candidates.selectPage", { count: selectable.length })}</span>
          </label>
          {data?.items.map((item) => {
            const override = profileOverrides[item.history_id] || item.profile_id;
            const canSelect = item.status === "pending" || (item.status === "profile_missing" && Boolean(profileOverrides[item.history_id]));
            return (
              <article key={item.history_id} data-testid="qcm-training-candidate" className="grid gap-3 rounded-md border border-border bg-card p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <label className="flex min-w-0 flex-1 items-start gap-3">
                    <input
                      type="checkbox"
                      aria-label={t("qcm.candidates.select", { question: item.question })}
                      checked={selected.has(item.history_id)}
                      disabled={!canSelect}
                      onChange={(event) => {
                        const next = new Set(selected);
                        if (event.currentTarget.checked) next.add(item.history_id);
                        else next.delete(item.history_id);
                        onSelectionChange(next);
                      }}
                      className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
                    />
                    <span className="min-w-0 break-words font-semibold leading-6 text-foreground">{item.question}</span>
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge variant={item.status === "pending" ? "success" : item.status === "conflict" || item.status === "profile_missing" || item.status === "source_changed" ? "warning" : "neutral"} label={candidateStatusLabel(item.status)} />
                    <StatusBadge variant="info" label={item.profile_name || item.profile_id || "-"} />
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_18rem]">
                  <div className="grid gap-2 text-sm">
                    {item.feedback_comment && <p className="rounded-md border border-primary/20 bg-primary/10 px-3 py-2 text-foreground">{item.feedback_comment}</p>}
                    <p className="text-xs text-muted">{formatDateTime(item.created_at)}</p>
                    {item.conflict_profile_ids.length > 0 && <p className="text-sm text-warning">{t("qcm.candidates.conflicts", { profiles: item.conflict_profile_ids.join(", ") })}</p>}
                  </div>
                  {(item.status === "pending" || item.status === "profile_missing") && (
                    <label className={fieldClass}>
                      <span>{t("qcm.candidates.confirmProfile")}</span>
                      <select value={override} onChange={(event) => onProfileOverrideChange(item.history_id, event.currentTarget.value)} className={controlClass}>
                        {!override && <option value="">{t("qcm.candidates.selectProfile")}</option>}
                        {activeProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} ({profile.id})</option>)}
                      </select>
                    </label>
                  )}
                </div>
                <div>
                  <a className={linkButtonClass} href={`${APP_ROUTES.feedbackManagement}?tab=appFeedback&history_id=${encodeURIComponent(item.history_id)}`}>
                    <Link2 size={15} aria-hidden="true" />
                    <span>{t("qcm.candidates.openFeedback")}</span>
                  </a>
                </div>
              </article>
            );
          })}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background p-3">
            <span className="text-sm text-muted">{t("qcm.candidates.page", { page, total: data?.total ?? 0 })}</span>
            <div className="flex gap-2">
              <Button type="button" variant="secondary" size="sm" disabled={!canGoPrevious} onClick={onPrevious}>{t("qcm.training.pagination.prev")}</Button>
              <Button type="button" variant="secondary" size="sm" disabled={!data?.next_cursor} onClick={onNext}>{t("qcm.training.pagination.next")}</Button>
            </div>
          </div>
        </div>
      ) : (
        <EmptyState title={t("qcm.candidates.emptyTitle")} hint={t("qcm.candidates.emptyHint")} />
      )}
    </div>
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

async function uploadClassifierTrainingFile(file: File, replace: boolean): Promise<ClassifierImportData> {
  const form = new FormData();
  form.append("file", file);
  form.append("replace", String(replace));
  const response = await apiFetch("/api/nl2sql/classifier/training-data/import", {
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
