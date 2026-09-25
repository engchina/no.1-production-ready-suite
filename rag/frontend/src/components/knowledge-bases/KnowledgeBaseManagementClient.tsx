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
  FieldError,
  FormStatus,
  ToggleChip,
} from "@engchina/production-ready-ui";
import { Archive, Database, Search } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { DegradedBanner } from "@/components/DegradedBanner";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  DEFAULT_KNOWLEDGE_BASE_NAME,
  type KnowledgeBaseStatus,
  type KnowledgeBaseSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import {
  useArchiveKnowledgeBase,
  useCreateKnowledgeBase,
  useKnowledgeBases,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { useWorkspaceState } from "@/lib/workspace-state";
import {
  KnowledgeBaseStatusPill,
  knowledgeBaseStatusLabel,
} from "./KnowledgeBaseStatusPill";

const LIMIT = 20;

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
const NAME_ERROR_ID = "knowledge-base-name-error";

/** ナレッジベース一覧。作成・一覧・アーカイブを扱う。詳細(所属文書・構築設定)は詳細ページへ。 */
export function KnowledgeBaseManagementClient() {
  const confirm = useConfirm();
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

  const archive = useArchiveKnowledgeBase();

  const resetView = (fn: () => void) => {
    fn();
    setOffset(0);
  };

  const handleArchive = async (knowledgeBase: KnowledgeBaseSummary) => {
    const ok = await confirm({
      title: t("knowledgeBases.confirm.archive.title"),
      description: t("knowledgeBases.confirm.archive.description", {
        name: knowledgeBase.name,
      }),
      confirmLabel: t("knowledgeBases.actions.archive"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    archive.mutate(knowledgeBase.id, {
      onSuccess: () => toast.success(t("knowledgeBases.toast.archived")),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.archive")),
    });
  };

  return (
    <div>
      <PageHeader wide title={t("nav.knowledgeBases")} subtitle={t("knowledgeBases.subtitle")} />
      <PageBody wide>
        <DegradedBanner
          messages={page?.warning_messages}
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />

        <KnowledgeBaseCreateForm
          onCreated={(id) => navigate(`${APP_ROUTES.knowledgeBases}/${id}`)}
        />

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
          <div className="relative w-full sm:w-auto">
            <Search
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
              aria-hidden
            />
            <input
              type="text"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") resetView(() => setQ(search.trim()));
              }}
              onBlur={() => resetView(() => setQ(search.trim()))}
              placeholder={t("knowledgeBases.search.placeholder")}
              aria-label={t("knowledgeBases.search.placeholder")}
              className="h-9 w-full rounded-md border border-border-control bg-surface py-2 pl-9 pr-3 text-sm outline-none focus-visible:border-focus-ring sm:w-64"
            />
          </div>
        </div>

        {query.isError ? (
          <ErrorState
            message={
              query.error instanceof ApiError ? query.error.message : t("knowledgeBases.error.load")
            }
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending ? (
          <KnowledgeBaseListSkeleton />
        ) : items.length > 0 ? (
          <>
            <DataTable<KnowledgeBaseSummary>
              columns={knowledgeBaseColumns({
                archivingId: archive.isPending ? archive.variables : undefined,
                onArchive: (knowledgeBase) => void handleArchive(knowledgeBase),
              })}
              rows={items}
              getRowKey={(knowledgeBase) => knowledgeBase.id}
              stickyHeader
              className="bounded-scroll-area-lg"
              tableClassName="w-full min-w-[760px] text-sm"
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
        ) : (
          <Card>
            <EmptyState title={t("knowledgeBases.empty.title")} hint={t("knowledgeBases.empty.hint")} />
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
            <div>
              <label htmlFor="knowledge-base-name" className="text-sm font-medium text-fg">
                {t("knowledgeBases.field.name")}
              </label>
              <input
                id="knowledge-base-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                onBlur={() => setTouched(true)}
                aria-invalid={Boolean(nameError)}
                aria-describedby={nameError ? NAME_ERROR_ID : undefined}
                className="mt-1 h-9 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:border-focus-ring"
              />
              <FieldError id={NAME_ERROR_ID} message={nameError} className="mt-1" />
            </div>
            <div>
              <label
                htmlFor="knowledge-base-description"
                className="text-sm font-medium text-fg"
              >
                {t("knowledgeBases.field.description")}
              </label>
              <input
                id="knowledge-base-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-border-control bg-surface-sunken px-3 text-sm outline-none focus-visible:border-focus-ring"
              />
            </div>
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
  archivingId,
  onArchive,
}: {
  archivingId: string | undefined;
  onArchive: (knowledgeBase: KnowledgeBaseSummary) => void;
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
      render: (knowledgeBase) => {
        const isDefault = knowledgeBase.name === DEFAULT_KNOWLEDGE_BASE_NAME;
        return (
          <div className="flex justify-end">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onArchive(knowledgeBase)}
              loading={archivingId === knowledgeBase.id}
              disabled={knowledgeBase.status === "ARCHIVED" || isDefault}
              aria-label={isDefault ? t("knowledgeBases.default.archiveDisabled") : undefined}
              title={isDefault ? t("knowledgeBases.default.archiveDisabled") : undefined}
              icon={Archive}
            >
              {t("knowledgeBases.actions.archive")}
            </Button>
          </div>
        );
      },
    },
  ];
}

function validateKnowledgeBaseName(name: string) {
  const cleaned = name.trim();
  if (!cleaned) return t("knowledgeBases.validation.nameRequired");
  if (cleaned.toUpperCase() === DEFAULT_KNOWLEDGE_BASE_NAME) {
    return t("knowledgeBases.validation.nameReserved");
  }
  return null;
}

function KnowledgeBaseListSkeleton() {
  return (
    <Card className="h-80 animate-pulse">
      <div className="h-full bg-surface-sunken" />
    </Card>
  );
}
