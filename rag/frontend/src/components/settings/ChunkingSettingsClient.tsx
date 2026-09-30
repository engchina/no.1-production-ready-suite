"use client";

import {
  PageBody,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Button,
  Disclosure,
  FormStatus,
  Switch,
  TextField,
  TimedLoadingState,
  FormSkeleton,
} from "@engchina/production-ready-ui";
import { useState } from "react";
import {
  CheckCircle2,
  RotateCcw,
  Save,
  Scissors,
  SlidersHorizontal,
} from "lucide-react";

import { ErrorState } from "@/components/StateViews";
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
  SMALL_TO_BIG_PARAMS,
  type SmallToBigParamField,
  chunkSizeLabelKey,
  chunkingStrategyPreset,
  isSemanticBoundaryStrategy,
  overlapLabelKey,
} from "@/lib/chunking";
import { useLeaveGuard } from "@/lib/leave-guard";
import { focusFirstInvalidField, numberRangeError } from "@/lib/required-fields";
import { useValuesChanged } from "@/lib/render-sync";
import { t, type I18nKey } from "@/lib/i18n";
import { useChunkingSettings, useUpdateChunkingSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

type ChunkingForm = ChunkingSettingsUpdate;
type ChunkingParamField =
  | "chunk_size"
  | "overlap"
  | "min_chars"
  | "delimiter"
  | SmallToBigParamField;

// 親子階層（small-to-big）は、削除した「親子階層」があった位置(3 番目)に置く(#271)。
const STRATEGY_ORDER: ChunkingStrategyName[] = [
  "structure_aware",
  "recursive_character",
  "small_to_big",
  "markdown_heading",
  "page_level",
  "fixed_size",
  "fixed_delimiter",
];

const SMALL_TO_BIG_PARAM_FIELDS: SmallToBigParamField[] = SMALL_TO_BIG_PARAMS.map(
  (spec) => spec.field
);

const STRATEGY_PARAM_FIELDS: Record<ChunkingStrategyName, ChunkingParamField[]> = {
  structure_aware: ["chunk_size", "overlap", "min_chars"],
  recursive_character: ["chunk_size", "overlap", "min_chars"],
  // rag_poc の「チャンキング」tab と同じ 5 項目(子・表の子・親の文字数、親の最大ページ数・child 数)。
  small_to_big: SMALL_TO_BIG_PARAM_FIELDS,
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
  const [fieldErrors, setFieldErrors] = useState<ChunkingFieldErrors>({});

  // server 値か保存中フラグが変わったレンダーで、フォームを server 値に戻す。
  const serverChanged = useValuesChanged([query.data, save.isPending]);
  if (serverChanged && query.data && !save.isPending) {
    setForm(formFromSettings(query.data));
  }

  // 未保存の選択があるときだけ、サイドナビ・内部リンク・再読込での離脱を確認する。
  useLeaveGuard(Boolean(query.data && form && serializeForm(form) !== serializeForm(formFromSettings(query.data))));

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-chunking-load"
          placement="page"
          testId="settings-chunking-loading"
        >
          <FormSkeleton fields={2} />
          <FormSkeleton />
        </TimedLoadingState>
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
        <ErrorState
          message={
            query.error instanceof ApiError ? query.error.message : t("settings.chunking.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  const settings = query.data;
  if (!settings || !form) return null;

  const dirty = serializeForm(form) !== serializeForm(formFromSettings(settings));
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.chunking.saveError");
  const strategies = orderedStrategies(settings.strategies);

  function updateForm(update: Partial<ChunkingForm>) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, ...update } : current));
    // 直した欄のエラーだけを消す。分割方式を変えたときは欄が入れ替わるので全部消す。
    setFieldErrors((current) =>
      "strategy" in update
        ? {}
        : Object.fromEntries(Object.entries(current).filter(([field]) => !(field in update))),
    );
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    setForm(formFromSettings(settings));
    setFieldErrors({});
  }

  function submit() {
    if (!form) return;
    // 保存を押したときに欄ごとに検証し、欄の直下に出して最初のエラーの欄へ移す（#541）。
    const errors = validateForm(form);
    setFieldErrors(errors);
    if (focusFirstInvalidField(chunkingFieldOrder(form).map((field) => [chunkingFieldId(field), errors[field]] as const))) {
      return;
    }
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
    <PageBody wide>
      <OverviewCard
        dirty={dirty}
        form={form}
        strategies={strategies}
        saving={save.isPending}
        hasFieldErrors={Object.values(fieldErrors).some(Boolean)}
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
        errors={fieldErrors}
        onChange={updateForm}
      />
    </PageBody>
  );
}

function OverviewCard({
  dirty,
  form,
  strategies,
  saving,
  hasFieldErrors,
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
  /** 欄のエラーがあるか（内容は欄の直下に出す。ここでは未保存の表示を控えるだけ）。 */
  hasFieldErrors: boolean;
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
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
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
          <div className="text-sm font-medium text-fg">
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
                <div key={strategy.name} className="relative min-w-0">
                  <input
                    id={`settings-chunking-strategy-${strategy.name}`}
                    className="peer absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
                    type="radio"
                    name="settings-chunking-strategy"
                    value={strategy.name}
                    checked={selected}
                    disabled={saving}
                    onChange={() => onStrategyChange(strategy.name)}
                  />
                  <label
                    htmlFor={`settings-chunking-strategy-${strategy.name}`}
                    className={cn(
                      "block h-full cursor-pointer min-h-[6.57rem] rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus-ring peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                      selected
                        ? "border-accent-emphasis bg-accent-subtle text-fg"
                        : "border-border bg-surface text-fg peer-hover:bg-surface-hover"
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
                            <CheckCircle2 size={16} className="shrink-0 text-accent-fg" aria-hidden />
                          ) : null}
                        </span>
                        <span className="mt-1 block text-xs leading-relaxed text-fg-muted">
                          {strategyDescription(strategy.name)}
                        </span>
                        {strategy.recommended_for.length ? (
                          <span className="mt-2 block text-xs text-fg-muted">
                            {t("settings.chunking.recommendedFor")}:{" "}
                            {strategy.recommended_for.join(", ")}
                          </span>
                        ) : null}
                      </span>
                    </span>
                  </label>
                </div>
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
            {!hasFieldErrors && dirty ? (
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
              aria-label={t("settings.chunking.actions.reset")} icon={RotateCcw}>
              {t("settings.chunking.actions.reset")}
            </Button>
            <Button
              type="button"
              loading={saving}
              disabled={!dirty}
              onClick={onSubmit}
              aria-label={t("settings.chunking.actions.save")} icon={Save}>
              {t("settings.chunking.actions.save")}
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
  errors,
  onChange,
}: {
  form: ChunkingForm;
  saving: boolean;
  errors: ChunkingFieldErrors;
  onChange: (update: Partial<ChunkingForm>) => void;
}) {
  const fields = STRATEGY_PARAM_FIELDS[form.strategy];
  const hasField = (field: ChunkingParamField) => fields.includes(field);
  const semanticBoundary = isSemanticBoundaryStrategy(form.strategy);
  // 親子階層（small-to-big）は検索用テキストを分割側（rag_engine）が組み立てるため、文脈ヘッダは効かない。
  const smallToBig = form.strategy === "small_to_big";
  const chunkSizeField = hasField("chunk_size") ? (
    <NumberField
      id={chunkingFieldId("chunk_size")}
      error={errors.chunk_size}
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
      id={chunkingFieldId("overlap")}
      error={errors.overlap}
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
      id={chunkingFieldId("min_chars")}
      error={errors.min_chars}
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
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-subtle text-success-fg">
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
          {!smallToBig ? (
            <div className="flex items-start justify-between gap-4 rounded-md border border-border bg-surface p-3 md:col-span-2">
              <div className="min-w-0">
                <div className="text-sm font-medium text-fg">
                  {t("settings.chunking.params.contextHeader")}
                </div>
                <p className="mt-1 text-xs leading-relaxed text-fg-muted">
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
          ) : null}
          {smallToBig
            ? SMALL_TO_BIG_PARAMS.map((spec) => (
                <NumberField
                  key={spec.field}
                  id={chunkingFieldId(spec.field)}
                  error={errors[spec.field]}
                  label={t(spec.labelKey)}
                  value={form[spec.field]}
                  min={spec.min}
                  max={spec.max}
                  step={spec.step}
                  disabled={saving}
                  helper={t(spec.hintKey)}
                  onChange={(value) => onChange({ [spec.field]: value })}
                />
              ))
            : null}
          {hasField("delimiter") ? (
            <TextField
              id="chunking-delimiter"
              maxLength={256}
              label={t("settings.chunking.params.delimiter")}
              value={form.delimiter}
              disabled={saving}
              helper={t("settings.chunking.params.delimiterHint")}
              error={errors.delimiter ?? undefined}
              required
              onValueChange={(value) => onChange({ delimiter: value })}
            />
          ) : null}
          {semanticBoundary ? (
            <Disclosure
              key={form.strategy}
              summary={t("settings.chunking.params.semanticDetails")}
              surface="sunken"
              className="md:col-span-2"
            >
              <p className="mb-3 text-xs leading-relaxed text-fg-muted">
                {paramsDescription(form.strategy)}
              </p>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {chunkSizeField}
                {overlapField}
                {minCharsField}
              </div>
            </Disclosure>
          ) : (
            <>
              {chunkSizeField}
              {overlapField}
            </>
          )}
          {!semanticBoundary ? minCharsField : null}
        </div>
      </CardContent>
    </Card>
  );
}

function NumberField({
  id,
  error,
  label,
  value,
  min,
  max,
  step,
  disabled,
  helper,
  onChange,
}: {
  id: string;
  /** 欄の直下に出すエラー（保存を押したときの検証）。 */
  error?: string | null;
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  disabled: boolean;
  helper?: string;
  onChange: (value: number) => void;
}) {
  // 数値の欄は空では保存できない（validateForm が止め、backend も範囲を検証する）ので、すべて必須（#531）。
  return (
    <TextField
      id={id}
      label={label}
      helper={helper}
      error={error ?? undefined}
      required
      type="number"
      inputMode="numeric"
      value={Number.isFinite(value) ? value : ""}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onValueChange={(next) => onChange(Number.parseInt(next, 10))}
    />
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-hover p-3">
      <dt className="text-xs font-medium text-fg-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-fg">{value}</dd>
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
      className={cn("h-9 w-12 shrink-0", selected ? "text-accent-fg" : "text-fg-muted")}
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
    case "small_to_big":
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
  if (strategy === "small_to_big") {
    return t("settings.chunking.params.smallToBigDescription");
  }
  return t("settings.chunking.params.description");
}

function paramLabel(field: ChunkingParamField) {
  const keyByField: Record<ChunkingParamField, I18nKey> = {
    chunk_size: "settings.chunking.params.chunkSize",
    overlap: "settings.chunking.params.overlap",
    min_chars: "settings.chunking.params.minChars",
    delimiter: "settings.chunking.params.delimiter",
    ...Object.fromEntries(SMALL_TO_BIG_PARAMS.map((spec) => [spec.field, spec.labelKey])),
  } as Record<ChunkingParamField, I18nKey>;
  return t(keyByField[field]);
}

function paramValue(form: ChunkingForm, field: ChunkingParamField) {
  const value = form[field];
  return typeof value === "number" ? value.toLocaleString("ja-JP") : String(value);
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

type ChunkingErrorField = ChunkingParamField | SmallToBigParamField;
type ChunkingFieldErrors = Partial<Record<ChunkingErrorField, string | null>>;

/** 欄の id（送信に失敗したら最初のエラーの欄へフォーカスする）。 */
function chunkingFieldId(field: ChunkingErrorField): string {
  return field === "delimiter" ? "chunking-delimiter" : `chunking-${field.replaceAll("_", "-")}`;
}

/** 画面の並び順（親子階層の欄 → 分割符 → chunk サイズ → overlap → 最小文字数）。 */
function chunkingFieldOrder(form: ChunkingForm): ChunkingErrorField[] {
  const smallToBigFields = form.strategy === "small_to_big" ? SMALL_TO_BIG_PARAMS.map((spec) => spec.field) : [];
  return [...smallToBigFields, "delimiter", "chunk_size", "overlap", "min_chars"];
}

/**
 * 欄ごとの検証（#541）。規則は backend（ChunkingSettingsUpdate）と同じ。空の数値（NaN）を 0 として扱わない。
 * 文言は「〇〇を入力してください。」「〇〇は N 以上 M 以下の整数を入力してください。」の型。
 */
function validateForm(form: ChunkingForm): ChunkingFieldErrors {
  const fields = STRATEGY_PARAM_FIELDS[form.strategy];
  const hasField = (field: ChunkingParamField) => fields.includes(field);
  const errors: ChunkingFieldErrors = {};
  const chunkSizeLabel = t(chunkSizeLabelKey(form.strategy));
  const overlapLabel = t(overlapLabelKey(form.strategy));
  const minCharsLabel = t("settings.chunking.params.minChars");
  if (hasField("delimiter") && !form.delimiter.trim()) {
    errors.delimiter = t("validation.required", { field: t("settings.chunking.params.delimiter") });
  }
  if (hasField("chunk_size")) {
    errors.chunk_size = numberRangeError(form.chunk_size, {
      label: chunkSizeLabel,
      min: CHUNK_SIZE_MIN_CHARS,
      max: CHUNK_SIZE_MAX_CHARS,
    });
  }
  if (hasField("overlap")) {
    errors.overlap =
      numberRangeError(form.overlap, { label: overlapLabel, min: 0, max: CHUNK_OVERLAP_MAX_CHARS }) ??
      (!errors.chunk_size && hasField("chunk_size") && form.overlap >= form.chunk_size
        ? t("validation.lessThan", { field: overlapLabel, other: chunkSizeLabel })
        : null);
  }
  if (form.strategy === "small_to_big") {
    for (const spec of SMALL_TO_BIG_PARAMS) {
      errors[spec.field] = numberRangeError(form[spec.field], {
        label: t(spec.labelKey),
        min: spec.min,
        max: spec.max,
      });
    }
  }
  if (hasField("min_chars")) {
    errors.min_chars =
      numberRangeError(form.min_chars, { label: minCharsLabel, min: 0, max: 2000 }) ??
      (!errors.chunk_size && hasField("chunk_size") && form.min_chars >= form.chunk_size
        ? t("validation.lessThan", { field: minCharsLabel, other: chunkSizeLabel })
        : null);
  }
  return errors;
}

function formFromSettings(settings: ChunkingSettingsData): ChunkingForm {
  return {
    strategy: settings.strategy,
    chunk_size: settings.chunk_size,
    overlap: settings.overlap,
    min_chars: settings.min_chars,
    delimiter: settings.delimiter || "\\n\\n",
    context_header_enabled: settings.context_header_enabled,
    chunk_child_target_chars: settings.chunk_child_target_chars,
    chunk_table_child_target_chars: settings.chunk_table_child_target_chars,
    chunk_parent_target_chars: settings.chunk_parent_target_chars,
    chunk_parent_max_pages: settings.chunk_parent_max_pages,
    chunk_parent_max_children: settings.chunk_parent_max_children,
  };
}

function serializeForm(form: ChunkingForm) {
  return JSON.stringify({
    strategy: form.strategy,
    chunk_size: form.chunk_size,
    overlap: form.overlap,
    min_chars: form.min_chars,
    delimiter: form.delimiter,
    context_header_enabled: form.context_header_enabled,
    chunk_child_target_chars: form.chunk_child_target_chars,
    chunk_table_child_target_chars: form.chunk_table_child_target_chars,
    chunk_parent_target_chars: form.chunk_parent_target_chars,
    chunk_parent_max_pages: form.chunk_parent_max_pages,
    chunk_parent_max_children: form.chunk_parent_max_children,
  });
}
