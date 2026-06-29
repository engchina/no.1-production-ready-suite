"use client";

import { ChevronDown, RotateCcw, Save, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleChip } from "@/components/ui/toggle-chip";
import {
  ApiError,
  type ChunkingStrategyName,
  type DocumentIngestionConfigData,
  type DocumentProcessingConfig,
  type GraphProfileName,
  type ParserAdapterBackend,
  type PreprocessProfileName,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useUpdateDocumentIngestionConfig } from "@/lib/queries";
import { parserBackendLabel } from "@/lib/source-profile-labels";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const PREPROCESS_VALUES = [
  "passthrough",
  "office_to_pdf",
  "pdf_to_page_images",
  "csv_to_json",
  "excel_to_json",
  "url_to_markdown",
  "image_enhance",
  "pii_redact",
] as const;
const PREPROCESS_OPTIONS: SelectFieldOption<PreprocessProfileName>[] = PREPROCESS_VALUES.map(
  (value) => ({
    value,
    label: t(`settings.preprocess.profile.${value}` as I18nKey),
  })
);

const PARSER_VALUES = [
  "docling",
  "marker",
  "unstructured",
  "unlimited_ocr",
  "mineru",
  "dots_ocr",
  "glm_ocr",
  "oci_genai_vision",
  "oci_document_understanding",
] as const;
const PARSER_OPTIONS: SelectFieldOption<ParserAdapterBackend>[] = PARSER_VALUES.map((value) => ({
  value,
  label: parserBackendLabel(value),
}));

const CHUNKING_VALUES = [
  "structure_aware",
  "recursive_character",
  "sentence_window",
  "hierarchical_parent_child",
  "markdown_heading",
  "page_level",
  "fixed_size",
  "fixed_delimiter",
] as const;
const CHUNKING_OPTIONS: SelectFieldOption<ChunkingStrategyName>[] = CHUNKING_VALUES.map(
  (value) => ({ value, label: t(`settings.chunking.strategy.${value}` as I18nKey) })
);

const GRAPH_VALUES = ["off", "entities", "full"] as const;
const GRAPH_OPTIONS: SelectFieldOption<GraphProfileName>[] = GRAPH_VALUES.map(
  (value) => ({ value, label: t(`settings.graph.profile.${value}` as I18nKey) })
);

const EDITED_FIELDS: Array<keyof DocumentProcessingConfig> = [
  "preprocess_profile",
  "parser_adapter_backend",
  "chunking_strategy",
  "graph_profile",
  "field_extraction_enabled",
  "asset_summary_enabled",
  "navigation_summary_enabled",
  "auto_parse_after_preprocess_enabled",
  "auto_chunk_after_extract_enabled",
  "auto_index_after_chunk_enabled",
];

function emptyConfig(): DocumentProcessingConfig {
  return {
    preprocess_profile: null,
    parser_adapter_backend: null,
    parser_docling_enabled: null,
    parser_marker_enabled: null,
    parser_unstructured_enabled: null,
    parser_unlimited_ocr_enabled: null,
    parser_mineru_enabled: null,
    parser_dots_ocr_enabled: null,
    parser_glm_ocr_enabled: null,
    chunking_strategy: null,
    chunk_size: null,
    chunk_overlap: null,
    chunk_child_size: null,
    chunk_sentence_window_size: null,
    chunk_min_chars: null,
    graph_profile: null,
    field_extraction_enabled: null,
    asset_summary_enabled: null,
    navigation_summary_enabled: null,
    auto_parse_after_preprocess_enabled: null,
    auto_chunk_after_extract_enabled: null,
    auto_index_after_chunk_enabled: null,
  };
}

function resolvedConfigs(data: DocumentIngestionConfigData) {
  const processing = data.processing_config ?? emptyConfig();
  const effective = data.effective_processing_config ?? {
    ...emptyConfig(),
    preprocess_profile: data.effective_preprocess_profile,
    parser_adapter_backend: data.effective_parser_adapter_backend as ParserAdapterBackend,
    chunking_strategy: data.effective_chunking_strategy as ChunkingStrategyName,
  };
  return { processing, effective };
}

function boolLabel(value: boolean | null) {
  if (value === null) return "—";
  return t(value ? "knowledgeBases.adapter.bool.enabled" : "knowledgeBases.adapter.bool.disabled");
}

function optionLabel<T extends string>(
  value: T | null,
  options: readonly SelectFieldOption<T>[]
) {
  return value === null ? "—" : (options.find((option) => option.value === value)?.label ?? value);
}

type Stage = { key: string; label: string; value: string; overridden: boolean };

function stagesFor(
  processing: DocumentProcessingConfig,
  effective: DocumentProcessingConfig
): Stage[] {
  return [
    {
      key: "preprocess",
      label: t("knowledgeBases.adapter.field.preprocessProfile"),
      value: optionLabel(effective.preprocess_profile, PREPROCESS_OPTIONS),
      overridden: processing.preprocess_profile !== null,
    },
    {
      key: "parser",
      label: t("knowledgeBases.adapter.field.parserBackend"),
      value: optionLabel(effective.parser_adapter_backend, PARSER_OPTIONS),
      overridden: processing.parser_adapter_backend !== null,
    },
    {
      key: "chunking",
      label: t("knowledgeBases.adapter.field.chunkingStrategy"),
      value: optionLabel(effective.chunking_strategy, CHUNKING_OPTIONS),
      overridden: processing.chunking_strategy !== null,
    },
    {
      key: "graph",
      label: t("knowledgeBases.adapter.field.graphProfile"),
      value: optionLabel(effective.graph_profile, GRAPH_OPTIONS),
      overridden: processing.graph_profile !== null,
    },
    ...(
      [
        ["field", "fieldExtraction", "field_extraction_enabled"],
        ["asset", "assetSummary", "asset_summary_enabled"],
        ["navigation", "navigationSummary", "navigation_summary_enabled"],
        ["auto-parse", "autoParseAfterPreprocess", "auto_parse_after_preprocess_enabled"],
        ["auto-chunk", "autoChunkAfterExtract", "auto_chunk_after_extract_enabled"],
        ["auto-index", "autoIndexAfterChunk", "auto_index_after_chunk_enabled"],
      ] as const
    ).map(([key, label, field]) => ({
      key,
      label: t(`knowledgeBases.adapter.field.${label}`),
      value: boolLabel(effective[field]),
      overridden: processing[field] !== null,
    })),
  ];
}

export function DocumentProcessingConfigPanel({
  documentId,
  data,
  loading,
  error,
  onRetry,
  disabled,
}: {
  documentId: string;
  data: DocumentIngestionConfigData | null;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  disabled: boolean;
}) {
  const save = useUpdateDocumentIngestionConfig();
  const [expanded, setExpanded] = useState(false);
  const configs = useMemo(() => (data ? resolvedConfigs(data) : null), [data]);
  const [form, setForm] = useState<DocumentProcessingConfig>(emptyConfig);

  useEffect(() => {
    if (configs) setForm(configs.processing);
  }, [configs]);

  const dirty = configs ? JSON.stringify(form) !== JSON.stringify(configs.processing) : false;
  const overrideCount = EDITED_FIELDS.filter((field) => form[field] !== null).length;
  const stages = useMemo(
    () => (configs ? stagesFor(form, configs.effective) : []),
    [configs, form]
  );

  const update = (patch: Partial<DocumentProcessingConfig>) =>
    setForm((current) => ({ ...current, ...patch }));

  const handleSave = () => {
    save.mutate(
      { id: documentId, config: form },
      {
        onSuccess: () => toast.success(t("documents.processingConfig.toast.saved")),
      }
    );
  };

  return (
    <section
      aria-label={t("flow.buildConfig.title")}
      className="rounded-md border border-border bg-background p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <SlidersHorizontal size={15} className="text-primary" aria-hidden />
            {t("flow.buildConfig.title")}
          </h3>
          <p className="mt-1 text-xs text-muted">{t("documents.processingConfig.subtitle")}</p>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          disabled={loading || Boolean(error) || !data}
          className="min-h-9 shrink-0"
        >
          <ChevronDown
            size={14}
            className={cn("transition-transform", expanded && "rotate-180")}
            aria-hidden
          />
          {t(expanded ? "documents.processingConfig.actions.close" : "documents.processingConfig.actions.edit")}
        </Button>
      </div>

      {loading ? (
        <div className="mt-3 space-y-2" role="status" aria-label={t("flow.buildConfig.loading")}>
          <Skeleton className="h-20 w-full" />
          <span className="sr-only">{t("flow.buildConfig.loading")}</span>
        </div>
      ) : error ? (
        <Banner severity="warning" title={t("flow.buildConfig.loadError")} className="mt-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p>{error instanceof ApiError ? error.message : t("flow.buildConfig.loadErrorHint")}</p>
            <Button type="button" variant="secondary" size="sm" onClick={onRetry}>
              <RotateCcw size={14} aria-hidden />
              {t("common.retry")}
            </Button>
          </div>
        </Banner>
      ) : configs ? (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-5">
            {stages.map((stage) => (
              <div
                key={stage.key}
                className={cn(
                  "min-w-0 rounded-md border px-2.5 py-2",
                  stage.overridden ? "border-info/40 bg-info-bg/40" : "border-border bg-card"
                )}
              >
                <div className="flex min-w-0 items-start justify-between gap-1">
                  <span className="min-w-0 text-[11px] leading-4 text-muted">{stage.label}</span>
                  {stage.overridden ? (
                    <span className="shrink-0 rounded-sm bg-info-bg px-1 text-[10px] font-medium text-info">
                      {t("knowledgeBases.adapter.ribbon.overrideBadge")}
                    </span>
                  ) : null}
                </div>
                <span className="mt-0.5 block break-words text-xs font-medium text-foreground">
                  {stage.value}
                </span>
              </div>
            ))}
          </div>

          {expanded ? (
            <div className="mt-4 space-y-4 border-t border-border pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted">{t("documents.processingConfig.editHint")}</p>
                <span className="rounded-md bg-muted/10 px-2 py-1 text-xs font-medium text-muted">
                  {overrideCount > 0
                    ? t("knowledgeBases.adapter.overrideCount", { count: overrideCount, total: 10 })
                    : t("knowledgeBases.adapter.overrideNone")}
                </span>
              </div>

              {disabled ? (
                <FormStatus tone="info" message={t("documents.processingConfig.blocked")} />
              ) : null}

              <div className="grid gap-3 lg:grid-cols-2">
                <SelectRow
                  id={`document-preprocess-${documentId}`}
                  label={t("knowledgeBases.adapter.field.preprocessProfile")}
                  value={form.preprocess_profile}
                  effectiveValue={configs.effective.preprocess_profile}
                  options={PREPROCESS_OPTIONS}
                  defaultValue="passthrough"
                  disabled={disabled}
                  onChange={(value) => update({ preprocess_profile: value })}
                />
                <SelectRow
                  id={`document-parser-${documentId}`}
                  label={t("knowledgeBases.adapter.field.parserBackend")}
                  value={form.parser_adapter_backend}
                  effectiveValue={configs.effective.parser_adapter_backend}
                  options={PARSER_OPTIONS}
                  defaultValue="docling"
                  disabled={disabled}
                  onChange={(value) => update({ parser_adapter_backend: value })}
                />
                <SelectRow
                  id={`document-chunking-${documentId}`}
                  label={t("knowledgeBases.adapter.field.chunkingStrategy")}
                  value={form.chunking_strategy}
                  effectiveValue={configs.effective.chunking_strategy}
                  options={CHUNKING_OPTIONS}
                  defaultValue="structure_aware"
                  disabled={disabled}
                  onChange={(value) => update({ chunking_strategy: value })}
                />
                <SelectRow
                  id={`document-graph-${documentId}`}
                  label={t("knowledgeBases.adapter.field.graphProfile")}
                  value={form.graph_profile}
                  effectiveValue={configs.effective.graph_profile}
                  options={GRAPH_OPTIONS}
                  defaultValue="off"
                  disabled={disabled}
                  onChange={(value) => update({ graph_profile: value })}
                />
                <BooleanRow
                  id={`document-field-${documentId}`}
                  label={t("knowledgeBases.adapter.field.fieldExtraction")}
                  value={form.field_extraction_enabled}
                  effectiveValue={configs.effective.field_extraction_enabled}
                  disabled={disabled}
                  onChange={(value) => update({ field_extraction_enabled: value })}
                />
                <BooleanRow
                  id={`document-asset-${documentId}`}
                  label={t("knowledgeBases.adapter.field.assetSummary")}
                  value={form.asset_summary_enabled}
                  effectiveValue={configs.effective.asset_summary_enabled}
                  disabled={disabled}
                  onChange={(value) => update({ asset_summary_enabled: value })}
                />
                <BooleanRow
                  id={`document-navigation-${documentId}`}
                  label={t("knowledgeBases.adapter.field.navigationSummary")}
                  value={form.navigation_summary_enabled}
                  effectiveValue={configs.effective.navigation_summary_enabled}
                  disabled={disabled}
                  onChange={(value) => update({ navigation_summary_enabled: value })}
                />
                <BooleanRow
                  id={`document-auto-parse-${documentId}`}
                  label={t("knowledgeBases.adapter.field.autoParseAfterPreprocess")}
                  value={form.auto_parse_after_preprocess_enabled}
                  effectiveValue={configs.effective.auto_parse_after_preprocess_enabled}
                  disabled={disabled}
                  onChange={(value) => update({ auto_parse_after_preprocess_enabled: value })}
                />
                <BooleanRow
                  id={`document-auto-chunk-${documentId}`}
                  label={t("knowledgeBases.adapter.field.autoChunkAfterExtract")}
                  value={form.auto_chunk_after_extract_enabled}
                  effectiveValue={configs.effective.auto_chunk_after_extract_enabled}
                  disabled={disabled}
                  onChange={(value) => update({ auto_chunk_after_extract_enabled: value })}
                />
                <BooleanRow
                  id={`document-auto-index-${documentId}`}
                  label={t("knowledgeBases.adapter.field.autoIndexAfterChunk")}
                  value={form.auto_index_after_chunk_enabled}
                  effectiveValue={configs.effective.auto_index_after_chunk_enabled}
                  disabled={disabled}
                  onChange={(value) => update({ auto_index_after_chunk_enabled: value })}
                />
              </div>

              {save.isError ? (
                <FormStatus
                  tone="danger"
                  message={
                    save.error instanceof ApiError
                      ? save.error.message
                      : t("documents.processingConfig.error.save")
                  }
                />
              ) : null}

              <div className="flex flex-wrap items-center justify-end gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="md"
                  onClick={() => setForm(configs.processing)}
                  disabled={!dirty || save.isPending || disabled}
                >
                  <RotateCcw size={15} aria-hidden />
                  {t("knowledgeBases.adapter.actions.reset")}
                </Button>
                <Button
                  type="button"
                  size="md"
                  onClick={handleSave}
                  loading={save.isPending}
                  disabled={!dirty || disabled}
                >
                  <Save size={15} aria-hidden />
                  {t("knowledgeBases.adapter.actions.save")}
                </Button>
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function SelectRow<T extends string>({
  id,
  label,
  value,
  effectiveValue,
  options,
  defaultValue,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: T | null;
  effectiveValue: T | null;
  options: readonly SelectFieldOption<T>[];
  defaultValue: T;
  disabled: boolean;
  onChange: (value: T | null) => void;
}) {
  const overriding = value !== null;
  const lastOverride = useRef<T>(value ?? effectiveValue ?? defaultValue);
  useEffect(() => {
    if (value !== null) lastOverride.current = value;
  }, [value]);
  return (
    <div className="space-y-2 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <div className="flex flex-wrap gap-1" role="group" aria-label={label}>
          <ToggleChip selected={!overriding} disabled={disabled} onClick={() => onChange(null)}>
            {t("knowledgeBases.adapter.inherit")}
          </ToggleChip>
          <ToggleChip
            selected={overriding}
            disabled={disabled}
            onClick={() => !overriding && onChange(lastOverride.current)}
          >
            {t("knowledgeBases.adapter.override")}
          </ToggleChip>
        </div>
      </div>
      {overriding ? (
        <SelectField
          id={id}
          label={label}
          value={value}
          options={options}
          onValueChange={onChange}
          className="[&>label]:sr-only"
          buttonClassName="min-h-11"
        />
      ) : (
        <p className="text-xs text-muted">
          {t("knowledgeBases.adapter.inheritResolved", {
            value: optionLabel(effectiveValue, options),
          })}
        </p>
      )}
    </div>
  );
}

function BooleanRow({
  id,
  label,
  value,
  effectiveValue,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: boolean | null;
  effectiveValue: boolean | null;
  disabled: boolean;
  onChange: (value: boolean | null) => void;
}) {
  const overriding = value !== null;
  const lastOverride = useRef(value ?? effectiveValue ?? true);
  useEffect(() => {
    if (value !== null) lastOverride.current = value;
  }, [value]);
  return (
    <div className="space-y-2 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span id={id} className="text-sm font-medium text-foreground">
          {label}
        </span>
        <div className="flex flex-wrap gap-1" role="group" aria-labelledby={id}>
          <ToggleChip selected={!overriding} disabled={disabled} onClick={() => onChange(null)}>
            {t("knowledgeBases.adapter.inherit")}
          </ToggleChip>
          <ToggleChip
            selected={overriding}
            disabled={disabled}
            onClick={() => !overriding && onChange(lastOverride.current)}
          >
            {t("knowledgeBases.adapter.override")}
          </ToggleChip>
        </div>
      </div>
      {overriding ? (
        <div className="flex flex-wrap gap-1" role="group" aria-labelledby={id}>
          <ToggleChip selected={value === true} disabled={disabled} onClick={() => onChange(true)}>
            {t("knowledgeBases.adapter.bool.enabled")}
          </ToggleChip>
          <ToggleChip selected={value === false} disabled={disabled} onClick={() => onChange(false)}>
            {t("knowledgeBases.adapter.bool.disabled")}
          </ToggleChip>
        </div>
      ) : (
        <p className="text-xs text-muted">
          {t("knowledgeBases.adapter.inheritResolved", { value: boolLabel(effectiveValue) })}
        </p>
      )}
    </div>
  );
}
