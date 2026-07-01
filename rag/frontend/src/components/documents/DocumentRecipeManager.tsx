"use client";

import {
  AlertCircle,
  Check,
  CheckCircle2,
  Circle,
  Clock3,
  Copy,
  Eye,
  LoaderCircle,
  Plus,
  RotateCcw,
  Search,
  SearchCheck,
  Settings2,
  Trash2,
} from "lucide-react";
import { useRef, useState, type ReactNode } from "react";

import { DocumentProcessingConfigPanel } from "./DocumentProcessingConfigPanel";
import {
  canAddRecipe,
  canDeleteRecipe,
  recipeConfigLocked,
  recipeIsActive,
  resolveSelectedRecipe,
} from "./DocumentRecipeManager.logic";
import { CitationCard, scoreMaximaForCitations } from "@/components/search/CitationCard";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  api,
  type DocumentIngestionConfigData,
  type DocumentRecipeStep,
  type DocumentRecipeView,
  type IngestionJobPhase,
  type RetrievedChunk,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useApproveDocumentRecipe,
  useCreateDocumentRecipe,
  useDeleteDocumentRecipe,
  useEnqueueDocumentRecipeJob,
} from "@/lib/queries";
import { cn } from "@/lib/utils";

const PHASES: Array<{ phase: IngestionJobPhase; label: I18nKey; shortLabel: I18nKey }> = [
  {
    phase: "PREPROCESS",
    label: "flow.step.preprocess",
    shortLabel: "documents.recipes.phase.preprocess",
  },
  {
    phase: "EXTRACT",
    label: "flow.step.extract",
    shortLabel: "documents.recipes.phase.extract",
  },
  {
    phase: "CHUNK",
    label: "flow.step.chunk",
    shortLabel: "documents.recipes.phase.chunk",
  },
  {
    phase: "INDEX",
    label: "flow.step.indexing",
    shortLabel: "documents.recipes.phase.index",
  },
];

type AddMode = "clone" | "defaults";

export function DocumentRecipeManager({
  documentId,
  recipes,
  selectedRecipeId,
  onSelect,
  loading,
  error,
  onRetry,
}: {
  documentId: string;
  recipes: DocumentRecipeView[];
  selectedRecipeId: string | null;
  onSelect: (recipeId: string) => void;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const selected = resolveSelectedRecipe(recipes, selectedRecipeId);
  const createRecipe = useCreateDocumentRecipe();
  const deleteRecipe = useDeleteDocumentRecipe();
  const enqueue = useEnqueueDocumentRecipeJob();
  const approve = useApproveDocumentRecipe();
  const confirm = useConfirm();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [addMode, setAddMode] = useState<AddMode>("clone");

  const openAddDialog = () => {
    setAddMode("clone");
    dialogRef.current?.showModal();
  };

  const active = selected ? recipeIsActive(selected) : false;
  const atMaximum = !canAddRecipe(recipes.length);
  const atMinimum = recipes.length <= 1;

  const handleCreate = () => {
    createRecipe.mutate(
      {
        id: documentId,
        copyFrom: addMode === "clone" ? selected?.recipe_id ?? null : null,
      },
      {
        onSuccess: (recipe) => {
          dialogRef.current?.close();
          onSelect(recipe.recipe_id);
        },
      }
    );
  };

  const handleDelete = async () => {
    if (!selected || !canDeleteRecipe(recipes.length, active)) return;
    const confirmed = await confirm({
      title: t("documents.recipes.deleteTitle"),
      description: t("documents.recipes.deleteDescription"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!confirmed) return;
    deleteRecipe.mutate(
      { id: documentId, recipeId: selected.recipe_id },
      {
        onSuccess: () => {
          const fallback = recipes.find((recipe) => recipe.recipe_id !== selected.recipe_id);
          if (fallback) onSelect(fallback.recipe_id);
        },
      }
    );
  };

  const handleProcess = () => {
    if (!selected || active) return;
    if (["PREPROCESSED", "REVIEW", "CHUNKED"].includes(selected.status)) {
      approve.mutate({ id: documentId, recipeId: selected.recipe_id });
      return;
    }
    enqueue.mutate({
      id: documentId,
      recipeId: selected.recipe_id,
      phase: selected.failed_phase ?? "PREPROCESS",
    });
  };

  if (loading) {
    return (
      <section aria-label={t("documents.recipes.title")} className="space-y-3">
        <Skeleton className="h-9 w-48" />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-28" />
          <Skeleton className="hidden h-28 sm:block" />
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <Banner severity="warning" title={t("documents.recipes.loadError")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p>{error instanceof ApiError ? error.message : t("flow.buildConfig.loadErrorHint")}</p>
          <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
            <RotateCcw size={14} aria-hidden />
            {t("common.retry")}
          </Button>
        </div>
      </Banner>
    );
  }

  if (!selected) {
    return <FormStatus tone="info" message={t("documents.recipes.empty")} />;
  }

  const configData = recipeConfigData(selected);
  const processPending = enqueue.isPending || approve.isPending;
  const processError = enqueue.error ?? approve.error;

  return (
    <section aria-label={t("documents.recipes.title")} className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Settings2 size={17} className="text-primary" aria-hidden />
            <h2 className="text-sm font-semibold text-foreground">
              {t("documents.recipes.title")}
            </h2>
            <span className="tnum rounded-md bg-muted/10 px-2 py-0.5 text-xs font-medium text-muted">
              {t("documents.recipes.count", { count: recipes.length })}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted">
            {atMaximum ? t("documents.recipes.max") : t("documents.recipes.subtitle")}
          </p>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={openAddDialog}
          disabled={atMaximum}
          title={atMaximum ? t("documents.recipes.max") : undefined}
        >
          <Plus size={15} aria-hidden />
          {t("documents.recipes.add")}
        </Button>
      </div>

      <div className="sm:hidden">
        <SelectField
          id={`recipe-select-${documentId}`}
          label={t("documents.recipes.select")}
          value={selected.recipe_id}
          options={recipes.map((recipe) => ({
            value: recipe.recipe_id,
            label: `${recipeName(recipe)} · ${t(recipeStatus(recipe).label)}`,
          }))}
          onValueChange={onSelect}
          buttonClassName="min-h-11"
        />
      </div>

      <div className="hidden gap-3 sm:grid sm:grid-cols-2 xl:grid-cols-3">
        {recipes.map((recipe) => (
          <RecipeCard
            key={recipe.recipe_id}
            recipe={recipe}
            selected={recipe.recipe_id === selected.recipe_id}
            onSelect={() => onSelect(recipe.recipe_id)}
          />
        ))}
      </div>

      <div className="rounded-lg border border-border bg-card p-3 sm:p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-base font-semibold text-foreground">{recipeName(selected)}</h3>
              <RecipeStatusBadge recipe={selected} />
              {selected.needs_reprocessing ? (
                <span className="rounded-full bg-warning-bg px-2 py-0.5 text-xs font-medium text-warning">
                  {t("documents.recipes.reprocess")}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-xs text-muted">
              {t("documents.recipes.updated", { time: formatDateTime(selected.updated_at) })}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => void handleDelete()}
              disabled={atMinimum || active || deleteRecipe.isPending}
              title={atMinimum ? t("documents.recipes.min") : undefined}
            >
              <Trash2 size={15} aria-hidden />
              {t("documents.recipes.delete")}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleProcess}
              loading={processPending}
              disabled={active}
            >
              {processButtonLabel(selected)}
            </Button>
          </div>
        </div>

        <RecipeSteps recipe={selected} />

        {selected.status === "ERROR" && selected.searchable ? (
          <Banner severity="warning" className="mt-3">
            {t("documents.recipes.staleError")}
          </Banner>
        ) : selected.error_message ? (
          <Banner severity="danger" className="mt-3">
            {selected.error_message}
          </Banner>
        ) : null}
        {processError ? (
          <FormStatus
            tone="danger"
            message={processError instanceof ApiError ? processError.message : t("flow.ingestFailed")}
          />
        ) : null}

        <div className="mt-4">
          <DocumentProcessingConfigPanel
            documentId={documentId}
            recipeId={selected.recipe_id}
            data={configData}
            loading={false}
            error={null}
            onRetry={onRetry}
            disabled={recipeConfigLocked(selected)}
          />
        </div>
        <RecipeComparison documentId={documentId} recipes={recipes} />
      </div>

      <dialog
        ref={dialogRef}
        className="m-auto w-[calc(100%-2rem)] max-w-lg rounded-xl border border-border bg-card p-0 text-foreground shadow-xl backdrop:bg-slate-950/45"
        onClose={() => setAddMode("clone")}
        onClick={(event) => {
          if (event.target === event.currentTarget) dialogRef.current?.close();
        }}
      >
        <div className="p-5">
          <h2 className="text-base font-semibold">{t("documents.recipes.addTitle")}</h2>
          <p className="mt-1 text-sm text-muted">{t("documents.recipes.addDescription")}</p>
          <div className="mt-4 grid gap-2">
            <AddModeOption
              selected={addMode === "clone"}
              icon={<Copy size={17} aria-hidden />}
              label={t("documents.recipes.clone")}
              onSelect={() => setAddMode("clone")}
            />
            <AddModeOption
              selected={addMode === "defaults"}
              icon={<Settings2 size={17} aria-hidden />}
              label={t("documents.recipes.defaults")}
              onSelect={() => setAddMode("defaults")}
            />
          </div>
          {createRecipe.error ? (
            <FormStatus
              tone="danger"
              message={
                createRecipe.error instanceof ApiError
                  ? createRecipe.error.message
                  : t("flow.ingestFailed")
              }
            />
          ) : null}
          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => dialogRef.current?.close()}>
              {t("common.cancel")}
            </Button>
            <Button type="button" onClick={handleCreate} loading={createRecipe.isPending}>
              <Plus size={15} aria-hidden />
              {t("documents.recipes.create")}
            </Button>
          </div>
        </div>
      </dialog>
    </section>
  );
}

function RecipeCard({
  recipe,
  selected,
  onSelect,
}: {
  recipe: DocumentRecipeView;
  selected: boolean;
  onSelect: () => void;
}) {
  const completed = completedStepCount(recipe);
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "min-w-0 rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-primary bg-primary/5 shadow-sm"
          : "border-border bg-card hover:border-primary/40 hover:bg-muted/5"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-semibold text-foreground">{recipeName(recipe)}</span>
        <span className="tnum text-xs font-medium text-muted">{completed}/4</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <RecipeStatusBadge recipe={recipe} />
      </div>
      <div className="mt-3 grid grid-cols-4 gap-1" aria-label={`${completed}/4`}>
        {PHASES.map(({ phase }) => {
          const step = recipe.steps.find((item) => item.phase === phase);
          return (
            <span
              key={phase}
              className={cn(
                "h-1.5 rounded-full",
                step?.status === "FAILED"
                  ? "bg-danger"
                  : step?.status === "RUNNING"
                    ? "bg-info"
                    : step?.status === "SUCCEEDED"
                      ? "bg-success"
                      : step?.status === "NEEDS_REVIEW"
                        ? "bg-warning"
                        : "bg-border"
              )}
            />
          );
        })}
      </div>
    </button>
  );
}

function RecipeSteps({ recipe }: { recipe: DocumentRecipeView }) {
  return (
    <ol className="mt-4 grid grid-cols-4 gap-1" aria-label={t("documents.recipes.title")}>
      {PHASES.map(({ phase, label, shortLabel }, index) => {
        const step = recipe.steps.find((item) => item.phase === phase);
        return (
          <li key={phase} className="relative min-w-0 text-center">
            {index > 0 ? (
              <span className="absolute right-1/2 top-3 h-px w-full bg-border" aria-hidden />
            ) : null}
            <span
              className={cn(
                "relative z-10 mx-auto flex size-6 items-center justify-center rounded-full border bg-card",
                stepTone(step)
              )}
            >
              <StepIcon step={step} />
            </span>
            <span className="mt-1.5 block min-h-7 px-0.5 text-[10px] leading-3 text-muted sm:text-xs sm:leading-4">
              <span className="sm:hidden">{t(shortLabel)}</span>
              <span className="hidden sm:inline">{t(label)}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function StepIcon({ step }: { step: DocumentRecipeStep | undefined }) {
  if (step?.status === "RUNNING") {
    return <LoaderCircle size={13} className="animate-spin" aria-hidden />;
  }
  if (step?.status === "FAILED") return <AlertCircle size={13} aria-hidden />;
  if (step?.status === "NEEDS_REVIEW") return <Eye size={13} aria-hidden />;
  if (step?.status === "SUCCEEDED") return <Check size={13} aria-hidden />;
  return <Circle size={10} aria-hidden />;
}

function RecipeStatusBadge({ recipe }: { recipe: DocumentRecipeView }) {
  const status = recipeStatus(recipe);
  const Icon = status.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", status.className)}>
      <Icon size={12} className={status.spin ? "animate-spin" : undefined} aria-hidden />
      {t(status.label)}
    </span>
  );
}

function recipeStatus(recipe: DocumentRecipeView) {
  const stepStatuses = recipe.steps.map((step) => step.status);
  if (stepStatuses.includes("RUNNING")) {
    return {
      label: "documents.recipes.status.running" as const,
      icon: LoaderCircle,
      className: "bg-info-bg text-info",
      spin: true,
    };
  }
  if (stepStatuses.includes("QUEUED")) {
    return {
      label: "documents.recipes.status.queued" as const,
      icon: Clock3,
      className: "bg-muted/10 text-muted",
      spin: false,
    };
  }
  if (recipe.status === "ERROR") {
    return {
      label: "documents.recipes.status.error" as const,
      icon: AlertCircle,
      className: "bg-danger-bg text-danger",
      spin: false,
    };
  }
  if (["PREPROCESSED", "REVIEW", "CHUNKED"].includes(recipe.status)) {
    return {
      label: "documents.recipes.status.review" as const,
      icon: Clock3,
      className: "bg-warning-bg text-warning",
      spin: false,
    };
  }
  if (recipe.searchable) {
    return {
      label: "documents.recipes.status.searchable" as const,
      icon: SearchCheck,
      className: "bg-success-bg text-success",
      spin: false,
    };
  }
  return {
    label: "documents.recipes.status.idle" as const,
    icon: Circle,
    className: "bg-muted/10 text-muted",
    spin: false,
  };
}

function stepTone(step: DocumentRecipeStep | undefined) {
  if (step?.status === "FAILED") return "border-danger text-danger";
  if (step?.status === "RUNNING") return "border-info text-info";
  if (step?.status === "NEEDS_REVIEW") return "border-warning text-warning";
  if (step?.status === "SUCCEEDED") return "border-success bg-success text-white";
  return "border-border text-muted";
}

function completedStepCount(recipe: DocumentRecipeView) {
  return recipe.steps.filter((step) => step.status === "SUCCEEDED").length;
}

function recipeName(recipe: DocumentRecipeView) {
  return t("documents.recipes.name", { slot: recipe.slot_no });
}

function processButtonLabel(recipe: DocumentRecipeView) {
  if (recipeIsActive(recipe)) return t("documents.recipes.status.running");
  if (recipe.status === "ERROR") return t("documents.recipes.retry");
  if (recipe.searchable) return t("documents.recipes.reprocessAction");
  if (["PREPROCESSED", "REVIEW", "CHUNKED"].includes(recipe.status)) {
    return t("documents.recipes.resume");
  }
  return t("documents.recipes.run");
}

function recipeConfigData(recipe: DocumentRecipeView): DocumentIngestionConfigData {
  const effective = recipe.effective_processing_config;
  return {
    document_id: recipe.document_id,
    is_indexed: recipe.searchable,
    processing_config: recipe.processing_config,
    effective_processing_config: effective,
    effective_preprocess_profile: effective.preprocess_profile ?? "passthrough",
    effective_chunking_strategy: effective.chunking_strategy ?? "structure_aware",
    effective_parser_adapter_backend: effective.parser_adapter_backend ?? "docling",
    observed_chunking_strategy: null,
    observed_parser_backend: null,
    chunking_drift: recipe.needs_reprocessing,
    parser_drift: recipe.needs_reprocessing,
    config_drift: recipe.needs_reprocessing,
    drift_fields: [],
  };
}

function RecipeComparison({
  documentId,
  recipes,
}: {
  documentId: string;
  recipes: DocumentRecipeView[];
}) {
  const searchable = recipes.filter((recipe) => recipe.searchable && recipe.active_chunk_set_id);
  const [open, setOpen] = useState(false);
  const [leftId, setLeftId] = useState(searchable[0]?.recipe_id ?? "");
  const [rightId, setRightId] = useState(searchable[1]?.recipe_id ?? "");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{
    left: RetrievedChunk[];
    right: RetrievedChunk[];
  } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const options = searchable.map((recipe) => ({
    value: recipe.recipe_id,
    label: recipeName(recipe),
  }));
  const left = searchable.find((recipe) => recipe.recipe_id === leftId) ?? searchable[0];
  const right =
    searchable.find((recipe) => recipe.recipe_id === rightId) ?? searchable[1] ?? searchable[0];

  const run = async () => {
    if (!left?.active_chunk_set_id || !right?.active_chunk_set_id || !query.trim()) return;
    setPending(true);
    setError("");
    setResults(null);
    try {
      const [leftResult, rightResult] = await Promise.all([
        api.search({
          query: query.trim(),
          top_k: 5,
          filters: { document_id: documentId, chunk_set_id: left.active_chunk_set_id },
        }),
        api.search({
          query: query.trim(),
          top_k: 5,
          filters: { document_id: documentId, chunk_set_id: right.active_chunk_set_id },
        }),
      ]);
      setResults({ left: leftResult.citations, right: rightResult.citations });
    } catch (reason) {
      setError(
        reason instanceof ApiError ? reason.message : t("documents.experiment.compare.error")
      );
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="mt-4 border-t border-border pt-4">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => setOpen((value) => !value)}
        disabled={searchable.length < 2}
      >
        <Search size={15} aria-hidden />
        {t("documents.experiment.compare.title")}
      </Button>
      {searchable.length < 2 ? (
        <p className="mt-1 text-xs text-muted">{t("documents.recipes.compareNeedsTwo")}</p>
      ) : null}
      {open ? (
        <div className="mt-3 space-y-3 rounded-lg border border-border bg-background p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField
              id={`recipe-compare-left-${documentId}`}
              label={t("documents.recipes.compareLeft")}
              value={left?.recipe_id ?? ""}
              options={options}
              onValueChange={setLeftId}
            />
            <SelectField
              id={`recipe-compare-right-${documentId}`}
              label={t("documents.recipes.compareRight")}
              value={right?.recipe_id ?? ""}
              options={options}
              onValueChange={setRightId}
            />
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="min-w-0 flex-1 text-sm font-medium text-foreground">
              {t("documents.experiment.compare.queryLabel")}
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("documents.experiment.compare.placeholder")}
                className="mt-1 h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onKeyDown={(event) => {
                  if (event.key === "Enter") void run();
                }}
              />
            </label>
            <Button
              type="button"
              onClick={() => void run()}
              loading={pending}
              disabled={!query.trim() || left?.recipe_id === right?.recipe_id}
            >
              {t("documents.experiment.compare.run")}
            </Button>
          </div>
          {error ? <FormStatus tone="danger" message={error} /> : null}
          {results ? (
            <div className="grid gap-4 lg:grid-cols-2">
              <ComparisonColumn
                title={left ? recipeName(left) : ""}
                chunks={results.left}
              />
              <ComparisonColumn
                title={right ? recipeName(right) : ""}
                chunks={results.right}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ComparisonColumn({ title, chunks }: { title: string; chunks: RetrievedChunk[] }) {
  const maxima = scoreMaximaForCitations(chunks);
  return (
    <section className="min-w-0">
      <h4 className="mb-2 text-sm font-semibold text-foreground">{title}</h4>
      <ol className="space-y-2">
        {chunks.map((chunk, index) => (
          <CitationCard
            key={`${chunk.chunk_id}-${index}`}
            chunk={chunk}
            index={index}
            scoreMaxima={maxima}
          />
        ))}
      </ol>
    </section>
  );
}

function AddModeOption({
  selected,
  icon,
  label,
  onSelect,
}: {
  selected: boolean;
  icon: ReactNode;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        "flex min-h-12 items-center gap-3 rounded-lg border px-3 text-left text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected ? "border-primary bg-primary/5 text-foreground" : "border-border text-muted"
      )}
    >
      <span className={cn("flex size-8 items-center justify-center rounded-md", selected ? "bg-primary/10 text-primary" : "bg-muted/10")}>
        {icon}
      </span>
      <span className="flex-1">{label}</span>
      {selected ? <CheckCircle2 size={17} className="text-primary" aria-hidden /> : null}
    </button>
  );
}
