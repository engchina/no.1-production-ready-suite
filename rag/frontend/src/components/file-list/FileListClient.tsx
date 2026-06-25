"use client";

import { Link } from "react-router-dom";
import { Search as SearchIcon, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "@/components/PageHeader";
import { DegradedBanner } from "@/components/DegradedBanner";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Banner } from "@/components/ui/banner";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  ApiError,
  type DocumentSummary,
  type FileStatus,
  type KnowledgeBaseRef,
} from "@/lib/api";
import {
  useDeleteDocument,
  useDocuments,
  useEnqueueDocumentIngestionJob,
  useKnowledgeBases,
} from "@/lib/queries";
import { useSelection } from "@/lib/useSelection";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { formatBytes, formatDateTime, formatNumber } from "@/lib/format";
import { toast } from "@/lib/toast";

const LIMIT = 20;
const FILTERS: (FileStatus | "ALL")[] = [
  "ALL",
  "UPLOADED",
  "PREPROCESSING",
  "INGESTING",
  "REVIEW",
  "CHUNKING",
  "CHUNKED",
  "INDEXING",
  "INDEXED",
  "ERROR",
];
const INGESTIBLE: ReadonlySet<FileStatus> = new Set(["UPLOADED", "ERROR"]);

/** 取込対象ドキュメントの一覧。絞り込み・検索・ページング・一括選択・行内アクション。 */
export function FileListClient() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [filter, setFilter] = useState<FileStatus | "ALL">("ALL");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("ALL");
  const [offset, setOffset] = useState(0);
  const [bulkIngest, setBulkIngest] = useState<{ done: number; total: number } | null>(null);
  const [bulkDelete, setBulkDelete] = useState<{ done: number; total: number } | null>(null);

  const selection = useSelection<string>();
  const status = filter === "ALL" ? undefined : filter;
  // 投入直後は UPLOADED→INGESTING の引き継ぎに数秒かかり、その瞬間はまだ非アクティブ。
  // この窓の間もポーリングを続けて取込開始を確実に拾う。
  const [graceActive, setGraceActive] = useState(false);
  const graceTimerRef = useRef<number | null>(null);
  const startGraceWindow = () => {
    setGraceActive(true);
    if (graceTimerRef.current != null) window.clearTimeout(graceTimerRef.current);
    graceTimerRef.current = window.setTimeout(() => setGraceActive(false), 30_000);
  };
  useEffect(
    () => () => {
      if (graceTimerRef.current != null) window.clearTimeout(graceTimerRef.current);
    },
    []
  );
  const query = useDocuments(
    {
      status,
      q: q || undefined,
      knowledge_base_id: knowledgeBaseId === "ALL" ? undefined : knowledgeBaseId,
      limit: LIMIT,
      offset,
    },
    { graceActive }
  );
  const knowledgeBases = useKnowledgeBases({ status: "ACTIVE", limit: 100, offset: 0 });

  const enqueueIngestion = useEnqueueDocumentIngestionJob();
  const deleteDocument = useDeleteDocument();

  const page = query.data;
  const items = page?.items ?? [];
  const pageIds = items.map((d) => d.id);
  const allSelected = pageIds.length > 0 && selection.count === pageIds.length;
  const selectedDocuments = items.filter((d) => selection.isSelected(d.id));
  const ingestibleSelected = selectedDocuments.filter((d) => INGESTIBLE.has(d.status));
  const bulkBusy = bulkIngest !== null || bulkDelete !== null;
  const knowledgeBaseOptions = useMemo<SelectFieldOption<string>[]>(
    () => [
      { value: "ALL", label: t("fileList.knowledgeBaseFilter.all") },
      ...((knowledgeBases.data?.items ?? []).map((knowledgeBase) => ({
        value: knowledgeBase.id,
        label: knowledgeBase.name,
        description: t("knowledgeBaseScope.documentCount", {
          count: knowledgeBase.document_count,
        }),
      })) satisfies SelectFieldOption<string>[]),
    ],
    [knowledgeBases.data?.items]
  );

  const resetView = (fn: () => void) => {
    fn();
    setOffset(0);
    selection.clear();
  };

  const runBulkIngest = async () => {
    const targets = ingestibleSelected.map((d) => d.id);
    if (targets.length === 0 || bulkBusy) return;
    setBulkIngest({ done: 0, total: targets.length });
    for (const [index, id] of targets.entries()) {
      try {
        await api.enqueueDocumentIngestionJob(id);
      } catch {
        // 個別失敗は継続。状態は再取得で反映される。
      }
      setBulkIngest({ done: index + 1, total: targets.length });
    }
    setBulkIngest(null);
    selection.clear();
    startGraceWindow();
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
    qc.invalidateQueries({ queryKey: ["dashboard", "summary"] });
  };

  const runBulkDelete = async () => {
    const targets = selectedDocuments.map((doc) => doc.id);
    if (targets.length === 0 || bulkBusy) return;
    const confirmed = await confirm({
      title: t("fileList.bulkDelete.confirm.title", { count: targets.length }),
      description: t("fileList.bulkDelete.confirm.description", { count: targets.length }),
      confirmLabel: t("fileList.bulkDelete.confirm.confirm"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!confirmed) return;

    setBulkDelete({ done: 0, total: targets.length });
    let deleted = 0;
    let failed = 0;
    let firstError: string | null = null;
    for (const [index, id] of targets.entries()) {
      try {
        await api.deleteDocument(id);
        deleted += 1;
      } catch (error) {
        failed += 1;
        firstError =
          firstError ??
          (error instanceof ApiError ? error.message : t("fileList.bulkDelete.toast.failedHint"));
      }
      setBulkDelete({ done: index + 1, total: targets.length });
    }
    setBulkDelete(null);
    selection.clear();
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
    qc.invalidateQueries({ queryKey: ["knowledge-bases"] });
    qc.invalidateQueries({ queryKey: ["documents", "stats"] });
    qc.invalidateQueries({ queryKey: ["dashboard", "summary"] });

    if (failed === 0) {
      toast.success(t("fileList.bulkDelete.toast.deleted", { count: deleted }));
    } else if (deleted > 0) {
      toast.warning(t("fileList.bulkDelete.toast.partial", { deleted, total: targets.length }), {
        description: firstError ?? t("fileList.bulkDelete.toast.failedHint"),
      });
    } else {
      toast.error(t("fileList.bulkDelete.toast.failed"), {
        description: firstError ?? t("fileList.bulkDelete.toast.failedHint"),
      });
    }
  };

  const runDelete = async (doc: DocumentSummary) => {
    const confirmed = await confirm({
      title: t("fileList.delete.confirm.title"),
      description: t("fileList.delete.confirm.description", { name: doc.file_name }),
      confirmLabel: t("fileList.delete.confirm.confirm"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!confirmed) return;
    try {
      const result = await deleteDocument.mutateAsync(doc.id);
      selection.clear();
      toast.success(t("fileList.delete.toast.deleted", { name: result.file_name }));
    } catch (error) {
      toast.error(
        error instanceof ApiError
          ? error.message
          : t("fileList.delete.toast.failed")
      );
    }
  };

  return (
    <div>
      <PageHeader title={t("nav.fileList")} subtitle={t("fileList.subtitle")} />
      <div className="space-y-4 p-8">
        {/* DB 停止時の縮退お知らせ(非ブロッキング) */}
        <DegradedBanner
          messages={page?.warning_messages}
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />

        {/* フィルタ + 検索 */}
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1" role="group" aria-label={t("fileList.filterAll")}>
            {FILTERS.map((f) => (
              <ToggleChip
                key={f}
                selected={filter === f}
                onClick={() => resetView(() => setFilter(f))}
              >
                {f === "ALL" ? t("fileList.filterAll") : t(`status.${f}`)}
              </ToggleChip>
            ))}
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <SelectField
              id="file-list-knowledge-base"
              label={t("fileList.knowledgeBaseFilter.label")}
              value={knowledgeBaseId}
              options={knowledgeBaseOptions}
              onValueChange={(value) => resetView(() => setKnowledgeBaseId(value))}
              className="w-60 [&_label]:text-xs"
              buttonClassName="bg-card"
            />
            <div className="relative">
              <SearchIcon
                size={15}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                aria-hidden
              />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") resetView(() => setQ(search.trim()));
                }}
                onBlur={() => resetView(() => setQ(search.trim()))}
                placeholder={t("fileList.searchPlaceholder")}
                aria-label={t("fileList.searchPlaceholder")}
                className="h-10 w-56 rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm outline-none focus-visible:border-primary"
              />
            </div>
          </div>
        </div>

        {knowledgeBases.isError ? (
          <Banner severity="warning" title={t("knowledgeBaseScope.loadWarning")}>
            <p>
              {knowledgeBases.error instanceof ApiError
                ? knowledgeBases.error.message
                : t("knowledgeBaseScope.loadWarningHint")}
            </p>
          </Banner>
        ) : null}

        {/* 一括操作バー */}
        {selection.count > 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/30 bg-info-bg/40 px-4 py-2.5">
            <span className="text-sm font-medium text-foreground">
              {t("fileList.selected", { count: selection.count })}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => void runBulkIngest()}
                loading={bulkIngest !== null}
                disabled={bulkBusy || ingestibleSelected.length === 0}
              >
                {bulkIngest === null ? <Sparkles size={14} aria-hidden /> : null}
                {bulkIngest
                  ? t("fileList.bulkQueueRunning", {
                      done: bulkIngest.done,
                      total: bulkIngest.total,
                    })
                  : `${t("fileList.bulkQueue")} (${ingestibleSelected.length})`}
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => void runBulkDelete()}
                loading={bulkDelete !== null}
                disabled={bulkBusy || selectedDocuments.length === 0}
              >
                {bulkDelete === null ? <Trash2 size={14} aria-hidden /> : null}
                {bulkDelete
                  ? t("fileList.bulkDeleteRunning", {
                      done: bulkDelete.done,
                      total: bulkDelete.total,
                    })
                  : `${t("fileList.bulkDelete")} (${selectedDocuments.length})`}
              </Button>
              <Button variant="ghost" size="sm" onClick={selection.clear} disabled={bulkBusy}>
                <X size={14} aria-hidden />
                {t("fileList.clearSelection")}
              </Button>
            </div>
          </div>
        ) : null}

        {query.isError ? (
          <ErrorState
            message={query.error instanceof ApiError ? query.error.message : "一覧の取得に失敗しました。"}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending ? (
          <Skeleton className="h-64 w-full rounded-lg" />
        ) : items.length > 0 ? (
          <>
            <Card className="overflow-hidden">
              <div className="bounded-scroll-area-lg overflow-x-auto">
                <table className="min-w-[980px] w-full text-sm">
                  <thead className="sticky top-0 z-10 bg-background text-left text-muted shadow-[inset_0_-1px_0_var(--border)]">
                    <tr>
                      <th className="w-10 px-4 py-3">
                        <input
                          type="checkbox"
                          checked={allSelected}
                          onChange={() => selection.toggleAll(pageIds)}
                          aria-label={t("fileList.selectAllAria")}
                          className="cursor-pointer accent-[var(--primary)]"
                        />
                      </th>
                      <th className="px-4 py-3 font-medium">{t("fileList.col.fileName")}</th>
                      <th className="px-4 py-3 font-medium">{t("fileList.col.knowledgeBases")}</th>
                      <th className="px-4 py-3 font-medium">{t("fileList.col.category")}</th>
                      <th className="px-4 py-3 font-medium">{t("fileList.col.status")}</th>
                      <th className="px-4 py-3 text-right font-medium">{t("fileList.col.size")}</th>
                      <th className="px-4 py-3 font-medium">{t("fileList.col.uploadedAt")}</th>
                      <th className="px-4 py-3 text-right font-medium">{t("fileList.col.actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((doc) => (
                      <Row
                        key={doc.id}
                        doc={doc}
                        selected={selection.isSelected(doc.id)}
                        onToggle={() => selection.toggle(doc.id)}
                        onIngest={(force) =>
                          enqueueIngestion.mutate(
                            { id: doc.id, force },
                            { onSuccess: startGraceWindow }
                          )
                        }
                        onDelete={() => void runDelete(doc)}
                        ingesting={
                          enqueueIngestion.isPending &&
                          enqueueIngestion.variables?.id === doc.id
                        }
                        deleting={
                          deleteDocument.isPending &&
                          deleteDocument.variables === doc.id
                        }
                        actionsDisabled={bulkBusy}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            {/* ページネーション */}
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
                    selection.clear();
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
                    selection.clear();
                  }}
                >
                  {t("pager.next")}
                </Button>
              </div>
            </div>
          </>
        ) : (
          <Card>
            <div className="p-5">
              <EmptyState title={t("fileList.empty")} />
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

function Row({
  doc,
  selected,
  onToggle,
  onIngest,
  onDelete,
  ingesting,
  deleting,
  actionsDisabled,
}: {
  doc: DocumentSummary;
  selected: boolean;
  onToggle: () => void;
  onIngest: (force: boolean) => void;
  onDelete: () => void;
  ingesting: boolean;
  deleting: boolean;
  actionsDisabled: boolean;
}) {
  return (
    <tr className={cn("border-t border-border", selected && "bg-info-bg/30")}>
      <td className="px-4 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={t("fileList.selectRowAria")}
          className="cursor-pointer accent-[var(--primary)]"
        />
      </td>
      <td className="max-w-[260px] px-4 py-3">
        <Link
          to={`${APP_ROUTES.documents}/${doc.id}`}
          className="block truncate font-medium text-primary hover:underline"
          title={doc.file_name}
        >
          {doc.file_name}
        </Link>
      </td>
      <td className="max-w-[240px] px-4 py-3">
        <KnowledgeBaseChips knowledgeBases={doc.knowledge_bases ?? []} />
      </td>
      <td className="px-4 py-3 text-muted">{doc.category_name ?? "—"}</td>
      <td className="px-4 py-3">
        <StatusBadge status={doc.status} />
      </td>
      <td className="tnum px-4 py-3 text-right text-muted">{formatBytes(doc.file_size_bytes)}</td>
      <td className="tnum px-4 py-3 text-muted">{formatDateTime(doc.uploaded_at)}</td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-2">
          {(doc.status === "UPLOADED" || doc.status === "ERROR") && (
            <Button
              size="sm"
              loading={ingesting}
              disabled={deleting || actionsDisabled}
              onClick={() => onIngest(false)}
            >
              {!ingesting ? <Sparkles size={14} aria-hidden /> : null}
              {t("action.enqueueIngestion")}
            </Button>
          )}
          <Button
            variant="danger"
            size="sm"
            loading={deleting}
            disabled={ingesting || actionsDisabled}
            onClick={onDelete}
            aria-label={t("fileList.delete.aria", { name: doc.file_name })}
          >
            {!deleting ? <Trash2 size={14} aria-hidden /> : null}
            {t("fileList.delete.action")}
          </Button>
        </div>
      </td>
    </tr>
  );
}

function KnowledgeBaseChips({ knowledgeBases }: { knowledgeBases: KnowledgeBaseRef[] }) {
  if (knowledgeBases.length === 0) {
    return <span className="text-muted">—</span>;
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {knowledgeBases.map((knowledgeBase) => (
        <span
          key={knowledgeBase.id}
          className="max-w-[12rem] truncate rounded-full border border-border bg-background px-2 py-0.5 text-xs font-medium text-foreground"
          title={knowledgeBase.name}
        >
          {knowledgeBase.name}
        </span>
      ))}
    </div>
  );
}
