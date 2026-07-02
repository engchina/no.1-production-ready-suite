"use client";

import { useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  RotateCcw,
  Save,
  Scissors,
  SlidersHorizontal,
} from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  ApiError,
  type ChunkingSettingsData,
  type ChunkingSettingsUpdate,
  type ChunkingStrategyName,
  type ChunkingStrategyStatusData,
} from "@/lib/api";
import {
  CHUNK_OVERLAP_MAX_CHARS,
  CHUNK_SIZE_MAX_CHARS,
  CHUNK_SIZE_MIN_CHARS,
  chunkSizeLabelKey,
  chunkingStrategyPreset,
  isSemanticBoundaryStrategy,
  overlapLabelKey,
} from "@/lib/chunking";
import { t, type I18nKey } from "@/lib/i18n";
import { useChunkingSettings, useUpdateChunkingSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

type ChunkingForm = ChunkingSettingsUpdate;
type ChunkingParamField =
  | "chunk_size"
  | "overlap"
  | "child_size"
  | "min_chars"
  | "delimiter";

const STRATEGY_ORDER: ChunkingStrategyName[] = [
  "structure_aware",
  "recursive_character",
  "hierarchical_parent_child",
  "markdown_heading",
  "page_level",
  "fixed_size",
  "fixed_delimiter",
];

const STRATEGY_PARAM_FIELDS: Record<ChunkingStrategyName, ChunkingParamField[]> = {
  structure_aware: ["chunk_size", "overlap", "min_chars"],
  recursive_character: ["chunk_size", "overlap", "min_chars"],
  hierarchical_parent_child: ["chunk_size", "overlap", "child_size", "min_chars"],
  markdown_heading: ["chunk_size", "overlap", "min_chars"],
  page_level: ["chunk_size", "overlap", "min_chars"],
  fixed_size: ["chunk_size", "overlap"],
  fixed_delimiter: ["delimiter"],
};

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
        onStrategyChange={(strategy) => {
          const preset = chunkingStrategyPreset(strategy);
          updateForm({
            strategy,
            chunk_size: preset.chunkSize,
            overlap: preset.overlap,
          });
        }}
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
        <FormStatus tone="info" message={t("settings.chunking.serviceNote")} />
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
                  <span className="flex items-start gap-3">
                    <ChunkStrategyDiagram strategy={strategy.name} selected={selected} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">
                          {strategyLabel(strategy.name)}
                        </span>
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
                    </span>
                  </span>
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
            label={t("settings.chunking.params.active")}
            value={paramSummary(form)}
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
  const fields = STRATEGY_PARAM_FIELDS[form.strategy];
  const hasField = (field: ChunkingParamField) => fields.includes(field);
  const semanticBoundary = isSemanticBoundaryStrategy(form.strategy);
  const chunkSizeField = hasField("chunk_size") ? (
    <NumberField
      label={t(chunkSizeLabelKey(form.strategy))}
      value={form.chunk_size}
      min={CHUNK_SIZE_MIN_CHARS}
      max={CHUNK_SIZE_MAX_CHARS}
      disabled={saving}
      onChange={(value) => onChange({ chunk_size: value })}
    />
  ) : null;
  const overlapField = hasField("overlap") ? (
    <NumberField
      label={t(overlapLabelKey(form.strategy))}
      value={form.overlap}
      min={0}
      max={CHUNK_OVERLAP_MAX_CHARS}
      disabled={saving}
      onChange={(value) => onChange({ overlap: value })}
    />
  ) : null;
  const minCharsField = hasField("min_chars") ? (
    <NumberField
      label={t("settings.chunking.params.minChars")}
      value={form.min_chars}
      min={0}
      max={2000}
      disabled={saving}
      helper={t("settings.chunking.params.minCharsHint")}
      onChange={(value) => onChange({ min_chars: value })}
    />
  ) : null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
            <SlidersHorizontal size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.chunking.params.title")}</CardTitle>
            <CardDescription>{paramsDescription(form.strategy)}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="flex items-start justify-between gap-4 rounded-md border border-border bg-card p-3 md:col-span-2">
            <div className="min-w-0">
              <div className="text-sm font-medium text-foreground">
                {t("settings.chunking.params.contextHeader")}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {t("settings.chunking.params.contextHeaderHint")}
              </p>
            </div>
            <Switch
              checked={form.context_header_enabled}
              disabled={saving}
              aria-label={t("settings.chunking.params.contextHeader")}
              onCheckedChange={(checked) => onChange({ context_header_enabled: checked })}
            />
          </div>
          {hasField("delimiter") ? (
            <TextField
              label={t("settings.chunking.params.delimiter")}
              value={form.delimiter}
              disabled={saving}
              helper={t("settings.chunking.params.delimiterHint")}
              onChange={(value) => onChange({ delimiter: value })}
            />
          ) : null}
          {semanticBoundary ? (
            <details
              key={form.strategy}
              className="group rounded-md border border-border bg-background p-3 md:col-span-2"
            >
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-sm text-sm font-semibold text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
                <span>{t("settings.chunking.params.semanticDetails")}</span>
                <ChevronDown
                  size={16}
                  className="shrink-0 text-muted transition-transform group-open:rotate-180"
                  aria-hidden
                />
              </summary>
              <p className="mb-3 text-xs leading-relaxed text-muted">
                {paramsDescription(form.strategy)}
              </p>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {chunkSizeField}
                {overlapField}
                {minCharsField}
              </div>
            </details>
          ) : (
            <>
              {chunkSizeField}
              {overlapField}
            </>
          )}
          {hasField("child_size") ? (
            <NumberField
              label={t("settings.chunking.params.childSize")}
              value={form.child_size}
              min={80}
              max={4000}
              disabled={saving}
              helper={t("settings.chunking.params.childSizeHint")}
              onChange={(value) => onChange({ child_size: value })}
            />
          ) : null}
          {!semanticBoundary ? minCharsField : null}
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

function TextField({
  label,
  value,
  disabled,
  helper,
  onChange,
}: {
  label: string;
  value: string;
  disabled: boolean;
  helper?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="space-y-1.5">
      <span className="block text-sm font-medium text-foreground">{label}</span>
      <input
        type="text"
        value={value}
        maxLength={256}
        aria-label={label}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary disabled:cursor-not-allowed disabled:opacity-50"
      />
      {helper ? <span className="block text-xs text-muted">{helper}</span> : null}
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  disabled,
  helper,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled: boolean;
  helper?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-y-1.5">
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

/**
 * 分割方式の概念を表す装飾 SVG(初学者の選択補助)。currentColor でテーマ追従、
 * 選択時は primary で強調。意味はラベル/説明が担うため aria-hidden。
 */
function ChunkStrategyDiagram({
  strategy,
  selected,
}: {
  strategy: ChunkingStrategyName;
  selected: boolean;
}) {
  return (
    <svg
      viewBox="0 0 48 36"
      className={cn("h-9 w-12 shrink-0", selected ? "text-primary" : "text-muted")}
      fill="currentColor"
      aria-hidden
    >
      {chunkStrategyDiagramShapes(strategy)}
    </svg>
  );
}

function chunkStrategyDiagramShapes(strategy: ChunkingStrategyName) {
  switch (strategy) {
    case "structure_aware":
      // 見出し + 字下げ本文(構造に沿う)
      return (
        <>
          <rect x="4" y="5" width="28" height="6" rx="2" opacity="0.9" />
          <rect x="10" y="15" width="34" height="4" rx="2" opacity="0.5" />
          <rect x="10" y="22" width="30" height="4" rx="2" opacity="0.5" />
          <rect x="10" y="29" width="34" height="4" rx="2" opacity="0.5" />
        </>
      );
    case "recursive_character":
      // 区切りで再帰分割(幅が不揃いの塊)
      return (
        <>
          <rect x="4" y="6" width="40" height="6" rx="2" opacity="0.85" />
          <rect x="4" y="15" width="26" height="6" rx="2" opacity="0.85" />
          <rect x="4" y="24" width="34" height="6" rx="2" opacity="0.85" />
        </>
      );
    case "hierarchical_parent_child":
      // 親ブロックの中に子チャンク
      return (
        <>
          <rect
            x="3"
            y="4"
            width="42"
            height="28"
            rx="3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            opacity="0.6"
          />
          <rect x="7" y="9" width="34" height="7" rx="2" opacity="0.85" />
          <rect x="7" y="20" width="34" height="7" rx="2" opacity="0.85" />
        </>
      );
    case "markdown_heading":
      // 見出しマーカー + 本文行
      return (
        <>
          <rect x="4" y="6" width="6" height="6" rx="1" opacity="0.9" />
          <rect x="13" y="7" width="29" height="4" rx="2" opacity="0.8" />
          <rect x="4" y="16" width="6" height="6" rx="1" opacity="0.9" />
          <rect x="13" y="17" width="23" height="4" rx="2" opacity="0.8" />
          <rect x="4" y="26" width="6" height="6" rx="1" opacity="0.9" />
          <rect x="13" y="27" width="27" height="4" rx="2" opacity="0.8" />
        </>
      );
    case "page_level":
      // ページ単位(重なるシート)
      return (
        <>
          <rect
            x="9"
            y="4"
            width="26"
            height="24"
            rx="2"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            opacity="0.45"
          />
          <rect
            x="13"
            y="9"
            width="26"
            height="24"
            rx="2"
            opacity="0.12"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <rect x="17" y="14" width="18" height="3" rx="1.5" opacity="0.6" />
          <rect x="17" y="20" width="18" height="3" rx="1.5" opacity="0.6" />
        </>
      );
    case "fixed_size":
      // 固定長(均等な塊)
      return (
        <>
          <rect x="4" y="6" width="40" height="6" rx="2" opacity="0.85" />
          <rect x="4" y="15" width="40" height="6" rx="2" opacity="0.85" />
          <rect x="4" y="24" width="40" height="6" rx="2" opacity="0.85" />
        </>
      );
    case "fixed_delimiter":
      // 区切り文字で分割(破線の境界)
      return (
        <>
          <rect x="4" y="5" width="40" height="9" rx="2" opacity="0.85" />
          <line
            x1="4"
            y1="18"
            x2="44"
            y2="18"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeDasharray="3 2"
            opacity="0.7"
          />
          <rect x="4" y="22" width="40" height="9" rx="2" opacity="0.85" />
        </>
      );
  }
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

function paramsDescription(strategy: ChunkingStrategyName) {
  if (strategy === "markdown_heading") {
    return t("settings.chunking.params.headingDescription");
  }
  if (strategy === "page_level") {
    return t("settings.chunking.params.pageDescription");
  }
  if (strategy === "fixed_delimiter") {
    return t("settings.chunking.params.delimiterDescription");
  }
  if (strategy === "fixed_size") {
    return t("settings.chunking.params.fixedSizeDescription");
  }
  return t("settings.chunking.params.description");
}

function paramLabel(field: ChunkingParamField) {
  const keyByField: Record<ChunkingParamField, I18nKey> = {
    chunk_size: "settings.chunking.params.chunkSize",
    overlap: "settings.chunking.params.overlap",
    child_size: "settings.chunking.params.childSize",
    min_chars: "settings.chunking.params.minChars",
    delimiter: "settings.chunking.params.delimiter",
  };
  return t(keyByField[field]);
}

function paramValue(form: ChunkingForm, field: ChunkingParamField) {
  return String(form[field]);
}

function paramSummary(form: ChunkingForm) {
  if (isSemanticBoundaryStrategy(form.strategy)) {
    return t("settings.chunking.params.semanticSummary", {
      size: form.chunk_size.toLocaleString("ja-JP"),
      overlap:
        form.overlap === 0
          ? t("settings.chunking.params.noOverlap")
          : t("settings.chunking.params.withOverlap", {
              overlap: form.overlap.toLocaleString("ja-JP"),
            }),
    });
  }
  return STRATEGY_PARAM_FIELDS[form.strategy]
    .map((field) => `${paramLabel(field)}: ${paramValue(form, field)}`)
    .join(" / ");
}

function validateForm(form: ChunkingForm): string | null {
  const fields = STRATEGY_PARAM_FIELDS[form.strategy];
  const hasField = (field: ChunkingParamField) => fields.includes(field);
  if (hasField("delimiter")) {
    return form.delimiter.trim() ? null : t("settings.chunking.params.delimiter");
  }
  if (
    hasField("chunk_size") &&
    (!Number.isFinite(form.chunk_size) ||
      form.chunk_size < CHUNK_SIZE_MIN_CHARS ||
      form.chunk_size > CHUNK_SIZE_MAX_CHARS)
  ) {
    return t(chunkSizeLabelKey(form.strategy));
  }
  if (
    hasField("overlap") &&
    (!Number.isFinite(form.overlap) ||
      form.overlap < 0 ||
      form.overlap > CHUNK_OVERLAP_MAX_CHARS)
  ) {
    return t(overlapLabelKey(form.strategy));
  }
  if (hasField("overlap") && form.overlap >= form.chunk_size) {
    return t(overlapLabelKey(form.strategy)) + " < " + t(chunkSizeLabelKey(form.strategy));
  }
  if (
    hasField("child_size") &&
    (!Number.isFinite(form.child_size) || form.child_size < 80 || form.child_size > 4000)
  ) {
    return t("settings.chunking.params.childSize");
  }
  if (hasField("child_size") && form.child_size >= form.chunk_size) {
    return (
      t("settings.chunking.params.childSize") + " < " + t("settings.chunking.params.chunkSize")
    );
  }
  if (
    hasField("min_chars") &&
    (!Number.isFinite(form.min_chars) || form.min_chars < 0 || form.min_chars > 2000)
  ) {
    return t("settings.chunking.params.minChars");
  }
  if (hasField("min_chars") && form.min_chars >= form.chunk_size) {
    return t("settings.chunking.params.minChars") + " < " + t("settings.chunking.params.chunkSize");
  }
  return null;
}

function formFromSettings(settings: ChunkingSettingsData): ChunkingForm {
  return {
    strategy: settings.strategy,
    chunk_size: settings.chunk_size,
    overlap: settings.overlap,
    child_size: settings.child_size,
    min_chars: settings.min_chars,
    delimiter: settings.delimiter || "\\n\\n",
    context_header_enabled: settings.context_header_enabled,
  };
}

function serializeForm(form: ChunkingForm) {
  return JSON.stringify({
    strategy: form.strategy,
    chunk_size: form.chunk_size,
    overlap: form.overlap,
    child_size: form.child_size,
    min_chars: form.min_chars,
    delimiter: form.delimiter,
    context_header_enabled: form.context_header_enabled,
  });
}
