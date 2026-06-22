"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, Scissors, SlidersHorizontal } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type ChunkingSettingsData,
  type ChunkingSettingsUpdate,
  type ChunkingStrategyName,
  type ChunkingStrategyStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useChunkingSettings, useUpdateChunkingSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

type ChunkingForm = ChunkingSettingsUpdate;

const STRATEGY_ORDER: ChunkingStrategyName[] = [
  "structure_aware",
  "recursive_character",
  "sentence_window",
  "hierarchical_parent_child",
  "markdown_heading",
  "page_level",
  "fixed_size",
];

/** 文書分割方式の現在設定とパラメータを管理する設定画面。 */
export function ChunkingSettingsClient() {
  const query = useChunkingSettings();
  const save = useUpdateChunkingSettings();
  const [form, setForm] = useState<ChunkingForm | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data && !save.isPending) {
      setForm(formFromSettings(query.data));
    }
  }, [query.data, save.isPending]);

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-48 w-full rounded-lg" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="p-8">
        <ErrorState
          message={
            query.error instanceof ApiError ? query.error.message : t("settings.chunking.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !form) return null;

  const dirty = serializeForm(form) !== serializeForm(formFromSettings(settings));
  const validationError = validateForm(form);
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.chunking.saveError");
  const strategies = orderedStrategies(settings.strategies);

  function updateForm(update: Partial<ChunkingForm>) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, ...update } : current));
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setForm(formFromSettings(settings));
  }

  function submit() {
    if (!form || validationError) return;
    save.mutate(form, {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setSuccessMessage(t("settings.chunking.actions.saved"));
      },
      onError: () => {
        setSuccessMessage(null);
      },
    });
  }

  return (
    <div className="space-y-5 p-8">
      <OverviewCard
        dirty={dirty}
        form={form}
        strategies={strategies}
        saving={save.isPending}
        validationError={validationError}
        successMessage={successMessage}
        errorMessage={save.isError ? saveError : null}
        onStrategyChange={(strategy) => updateForm({ strategy })}
        onReset={resetForm}
        onSubmit={submit}
      />
      <ParamsCard
        form={form}
        saving={save.isPending}
        validationError={validationError}
        onChange={updateForm}
      />
    </div>
  );
}

function OverviewCard({
  dirty,
  form,
  strategies,
  saving,
  validationError,
  successMessage,
  errorMessage,
  onStrategyChange,
  onReset,
  onSubmit,
}: {
  dirty: boolean;
  form: ChunkingForm;
  strategies: ChunkingStrategyStatusData[];
  saving: boolean;
  validationError: string | null;
  successMessage: string | null;
  errorMessage: string | null;
  onStrategyChange: (strategy: ChunkingStrategyName) => void;
  onReset: () => void;
  onSubmit: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
            <Scissors size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.chunking.overview.title")}</CardTitle>
            <CardDescription>{t("settings.chunking.overview.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <div className="text-sm font-medium text-foreground">
            {t("settings.chunking.strategy")}
          </div>
          <div
            role="radiogroup"
            aria-label={t("settings.chunking.strategy")}
            className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3"
          >
            {strategies.map((strategy) => {
              const selected = form.strategy === strategy.name;
              return (
                <button
                  key={strategy.name}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={saving}
                  onClick={() => onStrategyChange(strategy.name)}
                  className={cn(
                    "min-h-[92px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border bg-card text-foreground hover:bg-background"
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold">{strategyLabel(strategy.name)}</span>
                    {selected ? (
                      <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                    ) : null}
                  </span>
                  <span className="mt-1 block text-xs leading-relaxed text-muted">
                    {strategyDescription(strategy.name)}
                  </span>
                  {strategy.recommended_for.length ? (
                    <span className="mt-2 block text-[11px] text-muted">
                      {t("settings.chunking.recommendedFor")}:{" "}
                      {strategy.recommended_for.join(", ")}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </div>
        <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <RuntimeFact
            label={t("settings.chunking.strategy")}
            value={strategyLabel(form.strategy)}
          />
          <RuntimeFact
            label={t("settings.chunking.params.chunkSize")}
            value={String(form.chunk_size)}
          />
          <RuntimeFact
            label={t("settings.chunking.source")}
            value={t("settings.common.currentConfig")}
          />
        </dl>
        <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
          <div className="min-h-6">
            {validationError ? <FormStatus tone="danger" message={validationError} /> : null}
            {!validationError && dirty ? (
              <FormStatus tone="warning" message={t("settings.chunking.actions.unsaved")} />
            ) : null}
            {successMessage ? <FormStatus tone="success" message={successMessage} /> : null}
            {errorMessage ? <FormStatus tone="danger" message={errorMessage} /> : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={onReset}
              disabled={!dirty || saving}
              aria-label={t("settings.chunking.actions.reset")}
            >
              <RotateCcw size={15} aria-hidden />
              {t("settings.chunking.actions.reset")}
            </Button>
            <Button
              type="button"
              loading={saving}
              disabled={!dirty || Boolean(validationError)}
              onClick={onSubmit}
              aria-label={t("settings.chunking.actions.save")}
            >
              <Save size={15} aria-hidden />
              {saving
                ? t("settings.chunking.actions.saving")
                : t("settings.chunking.actions.save")}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ParamsCard({
  form,
  saving,
  validationError,
  onChange,
}: {
  form: ChunkingForm;
  saving: boolean;
  validationError: string | null;
  onChange: (update: Partial<ChunkingForm>) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
            <SlidersHorizontal size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.chunking.params.title")}</CardTitle>
            <CardDescription>{t("settings.chunking.params.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <NumberField
            label={t("settings.chunking.params.chunkSize")}
            value={form.chunk_size}
            min={200}
            max={4000}
            disabled={saving}
            onChange={(value) => onChange({ chunk_size: value })}
          />
          <NumberField
            label={t("settings.chunking.params.overlap")}
            value={form.overlap}
            min={0}
            max={1000}
            disabled={saving}
            onChange={(value) => onChange({ overlap: value })}
          />
          <NumberField
            label={t("settings.chunking.params.childSize")}
            value={form.child_size}
            min={80}
            max={4000}
            disabled={saving}
            helper={t("settings.chunking.params.childSizeHint")}
            applicable={form.strategy === "hierarchical_parent_child"}
            onChange={(value) => onChange({ child_size: value })}
          />
          <NumberField
            label={t("settings.chunking.params.sentenceWindowSize")}
            value={form.sentence_window_size}
            min={1}
            max={20}
            disabled={saving}
            helper={t("settings.chunking.params.sentenceWindowHint")}
            applicable={form.strategy === "sentence_window"}
            onChange={(value) => onChange({ sentence_window_size: value })}
          />
          <NumberField
            label={t("settings.chunking.params.minChars")}
            value={form.min_chars}
            min={0}
            max={2000}
            disabled={saving}
            helper={t("settings.chunking.params.minCharsHint")}
            onChange={(value) => onChange({ min_chars: value })}
          />
        </div>
        {validationError ? (
          <div className="mt-4">
            <FormStatus tone="danger" message={validationError} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  disabled,
  helper,
  applicable = true,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled: boolean;
  helper?: string;
  applicable?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label className={cn("space-y-1.5", !applicable && "opacity-60")}>
      <span className="block text-sm font-medium text-foreground">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        value={Number.isFinite(value) ? value : ""}
        min={min}
        max={max}
        aria-label={label}
        disabled={disabled}
        onChange={(event) => onChange(Number.parseInt(event.target.value, 10))}
        className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary disabled:cursor-not-allowed disabled:opacity-50"
      />
      {helper ? <span className="block text-xs text-muted">{helper}</span> : null}
    </label>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function orderedStrategies(strategies: ChunkingStrategyStatusData[]): ChunkingStrategyStatusData[] {
  const byName = new Map(strategies.map((strategy) => [strategy.name, strategy]));
  const ordered = STRATEGY_ORDER.map((name) => byName.get(name)).filter(
    (strategy): strategy is ChunkingStrategyStatusData => Boolean(strategy)
  );
  return ordered.length ? ordered : strategies;
}

function strategyLabel(strategy: ChunkingStrategyName) {
  return t(`settings.chunking.strategy.${strategy}` as I18nKey);
}

function strategyDescription(strategy: ChunkingStrategyName) {
  return t(`settings.chunking.strategy.${strategy}.description` as I18nKey);
}

function validateForm(form: ChunkingForm): string | null {
  if (form.overlap >= form.chunk_size) {
    return t("settings.chunking.params.overlap") + " < " + t("settings.chunking.params.chunkSize");
  }
  if (form.child_size >= form.chunk_size) {
    return (
      t("settings.chunking.params.childSize") + " < " + t("settings.chunking.params.chunkSize")
    );
  }
  if (form.min_chars >= form.chunk_size) {
    return t("settings.chunking.params.minChars") + " < " + t("settings.chunking.params.chunkSize");
  }
  if (!Number.isFinite(form.chunk_size) || form.chunk_size < 200 || form.chunk_size > 4000) {
    return t("settings.chunking.params.chunkSize");
  }
  return null;
}

function formFromSettings(settings: ChunkingSettingsData): ChunkingForm {
  return {
    strategy: settings.strategy,
    chunk_size: settings.chunk_size,
    overlap: settings.overlap,
    child_size: settings.child_size,
    sentence_window_size: settings.sentence_window_size,
    min_chars: settings.min_chars,
  };
}

function serializeForm(form: ChunkingForm) {
  return JSON.stringify({
    strategy: form.strategy,
    chunk_size: form.chunk_size,
    overlap: form.overlap,
    child_size: form.child_size,
    sentence_window_size: form.sentence_window_size,
    min_chars: form.min_chars,
  });
}
