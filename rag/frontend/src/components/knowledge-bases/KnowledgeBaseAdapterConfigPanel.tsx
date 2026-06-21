"use client";

import { useEffect, useMemo, useState } from "react";
import { RotateCcw, Save, SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import {
  ApiError,
  type ChunkingStrategyName,
  type EvaluationSuiteName,
  type GenerationProfileName,
  type GraphProfileName,
  type GuardrailPolicyName,
  type KnowledgeBaseAdapterConfig,
  type ParserAdapterBackend,
  type PreprocessProfileName,
  type PostRetrievalPipelineName,
  type RetrievalStrategyName,
  type VectorIndexProfileName,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useUpdateKnowledgeBase } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

/** 各カテゴリ選択肢(value と日本語ラベル)。グローバル設定画面の選択肢と整合させる。 */
const PREPROCESS_OPTIONS: SelectFieldOption<PreprocessProfileName>[] = [
  { value: "passthrough", label: "passthrough(変換なし)" },
  { value: "text_normalize", label: "text_normalize(テキスト正規化)" },
  { value: "office_to_pdf", label: "office_to_pdf(Office→PDF)" },
  { value: "pdf_to_page_images", label: "pdf_to_page_images(PDF→画像PDF)" },
  { value: "csv_to_json", label: "csv_to_json(CSV→JSON)" },
  { value: "excel_to_json", label: "excel_to_json(Excel→JSON)" },
];
// parser バックエンドはサービスとして起動できる parser microservice のみを並べる
// (local/auto は廃止)。グローバル設定の選択肢と整合させる。
const PARSER_OPTIONS: SelectFieldOption<ParserAdapterBackend>[] = [
  { value: "docling", label: "Docling" },
  { value: "marker", label: "Marker" },
  { value: "unstructured", label: "Unstructured" },
  { value: "mineru", label: "MinerU" },
  { value: "dots_ocr", label: "Dots.OCR" },
  { value: "glm_ocr", label: "GLM-OCR" },
  { value: "oci_genai_vision", label: "OCI Generative AI (Vision)" },
  { value: "oci_document_understanding", label: "OCI Document Understanding" },
];
const CHUNKING_OPTIONS: SelectFieldOption<ChunkingStrategyName>[] = [
  { value: "structure_aware", label: "structure_aware(構造認識)" },
  { value: "recursive_character", label: "recursive_character(固定長)" },
  { value: "sentence_window", label: "sentence_window(文単位)" },
  { value: "hierarchical_parent_child", label: "hierarchical_parent_child(親子)" },
  { value: "markdown_heading", label: "markdown_heading(章節)" },
  { value: "page_level", label: "page_level(ページ単位)" },
  { value: "fixed_size", label: "fixed_size(固定長)" },
];
const GRAPH_OPTIONS: SelectFieldOption<GraphProfileName>[] = [
  { value: "off", label: "off(構築なし)" },
  { value: "entities", label: "entities(エンティティ+関係)" },
  { value: "full", label: "full(claims+コミュニティ要約)" },
];
const RETRIEVAL_OPTIONS: SelectFieldOption<RetrievalStrategyName>[] = [
  { value: "hybrid_rrf", label: "hybrid_rrf(既定)" },
  { value: "vector", label: "vector" },
  { value: "keyword", label: "keyword" },
  { value: "graph_augmented", label: "graph_augmented" },
  { value: "select_ai_structured", label: "select_ai_structured" },
  { value: "business_context_strict", label: "business_context_strict" },
  { value: "corrective_multi_query", label: "corrective_multi_query" },
];
const GROUNDING_OPTIONS: SelectFieldOption<PostRetrievalPipelineName>[] = [
  { value: "custom", label: "custom(既定)" },
  { value: "lean", label: "lean" },
  { value: "verified_context", label: "verified_context" },
  { value: "context_enrich", label: "context_enrich" },
  { value: "compact", label: "compact" },
  { value: "full_governed", label: "full_governed" },
];
const GENERATION_OPTIONS: SelectFieldOption<GenerationProfileName>[] = [
  { value: "grounded_concise", label: "grounded_concise(既定)" },
  { value: "detailed_cited", label: "detailed_cited(出典明示)" },
  { value: "strict_extractive", label: "strict_extractive(抽出のみ)" },
  { value: "structured_json", label: "structured_json" },
  { value: "bilingual_ja_en", label: "bilingual_ja_en(日英)" },
];
const GUARDRAIL_OPTIONS: SelectFieldOption<GuardrailPolicyName>[] = [
  { value: "standard", label: "standard(既定)" },
  { value: "strict", label: "strict" },
  { value: "lenient", label: "lenient" },
  { value: "regulated", label: "regulated" },
];
const VECTOR_INDEX_OPTIONS: SelectFieldOption<VectorIndexProfileName>[] = [
  { value: "balanced", label: "balanced(既定)" },
  { value: "accurate", label: "accurate(高再現)" },
  { value: "fast", label: "fast(低レイテンシ)" },
];
const EVALUATION_OPTIONS: SelectFieldOption<EvaluationSuiteName>[] = [
  { value: "request_only", label: "request_only(既定)" },
  { value: "retrieval_focused", label: "retrieval_focused" },
  { value: "balanced", label: "balanced" },
  { value: "strict_ci", label: "strict_ci" },
  { value: "ragas_like", label: "ragas_like" },
];

/** パイプラインリボン(read-only 地図)の 1 段ぶんの表示データ。 */
interface RibbonStage {
  id: string;
  label: string;
  valueLabel: string;
  isOverride: boolean;
}

/** 上書き値 or 解決済み(継承)値からリボン 1 段の表示データを作る。 */
function resolveStage<T extends string>(
  id: string,
  label: string,
  override: T | null,
  effective: T | null,
  options: readonly SelectFieldOption<T>[]
): RibbonStage {
  const value = override ?? effective;
  const valueLabel =
    value !== null ? (options.find((option) => option.value === value)?.label ?? value) : "—";
  return { id, label, valueLabel, isOverride: override !== null };
}

/** ブール上書きの「有効/無効」ラベル。 */
function boolLabel(value: boolean): string {
  return value
    ? t("knowledgeBases.adapter.bool.enabled")
    : t("knowledgeBases.adapter.bool.disabled");
}

/** ブール軸からリボン 1 段の表示データを作る。 */
function resolveBoolStage(
  id: string,
  label: string,
  override: boolean | null,
  effective: boolean | null
): RibbonStage {
  const value = override ?? effective;
  const valueLabel = value !== null ? boolLabel(value) : "—";
  return { id, label, valueLabel, isOverride: override !== null };
}

interface KnowledgeBaseAdapterConfigPanelProps {
  knowledgeBaseId: string;
  adapterConfig: KnowledgeBaseAdapterConfig;
  /** グローバル既定で埋めた解決済み設定。継承行に「実際に効く値」を表示するために使う。 */
  effectiveConfig?: KnowledgeBaseAdapterConfig | null;
  disabled?: boolean;
}

/** 上書きサマリの対象段数(取込 7 + クエリ 6)。 */
const TOTAL_STAGES = 13;

/** 上書き対象 13 段のうち、非継承(上書き)の件数を数える。 */
function countOverrides(config: KnowledgeBaseAdapterConfig): number {
  const ingestion = [
    config.ingestion.preprocess_profile,
    config.ingestion.parser_adapter_backend,
    config.ingestion.chunking_strategy,
    config.ingestion.graph_profile,
    config.ingestion.field_extraction_enabled,
    config.ingestion.asset_summary_enabled,
    config.ingestion.navigation_summary_enabled,
  ];
  const query = [
    config.query.retrieval_strategy,
    config.query.post_retrieval_pipeline,
    config.query.generation_profile,
    config.query.guardrail_policy,
    config.query.vector_index_profile,
    config.query.evaluation_suite,
  ];
  return [...ingestion, ...query].filter((value) => value !== null).length;
}

/** KB 単位のアダプター上書きを編集するパネル。継承を既定とし、上書き時のみ選択肢を表示する。 */
export function KnowledgeBaseAdapterConfigPanel({
  knowledgeBaseId,
  adapterConfig,
  effectiveConfig = null,
  disabled = false,
}: KnowledgeBaseAdapterConfigPanelProps) {
  const save = useUpdateKnowledgeBase();
  const [form, setForm] = useState<KnowledgeBaseAdapterConfig>(adapterConfig);

  // KB を切り替えた、または保存完了で最新値が来たら form を同期する。
  useEffect(() => {
    if (!save.isPending) {
      setForm(adapterConfig);
    }
  }, [adapterConfig, save.isPending]);

  const dirty = useMemo(
    () => serialize(form) !== serialize(adapterConfig),
    [form, adapterConfig]
  );
  const overrideCount = useMemo(() => countOverrides(form), [form]);

  // パイプラインリボン(read-only 地図)。取込→検索のパイプライン順に各段の実効値を並べる。
  const ribbonIngest = useMemo<RibbonStage[]>(
    () => [
      resolveStage(
        "preprocess",
        t("knowledgeBases.adapter.field.preprocessProfile"),
        form.ingestion.preprocess_profile,
        effectiveConfig?.ingestion.preprocess_profile ?? null,
        PREPROCESS_OPTIONS
      ),
      resolveStage(
        "parser",
        t("knowledgeBases.adapter.field.parserBackend"),
        form.ingestion.parser_adapter_backend,
        effectiveConfig?.ingestion.parser_adapter_backend ?? null,
        PARSER_OPTIONS
      ),
      resolveStage(
        "chunking",
        t("knowledgeBases.adapter.field.chunkingStrategy"),
        form.ingestion.chunking_strategy,
        effectiveConfig?.ingestion.chunking_strategy ?? null,
        CHUNKING_OPTIONS
      ),
      resolveStage(
        "graph",
        t("knowledgeBases.adapter.field.graphProfile"),
        form.ingestion.graph_profile,
        effectiveConfig?.ingestion.graph_profile ?? null,
        GRAPH_OPTIONS
      ),
      resolveBoolStage(
        "field",
        t("knowledgeBases.adapter.field.fieldExtraction"),
        form.ingestion.field_extraction_enabled,
        effectiveConfig?.ingestion.field_extraction_enabled ?? null
      ),
      resolveBoolStage(
        "asset",
        t("knowledgeBases.adapter.field.assetSummary"),
        form.ingestion.asset_summary_enabled,
        effectiveConfig?.ingestion.asset_summary_enabled ?? null
      ),
      resolveBoolStage(
        "nav",
        t("knowledgeBases.adapter.field.navigationSummary"),
        form.ingestion.navigation_summary_enabled,
        effectiveConfig?.ingestion.navigation_summary_enabled ?? null
      ),
    ],
    [form.ingestion, effectiveConfig]
  );
  const ribbonQuery = useMemo<RibbonStage[]>(
    () => [
      resolveStage(
        "retrieval",
        t("knowledgeBases.adapter.field.retrievalStrategy"),
        form.query.retrieval_strategy,
        effectiveConfig?.query.retrieval_strategy ?? null,
        RETRIEVAL_OPTIONS
      ),
      resolveStage(
        "grounding",
        t("knowledgeBases.adapter.field.postRetrievalPipeline"),
        form.query.post_retrieval_pipeline,
        effectiveConfig?.query.post_retrieval_pipeline ?? null,
        GROUNDING_OPTIONS
      ),
      resolveStage(
        "generation",
        t("knowledgeBases.adapter.field.generationProfile"),
        form.query.generation_profile,
        effectiveConfig?.query.generation_profile ?? null,
        GENERATION_OPTIONS
      ),
      resolveStage(
        "guardrail",
        t("knowledgeBases.adapter.field.guardrailPolicy"),
        form.query.guardrail_policy,
        effectiveConfig?.query.guardrail_policy ?? null,
        GUARDRAIL_OPTIONS
      ),
      resolveStage(
        "vector",
        t("knowledgeBases.adapter.field.vectorIndexProfile"),
        form.query.vector_index_profile,
        effectiveConfig?.query.vector_index_profile ?? null,
        VECTOR_INDEX_OPTIONS
      ),
      resolveStage(
        "evaluation",
        t("knowledgeBases.adapter.field.evaluationSuite"),
        form.query.evaluation_suite,
        effectiveConfig?.query.evaluation_suite ?? null,
        EVALUATION_OPTIONS
      ),
    ],
    [form.query, effectiveConfig]
  );

  const updateIngestion = (patch: Partial<KnowledgeBaseAdapterConfig["ingestion"]>) =>
    setForm((current) => ({ ...current, ingestion: { ...current.ingestion, ...patch } }));
  const updateQuery = (patch: Partial<KnowledgeBaseAdapterConfig["query"]>) =>
    setForm((current) => ({ ...current, query: { ...current.query, ...patch } }));

  const handleSave = () => {
    save.mutate(
      { id: knowledgeBaseId, payload: { adapter_config: form } },
      {
        onSuccess: () => toast.success(t("knowledgeBases.adapter.toast.saved")),
      }
    );
  };

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("knowledgeBases.adapter.error.save");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <SlidersHorizontal className="size-4 text-muted" aria-hidden />
            {t("knowledgeBases.adapter.title")}
          </span>
          <span
            className={cn(
              "rounded-md px-2 py-0.5 text-xs font-medium",
              overrideCount > 0 ? "bg-info-bg text-info" : "bg-muted/10 text-muted"
            )}
          >
            {overrideCount > 0
              ? t("knowledgeBases.adapter.overrideCount", {
                  count: overrideCount,
                  total: TOTAL_STAGES,
                })
              : t("knowledgeBases.adapter.overrideNone")}
          </span>
        </CardTitle>
        <CardDescription>{t("knowledgeBases.adapter.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {disabled ? (
          <FormStatus tone="info" message={t("knowledgeBases.adapter.archivedHint")} />
        ) : null}

        <PipelineRibbon ingest={ribbonIngest} query={ribbonQuery} />

        <section className="space-y-4" aria-label={t("knowledgeBases.adapter.section.ingestion")}>
          <SectionHeading
            title={t("knowledgeBases.adapter.section.ingestion")}
            hint={t("knowledgeBases.adapter.section.ingestionHint")}
          />
          <AdapterSelectRow
            id={`kb-adapter-preprocess-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.preprocessProfile")}
            value={form.ingestion.preprocess_profile}
            effectiveValue={effectiveConfig?.ingestion.preprocess_profile ?? null}
            options={PREPROCESS_OPTIONS}
            disabled={disabled}
            defaultOnOverride="text_normalize"
            onChange={(value) => updateIngestion({ preprocess_profile: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-parser-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.parserBackend")}
            value={form.ingestion.parser_adapter_backend}
            effectiveValue={effectiveConfig?.ingestion.parser_adapter_backend ?? null}
            options={PARSER_OPTIONS}
            disabled={disabled}
            defaultOnOverride="docling"
            onChange={(value) => updateIngestion({ parser_adapter_backend: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-chunking-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.chunkingStrategy")}
            value={form.ingestion.chunking_strategy}
            effectiveValue={effectiveConfig?.ingestion.chunking_strategy ?? null}
            options={CHUNKING_OPTIONS}
            disabled={disabled}
            defaultOnOverride="markdown_heading"
            onChange={(value) => updateIngestion({ chunking_strategy: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-graph-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.graphProfile")}
            value={form.ingestion.graph_profile}
            effectiveValue={effectiveConfig?.ingestion.graph_profile ?? null}
            options={GRAPH_OPTIONS}
            disabled={disabled}
            defaultOnOverride="entities"
            onChange={(value) => updateIngestion({ graph_profile: value })}
          />
          <AdapterToggleRow
            id={`kb-adapter-field-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.fieldExtraction")}
            value={form.ingestion.field_extraction_enabled}
            effectiveValue={effectiveConfig?.ingestion.field_extraction_enabled ?? null}
            disabled={disabled}
            onChange={(value) => updateIngestion({ field_extraction_enabled: value })}
          />
          <AdapterToggleRow
            id={`kb-adapter-asset-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.assetSummary")}
            value={form.ingestion.asset_summary_enabled}
            effectiveValue={effectiveConfig?.ingestion.asset_summary_enabled ?? null}
            disabled={disabled}
            onChange={(value) => updateIngestion({ asset_summary_enabled: value })}
          />
          <AdapterToggleRow
            id={`kb-adapter-nav-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.navigationSummary")}
            value={form.ingestion.navigation_summary_enabled}
            effectiveValue={effectiveConfig?.ingestion.navigation_summary_enabled ?? null}
            disabled={disabled}
            onChange={(value) => updateIngestion({ navigation_summary_enabled: value })}
          />
        </section>

        <section className="space-y-4" aria-label={t("knowledgeBases.adapter.section.query")}>
          <SectionHeading
            title={t("knowledgeBases.adapter.section.query")}
            hint={t("knowledgeBases.adapter.section.queryHint")}
          />
          <AdapterSelectRow
            id={`kb-adapter-retrieval-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.retrievalStrategy")}
            value={form.query.retrieval_strategy}
            effectiveValue={effectiveConfig?.query.retrieval_strategy ?? null}
            options={RETRIEVAL_OPTIONS}
            disabled={disabled}
            defaultOnOverride="vector"
            onChange={(value) => updateQuery({ retrieval_strategy: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-grounding-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.postRetrievalPipeline")}
            value={form.query.post_retrieval_pipeline}
            effectiveValue={effectiveConfig?.query.post_retrieval_pipeline ?? null}
            options={GROUNDING_OPTIONS}
            disabled={disabled}
            defaultOnOverride="verified_context"
            onChange={(value) => updateQuery({ post_retrieval_pipeline: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-generation-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.generationProfile")}
            value={form.query.generation_profile}
            effectiveValue={effectiveConfig?.query.generation_profile ?? null}
            options={GENERATION_OPTIONS}
            disabled={disabled}
            defaultOnOverride="detailed_cited"
            onChange={(value) => updateQuery({ generation_profile: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-guardrail-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.guardrailPolicy")}
            value={form.query.guardrail_policy}
            effectiveValue={effectiveConfig?.query.guardrail_policy ?? null}
            options={GUARDRAIL_OPTIONS}
            disabled={disabled}
            defaultOnOverride="strict"
            onChange={(value) => updateQuery({ guardrail_policy: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-vector-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.vectorIndexProfile")}
            value={form.query.vector_index_profile}
            effectiveValue={effectiveConfig?.query.vector_index_profile ?? null}
            options={VECTOR_INDEX_OPTIONS}
            disabled={disabled}
            defaultOnOverride="accurate"
            onChange={(value) => updateQuery({ vector_index_profile: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-evaluation-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.evaluationSuite")}
            value={form.query.evaluation_suite}
            effectiveValue={effectiveConfig?.query.evaluation_suite ?? null}
            options={EVALUATION_OPTIONS}
            disabled={disabled}
            defaultOnOverride="strict_ci"
            onChange={(value) => updateQuery({ evaluation_suite: value })}
          />
        </section>

        {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}

        <div className="flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setForm(adapterConfig)}
            disabled={!dirty || save.isPending || disabled}
          >
            <RotateCcw className="size-4" aria-hidden />
            {t("knowledgeBases.adapter.actions.reset")}
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!dirty || save.isPending || disabled}
            loading={save.isPending}
          >
            <Save className="size-4" aria-hidden />
            {t("knowledgeBases.adapter.actions.save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** 取込→検索のパイプライン順に各段の実効値を一望する read-only 地図。編集は下のフォームで行う。 */
function PipelineRibbon({ ingest, query }: { ingest: RibbonStage[]; query: RibbonStage[] }) {
  return (
    <section
      aria-label={t("knowledgeBases.adapter.ribbon.title")}
      className="space-y-3 rounded-lg border border-border bg-background p-3"
    >
      <h3 className="text-sm font-semibold text-foreground">
        {t("knowledgeBases.adapter.ribbon.title")}
      </h3>
      <RibbonGroup label={t("knowledgeBases.adapter.ribbon.ingest")} stages={ingest} />
      <RibbonGroup label={t("knowledgeBases.adapter.ribbon.query")} stages={query} />
    </section>
  );
}

function RibbonGroup({ label, stages }: { label: string; stages: RibbonStage[] }) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs text-muted">{label}</p>
      <div className="flex flex-wrap gap-2">
        {stages.map((stage) => (
          <div
            key={stage.id}
            className={cn(
              "min-w-0 max-w-[12rem] rounded-md border px-2.5 py-1.5",
              stage.isOverride ? "border-info bg-info-bg/40" : "border-border bg-card"
            )}
          >
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[11px] text-muted">{stage.label}</span>
              {stage.isOverride ? (
                <span className="shrink-0 rounded-sm bg-info-bg px-1 text-[10px] font-medium text-info">
                  {t("knowledgeBases.adapter.ribbon.overrideBadge")}
                </span>
              ) : null}
            </div>
            <span className="block truncate text-xs font-medium text-foreground">
              {stage.valueLabel}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SectionHeading({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="text-xs text-muted">{hint}</p>
    </div>
  );
}

interface AdapterSelectRowProps<T extends string> {
  id: string;
  label: string;
  value: T | null;
  /** 継承時に「実際に効く値」として表示する解決済み値(グローバル既定 or 上書き)。 */
  effectiveValue?: T | null;
  options: readonly SelectFieldOption<T>[];
  defaultOnOverride: T;
  disabled?: boolean;
  onChange: (value: T | null) => void;
}

/** 継承/上書きトグル + 上書き時のみ表示する選択欄(段階的開示)。 */
function AdapterSelectRow<T extends string>({
  id,
  label,
  value,
  effectiveValue = null,
  options,
  defaultOnOverride,
  disabled = false,
  onChange,
}: AdapterSelectRowProps<T>) {
  const overriding = value !== null;
  // 継承時は解決済み値のラベルを引いて「実際に効く値」を見せる(値が無ければ汎用文言)。
  const resolvedLabel =
    effectiveValue !== null
      ? (options.find((option) => option.value === effectiveValue)?.label ?? effectiveValue)
      : null;
  return (
    <div className="space-y-2 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <div className="flex gap-1" role="group" aria-label={label}>
          <ToggleChip
            selected={!overriding}
            disabled={disabled}
            onClick={() => onChange(null)}
          >
            {t("knowledgeBases.adapter.inherit")}
          </ToggleChip>
          <ToggleChip
            selected={overriding}
            disabled={disabled}
            onClick={() => {
              if (!overriding) onChange(defaultOnOverride);
            }}
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
          onValueChange={(next) => onChange(next)}
          className="[&>label]:sr-only"
        />
      ) : (
        <p className="text-xs text-muted">
          {resolvedLabel !== null
            ? t("knowledgeBases.adapter.inheritResolved", { value: resolvedLabel })
            : t("knowledgeBases.adapter.inheritValue")}
        </p>
      )}
    </div>
  );
}

interface AdapterToggleRowProps {
  id: string;
  label: string;
  value: boolean | null;
  effectiveValue?: boolean | null;
  disabled?: boolean;
  onChange: (value: boolean | null) => void;
}

/** ブール軸の継承/上書きトグル + 上書き時の 有効/無効 選択(段階的開示)。 */
function AdapterToggleRow({
  id,
  label,
  value,
  effectiveValue = null,
  disabled = false,
  onChange,
}: AdapterToggleRowProps) {
  const overriding = value !== null;
  const resolvedLabel = effectiveValue !== null ? boolLabel(effectiveValue) : null;
  return (
    <div className="space-y-2 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span id={id} className="text-sm font-medium text-foreground">
          {label}
        </span>
        <div className="flex gap-1" role="group" aria-labelledby={id}>
          <ToggleChip selected={!overriding} disabled={disabled} onClick={() => onChange(null)}>
            {t("knowledgeBases.adapter.inherit")}
          </ToggleChip>
          <ToggleChip
            selected={overriding}
            disabled={disabled}
            onClick={() => {
              if (!overriding) onChange(true);
            }}
          >
            {t("knowledgeBases.adapter.override")}
          </ToggleChip>
        </div>
      </div>
      {overriding ? (
        <div className="flex gap-1" role="group" aria-labelledby={id}>
          <ToggleChip selected={value === true} disabled={disabled} onClick={() => onChange(true)}>
            {t("knowledgeBases.adapter.bool.enabled")}
          </ToggleChip>
          <ToggleChip
            selected={value === false}
            disabled={disabled}
            onClick={() => onChange(false)}
          >
            {t("knowledgeBases.adapter.bool.disabled")}
          </ToggleChip>
        </div>
      ) : (
        <p className="text-xs text-muted">
          {resolvedLabel !== null
            ? t("knowledgeBases.adapter.inheritResolved", { value: resolvedLabel })
            : t("knowledgeBases.adapter.inheritValue")}
        </p>
      )}
    </div>
  );
}

function serialize(config: KnowledgeBaseAdapterConfig): string {
  return JSON.stringify(config);
}
