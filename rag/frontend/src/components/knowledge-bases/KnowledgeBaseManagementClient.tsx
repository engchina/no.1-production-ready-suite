"use client";

import { Archive, Database, Search } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { DegradedBanner } from "@/components/DegradedBanner";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FormStatus } from "@/components/ui/form-status";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  DEFAULT_KNOWLEDGE_BASE_NAME,
  type KnowledgeBaseStatus,
  type KnowledgeBaseSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useArchiveKnowledgeBase,
  useCreateKnowledgeBase,
  useKnowledgeBases,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import {
  KnowledgeBaseStatusPill,
  knowledgeBaseStatusLabel,
} from "./KnowledgeBaseStatusPill";

const LIMIT = 20;
const FILTERS: (KnowledgeBaseStatus | "ALL")[] = ["ALL", "ACTIVE", "ARCHIVED"];
const NAME_ERROR_ID = "knowledge-base-name-error";

/** ナレッジベース一覧。作成・一覧・アーカイブを扱う。詳細(所属文書・構築設定)は詳細ページへ。 */
export function KnowledgeBaseManagementClient() {
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<KnowledgeBaseStatus | "ALL">("ACTIVE");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);

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
      <PageHeader title={t("nav.knowledgeBases")} subtitle={t("knowledgeBases.subtitle")} />
      <div className="space-y-4 p-8">
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
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
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
              className="h-9 w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm outline-none focus-visible:border-primary sm:w-64"
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
            <Card className="overflow-hidden">
              <div className="bounded-scroll-area-lg overflow-x-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="sticky top-0 z-10 bg-background text-left text-muted shadow-[inset_0_-1px_0_var(--border)]">
                    <tr>
                      <th className="px-4 py-3 font-medium">{t("knowledgeBases.col.name")}</th>
                      <th className="px-4 py-3 font-medium">{t("knowledgeBases.col.status")}</th>
                      <th className="px-4 py-3 text-right font-medium">{t("knowledgeBases.col.documents")}</th>
                      <th className="px-4 py-3 text-right font-medium">{t("knowledgeBases.col.indexed")}</th>
                      <th className="px-4 py-3 font-medium">{t("knowledgeBases.col.updated")}</th>
                      <th className="px-4 py-3 text-right font-medium">{t("knowledgeBases.col.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((knowledgeBase) => (
                      <KnowledgeBaseRow
                        key={knowledgeBase.id}
                        knowledgeBase={knowledgeBase}
                        archiving={archive.isPending && archive.variables === knowledgeBase.id}
                        onArchive={() => void handleArchive(knowledgeBase)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <div className="flex items-center justify-between">
              <span className="tnum text-xs text-muted">
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
      </div>
    </div>
  );
}

function KnowledgeBaseCreateForm({ onCreated }: { onCreated: (id: string) => void }) {
  const create = useCreateKnowledgeBase();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);

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
              <label htmlFor="knowledge-base-name" className="text-sm font-medium text-foreground">
                {t("knowledgeBases.field.name")}
              </label>
              <input
                id="knowledge-base-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                onBlur={() => setTouched(true)}
                aria-invalid={Boolean(nameError)}
                aria-describedby={nameError ? NAME_ERROR_ID : undefined}
                className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary"
              />
              <FieldError id={NAME_ERROR_ID} message={nameError} className="mt-1" />
            </div>
            <div>
              <label
                htmlFor="knowledge-base-description"
                className="text-sm font-medium text-foreground"
              >
                {t("knowledgeBases.field.description")}
              </label>
              <input
                id="knowledge-base-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus-visible:border-primary"
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
            <Button size="lg" loading={create.isPending} type="submit">
              <Database size={16} aria-hidden />
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

function KnowledgeBaseRow({
  knowledgeBase,
  archiving,
  onArchive,
}: {
  knowledgeBase: KnowledgeBaseSummary;
  archiving: boolean;
  onArchive: () => void;
}) {
  const isDefault = knowledgeBase.name === DEFAULT_KNOWLEDGE_BASE_NAME;
  return (
    <tr className="border-t border-border">
      <td className="max-w-[18rem] px-4 py-3">
        <Link
          to={`${APP_ROUTES.knowledgeBases}/${knowledgeBase.id}`}
          className="block max-w-full font-medium text-primary hover:underline"
        >
          <span className="block truncate">{knowledgeBase.name}</span>
        </Link>
        {knowledgeBase.description ? (
          <p className="mt-1 truncate text-xs text-muted">{knowledgeBase.description}</p>
        ) : null}
      </td>
      <td className="px-4 py-3">
        <KnowledgeBaseStatusPill status={knowledgeBase.status} />
      </td>
      <td className="tnum px-4 py-3 text-right text-muted">
        {formatNumber(knowledgeBase.document_count)}
      </td>
      <td className="tnum px-4 py-3 text-right text-muted">
        {formatNumber(knowledgeBase.indexed_document_count)}
      </td>
      <td className="tnum px-4 py-3 text-muted">{formatDateTime(knowledgeBase.updated_at)}</td>
      <td className="px-4 py-3">
        <div className="flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            onClick={onArchive}
            loading={archiving}
            disabled={knowledgeBase.status === "ARCHIVED" || isDefault}
            aria-label={isDefault ? t("knowledgeBases.default.archiveDisabled") : undefined}
            title={isDefault ? t("knowledgeBases.default.archiveDisabled") : undefined}
          >
            <Archive size={14} aria-hidden />
            {t("knowledgeBases.actions.archive")}
          </Button>
        </div>
      </td>
    </tr>
  );
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
      <div className="h-full bg-background/60" />
    </Card>
  );
}
