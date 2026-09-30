"use client";

import {
  PageBody,
  PageHeader,
  Button,
  Card,
  DataTable,
  type DataTableColumn,
  type EntityAction,
  ClearActionButton,
  RowActionMenu,
  SearchField,
  TableSkeleton,
  TimedLoadingState,
  ToggleChip,
  DEFAULT_PAGE_SIZE,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  offsetForPage,
  offsetPagination,
} from "@engchina/production-ready-ui";
import { Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { DegradedBanner } from "@/components/DegradedBanner";
import { ListPagination } from "@/components/ListPagination";
import { EditorDraftNotice } from "@/components/layout/EntityLayout";
import { readEditorDraft } from "@/components/layout/use-entity-editor-draft";
import { useAuth } from "@/components/security/AuthProvider";
import { EmptyState, ErrorState } from "@/components/StateViews";
import {
  ApiError,
  type KnowledgeBaseStatus,
  type KnowledgeBaseSummary,
} from "@/lib/api";
import { useEditorRoute } from "@/lib/editor-route";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { CAPABILITY_PERMISSIONS } from "@/lib/permissions";
import { useKnowledgeBases } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { useWorkspaceState } from "@/lib/workspace-state";
import { isKnowledgeBaseDraft, KnowledgeBaseEditor } from "./KnowledgeBaseEditor";
import {
  KnowledgeBaseStatusPill,
  knowledgeBaseStatusLabel,
} from "./KnowledgeBaseStatusPill";
import { useKnowledgeBaseActions } from "./knowledge-base-actions";

const LIMIT = DEFAULT_PAGE_SIZE;

interface KnowledgeBaseListView {
  filter: KnowledgeBaseStatus | "ALL";
  q: string;
  offset: number;
}
const INITIAL_VIEW: KnowledgeBaseListView = { filter: "ACTIVE", q: "", offset: 0 };

function isKnowledgeBaseListView(value: unknown): value is KnowledgeBaseListView {
  const view = value as KnowledgeBaseListView;
  return (
    typeof view === "object" &&
    view !== null &&
    (FILTERS as unknown[]).includes(view.filter) &&
    typeof view.q === "string" &&
    Number.isInteger(view.offset) &&
    view.offset >= 0
  );
}
const FILTERS: (KnowledgeBaseStatus | "ALL")[] = ["ALL", "ACTIVE", "ARCHIVED"];

/** ナレッジベースの作成・アーカイブは `rag.knowledge_bases.manage` を持つ利用者だけ（#214）。 */
function useCanManageKnowledgeBases(): boolean {
  return useAuth().hasPermission(CAPABILITY_PERMISSIONS.knowledgeBasesManage);
}

/**
 * ナレッジベース（`/knowledge-bases`）。業務ビューと同じ A 型（一覧 → 全画面エディタ。#555）。
 * `?id=` なし = 一覧 / `new` = 作成の画面。詳細（編集）は既存の URL `/knowledge-bases/:id` のまま
 * （`?id=<id>` で開かれたら詳細の URL へ置き換える）。
 */
export function KnowledgeBaseManagementClient() {
  const editor = useEditorRoute();
  const navigate = useNavigate();
  const { target } = editor;
  const canManage = useCanManageKnowledgeBases();
  const detailPath = (id: string) => `${APP_ROUTES.knowledgeBases}/${encodeURIComponent(id)}`;

  if (target.kind === "edit") return <Navigate to={detailPath(target.id)} replace />;
  // 作成は管理の権限がある利用者だけ（`?id=new` を直接開いても一覧を出す。業務ビューと同じ）。
  if (target.kind === "new" && canManage) {
    return (
      <KnowledgeBaseEditor
        key="new"
        onBack={() => editor.backToList()}
        // 作成した対象の詳細へ履歴を積まずに移る（戻るで空の作成の画面へ戻さない）。
        onCreated={(id) => navigate(detailPath(id), { replace: true })}
      />
    );
  }
  return (
    <KnowledgeBaseList
      onOpen={(id) => navigate(detailPath(id))}
      onCreate={canManage ? editor.openNew : undefined}
    />
  );
}

/** ナレッジベースの一覧。絞り込み・検索・ページ・行の操作（アーカイブ）。名前のリンク・行のクリックで詳細へ。 */
function KnowledgeBaseList({
  onOpen,
  onCreate,
}: {
  onOpen: (id: string) => void;
  /** 作成できない利用者（ナレッジベース管理の権限なし）では undefined。 */
  onCreate?: () => void;
}) {
  // 絞り込み・検索・ページは、ページを行き来しても再読込しても残す（workspace-state.md）。
  const [view, setView] = useWorkspaceState("knowledgeBases.view", INITIAL_VIEW, isKnowledgeBaseListView);
  const { filter, q, offset } = view;
  const setFilter = (next: KnowledgeBaseStatus | "ALL") => setView((current) => ({ ...current, filter: next }));
  const setQ = (next: string) => setView((current) => ({ ...current, q: next }));
  const setOffset = (next: number) => setView((current) => ({ ...current, offset: next }));

  const status = filter === "ALL" ? undefined : filter;
  const query = useKnowledgeBases({ status, q: q || undefined, limit: LIMIT, offset });
  const page = query.data;
  const items = useMemo(() => page?.items ?? [], [page?.items]);

  // 行の操作（アーカイブ）は詳細ページの ObjectActionBar と同じ定義を使う。
  const knowledgeBaseActions = useKnowledgeBaseActions();
  // 新規作成の下書きは作成の画面を閉じても同じタブに残る。一覧から再開できるようにする。
  const [newDraft] = useState(() => readEditorDraft("knowledgeBases.draft", "new", isKnowledgeBaseDraft));

  const resetView = (fn: () => void) => {
    fn();
    setOffset(0);
  };
  // 検索語は入力に合わせて適用する（SearchField の debounce・IME 対応。#535）。変わったときだけ先頭のページへ戻す。
  const applySearch = (next: string) => {
    if (next !== q) resetView(() => setQ(next));
  };

  // アーカイブなどで件数が減り、保存したページが範囲外になったら最後のページへ戻す。
  // 範囲外のまま「ナレッジベースがありません」を出さない。
  const outOfRange = Boolean(page && page.offset === offset && items.length === 0 && offset > 0);
  const lastPageOffset =
    page && page.total > 0 ? Math.floor((page.total - 1) / LIMIT) * LIMIT : 0;
  const movingToLastPage = outOfRange && lastPageOffset !== offset;
  if (movingToLastPage) setOffset(lastPageOffset);

  return (
    <div>
      <PageHeader
        wide
        title={t("nav.knowledgeBases")}
        subtitle={t("knowledgeBases.subtitle")}
        actions={
          onCreate
            ? [
                {
                  id: "create",
                  kind: "primary",
                  label: t("knowledgeBases.actions.newKnowledgeBase"),
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

        {newDraft && onCreate ? (
          <EditorDraftNotice message={t("knowledgeBases.draftPending")} onOpen={onCreate} />
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div
            className="flex flex-wrap items-center gap-1"
            role="group"
            aria-label={t("knowledgeBases.filter.aria")}
          >
            {FILTERS.map((item) => (
              <ToggleChip
                key={item}
                selected={filter === item}
                onClick={() => resetView(() => setFilter(item))}
              >
                {item === "ALL" ? t("knowledgeBases.filter.all") : knowledgeBaseStatusLabel(item)}
              </ToggleChip>
            ))}
          </div>
          <SearchField
            id="knowledge-base-search"
            label={t("knowledgeBases.search.placeholder")}
            labelHidden
            value={q}
            onSearch={applySearch}
            clearLabel={t("common.clearSearch")}
            resultCountLabel={
              page ? t("common.searchResultCount", { count: formatNumber(page.total) }) : ""
            }
            placeholder={t("knowledgeBases.search.placeholder")}
            className="w-full sm:w-64"
          />
        </div>

        {query.isError ? (
          <ErrorState
            message={
              query.error instanceof ApiError ? query.error.message : t("knowledgeBases.error.load")
            }
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending || movingToLastPage ? (
          <TimedLoadingState
            label={t("knowledgeBases.loading")}
            operationKey="knowledge-bases-load"
            testId="knowledge-bases-loading"
          >
            <TableSkeleton columns={6} />
          </TimedLoadingState>
        ) : items.length > 0 ? (
          <div className="grid gap-2">
            <DataTable<KnowledgeBaseSummary>
              columns={knowledgeBaseColumns({ actionsFor: knowledgeBaseActions })}
              rows={items}
              getRowKey={(knowledgeBase) => knowledgeBase.id}
              // 行の操作以外の領域のクリックで詳細を開く（page-archetypes.md §0-7。業務ビューと同じ）。
              // キーボードでは先頭セルの名前のリンクで開く。アーカイブ済みも詳細は閲覧できる。
              onRowClick={(knowledgeBase) => onOpen(knowledgeBase.id)}
              rowProps={(knowledgeBase) => ({
                className: INFORMATION_TABLE_ROW_CLASS,
                "data-testid": `knowledge-base-row-${knowledgeBase.id}`,
              })}
              stickyHeader
              visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
              scrollAriaLabel={t("knowledgeBases.scrollLabel")}
              scrollTestId="knowledge-bases-scroll-region"
              tableClassName="w-full min-w-[54.29rem] text-sm"
              ariaLabel={t("knowledgeBases.list.aria")}
            />
            <ListPagination
              {...offsetPagination({ offset, limit: LIMIT, total: page?.total ?? 0, count: items.length })}
              onPageChange={(next) => setOffset(offsetForPage(next, LIMIT))}
              testId="knowledge-bases-pagination"
            />
          </div>
        ) : (
          <Card>
            {q ? (
              <EmptyState
                title={t("knowledgeBases.search.noResultsTitle")}
                hint={t("knowledgeBases.search.noResultsHint")}
                action={
                  <ClearActionButton
                    label={t("common.clearSearch")}
                    matchButtonHeight
                    onClick={() => applySearch("")}
                  />
                }
              />
            ) : (
              <EmptyState
                title={t("knowledgeBases.empty.title")}
                hint={
                  onCreate ? t("knowledgeBases.empty.hint") : t("knowledgeBases.empty.restrictedHint")
                }
                // 空の一覧から次の行動へ進めるよう、作成の入口を空の状態にも置く（ヘッダーの「新規作成」と同じ）。
                action={
                  onCreate ? (
                    <Button variant="secondary" icon={Plus} onClick={onCreate}>
                      {t("knowledgeBases.actions.createFirst")}
                    </Button>
                  ) : undefined
                }
              />
            )}
          </Card>
        )}
      </PageBody>
    </div>
  );
}

/** 一覧の列定義。名前列を行見出しにし、操作列は右寄せにする。 */
function knowledgeBaseColumns({
  actionsFor,
}: {
  actionsFor: (knowledgeBase: KnowledgeBaseSummary) => EntityAction[];
}): DataTableColumn<KnowledgeBaseSummary>[] {
  return [
    {
      key: "name",
      header: t("knowledgeBases.col.name"),
      rowHeader: true,
      className: "max-w-[18rem]",
      render: (knowledgeBase) => (
        <>
          <Link
            to={`${APP_ROUTES.knowledgeBases}/${knowledgeBase.id}`}
            className="block max-w-full font-medium text-accent-fg hover:underline"
          >
            <span className="block truncate">{knowledgeBase.name}</span>
          </Link>
          {knowledgeBase.description ? (
            <p className="mt-1 truncate text-xs text-fg-muted">{knowledgeBase.description}</p>
          ) : null}
        </>
      ),
    },
    {
      key: "status",
      header: t("knowledgeBases.col.status"),
      render: (knowledgeBase) => <KnowledgeBaseStatusPill status={knowledgeBase.status} />,
    },
    {
      key: "documents",
      header: t("knowledgeBases.col.documents"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (knowledgeBase) => formatNumber(knowledgeBase.document_count),
    },
    {
      key: "indexed",
      header: t("knowledgeBases.col.indexed"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (knowledgeBase) => formatNumber(knowledgeBase.indexed_document_count),
    },
    {
      key: "updated",
      header: t("knowledgeBases.col.updated"),
      className: "tnum text-fg-muted",
      render: (knowledgeBase) => formatDateTime(knowledgeBase.updated_at),
    },
    {
      key: "actions",
      header: t("knowledgeBases.col.actions"),
      align: "right",
      render: (knowledgeBase) => (
        <RowActionMenu
          actions={actionsFor(knowledgeBase)}
          ariaLabel={t("common.objectActions.aria", { name: knowledgeBase.name })}
          testId={`knowledge-base-row-actions-${knowledgeBase.id}`}
        />
      ),
    },
  ];
}
