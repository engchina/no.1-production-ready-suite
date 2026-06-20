"use client";

import { Archive, Pencil, Sparkles, UserCog } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";

import { PageHeader } from "@/components/PageHeader";
import { DegradedBanner } from "@/components/DegradedBanner";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { KnowledgeBaseScopePicker } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  type BusinessViewConfig,
  type BusinessViewDetail,
  type BusinessViewStatus,
  type BusinessViewSummary,
  type EvaluationSuiteName,
  type GenerationProfileName,
  type GuardrailPolicyName,
  type KnowledgeBaseQueryConfig,
  type PostRetrievalPipelineName,
  type RetrievalStrategyName,
  type VectorIndexProfileName,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useArchiveBusinessView,
  useBusinessView,
  useBusinessViews,
  useCreateBusinessView,
  useUpdateBusinessView,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const LIMIT = 20;
const FILTERS: (BusinessViewStatus | "ALL")[] = ["ALL", "ACTIVE", "ARCHIVED"];
const NAME_ERROR_ID = "business-view-name-error";
const SCOPE_ERROR_ID = "business-view-scope-error";

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

function emptyQueryConfig(): KnowledgeBaseQueryConfig {
  return {
    retrieval_strategy: null,
    post_retrieval_pipeline: null,
    generation_profile: null,
    guardrail_policy: null,
    vector_index_profile: null,
    evaluation_suite: null,
  };
}

function emptyConfig(): BusinessViewConfig {
  return {
    version: 1,
    knowledge_base_ids: [],
    query: emptyQueryConfig(),
    system_prompt: null,
    default_language: null,
  };
}

/** 業務アシスタント(Business View)管理。複数 KB を業務視点で束ね、方針と persona を設定する。 */
export function BusinessViewManagementClient() {
  const confirm = useConfirm();
  const [filter, setFilter] = useState<BusinessViewStatus | "ALL">("ACTIVE");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [editingId, setEditingId] = useState<string | null>(null);

  const status = filter === "ALL" ? undefined : filter;
  const query = useBusinessViews({ status, q: q || undefined, limit: LIMIT, offset });
  const page = query.data;
  const items = useMemo(() => page?.items ?? [], [page?.items]);
  const archive = useArchiveBusinessView();
  const editingDetail = useBusinessView(editingId);

  const handleArchive = async (view: BusinessViewSummary) => {
    const ok = await confirm({
      title: t("businessViews.confirm.archive.title"),
      description: t("businessViews.confirm.archive.description", { name: view.name }),
      confirmLabel: t("businessViews.actions.archive"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    archive.mutate(view.id, {
      onSuccess: () => {
        if (editingId === view.id) setEditingId(null);
        toast.success(t("businessViews.toast.archived"));
      },
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t("businessViews.error.archive")),
    });
  };

  return (
    <div>
      <PageHeader title={t("nav.businessViews")} subtitle={t("businessViews.subtitle")} />
      <div className="grid grid-cols-1 gap-5 p-8">
        <DegradedBanner
          messages={page?.warning_messages}
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />

        {editingId && editingDetail.data ? (
          <BusinessViewForm
            key={editingId}
            mode="edit"
            initial={editingDetail.data}
            onDone={() => setEditingId(null)}
            onCancel={() => setEditingId(null)}
          />
        ) : (
          <BusinessViewForm mode="create" onDone={() => setOffset(0)} />
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div
            className="flex flex-wrap items-center gap-1"
            role="group"
            aria-label={t("businessViews.filter.aria")}
          >
            {FILTERS.map((item) => (
              <ToggleChip
                key={item}
                selected={filter === item}
                onClick={() => {
                  setFilter(item);
                  setOffset(0);
                }}
              >
                {item === "ALL"
                  ? t("businessViews.filter.all")
                  : item === "ACTIVE"
                    ? t("businessViews.filter.active")
                    : t("businessViews.filter.archived")}
              </ToggleChip>
            ))}
          </div>
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setQ(search.trim());
              setOffset(0);
            }}
          >
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("businessViews.search.placeholder")}
              aria-label={t("businessViews.search.placeholder")}
              className="h-9 w-56 rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary"
            />
            <Button size="sm" variant="secondary" type="submit">
              {t("businessViews.search.placeholder")}
            </Button>
          </form>
        </div>

        {query.isError ? (
          <ErrorState
            message={
              query.error instanceof ApiError ? query.error.message : t("businessViews.error.title")
            }
            onRetry={() => void query.refetch()}
          />
        ) : items.length === 0 && !query.isFetching ? (
          <EmptyState
            title={t("businessViews.empty.title")}
            hint={t("businessViews.empty.description")}
          />
        ) : (
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {items.map((view) => (
              <BusinessViewCard
                key={view.id}
                view={view}
                archiving={archive.isPending}
                onEdit={() => setEditingId(view.id)}
                onArchive={() => void handleArchive(view)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function BusinessViewCard({
  view,
  archiving,
  onEdit,
  onArchive,
}: {
  view: BusinessViewSummary;
  archiving: boolean;
  onEdit: () => void;
  onArchive: () => void;
}) {
  const isArchived = view.status === "ARCHIVED";
  return (
    <li className="flex min-w-0 flex-col rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 truncate font-medium text-foreground">
            <UserCog size={15} className="shrink-0 text-primary" aria-hidden />
            <span className="truncate">{view.name}</span>
          </p>
          {view.description ? (
            <p className="mt-1 line-clamp-2 text-xs text-muted">{view.description}</p>
          ) : null}
        </div>
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
            isArchived ? "bg-muted/15 text-muted" : "bg-success-bg text-success"
          )}
        >
          {t(`businessViews.status.${view.status}` as const)}
        </span>
      </div>
      <dl className="mt-3 space-y-1 text-xs text-muted">
        <div>{t("businessViews.list.knowledgeBaseCount", { count: view.knowledge_base_count })}</div>
        <div>{t("businessViews.list.updatedAt", { value: formatDateTime(view.updated_at) })}</div>
      </dl>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button size="sm" variant="secondary" onClick={onEdit} disabled={isArchived}>
          <Pencil size={14} aria-hidden />
          {t("businessViews.actions.edit")}
        </Button>
        {!isArchived ? (
          <Button size="sm" variant="ghost" onClick={onArchive} disabled={archiving}>
            <Archive size={14} aria-hidden />
            {t("businessViews.actions.archive")}
          </Button>
        ) : null}
      </div>
    </li>
  );
}

function BusinessViewForm({
  mode,
  initial,
  onDone,
  onCancel,
}: {
  mode: "create" | "edit";
  initial?: BusinessViewDetail;
  onDone: (id: string) => void;
  onCancel?: () => void;
}) {
  const create = useCreateBusinessView();
  const update = useUpdateBusinessView();
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [config, setConfig] = useState<BusinessViewConfig>(initial?.config ?? emptyConfig());
  const [touched, setTouched] = useState(false);

  const pending = create.isPending || update.isPending;
  const nameError = touched && !name.trim() ? t("businessViews.nameRequired") : null;
  const scopeError =
    touched && config.knowledge_base_ids.length === 0
      ? t("businessViews.knowledgeBasesRequired")
      : null;

  const updateQuery = (patch: Partial<KnowledgeBaseQueryConfig>) =>
    setConfig((current) => ({ ...current, query: { ...current.query, ...patch } }));

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setTouched(true);
    if (!name.trim() || config.knowledge_base_ids.length === 0) return;
    const payload = {
      name: name.trim(),
      description: description.trim() || null,
      config,
    };
    if (mode === "edit" && initial) {
      update.mutate(
        { id: initial.id, payload },
        {
          onSuccess: (detail) => {
            toast.success(t("businessViews.toast.updated"));
            onDone(detail.id);
          },
          onError: (error) =>
            toast.error(
              error instanceof ApiError ? error.message : t("businessViews.error.update")
            ),
        }
      );
      return;
    }
    create.mutate(payload, {
      onSuccess: (detail) => {
        setName("");
        setDescription("");
        setConfig(emptyConfig());
        setTouched(false);
        toast.success(t("businessViews.toast.created"));
        onDone(detail.id);
      },
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t("businessViews.error.create")),
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles size={18} className="text-primary" aria-hidden />
          {mode === "edit" ? t("businessViews.edit.title") : t("businessViews.create.title")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-5">
          <div className="grid gap-3 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
            <div>
              <label htmlFor="business-view-name" className="text-sm font-medium text-foreground">
                {t("businessViews.field.name")}
              </label>
              <input
                id="business-view-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                onBlur={() => setTouched(true)}
                placeholder={t("businessViews.field.namePlaceholder")}
                aria-invalid={Boolean(nameError)}
                aria-describedby={nameError ? NAME_ERROR_ID : undefined}
                className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary"
              />
              <FieldError id={NAME_ERROR_ID} message={nameError} className="mt-1" />
            </div>
            <div>
              <label
                htmlFor="business-view-description"
                className="text-sm font-medium text-foreground"
              >
                {t("businessViews.field.description")}
              </label>
              <input
                id="business-view-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder={t("businessViews.field.descriptionPlaceholder")}
                className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary"
              />
            </div>
          </div>

          <div>
            <KnowledgeBaseScopePicker
              selectedIds={config.knowledge_base_ids}
              onChange={(ids) => setConfig((current) => ({ ...current, knowledge_base_ids: ids }))}
              disabled={pending}
              label={t("businessViews.field.knowledgeBases")}
              helper={t("businessViews.field.knowledgeBasesHelper")}
              emptySelectionText={t("businessViews.knowledgeBasesRequired")}
            />
            <FieldError id={SCOPE_ERROR_ID} message={scopeError} className="mt-1" />
          </div>

          <fieldset className="space-y-3 rounded-lg border border-border p-4">
            <legend className="px-1 text-sm font-semibold text-foreground">persona</legend>
            <div>
              <label
                htmlFor="business-view-system-prompt"
                className="text-sm font-medium text-foreground"
              >
                {t("businessViews.field.systemPrompt")}
              </label>
              <textarea
                id="business-view-system-prompt"
                value={config.system_prompt ?? ""}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    system_prompt: event.target.value || null,
                  }))
                }
                placeholder={t("businessViews.field.systemPromptPlaceholder")}
                rows={3}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:border-primary"
              />
              <p className="mt-1 text-xs text-muted">
                {t("businessViews.field.systemPromptHelper")}
              </p>
            </div>
            <div className="max-w-xs">
              <label
                htmlFor="business-view-language"
                className="text-sm font-medium text-foreground"
              >
                {t("businessViews.field.defaultLanguage")}
              </label>
              <input
                id="business-view-language"
                value={config.default_language ?? ""}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    default_language: event.target.value || null,
                  }))
                }
                placeholder={t("businessViews.field.defaultLanguagePlaceholder")}
                className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary"
              />
            </div>
          </fieldset>

          <fieldset className="space-y-3 rounded-lg border border-border p-4">
            <legend className="px-1 text-sm font-semibold text-foreground">
              {t("businessViews.query.title")}
            </legend>
            <p className="text-xs text-muted">{t("businessViews.query.helper")}</p>
            <div className="grid gap-3 md:grid-cols-2">
              <QuerySelectRow
                id="business-view-retrieval"
                label={t("businessViews.field.retrieval")}
                value={config.query.retrieval_strategy}
                options={RETRIEVAL_OPTIONS}
                defaultOnOverride="vector"
                disabled={pending}
                onChange={(value) => updateQuery({ retrieval_strategy: value })}
              />
              <QuerySelectRow
                id="business-view-grounding"
                label={t("businessViews.field.grounding")}
                value={config.query.post_retrieval_pipeline}
                options={GROUNDING_OPTIONS}
                defaultOnOverride="verified_context"
                disabled={pending}
                onChange={(value) => updateQuery({ post_retrieval_pipeline: value })}
              />
              <QuerySelectRow
                id="business-view-generation"
                label={t("businessViews.field.generation")}
                value={config.query.generation_profile}
                options={GENERATION_OPTIONS}
                defaultOnOverride="detailed_cited"
                disabled={pending}
                onChange={(value) => updateQuery({ generation_profile: value })}
              />
              <QuerySelectRow
                id="business-view-guardrail"
                label={t("businessViews.field.guardrail")}
                value={config.query.guardrail_policy}
                options={GUARDRAIL_OPTIONS}
                defaultOnOverride="strict"
                disabled={pending}
                onChange={(value) => updateQuery({ guardrail_policy: value })}
              />
              <QuerySelectRow
                id="business-view-vector-index"
                label={t("businessViews.field.vectorIndex")}
                value={config.query.vector_index_profile}
                options={VECTOR_INDEX_OPTIONS}
                defaultOnOverride="accurate"
                disabled={pending}
                onChange={(value) => updateQuery({ vector_index_profile: value })}
              />
              <QuerySelectRow
                id="business-view-evaluation"
                label={t("businessViews.field.evaluation")}
                value={config.query.evaluation_suite}
                options={EVALUATION_OPTIONS}
                defaultOnOverride="balanced"
                disabled={pending}
                onChange={(value) => updateQuery({ evaluation_suite: value })}
              />
            </div>
          </fieldset>

          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            <Button size="lg" loading={pending} type="submit">
              <Sparkles size={16} aria-hidden />
              {mode === "edit"
                ? t("businessViews.actions.save")
                : t("businessViews.actions.create")}
            </Button>
            {mode === "edit" && onCancel ? (
              <Button size="lg" variant="ghost" type="button" onClick={onCancel} disabled={pending}>
                {t("businessViews.actions.cancel")}
              </Button>
            ) : null}
            <FormStatus
              tone="danger"
              message={
                create.isError
                  ? create.error instanceof ApiError
                    ? create.error.message
                    : t("businessViews.error.create")
                  : update.isError
                    ? update.error instanceof ApiError
                      ? update.error.message
                      : t("businessViews.error.update")
                    : null
              }
            />
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

/** 継承/上書きトグル + 上書き時のみ表示する選択欄(段階的開示)。 */
function QuerySelectRow<T extends string>({
  id,
  label,
  value,
  options,
  defaultOnOverride,
  disabled = false,
  onChange,
}: {
  id: string;
  label: string;
  value: T | null;
  options: readonly SelectFieldOption<T>[];
  defaultOnOverride: T;
  disabled?: boolean;
  onChange: (value: T | null) => void;
}) {
  const overriding = value !== null;
  return (
    <div className="space-y-2 rounded-lg border border-border bg-background p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <div className="flex gap-1" role="group" aria-label={label}>
          <ToggleChip selected={!overriding} disabled={disabled} onClick={() => onChange(null)}>
            {t("businessViews.inherit")}
          </ToggleChip>
          <ToggleChip
            selected={overriding}
            disabled={disabled}
            onClick={() => {
              if (!overriding) onChange(defaultOnOverride);
            }}
          >
            {options[0]?.label ?? label}
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
      ) : null}
    </div>
  );
}
