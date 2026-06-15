"use client";

import { Archive, Database, FilePlus2, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  type DocumentSummary,
  type KnowledgeBaseStatus,
  type KnowledgeBaseSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useArchiveKnowledgeBase,
  useAssignDocumentsToKnowledgeBase,
  useCreateKnowledgeBase,
  useDocuments,
  useKnowledgeBases,
  useRemoveDocumentFromKnowledgeBase,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const LIMIT = 20;
const FILTERS: (KnowledgeBaseStatus | "ALL")[] = ["ALL", "ACTIVE", "ARCHIVED"];
const NAME_ERROR_ID = "knowledge-base-name-error";

/** ナレッジベース管理。作成、一覧、所属文書の追加/解除、アーカイブを扱う。 */
export function KnowledgeBaseManagementClient() {
  const confirm = useConfirm();
  const [filter, setFilter] = useState<KnowledgeBaseStatus | "ALL">("ACTIVE");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const status = filter === "ALL" ? undefined : filter;
  const query = useKnowledgeBases({ status, q: q || undefined, limit: LIMIT, offset });
  const page = query.data;
  const items = useMemo(() => page?.items ?? [], [page?.items]);
  const selected = items.find((item) => item.id === selectedId) ?? items[0] ?? null;

  useEffect(() => {
    if (!selectedId && items[0]) {
      setSelectedId(items[0].id);
      return;
    }
    if (
      selectedId &&
      items.length > 0 &&
      !query.isFetching &&
      !items.some((item) => item.id === selectedId)
    ) {
      setSelectedId(items[0].id);
    }
  }, [items, query.isFetching, selectedId]);

  const archive = useArchiveKnowledgeBase();

  const resetView = (fn: () => void) => {
    fn();
    setOffset(0);
    setSelectedId(null);
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
      <div className="grid gap-5 p-8 xl:grid-cols-[minmax(0,1fr)_25rem]">
        <div className="space-y-4">
          <KnowledgeBaseCreateForm onCreated={(id) => setSelectedId(id)} />

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
            <div className="relative">
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
                className="h-9 w-64 rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm outline-none focus-visible:border-primary"
              />
            </div>
          </div>

          {query.isError ? (
            <ErrorState
              message={
                query.error instanceof ApiError
                  ? query.error.message
                  : t("knowledgeBases.error.load")
              }
              onRetry={() => void query.refetch()}
            />
          ) : query.isPending ? (
            <KnowledgeBaseListSkeleton />
          ) : items.length > 0 ? (
            <>
              <Card className="overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-background text-left text-muted">
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
                        selected={selected?.id === knowledgeBase.id}
                        archiving={archive.isPending && archive.variables === knowledgeBase.id}
                        onSelect={() => setSelectedId(knowledgeBase.id)}
                        onArchive={() => void handleArchive(knowledgeBase)}
                      />
                    ))}
                  </tbody>
                </table>
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
                    onClick={() => {
                      setOffset(Math.max(0, offset - LIMIT));
                      setSelectedId(null);
                    }}
                  >
                    {t("pager.prev")}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!page?.has_next}
                    onClick={() => {
                      setOffset(offset + LIMIT);
                      setSelectedId(null);
                    }}
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

        <KnowledgeBaseDetailPanel knowledgeBase={selected} />
      </div>
    </div>
  );
}

function KnowledgeBaseCreateForm({ onCreated }: { onCreated: (id: string) => void }) {
  const create = useCreateKnowledgeBase();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [touched, setTouched] = useState(false);

  const nameError = touched && !name.trim() ? t("knowledgeBases.validation.nameRequired") : null;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setTouched(true);
    if (!name.trim()) return;
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
  selected,
  archiving,
  onSelect,
  onArchive,
}: {
  knowledgeBase: KnowledgeBaseSummary;
  selected: boolean;
  archiving: boolean;
  onSelect: () => void;
  onArchive: () => void;
}) {
  return (
    <tr className={cn("border-t border-border", selected && "bg-info-bg/30")}>
      <td className="max-w-[18rem] px-4 py-3">
        <button
          type="button"
          onClick={onSelect}
          className="block max-w-full cursor-pointer text-left font-medium text-primary hover:underline"
        >
          <span className="block truncate">{knowledgeBase.name}</span>
        </button>
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
            disabled={knowledgeBase.status === "ARCHIVED"}
          >
            <Archive size={14} aria-hidden />
            {t("knowledgeBases.actions.archive")}
          </Button>
        </div>
      </td>
    </tr>
  );
}

function KnowledgeBaseStatusPill({ status }: { status: KnowledgeBaseStatus }) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full px-2 py-0.5 text-xs font-medium",
        status === "ACTIVE" && "bg-success-bg text-success",
        status === "ARCHIVED" && "bg-muted/10 text-muted"
      )}
    >
      {knowledgeBaseStatusLabel(status)}
    </span>
  );
}

function KnowledgeBaseDetailPanel({ knowledgeBase }: { knowledgeBase: KnowledgeBaseSummary | null }) {
  if (!knowledgeBase) {
    return (
      <Card className="h-fit">
        <EmptyState
          title={t("knowledgeBases.detail.empty.title")}
          hint={t("knowledgeBases.detail.empty.hint")}
        />
      </Card>
    );
  }

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle>{t("knowledgeBases.detail.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-5">
          <div>
            <div className="flex items-center justify-between gap-3">
              <h2 className="min-w-0 truncate text-base font-semibold text-foreground">
                {knowledgeBase.name}
              </h2>
              <KnowledgeBaseStatusPill status={knowledgeBase.status} />
            </div>
            {knowledgeBase.description ? (
              <p className="mt-1 text-sm text-muted">{knowledgeBase.description}</p>
            ) : null}
          </div>

          <div className="grid grid-cols-3 gap-2">
            <Metric label={t("knowledgeBases.metric.documents")} value={knowledgeBase.document_count} />
            <Metric label={t("knowledgeBases.metric.indexed")} value={knowledgeBase.indexed_document_count} />
            <Metric label={t("knowledgeBases.metric.errors")} value={knowledgeBase.error_document_count} />
          </div>

          {knowledgeBase.status === "ACTIVE" ? (
            <DocumentAssignment knowledgeBase={knowledgeBase} />
          ) : (
            <p className="rounded-md border border-border bg-background px-3 py-2 text-sm text-muted">
              {t("knowledgeBases.detail.archivedHint")}
            </p>
          )}

          <KnowledgeBaseDocuments knowledgeBase={knowledgeBase} />
        </div>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="tnum mt-1 text-lg font-semibold text-foreground">{formatNumber(value)}</p>
    </div>
  );
}

function DocumentAssignment({ knowledgeBase }: { knowledgeBase: KnowledgeBaseSummary }) {
  const allDocuments = useDocuments({ limit: 100, offset: 0 });
  const assign = useAssignDocumentsToKnowledgeBase();
  const [documentId, setDocumentId] = useState("");

  const options = useMemo(() => {
    const documents = allDocuments.data?.items ?? [];
    return documents.filter((document) => !documentHasKnowledgeBase(document, knowledgeBase.id));
  }, [allDocuments.data?.items, knowledgeBase.id]);

  const selectOptions = useMemo<SelectFieldOption[]>(
    () => options.map((document) => ({ value: document.id, label: document.file_name })),
    [options]
  );

  useEffect(() => {
    if (!documentId && options[0]) {
      setDocumentId(options[0].id);
      return;
    }
    if (documentId && options.length > 0 && !options.some((document) => document.id === documentId)) {
      setDocumentId(options[0].id);
    }
  }, [documentId, options]);

  const handleAssign = () => {
    if (!documentId) return;
    assign.mutate(
      { id: knowledgeBase.id, documentIds: [documentId] },
      {
        onSuccess: () => {
          setDocumentId("");
          toast.success(t("knowledgeBases.toast.assigned"));
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.assign")),
      }
    );
  };

  return (
    <div className="space-y-2 border-t border-border pt-4">
      <div className="flex items-end gap-2">
        <SelectField
          id="knowledge-base-add-document"
          label={t("knowledgeBases.assignment.title")}
          value={documentId}
          options={selectOptions}
          onValueChange={setDocumentId}
          placeholder={t("knowledgeBases.assignment.noOptions")}
          className="min-w-0 flex-1"
          buttonClassName="h-9"
        />
        <Button
          type="button"
          variant="secondary"
          size="md"
          onClick={handleAssign}
          loading={assign.isPending}
          disabled={!documentId}
        >
          <FilePlus2 size={15} aria-hidden />
          {t("knowledgeBases.actions.assign")}
        </Button>
      </div>
      {allDocuments.isError ? (
        <FormStatus
          tone="danger"
          message={
            allDocuments.error instanceof ApiError
              ? allDocuments.error.message
              : t("knowledgeBases.error.documents")
          }
        />
      ) : null}
    </div>
  );
}

function KnowledgeBaseDocuments({ knowledgeBase }: { knowledgeBase: KnowledgeBaseSummary }) {
  const confirm = useConfirm();
  const documents = useDocuments({ knowledge_base_id: knowledgeBase.id, limit: 50, offset: 0 });
  const remove = useRemoveDocumentFromKnowledgeBase();

  const handleRemove = async (document: DocumentSummary) => {
    const ok = await confirm({
      title: t("knowledgeBases.confirm.remove.title"),
      description: t("knowledgeBases.confirm.remove.description", {
        fileName: document.file_name,
        name: knowledgeBase.name,
      }),
      confirmLabel: t("knowledgeBases.actions.remove"),
      tone: "warning",
    });
    if (!ok) return;
    remove.mutate(
      { knowledgeBaseId: knowledgeBase.id, documentId: document.id },
      {
        onSuccess: () => toast.success(t("knowledgeBases.toast.removed")),
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.remove")),
      }
    );
  };

  return (
    <div className="space-y-2 border-t border-border pt-4">
      <h3 className="text-sm font-medium text-foreground">{t("knowledgeBases.documents.title")}</h3>
      {documents.isError ? (
        <ErrorState
          message={
            documents.error instanceof ApiError
              ? documents.error.message
              : t("knowledgeBases.error.documents")
          }
          onRetry={() => void documents.refetch()}
        />
      ) : documents.isPending ? (
        <KnowledgeBaseDocumentsSkeleton />
      ) : documents.data.items.length > 0 ? (
        <ul className="divide-y divide-border rounded-md border border-border">
          {documents.data.items.map((document) => (
            <li key={document.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <Link
                to={`${APP_ROUTES.documents}/${document.id}`}
                className="min-w-0 truncate text-sm font-medium text-primary hover:underline"
                title={document.file_name}
              >
                {document.file_name}
              </Link>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void handleRemove(document)}
                loading={remove.isPending && remove.variables?.documentId === document.id}
              >
                <Trash2 size={14} aria-hidden />
                {t("knowledgeBases.actions.remove")}
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          title={t("knowledgeBases.documents.empty.title")}
          hint={t("knowledgeBases.documents.empty.hint")}
        />
      )}
    </div>
  );
}

function documentHasKnowledgeBase(document: DocumentSummary, knowledgeBaseId: string) {
  return document.knowledge_bases.some((knowledgeBase) => knowledgeBase.id === knowledgeBaseId);
}

function knowledgeBaseStatusLabel(status: KnowledgeBaseStatus) {
  return status === "ACTIVE"
    ? t("knowledgeBases.status.ACTIVE")
    : t("knowledgeBases.status.ARCHIVED");
}

function KnowledgeBaseListSkeleton() {
  return (
    <Card className="h-80 animate-pulse">
      <div className="h-full bg-background/60" />
    </Card>
  );
}

function KnowledgeBaseDocumentsSkeleton() {
  return (
    <div className="space-y-2" role="status" aria-label={t("knowledgeBases.documents.loading")}>
      <div className="h-9 rounded-md bg-background" />
      <div className="h-9 rounded-md bg-background" />
      <div className="h-9 rounded-md bg-background" />
    </div>
  );
}
