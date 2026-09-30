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
  SaveErrorBanner,
  SelectField,
  type SelectFieldOption,
  StatusBadge,
  TableSkeleton,
  FormSkeleton,
  TimedLoadingState,
  ToggleChip,
  DEFAULT_PAGE_SIZE,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  offsetForPage,
  offsetPagination,
  RowTitleButton,
  ClearActionButton,
  SearchField,
  TextField,
  ListToolbar,
} from "@engchina/production-ready-ui";
import { Archive, Plus, RotateCcw, Save, Sparkles } from "lucide-react";
import { useMemo, useRef, useState, type FormEvent } from "react";

import { DegradedBanner } from "@/components/DegradedBanner";
import { ListPagination } from "@/components/ListPagination";
import { EmptyState, ErrorState } from "@/components/StateViews";
import {
  KnowledgeBaseScopePicker,
  useKnowledgeBaseSelectionHealth,
} from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import {
  EditorDraftNotice,
  EditorTargetState,
} from "@/components/layout/EntityLayout";
import {
  readEditorDraft,
  useEntityEditorDraft,
} from "@/components/layout/use-entity-editor-draft";
import { useAuth } from "@/components/security/AuthProvider";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  DEFAULT_BUSINESS_VIEW_NAME,
  type AnswerFlowName,
  type QueryStrategyName,
  type BusinessViewConfig,
  type BusinessViewDetail,
  type BusinessViewStatus,
  type BusinessViewSummary,
  type GuardrailPolicyName,
  type KnowledgeBaseQueryConfig,
} from "@/lib/api";
import { useEditorRoute } from "@/lib/editor-route";
import type { KnowledgeBaseSelectionHealth } from "@/lib/knowledge-base-refs";
import { formatDateTime, formatNumber } from "@/lib/format";
import { confirmPendingLeave } from "@/lib/leave-guard";
import { t } from "@/lib/i18n";
import { CAPABILITY_PERMISSIONS } from "@/lib/permissions";
import {
  firstInvalidFieldId,
  focusFirstInvalidField,
  requiredTextError,
} from "@/lib/required-fields";
import {
  useArchiveBusinessView,
  useBusinessView,
  useBusinessViews,
  useCreateBusinessView,
  useUpdateBusinessView,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { useWorkspaceState } from "@/lib/workspace-state";
import { BusinessViewKnowledgePanel } from "./BusinessViewKnowledgePanel";

const LIMIT = DEFAULT_PAGE_SIZE;

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
const SCOPE_ERROR_ID = "business-view-scope-error";
// API（BusinessViewCreateRequest / UpdateRequest）の上限。超えると 422 の英語の検証メッセージに
// なるため入力で止める。
const NAME_MAX_LENGTH = 256;
const DESCRIPTION_MAX_LENGTH = 2000;

const QUERY_STRATEGY_OPTIONS: SelectFieldOption<QueryStrategyName>[] = (
  [
    "auto_routing",
    "simple_retrieval",
    "rag_fusion",
    "query_decomposition",
    "step_back_prompting",
    "hyde",
  ] as const
).map((value) => ({ value, label: t(`businessViews.queryStrategy.${value}`) }));
const ANSWER_FLOW_OPTIONS: SelectFieldOption<AnswerFlowName>[] = [
  { value: "crag", label: t("businessViews.answerFlow.crag") },
  { value: "standard_rag", label: t("businessViews.answerFlow.standard_rag") },
];
// 0〜20(backend の rag_neighbor_child_count と同じ範囲)。SelectField は文字列値を扱う。
const NEIGHBOR_CHILD_COUNT_OPTIONS: SelectFieldOption<string>[] = Array.from({ length: 21 }, (_, n) => ({
  value: String(n),
  label: String(n),
}));
const GUARDRAIL_OPTIONS: SelectFieldOption<GuardrailPolicyName>[] = [
  { value: "standard", label: t("settings.guardrail.policy.standard") },
  { value: "strict", label: t("settings.guardrail.policy.strict") },
  { value: "lenient", label: t("settings.guardrail.policy.lenient") },
  { value: "regulated", label: t("settings.guardrail.policy.regulated") },
];
function emptyQueryConfig(): KnowledgeBaseQueryConfig {
  return { guardrail_policy: null };
}

/** 旧保存 JSON に無い項目は null(継承)で補う。 */
function normalizeBusinessViewConfig(config: BusinessViewConfig): BusinessViewConfig {
  return { ...config, query: { ...emptyQueryConfig(), ...config.query } };
}

function emptyConfig(): BusinessViewConfig {
  return {
    version: 1,
    knowledge_base_ids: [],
    query: emptyQueryConfig(),
    serving_mode: "fused",
  };
}


/**
 * 業務ビュー(Business View)管理。複数 KB を業務視点で束ね、回答の設定と安全チェックを上書きする。
 * A 型（一覧 → 全画面エディタ）。編集対象は URL の `?id=`（なし = 一覧 / `new` = 新規 / `<id>` = 編集）。
 */
export function BusinessViewManagementClient() {
  const editor = useEditorRoute();
  const { target } = editor;
  const canManage = useCanManageBusinessViews();

  // 作成は業務ビュー管理の権限がある利用者だけ（`?id=new` を直接開いても一覧を出す。#214）。
  if (target.kind === "list" || (target.kind === "new" && !canManage)) {
    return (
      <BusinessViewList
        onOpen={(id) => editor.openItem(id)}
        itemHref={editor.itemHref}
        onCreate={canManage ? editor.openNew : undefined}
      />
    );
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
/** 業務ビューの作成・アーカイブは `rag.business_views.manage` を持つ利用者だけ（#214）。 */
function useCanManageBusinessViews(): boolean {
  return useAuth().hasPermission(CAPABILITY_PERMISSIONS.businessViewsManage);
}

function useBusinessViewActions(onArchived?: (id: string) => void) {
  const confirm = useConfirm();
  const archive = useArchiveBusinessView();
  const canManage = useCanManageBusinessViews();

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
        visible: canManage && target.status !== "ARCHIVED",
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
  itemHref,
  onCreate,
}: {
  onOpen: (id: string) => void;
  /** 業務ビューのエディタの URL（名前のリンク。新しいタブで開ける。#583）。 */
  itemHref: (id: string) => string;
  /** 作成できない利用者（業務ビュー管理の権限なし）では undefined。 */
  onCreate?: () => void;
}) {
  // 絞り込み・検索・ページは、ページを行き来しても再読込しても残す（workspace-state.md）。
  const [view, setView] = useWorkspaceState("businessViews.view", INITIAL_VIEW, isBusinessViewListView);
  const { filter, q, offset } = view;
  const setFilter = (next: BusinessViewStatus | "ALL") =>
    setView((current) => ({ ...current, filter: next, offset: 0 }));
  const setQ = (next: string) => setView((current) => ({ ...current, q: next, offset: 0 }));
  const setOffset = (next: number) => setView((current) => ({ ...current, offset: next }));

  const status = filter === "ALL" ? undefined : filter;
  const query = useBusinessViews({ status, q: q || undefined, limit: LIMIT, offset });
  const page = query.data;
  const items = useMemo(() => page?.items ?? [], [page?.items]);
  const actionsFor = useBusinessViewActions();
  // 参照 KB がアーカイブ・削除されて検索対象から外れている業務ビュー（#302）。
  const hasKnowledgeBaseIssues = items.some(
    (item) => item.status !== "ARCHIVED" && knowledgeBaseIssueCount(item) > 0
  );
  // 新規作成の下書きはエディタを閉じても同じタブに残る。一覧から再開できるようにする。
  const [newDraft] = useState(() => readEditorDraft("businessViews.draft", "new", isBusinessViewDraft));

  return (
    <div>
      <PageHeader
        wide
        title={t("nav.businessViews")}
        subtitle={t("businessViews.subtitle")}
        actions={
          onCreate
            ? [
                {
                  id: "create",
                  kind: "primary",
                  label: t("businessViews.actions.newView"),
                  icon: Plus,
                  onClick: onCreate,
                },
              ]
            : []
        }
      />
      <PageBody wide className="grid grid-cols-1 gap-5">
        <DegradedBanner
          messages={page?.warning_messages}
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />

        {hasKnowledgeBaseIssues ? (
          <Banner
            severity="warning"
            title={t("businessViews.knowledgeBaseIssues.listTitle")}
          >
            <p>{t("businessViews.knowledgeBaseIssues.listHint")}</p>
          </Banner>
        ) : null}

        {newDraft && onCreate ? (
          <EditorDraftNotice message={t("businessViews.draftPending")} onOpen={onCreate} />
        ) : null}

        {/* 一覧のツールバー: 左に検索、その右に状態の絞り込み（page-archetypes.md「一覧のツールバー」。#600）。
            一覧の絞り込みは入力に合わせて適用する（検索ボタンを置かない。#535）。 */}
        <ListToolbar
          search={
            <SearchField
              id="business-view-search"
              label={t("businessViews.search.placeholder")}
              labelHidden
              value={q}
              onSearch={(next) => {
                if (next !== q) setQ(next);
              }}
              clearLabel={t("common.clearSearch")}
              resultCountLabel={
                page ? t("common.searchResultCount", { count: formatNumber(page.total) }) : ""
              }
              placeholder={t("businessViews.search.placeholder")}
            />
          }
          filters={
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
          }
          testId="business-view-list-toolbar"
        />

        {query.isError ? (
          <ErrorState
            message={
              query.error instanceof ApiError ? query.error.message : t("businessViews.error.title")
            }
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending ? (
          <TimedLoadingState
            label={t("businessViews.loading")}
            operationKey="business-views-load"
            testId="business-views-loading"
          >
            <TableSkeleton columns={5} />
          </TimedLoadingState>
        ) : items.length === 0 && !query.isFetching ? (
          <Card>
            {q ? (
              <EmptyState
                title={t("businessViews.search.noResultsTitle")}
                hint={t("businessViews.search.noResultsHint")}
                action={
                  <ClearActionButton
                    label={t("common.clearSearch")}
                    onClick={() => setQ("")}
                  />
                }
              />
            ) : (
              <EmptyState
                title={t("businessViews.empty.title")}
                hint={
                  onCreate
                    ? t("businessViews.empty.description")
                    : t("businessViews.empty.restrictedDescription")
                }
                // 空の一覧から次の行動へ進めるよう、作成の入口を空の状態にも置く（ヘッダーの「新規作成」と同じ。#555）。
                action={
                  onCreate ? (
                    <Button variant="secondary" icon={Plus} onClick={onCreate}>
                      {t("businessViews.actions.createFirst")}
                    </Button>
                  ) : undefined
                }
              />
            )}
          </Card>
        ) : (
          <div className="grid gap-2">
            <DataTable<BusinessViewSummary>
              columns={businessViewColumns({ onOpen, itemHref, actionsFor })}
              rows={items}
              getRowKey={(item) => item.id}
              // 行の操作以外の領域のクリックでエディタを開く（page-archetypes.md §0-7）。
              // アーカイブ済みは編集できないため開かない。
              onRowClick={(item) => {
                if (item.status !== "ARCHIVED") onOpen(item.id);
              }}
              rowProps={(item) => ({
                className: cn(
                  INFORMATION_TABLE_ROW_CLASS,
                  "align-top",
                  item.status === "ARCHIVED" && "cursor-default"
                ),
                "data-testid": `business-view-row-${item.id}`,
              })}
              stickyHeader
              visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
              scrollAriaLabel={t("businessViews.list.scrollLabel")}
              scrollTestId="business-views-scroll-region"
              tableClassName="w-full min-w-[51.43rem] text-sm"
              ariaLabel={t("businessViews.list.aria")}
            />
            <ListPagination
              {...offsetPagination({ offset, limit: LIMIT, total: page?.total ?? 0, count: items.length })}
              onPageChange={(next) => setOffset(offsetForPage(next, LIMIT))}
              testId="business-views-pagination"
            />
          </div>
        )}
      </PageBody>
    </div>
  );
}

/** 一覧の列定義。名前列を行見出しにし、操作列は右寄せにする。 */
function businessViewColumns({
  onOpen,
  itemHref,
  actionsFor,
}: {
  onOpen: (id: string) => void;
  itemHref: (id: string) => string;
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
            aria-label={t("businessViews.actions.editNamed", { name: view.name })}
            href={itemHref(view.id)}
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
      render: (view) => {
        const archived = view.archived_knowledge_base_count ?? 0;
        const missing = view.missing_knowledge_base_count ?? 0;
        // アーカイブ済みの業務ビューは検索に使われないため、参照 KB の警告は出さない。
        if (view.status === "ARCHIVED" || archived + missing === 0) {
          return formatNumber(view.knowledge_base_count);
        }
        return (
          <span
            className="inline-flex items-center justify-end gap-2"
            data-testid={`business-view-kb-issues-${view.id}`}
          >
            <StatusBadge variant="warning" label={t("businessViews.knowledgeBaseIssues.badge")} />
            <span aria-hidden>{formatNumber(view.knowledge_base_count)}</span>
            <span className="sr-only">
              {t("businessViews.knowledgeBaseIssues.badgeAria", {
                total: view.knowledge_base_count,
                archived,
                missing,
              })}
            </span>
          </span>
        );
      },
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

  return (
    <EditorTargetState
      id={id}
      listLabel={t("nav.businessViews")}
      error={detail.error}
      loadingLabel={t("businessViews.detail.loading")}
      loadingTestId="business-view-detail-loading"
      errorFallback={t("businessViews.error.title")}
      skeleton={<FormSkeleton fields={6} />}
      onBack={onBack}
      onRetry={() => void detail.refetch()}
    />
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
  const actionsFor = useBusinessViewActions(onArchived);
  const formRef = useRef<HTMLFormElement>(null);
  // 未保存の下書き（`?id=` の値ごと）と離脱の確認は、ナレッジベースのエディタと共有の部品で持つ（#555）。
  const editor = useEntityEditorDraft<BusinessViewDraft>({
    field: "businessViews.draft",
    scope: initial?.id ?? "new",
    initial: {
      name: initial?.name ?? "",
      description: initial?.description ?? "",
      config: initial?.config ? normalizeBusinessViewConfig(initial.config) : emptyConfig(),
    },
    isDraft: isBusinessViewDraft,
    signature: draftSignature,
    leaveDescription: t("businessViews.leaveGuard.description"),
  });
  const { draft, setDraft, dirty } = editor;
  const { name, description, config } = draft;
  const setName = (value: string) => setDraft((current) => ({ ...current, name: value }));
  const setDescription = (value: string) =>
    setDraft((current) => ({ ...current, description: value }));
  const setConfig = (next: BusinessViewConfig | ((current: BusinessViewConfig) => BusinessViewConfig)) =>
    setDraft((current) => ({
      ...current,
      config: typeof next === "function" ? next(current.config) : next,
    }));
  const [touched, setTouched] = useState(false);
  // 説明はフォーカスが外れたとき・送信したときから検証結果を出す（messaging.md §3.2。#521）。
  const [descriptionTouched, setDescriptionTouched] = useState(false);

  // 左上の「一覧へ戻る」は、エディタのフォームだけでなくページのほかの未保存（知識の節・構築設定など）も
  // リンク・戻るボタンと同じく 1 回だけ確認する（#586 / #618）。
  const back = async () => {
    if (await confirmPendingLeave()) onBack();
  };

  const discard = () => {
    editor.discard();
    setTouched(false);
    setDescriptionTouched(false);
  };

  const isDefault = initial?.name === DEFAULT_BUSINESS_VIEW_NAME;
  const isArchived = initial?.status === "ARCHIVED";
  // 選択中（下書きを含む）の参照 KB のうち、アーカイブ済み・見つからないもの（#302）。
  // 範囲外の KB も名前で出せるよう、詳細が返す tenant 内の参照を併せて使う。
  const knowledgeBaseHealth = useKnowledgeBaseSelectionHealth(config.knowledge_base_ids, {
    known: initial?.knowledge_bases,
    knownMissingIds: initial?.missing_knowledge_base_ids,
  });

  const pending = create.isPending || update.isPending;
  // 保存の失敗は欄に結び付かないため、ヘッダーの直下の 1 か所だけに出す（messaging.md §3.3.1。#585）。
  const saveMutation = mode === "edit" ? update : create;
  const saveError = saveMutation.isError
    ? saveMutation.error instanceof ApiError
      ? saveMutation.error.message
      : t(mode === "edit" ? "businessViews.error.update" : "businessViews.error.create")
    : null;
  // アーカイブ済みは保存できないので、入力できないようにする（入力しても保存できない欄を出さない。#555）。
  const locked = pending || isArchived;
  const nameError = touched && !isDefault ? validateBusinessViewName(name) : null;
  // 説明は必須（#521）。説明が空の既存の業務ビュー（DEFAULT を含む）は、保存するときに入力を求める。
  const descriptionError = descriptionTouched ? validateBusinessViewDescription(description) : null;
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
    setDescriptionTouched(true);
    const fieldErrors = [
      ["business-view-name", validateBusinessViewName(name, isDefault)],
      ["business-view-description", validateBusinessViewDescription(description)],
    ] as const;
    if (firstInvalidFieldId(fieldErrors)) {
      focusFirstInvalidField(fieldErrors);
      return;
    }
    if (config.knowledge_base_ids.length === 0) return;
    if (mode === "edit" && initial) {
      update.mutate(
        {
          id: initial.id,
          payload: {
            ...(!isDefault ? { name: name.trim() } : {}),
            description: description.trim(),
            config,
          },
        },
        {
          onSuccess: (detail) => {
            // 保存した値を基準にして dirty を判定し直す（下書きも消える）。
            editor.markSaved(draft);
            toast.success(t("businessViews.toast.updated"));
            onSaved(detail.id);
          },
        }
      );
      return;
    }
    create.mutate(
      { name: name.trim(), description: description.trim(), config },
      {
        onSuccess: (detail) => {
          editor.markSaved(draft);
          toast.success(t("businessViews.toast.created"));
          // 作成した対象のエディタへ履歴を積まずに移る（戻るで空の新規フォームへ戻さない）。
          onSaved(detail.id);
        },
      }
    );
  };

  const title = initial?.name ?? t("businessViews.create.title");

  return (
    <div>
      <PageHeader
        wide
        title={title}
        status={
          initial ? (
            <StatusBadge
              variant={initial.status === "ARCHIVED" ? "neutral" : "success"}
              label={t(`businessViews.status.${initial.status}` as const)}
            />
          ) : undefined
        }
        subtitle={initial ? initial.description || t("businessViews.subtitle") : t("businessViews.subtitle")}
        meta={
          initial ? (
            <span className="tnum flex flex-wrap gap-x-3 gap-y-1" data-testid="business-view-meta">
              <span>
                {t("businessViews.meta.knowledgeBases", {
                  count: formatNumber(initial.knowledge_base_count),
                })}
              </span>
              <span>{t("editor.meta.updated", { date: formatDateTime(initial.updated_at) })}</span>
            </span>
          ) : undefined
        }
        // 一覧へ戻るは左上、保存は右端の primary、変更を破棄はその左（#618）。
        back={{
          label: t("common.backToList"),
          ariaLabel: t("editor.backToListOf", { list: t("nav.businessViews") }),
          onClick: () => void back(),
          testId: "editor-back",
        }}
        actions={[
          ...(isArchived
            ? []
            : [
                {
                  id: "discard",
                  kind: "secondary" as const,
                  label: t("editor.actions.discard"),
                  icon: RotateCcw,
                  disabled: !dirty || pending,
                  onClick: discard,
                },
              ]),
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
        <SaveErrorBanner
          message={saveError}
          attemptKey={saveMutation.submittedAt}
          testId="business-view-save-error"
        />
        {isArchived ? (
          <Banner severity="warning">{t("businessViews.archivedReadonly")}</Banner>
        ) : (
          <KnowledgeBaseIssuesBanner
            health={knowledgeBaseHealth}
            selectedCount={config.knowledge_base_ids.length}
          />
        )}
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
              {editor.restored ? (
                <FormStatus tone="info" message={t("editor.draftRestored")} />
              ) : null}
              <div className="grid gap-3 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
                <TextField
                  id="business-view-name"
                  label={t("businessViews.field.name")}
                  required={!isDefault && !isArchived}
                  value={name}
                  onValueChange={setName}
                  onBlur={() => setTouched(true)}
                  readOnly={isDefault || isArchived}
                  aria-readonly={isDefault || isArchived || undefined}
                  placeholder={isArchived ? undefined : t("businessViews.field.namePlaceholder")}
                  helper={isDefault ? t("businessViews.default.nameFixed") : undefined}
                  error={nameError || undefined}
                  maxLength={NAME_MAX_LENGTH}
                  inputClassName={isDefault || isArchived ? "cursor-default text-fg-muted" : undefined}
                />
                <TextField
                  id="business-view-description"
                  label={t("businessViews.field.description")}
                  required={!isArchived}
                  value={description}
                  onValueChange={setDescription}
                  onBlur={() => setDescriptionTouched(true)}
                  readOnly={isArchived}
                  aria-readonly={isArchived || undefined}
                  placeholder={isArchived ? undefined : t("businessViews.field.descriptionPlaceholder")}
                  helper={t("businessViews.field.descriptionHelper")}
                  error={descriptionError || undefined}
                  maxLength={DESCRIPTION_MAX_LENGTH}
                  inputClassName={isArchived ? "cursor-default text-fg-muted" : undefined}
                />
              </div>

              <div>
                <KnowledgeBaseScopePicker
                  knownKnowledgeBases={initial?.knowledge_bases}
                  knownMissingIds={initial?.missing_knowledge_base_ids}
                  selectedIds={config.knowledge_base_ids}
                  onChange={(ids) => setConfig((current) => ({ ...current, knowledge_base_ids: ids }))}
                  disabled={locked || isDefault}
                  label={t("businessViews.field.knowledgeBases")}
                  helper={
                    isDefault
                      ? t("businessViews.default.knowledgeBaseFixed")
                      : t("businessViews.field.knowledgeBasesHelper")
                  }
                  emptySelectionText={t("businessViews.knowledgeBasesRequired")}
                  required={!isDefault}
                  errorId={scopeError ? SCOPE_ERROR_ID : undefined}
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
                    id="business-view-query-strategy"
                    label={t("businessViews.field.queryStrategy")}
                    value={config.query.query_strategy ?? null}
                    options={QUERY_STRATEGY_OPTIONS}
                    defaultOnOverride="simple_retrieval"
                    disabled={locked}
                    onChange={(value) => updateQuery({ query_strategy: value })}
                  />
                  <QuerySelectRow
                    id="business-view-answer-flow"
                    label={t("businessViews.field.answerFlow")}
                    value={config.query.answer_flow ?? null}
                    options={ANSWER_FLOW_OPTIONS}
                    defaultOnOverride="standard_rag"
                    disabled={locked}
                    onChange={(value) => updateQuery({ answer_flow: value })}
                  />
                  <QuerySelectRow
                    id="business-view-neighbor-child-count"
                    label={t("businessViews.field.neighborChildCount")}
                    value={
                      config.query.neighbor_child_count == null
                        ? null
                        : String(config.query.neighbor_child_count)
                    }
                    options={NEIGHBOR_CHILD_COUNT_OPTIONS}
                    defaultOnOverride="3"
                    disabled={locked}
                    onChange={(value) =>
                      updateQuery({
                        neighbor_child_count: value === null ? null : Number(value),
                      })
                    }
                  />
                  <div className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 md:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]">
                    <h3 className="text-sm font-medium text-fg">
                      {t("businessViews.field.answerOptions")}
                    </h3>
                    <div className="min-w-0 space-y-2">
                      <QueryToggleRow
                        label={t("businessViews.field.rerank")}
                        value={config.query.rerank_enabled ?? null}
                        disabled={locked}
                        onChange={(value) => updateQuery({ rerank_enabled: value })}
                      />
                      <QueryToggleRow
                        label={t("businessViews.field.screenLinking")}
                        description={t("businessViews.field.screenLinkingHelper")}
                        descriptionId="business-view-screen-linking-helper"
                        value={config.query.screen_linking_enabled ?? null}
                        disabled={locked}
                        onChange={(value) =>
                          updateQuery({ screen_linking_enabled: value })
                        }
                      />
                      <QueryToggleRow
                        label={t("businessViews.field.autoFieldFilter")}
                        description={t("businessViews.field.autoFieldFilterHelper")}
                        descriptionId="business-view-auto-field-filter-helper"
                        value={config.query.auto_field_filter_enabled ?? null}
                        disabled={locked}
                        onChange={(value) =>
                          updateQuery({ auto_field_filter_enabled: value })
                        }
                      />
                    </div>
                  </div>
                  <QuerySelectRow
                    id="business-view-guardrail"
                    label={t("businessViews.field.guardrail")}
                    value={config.query.guardrail_policy}
                    options={GUARDRAIL_OPTIONS}
                    defaultOnOverride="strict"
                    disabled={locked}
                    onChange={(value) => updateQuery({ guardrail_policy: value })}
                  />
                </div>
              </fieldset>
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

/** 参照 KB のうち検索対象にならない件数（一覧の要約）。 */
function knowledgeBaseIssueCount(view: BusinessViewSummary): number {
  return (view.archived_knowledge_base_count ?? 0) + (view.missing_knowledge_base_count ?? 0);
}

// 警告に名前を並べる上限。超えた分は件数だけにする。
const ISSUE_NAMES_LIMIT = 5;

function issueNames(names: string[]): string {
  const shown = names.slice(0, ISSUE_NAMES_LIMIT).join("、");
  const rest = names.length - ISSUE_NAMES_LIMIT;
  return rest > 0 ? `${shown}${t("businessViews.knowledgeBaseIssues.more", { count: rest })}` : shown;
}

/**
 * 参照 KB にアーカイブ済み・見つからないものがあるときの警告（#302）。これらは検索されず、
 * すべてが該当すると検索・回答は 0 件になる。
 */
function KnowledgeBaseIssuesBanner({
  health,
  selectedCount,
}: {
  health: KnowledgeBaseSelectionHealth;
  selectedCount: number;
}) {
  const { archived, missing } = health;
  if (archived.length + missing.length === 0) return null;
  const allUnavailable = selectedCount > 0 && archived.length + missing.length >= selectedCount;
  return (
    <Banner
      severity="warning"
      title={t("businessViews.knowledgeBaseIssues.title")}
    >
      <div className="space-y-1" data-testid="business-view-kb-issues">
        {archived.length > 0 ? (
          <p className="break-words">
            {t("businessViews.knowledgeBaseIssues.archived", {
              count: archived.length,
              names: issueNames(archived.map((item) => item.name)),
            })}
          </p>
        ) : null}
        {missing.length > 0 ? (
          <p className="break-words">
            {t("businessViews.knowledgeBaseIssues.missing", {
              count: missing.length,
              names: issueNames(missing.map((item) => item.id)),
            })}
          </p>
        ) : null}
        {allUnavailable ? (
          <p className="font-medium">{t("businessViews.knowledgeBaseIssues.allUnavailable")}</p>
        ) : null}
        <p>{t("businessViews.knowledgeBaseIssues.hint")}</p>
      </div>
    </Banner>
  );
}

/** 説明の検証（作成・編集で共通。#521）。空・空白だけは入力を求める。 */
function validateBusinessViewDescription(description: string) {
  return requiredTextError(description, t("businessViews.descriptionRequired"));
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
  description,
  descriptionId,
  value,
  disabled,
  onChange,
}: {
  label: string;
  /** 行の下に出す説明(費用・向く質問など)。選択肢のグループの説明として読み上げる。 */
  description?: string;
  descriptionId?: string;
  value: boolean | null;
  disabled: boolean;
  onChange: (value: boolean | null) => void;
}) {
  const describedBy = description ? descriptionId : undefined;
  return (
    // 広い画面でも選択肢を名前のすぐ右に並べる（右端へ離さない。1920px で名前と選択肢の対応が追えるように。#555）。
    <div className="grid items-center gap-2 sm:grid-cols-[minmax(10rem,16rem)_auto] sm:justify-start">
      <span className="text-sm text-fg">{label}</span>
      <div
        className="flex flex-wrap gap-1"
        role="group"
        aria-label={label}
        aria-describedby={describedBy}
      >
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
      {description ? (
        <p id={describedBy} className="text-xs text-fg-muted sm:col-span-2">
          {description}
        </p>
      ) : null}
    </div>
  );
}
