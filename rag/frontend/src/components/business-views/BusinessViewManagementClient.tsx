"use client";

import {
  PageBody,
  PageHeader,
  Banner,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DataTable,
  type DataTableColumn,
  type EntityAction,
  FieldError,
  FormStatus,
  ObjectActionBar,
  RowActionMenu,
  SelectField,
  type SelectFieldOption,
  StatusBadge,
  ToggleChip,
} from "@engchina/production-ready-ui";
import { Archive, ArrowLeft, FilePen, Plus, RotateCcw, Save, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { DegradedBanner } from "@/components/DegradedBanner";
import { EmptyState, ErrorState, LoadingState } from "@/components/StateViews";
import { KnowledgeBaseScopePicker } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import {
  EditorBreadcrumbs,
  MissingEditorTarget,
  RowTitleButton,
} from "@/components/layout/EntityLayout";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  DEFAULT_BUSINESS_VIEW_NAME,
  type AnswerEngineName,
  type DocragAnswerFlowName,
  type DocragQueryStrategyName,
  type TextSearchTokenizerName,
  type BusinessViewConfig,
  type BusinessViewDetail,
  type BusinessViewStatus,
  type BusinessViewSummary,
  type EvaluationSuiteName,
  type GenerationProfileName,
  type GuardrailPolicyName,
  type KnowledgeBaseQueryConfig,
  type PostRetrievalPipelineName,
  type RetrievalModeName,
  type RetrievalStrategyName,
} from "@/lib/api";
import { useEditorRoute } from "@/lib/editor-route";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useCustomLeaveGuard } from "@/lib/leave-guard";
import {
  useArchiveBusinessView,
  useBusinessView,
  useBusinessViews,
  useCreateBusinessView,
  useUpdateBusinessView,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { readWorkspace, removeWorkspace, useWorkspaceState, writeWorkspace } from "@/lib/workspace-state";
import { BusinessViewKnowledgePanel } from "./BusinessViewKnowledgePanel";

const LIMIT = 20;

/**
 * 一覧の絞り込み・検索・ページ。編集対象は URL の `?id=` が唯一の情報源なので、ここには持たない（#147）。
 * #132 の保存値に残る `editingId` は読み捨てる。
 */
interface BusinessViewListView {
  filter: BusinessViewStatus | "ALL";
  q: string;
  offset: number;
}
const INITIAL_VIEW: BusinessViewListView = { filter: "ACTIVE", q: "", offset: 0 };

function isBusinessViewListView(value: unknown): value is BusinessViewListView {
  const view = value as BusinessViewListView;
  return (
    typeof view === "object" &&
    view !== null &&
    (FILTERS as unknown[]).includes(view.filter) &&
    typeof view.q === "string" &&
    Number.isInteger(view.offset) &&
    view.offset >= 0
  );
}

interface BusinessViewDraft {
  name: string;
  description: string;
  config: BusinessViewConfig;
}

function isBusinessViewDraft(value: unknown): value is BusinessViewDraft {
  const draft = value as BusinessViewDraft;
  return (
    typeof draft === "object" &&
    draft !== null &&
    typeof draft.name === "string" &&
    typeof draft.description === "string" &&
    typeof draft.config === "object" &&
    draft.config !== null &&
    Array.isArray(draft.config.knowledge_base_ids) &&
    typeof draft.config.query === "object"
  );
}

function isDraftOrNull(value: unknown): value is BusinessViewDraft | null {
  return value === null || isBusinessViewDraft(value);
}

/** 業務ビューごとの未保存の下書き（`?id=` の値で分ける。新規は `new`）。 */
function readDraft(scope: string): BusinessViewDraft | null {
  return readWorkspace<BusinessViewDraft | null>("businessViews.draft", null, isDraftOrNull, scope);
}

/** KB の並び順は意味を持たないため、集合として比べる。 */
function draftSignature(draft: BusinessViewDraft) {
  return JSON.stringify({
    ...draft,
    name: draft.name.trim(),
    description: draft.description.trim(),
    config: {
      ...draft.config,
      knowledge_base_ids: [...draft.config.knowledge_base_ids].sort(),
    },
  });
}
const FILTERS: (BusinessViewStatus | "ALL")[] = ["ALL", "ACTIVE", "ARCHIVED"];
const NAME_ERROR_ID = "business-view-name-error";
const NAME_HELPER_ID = "business-view-name-helper";
const SCOPE_ERROR_ID = "business-view-scope-error";

const RETRIEVAL_OPTIONS: SelectFieldOption<RetrievalModeName>[] = [
  { value: "hybrid_rrf", label: t("settings.retrieval.strategy.hybrid_rrf") },
  { value: "vector", label: t("settings.retrieval.strategy.vector") },
  { value: "keyword", label: t("settings.retrieval.strategy.keyword") },
  { value: "graph_augmented", label: t("settings.retrieval.strategy.graph_augmented") },
  {
    value: "reasoning_tree_search",
    label: t("settings.retrieval.strategy.reasoning_tree_search"),
  },
];

// legacy 複合戦略 -> 検索モード + 明示トグルの読み替え(backend の decompose と同義)。
// 編集フォームへ読み込む時点で正規化するため、保存は常に新形式になる。
const LEGACY_RETRIEVAL_MAP: Partial<
  Record<
    RetrievalStrategyName,
    { mode: RetrievalModeName; toggles: Partial<KnowledgeBaseQueryConfig> }
  >
> = {
  business_context_strict: {
    mode: "hybrid_rrf",
    toggles: { retrieval_gap_stop: true, retrieval_business_fit_weighting: true },
  },
  corrective_multi_query: {
    mode: "hybrid_rrf",
    toggles: { retrieval_query_expansion: true, retrieval_corrective: true },
  },
};

function normalizeBusinessViewConfig(config: BusinessViewConfig): BusinessViewConfig {
  // 旧保存 JSON に無いトグルは null(継承)で補完してから legacy を読み替える。
  const query = { ...emptyQueryConfig(), ...config.query };
  const legacy = query.retrieval_strategy ? LEGACY_RETRIEVAL_MAP[query.retrieval_strategy] : undefined;
  if (!legacy) return { ...config, query };
  return {
    ...config,
    query: { ...query, retrieval_strategy: legacy.mode, ...legacy.toggles },
  };
}
const GROUNDING_OPTIONS: SelectFieldOption<PostRetrievalPipelineName>[] = [
  { value: "custom", label: t("settings.grounding.pipeline.custom") },
  { value: "lean", label: t("settings.grounding.pipeline.lean") },
  { value: "verified_context", label: t("settings.grounding.pipeline.verified_context") },
  { value: "context_enrich", label: t("settings.grounding.pipeline.context_enrich") },
  { value: "compact", label: t("settings.grounding.pipeline.compact") },
  { value: "full_governed", label: t("settings.grounding.pipeline.full_governed") },
];
const GENERATION_OPTIONS: SelectFieldOption<GenerationProfileName>[] = [
  { value: "grounded_concise", label: t("settings.generation.profile.grounded_concise") },
  { value: "detailed_cited", label: t("settings.generation.profile.detailed_cited") },
  { value: "strict_extractive", label: t("settings.generation.profile.strict_extractive") },
  { value: "structured_json", label: t("settings.generation.profile.structured_json") },
  { value: "bilingual_ja_en", label: t("settings.generation.profile.bilingual_ja_en") },
  { value: "inline_cited", label: t("settings.generation.profile.inline_cited") },
  { value: "custom", label: t("settings.generation.profile.custom") },
];
const TOKENIZER_OPTIONS: SelectFieldOption<TextSearchTokenizerName>[] = [
  { value: "builtin", label: t("businessViews.tokenizer.builtin") },
  { value: "sudachi", label: t("businessViews.tokenizer.sudachi") },
];
const ANSWER_ENGINE_OPTIONS: SelectFieldOption<AnswerEngineName>[] = [
  { value: "standard", label: t("businessViews.answerEngine.standard") },
  { value: "docrag", label: t("businessViews.answerEngine.docrag") },
];
const DOCRAG_QUERY_STRATEGY_OPTIONS: SelectFieldOption<DocragQueryStrategyName>[] = (
  [
    "auto_routing",
    "simple_retrieval",
    "rag_fusion",
    "query_decomposition",
    "step_back_prompting",
    "hyde",
  ] as const
).map((value) => ({ value, label: t(`businessViews.docragQueryStrategy.${value}`) }));
const DOCRAG_ANSWER_FLOW_OPTIONS: SelectFieldOption<DocragAnswerFlowName>[] = [
  { value: "crag", label: t("businessViews.docragAnswerFlow.crag") },
  { value: "standard_rag", label: t("businessViews.docragAnswerFlow.standard_rag") },
];
// 0〜20(backend の rag_docrag_neighbor_child_count と同じ範囲)。SelectField は文字列値を扱う。
const DOCRAG_NEIGHBOR_OPTIONS: SelectFieldOption<string>[] = Array.from({ length: 21 }, (_, n) => ({
  value: String(n),
  label: String(n),
}));
const GUARDRAIL_OPTIONS: SelectFieldOption<GuardrailPolicyName>[] = [
  { value: "standard", label: t("settings.guardrail.policy.standard") },
  { value: "strict", label: t("settings.guardrail.policy.strict") },
  { value: "lenient", label: t("settings.guardrail.policy.lenient") },
  { value: "regulated", label: t("settings.guardrail.policy.regulated") },
];
const EVALUATION_OPTIONS: SelectFieldOption<EvaluationSuiteName>[] = [
  { value: "request_only", label: t("settings.evaluation.suite.request_only") },
  { value: "retrieval_focused", label: t("settings.evaluation.suite.retrieval_focused") },
  { value: "balanced", label: t("settings.evaluation.suite.balanced") },
  { value: "strict_ci", label: t("settings.evaluation.suite.strict_ci") },
  { value: "ragas_like", label: t("settings.evaluation.suite.ragas_like") },
];
function emptyQueryConfig(): KnowledgeBaseQueryConfig {
  return {
    retrieval_strategy: null,
    retrieval_query_expansion: null,
    retrieval_query_expansion_llm: null,
    retrieval_gap_stop: null,
    retrieval_corrective: null,
    retrieval_business_fit_weighting: null,
    post_retrieval_pipeline: null,
    generation_profile: null,
    guardrail_policy: null,
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
    serving_mode: "fused",
  };
}


/**
 * 業務ビュー(Business View)管理。複数 KB を業務視点で束ね、検索・回答方針と persona を設定する。
 * A 型（一覧 → 全画面エディタ）。編集対象は URL の `?id=`（なし = 一覧 / `new` = 新規 / `<id>` = 編集）。
 */
export function BusinessViewManagementClient() {
  const editor = useEditorRoute();
  const { target } = editor;

  if (target.kind === "list") {
    return <BusinessViewList onOpen={(id) => editor.openItem(id)} onCreate={editor.openNew} />;
  }
  if (target.kind === "new") {
    return (
      <BusinessViewEditor
        key="new"
        onBack={() => editor.backToList()}
        onSaved={(id) => editor.openItem(id, { replace: true })}
        onArchived={() => editor.backToList({ replace: true })}
      />
    );
  }
  return (
    <BusinessViewEditRoute
      key={target.id}
      id={target.id}
      onBack={() => editor.backToList()}
      onArchived={() => editor.backToList({ replace: true })}
    />
  );
}

/**
 * 業務ビュー 1 件の操作。一覧の行（RowActionMenu）とエディタ（ObjectActionBar）で同じ定義を使う
 * （buttons.md §5.1）。編集は選択の導線なので、名前のボタンと行のクリックに置く。
 */
function useBusinessViewActions(onArchived?: (id: string) => void) {
  const confirm = useConfirm();
  const archive = useArchiveBusinessView();

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
        toast.success(t("businessViews.toast.archived"));
        onArchived?.(view.id);
      },
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t("businessViews.error.archive")),
    });
  };

  return (target: BusinessViewSummary): EntityAction[] => {
    const isDefault = target.name === DEFAULT_BUSINESS_VIEW_NAME;
    return [
      {
        id: "archive",
        label: t("businessViews.actions.archive"),
        ariaLabel: isDefault ? t("businessViews.default.archiveDisabled") : undefined,
        icon: Archive,
        tone: "danger",
        visible: target.status !== "ARCHIVED",
        disabled: isDefault || archive.isPending,
        loading: archive.isPending && archive.variables === target.id,
        testId: `business-view-archive-${target.id}`,
        onSelect: () => handleArchive(target),
      },
    ];
  };
}

function BusinessViewList({
  onOpen,
  onCreate,
}: {
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  // 絞り込み・検索・ページは、ページを行き来しても再読込しても残す（workspace-state.md）。
  const [view, setView] = useWorkspaceState("businessViews.view", INITIAL_VIEW, isBusinessViewListView);
  const { filter, q, offset } = view;
  const [search, setSearch] = useState(q);
  const setFilter = (next: BusinessViewStatus | "ALL") =>
    setView((current) => ({ ...current, filter: next, offset: 0 }));
  const setQ = (next: string) => setView((current) => ({ ...current, q: next, offset: 0 }));
  const setOffset = (next: number) => setView((current) => ({ ...current, offset: next }));

  const status = filter === "ALL" ? undefined : filter;
  const query = useBusinessViews({ status, q: q || undefined, limit: LIMIT, offset });
  const page = query.data;
  const items = useMemo(() => page?.items ?? [], [page?.items]);
  const actionsFor = useBusinessViewActions();
  // 新規作成の下書きはエディタを閉じても同じタブに残る。一覧から再開できるようにする。
  const [newDraft] = useState(() => readDraft("new"));

  return (
    <div>
      <PageHeader
        wide
        title={t("nav.businessViews")}
        subtitle={t("businessViews.subtitle")}
        actions={[
          {
            id: "create",
            kind: "primary",
            label: t("businessViews.actions.newView"),
            icon: Plus,
            onClick: onCreate,
          },
        ]}
      />
      <PageBody wide className="grid grid-cols-1 gap-5">
        <DegradedBanner
          messages={page?.warning_messages}
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />

        {newDraft ? (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface-sunken p-3">
            <FormStatus tone="info" message={t("businessViews.draftPending")} />
            <Button size="sm" variant="secondary" icon={FilePen} onClick={onCreate}>
              {t("businessViews.actions.openDraft")}
            </Button>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div
            className="flex flex-wrap items-center gap-1"
            role="group"
            aria-label={t("businessViews.filter.aria")}
          >
            {FILTERS.map((item) => (
              <ToggleChip key={item} selected={filter === item} onClick={() => setFilter(item)}>
                {item === "ALL"
                  ? t("businessViews.filter.all")
                  : item === "ACTIVE"
                    ? t("businessViews.filter.active")
                    : t("businessViews.filter.archived")}
              </ToggleChip>
            ))}
          </div>
          {/* 375px 幅では入力欄の固定 w-56 と検索ボタンが収まらず親を押し広げるため、
              狭い幅では行いっぱいに伸ばし、sm 以上で従来の固定幅へ戻す。 */}
          <form
            className="flex w-full min-w-0 items-center gap-2 sm:w-auto"
            onSubmit={(event) => {
              event.preventDefault();
              setQ(search.trim());
            }}
          >
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("businessViews.search.placeholder")}
              aria-label={t("businessViews.search.placeholder")}
              className="h-9 w-full min-w-0 rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:border-focus-ring sm:w-56"
            />
            <Button size="sm" variant="secondary" type="submit" className="shrink-0">
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
          <Card>
            <EmptyState
              title={t("businessViews.empty.title")}
              hint={t("businessViews.empty.description")}
            />
          </Card>
        ) : (
          <>
            <DataTable<BusinessViewSummary>
              columns={businessViewColumns({ onOpen, actionsFor })}
              rows={items}
              getRowKey={(item) => item.id}
              loading={query.isPending}
              // 行の操作以外の領域のクリックでエディタを開く（page-archetypes.md §0-7）。
              // アーカイブ済みは編集できないため開かない。
              onRowClick={(item) => {
                if (item.status !== "ARCHIVED") onOpen(item.id);
              }}
              rowProps={(item) => ({
                className: cn("align-top", item.status === "ARCHIVED" && "cursor-default"),
                "data-testid": `business-view-row-${item.id}`,
              })}
              stickyHeader
              className="bounded-scroll-area-lg"
              tableClassName="w-full min-w-[720px] text-sm"
              ariaLabel={t("businessViews.list.aria")}
            />
            <div className="flex items-center justify-between">
              <span className="tnum text-xs text-fg-muted">
                {t("pager.range", {
                  start: page && page.total === 0 ? 0 : offset + 1,
                  end: offset + items.length,
                  total: formatNumber(page?.total ?? 0),
                })}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                >
                  {t("pager.prev")}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!page?.has_next}
                  onClick={() => setOffset(offset + LIMIT)}
                >
                  {t("pager.next")}
                </Button>
              </div>
            </div>
          </>
        )}
      </PageBody>
    </div>
  );
}

/** 一覧の列定義。名前列を行見出しにし、操作列は右寄せにする。 */
function businessViewColumns({
  onOpen,
  actionsFor,
}: {
  onOpen: (id: string) => void;
  actionsFor: (view: BusinessViewSummary) => EntityAction[];
}): DataTableColumn<BusinessViewSummary>[] {
  return [
    {
      key: "name",
      header: t("businessViews.col.name"),
      rowHeader: true,
      className: "max-w-[22rem]",
      render: (view) =>
        view.status === "ARCHIVED" ? (
          <div className="min-w-0">
            <p className="break-words font-medium text-fg-muted">{view.name}</p>
            {view.description ? (
              <p className="mt-0.5 line-clamp-2 text-xs text-fg-muted">{view.description}</p>
            ) : null}
          </div>
        ) : (
          <RowTitleButton
            title={view.name}
            subtitle={view.description ?? undefined}
            ariaLabel={t("businessViews.actions.editNamed", { name: view.name })}
            onClick={() => onOpen(view.id)}
          />
        ),
    },
    {
      key: "status",
      header: t("businessViews.col.status"),
      render: (view) => (
        <StatusBadge
          variant={view.status === "ARCHIVED" ? "neutral" : "success"}
          label={t(`businessViews.status.${view.status}` as const)}
        />
      ),
    },
    {
      key: "knowledgeBases",
      header: t("businessViews.col.knowledgeBases"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (view) => formatNumber(view.knowledge_base_count),
    },
    {
      key: "updated",
      header: t("businessViews.col.updated"),
      className: "tnum whitespace-nowrap text-fg-muted",
      render: (view) => formatDateTime(view.updated_at),
    },
    {
      key: "actions",
      header: t("businessViews.col.actions"),
      align: "right",
      render: (view) => (
        <RowActionMenu
          actions={actionsFor(view)}
          ariaLabel={t("common.objectActions.aria", { name: view.name })}
          testId={`business-view-row-actions-${view.id}`}
        />
      ),
    },
  ];
}

/** `?id=<id>` の対象を読み込んでエディタを出す。見つからないときは一覧へ戻る導線を出す（別の対象へ置き換えない）。 */
function BusinessViewEditRoute({
  id,
  onBack,
  onArchived,
}: {
  id: string;
  onBack: () => void;
  onArchived: () => void;
}) {
  const detail = useBusinessView(id);

  if (detail.data) {
    return (
      <BusinessViewEditor
        initial={detail.data}
        onBack={onBack}
        onSaved={() => undefined}
        onArchived={onArchived}
      />
    );
  }

  const notFound = detail.error instanceof ApiError && detail.error.status === 404;
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.businessViews")}
        breadcrumbs={
          <EditorBreadcrumbs
            listLabel={t("nav.businessViews")}
            listHref={APP_ROUTES.businessViews}
            current={id}
          />
        }
        // 見つからないときは本文の「一覧へ戻る」だけにし、同じボタンを重ねない。
        actions={
          notFound
            ? undefined
            : [
                {
                  id: "back",
                  kind: "secondary",
                  label: t("common.backToList"),
                  icon: ArrowLeft,
                  onClick: onBack,
                },
              ]
        }
      />
      <PageBody wide>
        {detail.isPending ? (
          <LoadingState rows={6} label={t("nav.businessViews")} />
        ) : notFound ? (
          <Card>
            <MissingEditorTarget id={id} onBack={onBack} />
          </Card>
        ) : (
          <ErrorState
            message={
              detail.error instanceof ApiError ? detail.error.message : t("businessViews.error.title")
            }
            onRetry={() => void detail.refetch()}
          />
        )}
      </PageBody>
    </div>
  );
}

/** 業務ビューの全画面エディタ（新規 / 編集）。上部に 戻る / 保存、エディタの見出しに対象の操作を置く。 */
function BusinessViewEditor({
  initial,
  onBack,
  onSaved,
  onArchived,
}: {
  initial?: BusinessViewDetail;
  onBack: () => void;
  onSaved: (id: string) => void;
  onArchived: () => void;
}) {
  const mode: "create" | "edit" = initial ? "edit" : "create";
  const create = useCreateBusinessView();
  const update = useUpdateBusinessView();
  const confirm = useConfirm();
  const actionsFor = useBusinessViewActions(onArchived);
  const formRef = useRef<HTMLFormElement>(null);
  // 未保存の下書きは同じタブの sessionStorage に `?id=` の値ごとに残し、再読込・ページ往復で再開できるようにする。
  const draftScope = initial?.id ?? "new";
  const [baseline, setBaseline] = useState<BusinessViewDraft>(() => ({
    name: initial?.name ?? "",
    description: initial?.description ?? "",
    config: initial?.config ? normalizeBusinessViewConfig(initial.config) : emptyConfig(),
  }));
  const [restored] = useState(() => readDraft(draftScope));
  const [name, setName] = useState(restored?.name ?? baseline.name);
  const [description, setDescription] = useState(restored?.description ?? baseline.description);
  const [config, setConfig] = useState<BusinessViewConfig>(restored?.config ?? baseline.config);
  const [touched, setTouched] = useState(false);
  const draft = useMemo(() => ({ name, description, config }), [name, description, config]);
  const dirty = draftSignature(draft) !== draftSignature(baseline);

  useEffect(() => {
    if (dirty) writeWorkspace("businessViews.draft", draft, draftScope);
    else removeWorkspace("businessViews.draft", draftScope);
  }, [dirty, draft, draftScope]);

  // 下書きはこのタブに残るため、確認では「破棄」ではなく未保存であることを伝える。
  const confirmLeave = () =>
    confirm({
      title: t("businessViews.leaveGuard.title"),
      description: t("businessViews.leaveGuard.description"),
      confirmLabel: t("businessViews.leaveGuard.confirm"),
      tone: "warning",
      dismissOnOverlay: false,
    });
  useCustomLeaveGuard(dirty, confirmLeave);

  const back = async () => {
    if (dirty && !(await confirmLeave())) return;
    onBack();
  };

  const discard = () => {
    setName(baseline.name);
    setDescription(baseline.description);
    setConfig(baseline.config);
    setTouched(false);
  };

  const isDefault = initial?.name === DEFAULT_BUSINESS_VIEW_NAME;
  const isArchived = initial?.status === "ARCHIVED";

  const pending = create.isPending || update.isPending;
  const nameError = touched && !isDefault ? validateBusinessViewName(name) : null;
  const scopeError =
    touched && config.knowledge_base_ids.length === 0
      ? t("businessViews.knowledgeBasesRequired")
      : null;

  const updateQuery = (patch: Partial<KnowledgeBaseQueryConfig>) =>
    setConfig((current) => ({ ...current, query: { ...current.query, ...patch } }));

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isArchived) return;
    setTouched(true);
    if (validateBusinessViewName(name, isDefault) || config.knowledge_base_ids.length === 0) return;
    if (mode === "edit" && initial) {
      update.mutate(
        {
          id: initial.id,
          payload: {
            ...(!isDefault ? { name: name.trim() } : {}),
            description: description.trim() || null,
            config,
          },
        },
        {
          onSuccess: (detail) => {
            // 保存した値を基準にして dirty を判定し直す（下書きも消える）。
            setBaseline(draft);
            removeWorkspace("businessViews.draft", draftScope);
            toast.success(t("businessViews.toast.updated"));
            onSaved(detail.id);
          },
          onError: (error) =>
            toast.error(
              error instanceof ApiError ? error.message : t("businessViews.error.update")
            ),
        }
      );
      return;
    }
    create.mutate(
      { name: name.trim(), description: description.trim() || null, config },
      {
        onSuccess: (detail) => {
          setBaseline(draft);
          removeWorkspace("businessViews.draft", draftScope);
          toast.success(t("businessViews.toast.created"));
          // 作成した対象のエディタへ履歴を積まずに移る（戻るで空の新規フォームへ戻さない）。
          onSaved(detail.id);
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("businessViews.error.create")),
      }
    );
  };

  const title = initial?.name ?? t("businessViews.create.title");

  return (
    <div>
      <PageHeader
        wide
        title={title}
        subtitle={initial ? initial.description || t("businessViews.subtitle") : t("businessViews.subtitle")}
        breadcrumbs={
          <EditorBreadcrumbs
            listLabel={t("nav.businessViews")}
            listHref={APP_ROUTES.businessViews}
            current={title}
          />
        }
        actions={[
          {
            id: "back",
            kind: "secondary",
            label: t("common.backToList"),
            icon: ArrowLeft,
            onClick: () => void back(),
          },
          {
            id: "save",
            kind: "primary",
            label: mode === "edit" ? t("businessViews.actions.save") : t("businessViews.actions.create"),
            icon: mode === "edit" ? Save : Sparkles,
            loading: pending,
            disabled: isArchived,
            onClick: () => formRef.current?.requestSubmit(),
          },
        ]}
        moreActionsLabel={t("common.objectActions.more")}
      />
      <PageBody wide className="grid grid-cols-1 gap-5">
        {isArchived ? (
          <Banner severity="warning">{t("businessViews.archivedReadonly")}</Banner>
        ) : null}
        <Card>
          <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
            <CardTitle className="flex items-center gap-2">
              <Sparkles size={20} className="text-accent-fg" aria-hidden />
              {t("businessViews.form.title")}
            </CardTitle>
            {initial ? (
              <ObjectActionBar
                actions={actionsFor(initial)}
                ariaLabel={t("common.objectActions.aria", { name: initial.name })}
                moreLabel={t("common.objectActions.more")}
                testId="business-view-detail-actions"
              />
            ) : null}
          </CardHeader>
          <CardContent>
            <form ref={formRef} onSubmit={handleSubmit} className="space-y-5">
              {restored && dirty ? (
                <FormStatus tone="info" message={t("businessViews.draftRestored")} />
              ) : null}
              <div className="grid gap-3 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
                <div>
                  <label htmlFor="business-view-name" className="text-sm font-medium text-fg">
                    {t("businessViews.field.name")}
                  </label>
                  <input
                    id="business-view-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    onBlur={() => setTouched(true)}
                    readOnly={isDefault}
                    aria-readonly={isDefault || undefined}
                    placeholder={t("businessViews.field.namePlaceholder")}
                    aria-invalid={Boolean(nameError)}
                    aria-describedby={
                      [isDefault ? NAME_HELPER_ID : "", nameError ? NAME_ERROR_ID : ""]
                        .filter(Boolean)
                        .join(" ") || undefined
                    }
                    className={cn(
                      "mt-1 h-9 w-full rounded-md border border-border-control px-3 text-sm outline-none focus-visible:border-focus-ring",
                      isDefault ? "cursor-default bg-surface-sunken text-fg-muted" : "bg-surface-sunken"
                    )}
                  />
                  {isDefault ? (
                    <p id={NAME_HELPER_ID} className="mt-1 text-xs text-fg-muted">
                      {t("businessViews.default.nameFixed")}
                    </p>
                  ) : null}
                  <FieldError id={NAME_ERROR_ID} message={nameError} className="mt-1" />
                </div>
                <div>
                  <label
                    htmlFor="business-view-description"
                    className="text-sm font-medium text-fg"
                  >
                    {t("businessViews.field.description")}
                  </label>
                  <input
                    id="business-view-description"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder={t("businessViews.field.descriptionPlaceholder")}
                    className="mt-1 h-9 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:border-focus-ring"
                  />
                </div>
              </div>

              <div>
                <KnowledgeBaseScopePicker
                  selectedIds={config.knowledge_base_ids}
                  onChange={(ids) => setConfig((current) => ({ ...current, knowledge_base_ids: ids }))}
                  disabled={pending || isDefault}
                  label={t("businessViews.field.knowledgeBases")}
                  helper={
                    isDefault
                      ? t("businessViews.default.knowledgeBaseFixed")
                      : t("businessViews.field.knowledgeBasesHelper")
                  }
                  emptySelectionText={t("businessViews.knowledgeBasesRequired")}
                />
                <FieldError id={SCOPE_ERROR_ID} message={scopeError} className="mt-1" />
              </div>

              <fieldset className="space-y-3 rounded-lg border border-border p-4">
                <legend className="px-1 text-sm font-semibold text-fg">
                  {t("businessViews.query.title")}
                </legend>
                <p className="text-xs text-fg-muted">{t("businessViews.query.helper")}</p>
                <div className="space-y-3">
                  <QuerySelectRow
                    id="business-view-retrieval"
                    label={t("businessViews.field.retrieval")}
                    value={config.query.retrieval_strategy as RetrievalModeName | null}
                    options={RETRIEVAL_OPTIONS}
                    defaultOnOverride="vector"
                    disabled={pending}
                    onChange={(value) => updateQuery({ retrieval_strategy: value })}
                  />
                  <div className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 md:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]">
                    <h3 className="text-sm font-medium text-fg">
                      {t("settings.retrieval.toggles")}
                    </h3>
                    <div className="min-w-0 space-y-2">
                      <QueryToggleRow
                        label={t("settings.retrieval.queryExpansion")}
                        value={config.query.retrieval_query_expansion}
                        disabled={pending}
                        onChange={(value) => updateQuery({ retrieval_query_expansion: value })}
                      />
                      <QueryToggleRow
                        label={t("settings.retrieval.toggle.queryExpansionLlm")}
                        value={config.query.retrieval_query_expansion_llm}
                        disabled={pending}
                        onChange={(value) => updateQuery({ retrieval_query_expansion_llm: value })}
                      />
                      <QueryToggleRow
                        label={t("settings.retrieval.gapStop")}
                        value={config.query.retrieval_gap_stop}
                        disabled={pending}
                        onChange={(value) => updateQuery({ retrieval_gap_stop: value })}
                      />
                      <QueryToggleRow
                        label={t("settings.retrieval.businessFit")}
                        value={config.query.retrieval_business_fit_weighting}
                        disabled={pending}
                        onChange={(value) => updateQuery({ retrieval_business_fit_weighting: value })}
                      />
                      <QueryToggleRow
                        label={t("settings.retrieval.corrective")}
                        value={config.query.retrieval_corrective}
                        disabled={pending}
                        onChange={(value) => updateQuery({ retrieval_corrective: value })}
                      />
                    </div>
                  </div>
                  <QuerySelectRow
                    id="business-view-tokenizer"
                    label={t("businessViews.field.tokenizer")}
                    value={config.query.text_search_tokenizer ?? null}
                    options={TOKENIZER_OPTIONS}
                    defaultOnOverride="sudachi"
                    disabled={pending}
                    onChange={(value) => updateQuery({ text_search_tokenizer: value })}
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
                    id="business-view-answer-engine"
                    label={t("businessViews.field.answerEngine")}
                    value={config.query.answer_engine ?? null}
                    options={ANSWER_ENGINE_OPTIONS}
                    defaultOnOverride="docrag"
                    disabled={pending}
                    onChange={(value) => updateQuery({ answer_engine: value })}
                  />
                  {config.query.answer_engine !== "standard" ? (
                    <>
                      <p className="text-xs text-fg-muted">{t("businessViews.docrag.helper")}</p>
                      <QuerySelectRow
                        id="business-view-docrag-query-strategy"
                        label={t("businessViews.field.docragQueryStrategy")}
                        value={config.query.docrag_query_strategy ?? null}
                        options={DOCRAG_QUERY_STRATEGY_OPTIONS}
                        defaultOnOverride="simple_retrieval"
                        disabled={pending}
                        onChange={(value) => updateQuery({ docrag_query_strategy: value })}
                      />
                      <QuerySelectRow
                        id="business-view-docrag-answer-flow"
                        label={t("businessViews.field.docragAnswerFlow")}
                        value={config.query.docrag_answer_flow ?? null}
                        options={DOCRAG_ANSWER_FLOW_OPTIONS}
                        defaultOnOverride="standard_rag"
                        disabled={pending}
                        onChange={(value) => updateQuery({ docrag_answer_flow: value })}
                      />
                      <QuerySelectRow
                        id="business-view-docrag-neighbor"
                        label={t("businessViews.field.docragNeighborChildCount")}
                        value={
                          config.query.docrag_neighbor_child_count == null
                            ? null
                            : String(config.query.docrag_neighbor_child_count)
                        }
                        options={DOCRAG_NEIGHBOR_OPTIONS}
                        defaultOnOverride="3"
                        disabled={pending}
                        onChange={(value) =>
                          updateQuery({
                            docrag_neighbor_child_count: value === null ? null : Number(value),
                          })
                        }
                      />
                      <div className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 md:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]">
                        <h3 className="text-sm font-medium text-fg">
                          {t("businessViews.field.docragOptions")}
                        </h3>
                        <div className="min-w-0 space-y-2">
                          <QueryToggleRow
                            label={t("businessViews.field.docragRerank")}
                            value={config.query.docrag_rerank_enabled ?? null}
                            disabled={pending}
                            onChange={(value) => updateQuery({ docrag_rerank_enabled: value })}
                          />
                        </div>
                      </div>
                    </>
                  ) : null}
                  <QuerySelectRow
                    id="business-view-generation"
                    label={t("businessViews.field.generation")}
                    value={config.query.generation_profile}
                    options={GENERATION_OPTIONS}
                    defaultOnOverride="detailed_cited"
                    disabled={pending}
                    onChange={(value) => updateQuery({ generation_profile: value })}
                  />
                  <div className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 md:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]">
                    <h3 className="text-sm font-medium text-fg">
                      {t("businessViews.field.prompt")}
                    </h3>
                    <div className="min-w-0 space-y-3">
                      <div>
                        <label
                          htmlFor="business-view-system-prompt"
                          className="text-sm font-medium text-fg"
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
                          disabled={pending}
                          className="mt-1 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm outline-none focus-visible:border-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
                        />
                        <p className="mt-1 text-xs text-fg-muted">
                          {t("businessViews.field.systemPromptHelper")}
                        </p>
                      </div>
                      <div className="grid gap-x-6 gap-y-4 lg:grid-cols-2">
                        <div>
                          <label
                            htmlFor="business-view-language"
                            className="text-sm font-medium text-fg"
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
                            disabled={pending}
                            className="mt-1 h-9 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:border-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
                          />
                        </div>
                      </div>
                    </div>
                  </div>
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
                <Button
                  size="sm"
                  variant="ghost"
                  type="button"
                  icon={RotateCcw}
                  onClick={discard}
                  disabled={!dirty || pending}
                >
                  {t("businessViews.actions.discard")}
                </Button>
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
        {initial ? (
          <BusinessViewKnowledgePanel key={`knowledge-${initial.id}`} businessViewId={initial.id} />
        ) : null}
      </PageBody>
    </div>
  );
}


function validateBusinessViewName(name: string, allowDefault = false) {
  const cleaned = name.trim();
  if (!cleaned) return t("businessViews.nameRequired");
  if (!allowDefault && cleaned.toUpperCase() === DEFAULT_BUSINESS_VIEW_NAME) {
    return t("businessViews.nameReserved");
  }
  return null;
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
    <div className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 md:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]">
      <h3 className="text-sm font-medium text-fg">{label}</h3>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap gap-1" role="group" aria-label={label}>
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
            {t("businessViews.override")}
          </ToggleChip>
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
    </div>
  );
}

/** 検索オプションの三値行(グローバル継承 / ON / OFF)。null は継承。 */
function QueryToggleRow({
  label,
  value,
  disabled,
  onChange,
}: {
  label: string;
  value: boolean | null;
  disabled: boolean;
  onChange: (value: boolean | null) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-sm text-fg">{label}</span>
      <div className="flex flex-wrap gap-1" role="group" aria-label={label}>
        <ToggleChip selected={value === null} disabled={disabled} onClick={() => onChange(null)}>
          {t("businessViews.inherit")}
        </ToggleChip>
        <ToggleChip selected={value === true} disabled={disabled} onClick={() => onChange(true)}>
          ON
        </ToggleChip>
        <ToggleChip selected={value === false} disabled={disabled} onClick={() => onChange(false)}>
          OFF
        </ToggleChip>
      </div>
    </div>
  );
}
