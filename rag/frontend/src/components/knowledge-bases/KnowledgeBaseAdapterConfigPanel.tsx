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

/** 各カテゴリ選択肢(value と日本語ラベル)。グローバル設定画面の選択肢と整合させる。 */
const PREPROCESS_OPTIONS: SelectFieldOption<PreprocessProfileName>[] = [
  { value: "passthrough", label: "passthrough(変換なし)" },
  { value: "text_normalize", label: "text_normalize(テキスト正規化)" },
  { value: "office_to_pdf", label: "office_to_pdf(Office→PDF)" },
  { value: "pdf_to_page_images", label: "pdf_to_page_images(PDF→画像PDF)" },
  { value: "csv_to_json", label: "csv_to_json(CSV→JSON)" },
  { value: "excel_to_json", label: "excel_to_json(Excel→JSON)" },
];
const PARSER_OPTIONS: SelectFieldOption<ParserAdapterBackend>[] = [
  { value: "local", label: "local(内蔵パーサ)" },
  { value: "auto", label: "auto(source 別自動ルーティング)" },
  { value: "docling", label: "Docling" },
  { value: "marker", label: "Marker" },
  { value: "unstructured", label: "Unstructured" },
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

interface KnowledgeBaseAdapterConfigPanelProps {
  knowledgeBaseId: string;
  adapterConfig: KnowledgeBaseAdapterConfig;
  disabled?: boolean;
}

/** KB 単位のアダプター上書きを編集するパネル。継承を既定とし、上書き時のみ選択肢を表示する。 */
export function KnowledgeBaseAdapterConfigPanel({
  knowledgeBaseId,
  adapterConfig,
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
        <CardTitle className="flex items-center gap-2 text-base">
          <SlidersHorizontal className="size-4 text-muted" aria-hidden />
          {t("knowledgeBases.adapter.title")}
        </CardTitle>
        <CardDescription>{t("knowledgeBases.adapter.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {disabled ? (
          <FormStatus tone="info" message={t("knowledgeBases.adapter.archivedHint")} />
        ) : null}

        <section className="space-y-4" aria-label={t("knowledgeBases.adapter.section.ingestion")}>
          <SectionHeading
            title={t("knowledgeBases.adapter.section.ingestion")}
            hint={t("knowledgeBases.adapter.section.ingestionHint")}
          />
          <AdapterSelectRow
            id={`kb-adapter-preprocess-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.preprocessProfile")}
            value={form.ingestion.preprocess_profile}
            options={PREPROCESS_OPTIONS}
            disabled={disabled}
            defaultOnOverride="text_normalize"
            onChange={(value) => updateIngestion({ preprocess_profile: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-parser-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.parserBackend")}
            value={form.ingestion.parser_adapter_backend}
            options={PARSER_OPTIONS}
            disabled={disabled}
            defaultOnOverride="docling"
            onChange={(value) => updateIngestion({ parser_adapter_backend: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-chunking-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.chunkingStrategy")}
            value={form.ingestion.chunking_strategy}
            options={CHUNKING_OPTIONS}
            disabled={disabled}
            defaultOnOverride="markdown_heading"
            onChange={(value) => updateIngestion({ chunking_strategy: value })}
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
            options={RETRIEVAL_OPTIONS}
            disabled={disabled}
            defaultOnOverride="vector"
            onChange={(value) => updateQuery({ retrieval_strategy: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-grounding-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.postRetrievalPipeline")}
            value={form.query.post_retrieval_pipeline}
            options={GROUNDING_OPTIONS}
            disabled={disabled}
            defaultOnOverride="verified_context"
            onChange={(value) => updateQuery({ post_retrieval_pipeline: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-generation-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.generationProfile")}
            value={form.query.generation_profile}
            options={GENERATION_OPTIONS}
            disabled={disabled}
            defaultOnOverride="detailed_cited"
            onChange={(value) => updateQuery({ generation_profile: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-guardrail-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.guardrailPolicy")}
            value={form.query.guardrail_policy}
            options={GUARDRAIL_OPTIONS}
            disabled={disabled}
            defaultOnOverride="strict"
            onChange={(value) => updateQuery({ guardrail_policy: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-vector-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.vectorIndexProfile")}
            value={form.query.vector_index_profile}
            options={VECTOR_INDEX_OPTIONS}
            disabled={disabled}
            defaultOnOverride="accurate"
            onChange={(value) => updateQuery({ vector_index_profile: value })}
          />
          <AdapterSelectRow
            id={`kb-adapter-evaluation-${knowledgeBaseId}`}
            label={t("knowledgeBases.adapter.field.evaluationSuite")}
            value={form.query.evaluation_suite}
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
  options,
  defaultOnOverride,
  disabled = false,
  onChange,
}: AdapterSelectRowProps<T>) {
  const overriding = value !== null;
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
        <p className="text-xs text-muted">{t("knowledgeBases.adapter.inheritValue")}</p>
      )}
    </div>
  );
}

function serialize(config: KnowledgeBaseAdapterConfig): string {
  return JSON.stringify(config);
}
