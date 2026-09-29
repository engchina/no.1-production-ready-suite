"use client";

import {
  PageBody,
  PageHeader,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DataTable,
  type DataTableColumn,
  type EntityAction,
  FormStatus,
  RowActionMenu,
  TableSkeleton,
  TextField,
  TimedLoadingState,
  ToggleChip,
  DEFAULT_PAGE_SIZE,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  offsetForPage,
  offsetPagination,
} from "@engchina/production-ready-ui";
import { Database, Search } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { DegradedBanner } from "@/components/DegradedBanner";
import { ListPagination } from "@/components/ListPagination";
import { useAuth } from "@/components/security/AuthProvider";
import { EmptyState, ErrorState } from "@/components/StateViews";
import {
  ApiError,
  type KnowledgeBaseStatus,
  type KnowledgeBaseSummary,
} from "@/lib/api";
import { isSubmitEnter } from "@/lib/keyboard";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { CAPABILITY_PERMISSIONS } from "@/lib/permissions";
import { useCreateKnowledgeBase, useKnowledgeBases } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { useWorkspaceState } from "@/lib/workspace-state";
import {
  KnowledgeBaseStatusPill,
  knowledgeBaseStatusLabel,
} from "./KnowledgeBaseStatusPill";
import {
  DESCRIPTION_MAX_LENGTH,
  NAME_MAX_LENGTH,
  useKnowledgeBaseActions,
  validateKnowledgeBaseName,
} from "./knowledge-base-actions";

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

/** ナレッジベース一覧。作成・一覧・アーカイブを扱う。詳細(所属文書・構築設定)は詳細ページへ。 */
export function KnowledgeBaseManagementClient() {
  const navigate = useNavigate();
  // 絞り込み・検索・ページは、ページを行き来しても再読込しても残す（workspace-state.md）。
  const [view, setView] = useWorkspaceState("knowledgeBases.view", INITIAL_VIEW, isKnowledgeBaseListView);
  const { filter, q, offset } = view;
  const [search, setSearch] = useState(q);
  const setFilter = (next: KnowledgeBaseStatus | "ALL") => setView((current) => ({ ...current, filter: next }));
  const setQ = (next: string) => setView((current) => ({ ...current, q: next }));
  const setOffset = (next: number) => setView((current) => ({ ...current, offset: next }));

  const status = filter === "ALL" ? undefined : filter;
  const query = useKnowledgeBases({ status, q: q || undefined, limit: LIMIT, offset });
  const page = query.data;
  const items = useMemo(() => page?.items ?? [], [page?.items]);

  // 行の操作（アーカイブ）は詳細ページの ObjectActionBar と同じ定義を使う。
  const knowledgeBaseActions = useKnowledgeBaseActions();
  // 作成・アーカイブはナレッジベース管理の権限がある利用者だけ（#214）。
  const canManage = useAuth().hasPermission(CAPABILITY_PERMISSIONS.knowledgeBasesManage);

  const resetView = (fn: () => void) => {
    fn();
    setOffset(0);
  };
  // 検索語が変わったときだけ先頭のページへ戻す（フォーカスが外れただけでページを戻さない）。
  const applySearch = () => {
    const next = search.trim();
    if (next !== q) resetView(() => setQ(next));
  };
  // クリアは入力と適用中の検索語の両方を消す（blur を待たずに一覧を戻す）。
  const clearSearch = () => {
    setSearch("");
    if (q !== "") resetView(() => setQ(""));
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
      <PageHeader wide title={t("nav.knowledgeBases")} subtitle={t("knowledgeBases.subtitle")} />
      <PageBody wide>
        <DegradedBanner
          messages={page?.warning_messages}
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />

        {canManage ? (
          <KnowledgeBaseCreateForm
            onCreated={(id) => navigate(`${APP_ROUTES.knowledgeBases}/${id}`)}
          />
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
          <TextField
            id="knowledge-base-search"
            label={t("knowledgeBases.search.placeholder")}
            labelHidden
            value={search}
            onValueChange={setSearch}
            onKeyDown={(event) => {
              if (isSubmitEnter(event)) applySearch();
            }}
            onBlur={applySearch}
            onClear={clearSearch}
            clearLabel={t("common.clearSearch")}
            placeholder={t("knowledgeBases.search.placeholder")}
            leadingIcon={Search}
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
              rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
              stickyHeader
              visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
              scrollAriaLabel={t("knowledgeBases.scrollLabel")}
              scrollTestId="knowledge-bases-scroll-region"
              tableClassName="w-full min-w-[760px] text-sm"
            />
            <ListPagination
              {...offsetPagination({ offset, limit: LIMIT, total: page?.total ?? 0, count: items.length })}
              onPageChange={(next) => setOffset(offsetForPage(next, LIMIT))}
              testId="knowledge-bases-pagination"
            />
          </div>
        ) : (
          <Card>
            <EmptyState
              title={t("knowledgeBases.empty.title")}
              hint={
                canManage ? t("knowledgeBases.empty.hint") : t("knowledgeBases.empty.restrictedHint")
              }
            />
          </Card>
        )}
      </PageBody>
    </div>
  );
}

function KnowledgeBaseCreateForm({ onCreated }: { onCreated: (id: string) => void }) {
  const create = useCreateKnowledgeBase();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);
  // 作成前の入力があるときだけ離脱を確認する（作成成功で入力は空に戻る）。
  useLeaveGuard(Boolean(name.trim() || description.trim()));

  const nameError = touched ? validateKnowledgeBaseName(name) : null;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setTouched(true);
    if (validateKnowledgeBaseName(name)) return;
    create.mutate(
      {
        name: name.trim(),
        description: description.trim() || null,
        default_search_mode: "hybrid",
        retrieval_config: {},
      },
      {
        onSuccess: (detail) => {
          setName("");
          setDescription("");
          setTouched(false);
          onCreated(detail.id);
          toast.success(t("knowledgeBases.toast.created"));
        },
      }
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("knowledgeBases.create.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-3 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
            <TextField
              id="knowledge-base-name"
              label={t("knowledgeBases.field.name")}
              required
              requiredLabel={t("common.required")}
              value={name}
              onValueChange={setName}
              onBlur={() => setTouched(true)}
              error={nameError ?? undefined}
              maxLength={NAME_MAX_LENGTH}
            />
            <TextField
              id="knowledge-base-description"
              label={t("knowledgeBases.field.description")}
              value={description}
              onValueChange={setDescription}
              maxLength={DESCRIPTION_MAX_LENGTH}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            <Button size="lg" loading={create.isPending} type="submit" icon={Database}>
              {t("knowledgeBases.actions.create")}
            </Button>
            <FormStatus
              tone={create.isError ? "danger" : "success"}
              message={
                create.isError
                  ? create.error instanceof ApiError
                    ? create.error.message
                    : t("knowledgeBases.error.create")
                  : create.isSuccess
                    ? t("knowledgeBases.toast.created")
                    : null
              }
            />
          </div>
        </form>
      </CardContent>
    </Card>
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
