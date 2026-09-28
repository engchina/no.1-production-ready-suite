"use client";

import {
  PageBody,
  PageHeader,
  Button,
  Card,
  Banner,
  DataTable,
  type DataTableColumn,
  type EntityAction,
  RowActionMenu,
  SelectField,
  type SelectFieldOption,
  ToggleChip,
  Skeleton,
} from "@engchina/production-ready-ui";
import { Link } from "react-router-dom";
import { RotateCcw, Search as SearchIcon, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { DegradedBanner } from "@/components/DegradedBanner";
import { StatusBadge } from "@/components/StatusBadge";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { EmptyState, ErrorState } from "@/components/StateViews";
import {
  api,
  ApiError,
  type DocumentDeleteImpact,
  type DocumentSummary,
  type FileStatus,
  type IngestionJob,
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
import { formatBytes, formatDateTime, formatNumber } from "@/lib/format";
import { toast } from "@/lib/toast";
import { useWorkspaceState } from "@/lib/workspace-state";
import { ingestionSkipReasonLabel } from "@/lib/source-profile-labels";

import {
  classifyEnqueuedJob,
  FILE_LIST_FILTERS as FILTERS,
  FILE_LIST_QUERY_MAX_LENGTH,
  INITIAL_FILE_LIST_VIEW as INITIAL_VIEW,
  isFileListView,
  outOfRangeOffset,
  summarizeEnqueueOutcomes,
  type EnqueueOutcome,
} from "./FileListClient.logic";
import { deleteConfirmDescription } from "./document-delete-impact";

const LIMIT = 20;
const INGESTIBLE: ReadonlySet<FileStatus> = new Set(["UPLOADED", "ERROR"]);

/** 取込対象ドキュメントの一覧。絞り込み・検索・ページング・一括選択・行内アクション。 */
export function FileListClient() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  // 絞り込み・検索・ページは、ページを行き来しても再読込しても残す（workspace-state.md）。
  // 一括削除につながる行の選択は保存せず、ページを離れたら解除する。
  const [view, setView] = useWorkspaceState("fileList.view", INITIAL_VIEW, isFileListView);
  const { filter, q, knowledgeBaseId, offset } = view;
  const [search, setSearch] = useState(q);
  const setFilter = (next: FileStatus | "ALL") => setView((current) => ({ ...current, filter: next }));
  const setQ = (next: string) => setView((current) => ({ ...current, q: next }));
  const setKnowledgeBaseId = (next: string) =>
    setView((current) => ({ ...current, knowledgeBaseId: next }));
  const setOffset = (next: number) => setView((current) => ({ ...current, offset: next }));
  const [bulkIngest, setBulkIngest] = useState<{ done: number; total: number } | null>(null);
  const [bulkDelete, setBulkDelete] = useState<{ done: number; total: number } | null>(null);
  // 削除の影響（正本を参照する重複文書）を確かめている間は、ほかの操作と同じ削除を止める（#303）。
  const [deleteImpactPending, setDeleteImpactPending] = useState(false);

  const selection = useSelection<string>();
  const status = filter === "ALL" ? undefined : filter;
  // 投入直後は UPLOADED→INGESTING の引き継ぎに数秒かかり、その瞬間はまだ非アクティブ。
  // この窓の間もポーリングを続けて取込開始を確実に拾う。
  const [graceActive, setGraceActive] = useState(false);
  // 窓を開き直すたびに増やす。増えるたびに 30 秒のタイマーを掛け直す（前のタイマーは cleanup で消える）。
  const [graceWindow, setGraceWindow] = useState(0);
  const startGraceWindow = () => {
    setGraceActive(true);
    setGraceWindow((current) => current + 1);
  };
  useEffect(() => {
    if (graceWindow === 0) return;
    const timer = window.setTimeout(() => setGraceActive(false), 30_000);
    return () => window.clearTimeout(timer);
  }, [graceWindow]);
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
  // 復元した KB 絞り込みが削除・アーカイブ済みなら、その条件だけ「すべて」に戻す。
  const knowledgeBaseFilterMissing =
    knowledgeBaseId !== "ALL" &&
    Boolean(knowledgeBases.data) &&
    !knowledgeBases.data?.has_next &&
    !knowledgeBases.data?.items.some((knowledgeBase) => knowledgeBase.id === knowledgeBaseId);
  useEffect(() => {
    if (knowledgeBaseFilterMissing) {
      setView((current) => ({ ...current, knowledgeBaseId: "ALL", offset: 0 }));
    }
  }, [knowledgeBaseFilterMissing, setView]);

  const enqueueIngestion = useEnqueueDocumentIngestionJob();
  const deleteDocument = useDeleteDocument();

  const page = query.data;
  const items = page?.items ?? [];
  const pageIds = items.map((d) => d.id);
  // 再取得で一覧から消えた行（他の画面で削除・状態の絞り込みから外れた）は選択に数えない。
  const selectedDocuments = items.filter((d) => selection.isSelected(d.id));
  const selectedCount = selectedDocuments.length;
  const allSelected = pageIds.length > 0 && selectedCount === pageIds.length;

  // 表示中のページが範囲外になったら（最後のページの最後の行を削除した等）、残っている最後の
  // ページへ移す。空のページに「該当なし」とだけ出してページ送りも消える状態にしない（#281）。
  // DB の縮退応答（warning 付きの空一覧）ではページ位置を保つ。
  const correctedOffset =
    page && !query.isPlaceholderData && (page.warning_messages?.length ?? 0) === 0
      ? outOfRangeOffset({ offset, total: page.total, limit: LIMIT })
      : null;
  useEffect(() => {
    if (correctedOffset !== null) {
      setView((current) => ({ ...current, offset: correctedOffset }));
    }
  }, [correctedOffset, setView]);
  const ingestibleSelected = selectedDocuments.filter((d) => INGESTIBLE.has(d.status));
  const bulkBusy = bulkIngest !== null || bulkDelete !== null || deleteImpactPending;
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

  // 検索語が変わったときだけ先頭ページへ戻し選択を解除する。入力欄から focus を外しただけで
  // ページ位置と選択を失わない（一括操作のボタンを押す直前の blur で選択が消えていた。#281）。
  const commitSearch = () => {
    const next = search.trim();
    if (next === q) return;
    resetView(() => setQ(next));
  };

  const runRowIngest = (doc: DocumentSummary) => {
    enqueueIngestion.mutate(
      { id: doc.id, force: false },
      {
        onSuccess: (job) => {
          startGraceWindow();
          notifyRowEnqueued(doc, job);
        },
        onError: (error) => {
          toast.error(t("fileList.ingest.toast.failed", { name: doc.file_name }), {
            description:
              error instanceof ApiError ? error.message : t("fileList.ingest.toast.failedHint"),
          });
        },
      }
    );
  };

  const runBulkIngest = async () => {
    const targets = ingestibleSelected.map((d) => d.id);
    if (targets.length === 0 || bulkBusy) return;
    setBulkIngest({ done: 0, total: targets.length });
    const outcomes: EnqueueOutcome[] = [];
    for (const [index, id] of targets.entries()) {
      try {
        outcomes.push(classifyEnqueuedJob(await api.enqueueDocumentIngestionJob(id)));
      } catch (error) {
        // 個別失敗は継続し、最後にまとめて知らせる。
        outcomes.push({
          kind: "failed",
          message:
            error instanceof ApiError ? error.message : t("fileList.ingest.toast.failedHint"),
        });
      }
      setBulkIngest({ done: index + 1, total: targets.length });
    }
    setBulkIngest(null);
    selection.clear();
    startGraceWindow();
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["documents", "ingestion-jobs"] });
    notifyBulkEnqueued(summarizeEnqueueOutcomes(outcomes), targets.length);
  };

  const loadDeleteImpacts = async (ids: string[]): Promise<DocumentDeleteImpact[] | null> => {
    setDeleteImpactPending(true);
    try {
      return await api.getDocumentDeleteImpact(ids);
    } catch (error) {
      toast.error(t("fileList.delete.impact.loadFailed"), {
        description:
          error instanceof ApiError ? error.message : t("fileList.delete.impact.loadFailedHint"),
      });
      return null;
    } finally {
      setDeleteImpactPending(false);
    }
  };

  const runBulkDelete = async () => {
    const targets = selectedDocuments.map((doc) => doc.id);
    if (targets.length === 0 || bulkBusy) return;
    const impacts = await loadDeleteImpacts(targets);
    if (!impacts) return;
    const confirmed = await confirm({
      title: t("fileList.bulkDelete.confirm.title", { count: targets.length }),
      description: deleteConfirmDescription(
        t("fileList.bulkDelete.confirm.description", { count: targets.length }),
        impacts,
        { bulk: true }
      ),
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
    if (deleteImpactPending) return;
    const impacts = await loadDeleteImpacts([doc.id]);
    if (!impacts) return;
    const confirmed = await confirm({
      title: t("fileList.delete.confirm.title"),
      description: deleteConfirmDescription(
        t("fileList.delete.confirm.description", { name: doc.file_name }),
        impacts,
        { bulk: false }
      ),
      confirmLabel: t("fileList.delete.confirm.confirm"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!confirmed) return;
    try {
      const result = await deleteDocument.mutateAsync(doc.id);
      selection.clear();
      // 文書は消えたが原本・artifact の削除に失敗した警告は、成功として黙らせない（#281）。
      const warnings = result.warning_messages ?? [];
      if (warnings.length > 0) {
        toast.warning(t("fileList.delete.toast.deletedWithWarning", { name: result.file_name }), {
          description: warnings.join(" "),
        });
      } else {
        toast.success(t("fileList.delete.toast.deleted", { name: result.file_name }));
      }
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
      <PageHeader wide title={t("nav.fileList")} subtitle={t("fileList.subtitle")} />
      <PageBody wide>
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
              buttonClassName="bg-surface"
            />
            <div className="relative">
              <SearchIcon
                size={16}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted"
                aria-hidden
              />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitSearch();
                }}
                onBlur={commitSearch}
                maxLength={FILE_LIST_QUERY_MAX_LENGTH}
                placeholder={t("fileList.searchPlaceholder")}
                aria-label={t("fileList.searchPlaceholder")}
                className="h-10 w-56 rounded-md border border-border-control bg-surface py-2 pl-9 pr-3 text-sm outline-none focus-visible:border-focus-ring"
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
        {selectedCount > 0 || bulkBusy ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-accent-emphasis bg-info-subtle px-4 py-2.5">
            {/* 進み具合はボタンのラベルではなくここに出す（loading 中にラベルを差し替えない）。 */}
            <span className="text-sm font-medium text-fg" role="status">
              {bulkIngest
                ? t("fileList.bulkQueueRunning", { done: bulkIngest.done, total: bulkIngest.total })
                : bulkDelete
                  ? t("fileList.bulkDeleteRunning", {
                      done: bulkDelete.done,
                      total: bulkDelete.total,
                    })
                  : t("fileList.selected", { count: selectedCount })}
            </span>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={() => void runBulkIngest()}
                loading={bulkIngest !== null}
                disabled={bulkBusy || ingestibleSelected.length === 0} icon={Sparkles}>
                {`${t("fileList.bulkQueue")} (${ingestibleSelected.length})`}
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => void runBulkDelete()}
                loading={bulkDelete !== null}
                disabled={bulkBusy || selectedDocuments.length === 0} icon={Trash2}>
                {`${t("fileList.bulkDelete")} (${selectedDocuments.length})`}
              </Button>
              <Button variant="ghost" size="sm" onClick={selection.clear} disabled={bulkBusy} icon={X}>
                {t("fileList.clearSelection")}
              </Button>
            </div>
          </div>
        ) : null}

        {query.isError ? (
          <ErrorState
            message={query.error instanceof ApiError ? query.error.message : t("fileList.loadError")}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending ? (
          <Skeleton className="h-64 w-full rounded-lg" />
        ) : items.length > 0 ? (
          <>
            <DataTable<DocumentSummary>
              columns={documentColumns({
                allSelected,
                onToggleAll: () => selection.toggleAll(pageIds),
                isSelected: (doc) => selection.isSelected(doc.id),
                onToggle: (doc) => selection.toggle(doc.id),
                onIngest: runRowIngest,
                onDelete: (doc) => void runDelete(doc),
                isIngesting: (doc) =>
                  enqueueIngestion.isPending &&
                  enqueueIngestion.variables?.id === doc.id,
                isDeleting: (doc) =>
                  deleteDocument.isPending && deleteDocument.variables === doc.id,
                // 一括選択中は行の操作を止め、一括操作のバーに集める（buttons.md §5.1）。
                actionsDisabled: bulkBusy || selectedCount > 0,
              })}
              rows={items}
              getRowKey={(doc) => doc.id}
              isRowSelected={(doc) => selection.isSelected(doc.id)}
              stickyHeader
              className="bounded-scroll-area-lg"
              tableClassName="w-full min-w-[980px] text-sm"
            />

            {/* ページネーション */}
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
      </PageBody>
    </div>
  );
}

/** 行の「ファイル準備を実行」の結果を知らせる。SKIPPED は状態が変わらないため理由を出す。 */
function notifyRowEnqueued(doc: DocumentSummary, job: IngestionJob) {
  const outcome = classifyEnqueuedJob(job);
  if (outcome.kind === "skipped") {
    toast.warning(t("fileList.ingest.toast.skipped", { name: doc.file_name }), {
      description: ingestionSkipReasonLabel(outcome.job.skip_reason),
    });
    return;
  }
  toast.info(t("fileList.ingest.toast.queued", { name: doc.file_name }));
}

/** 一括投入の結果を 1 回だけ知らせる。部分失敗・スキップを成功として黙らせない。 */
function notifyBulkEnqueued(summary: ReturnType<typeof summarizeEnqueueOutcomes>, total: number) {
  if (summary.skipped === 0 && summary.failed === 0) {
    toast.info(t("fileList.bulkQueue.toast.queued", { count: summary.queued }));
    return;
  }
  const reason =
    summary.firstError ??
    (summary.firstSkipReason !== null || summary.skipped > 0
      ? ingestionSkipReasonLabel(summary.firstSkipReason)
      : "");
  const description = t("fileList.bulkQueue.toast.detail", {
    skipped: summary.skipped,
    failed: summary.failed,
    reason,
  });
  if (summary.queued === 0) {
    toast.error(t("fileList.bulkQueue.toast.failed"), { description });
    return;
  }
  toast.warning(t("fileList.bulkQueue.toast.partial", { queued: summary.queued, total }), {
    description,
  });
}

/** 一覧の列定義。先頭列は一括選択のチェックボックス、ファイル名列を行見出しにする。 */
function documentColumns({
  allSelected,
  onToggleAll,
  isSelected,
  onToggle,
  onIngest,
  onDelete,
  isIngesting,
  isDeleting,
  actionsDisabled,
}: {
  allSelected: boolean;
  onToggleAll: () => void;
  isSelected: (doc: DocumentSummary) => boolean;
  onToggle: (doc: DocumentSummary) => void;
  onIngest: (doc: DocumentSummary) => void;
  onDelete: (doc: DocumentSummary) => void;
  isIngesting: (doc: DocumentSummary) => boolean;
  isDeleting: (doc: DocumentSummary) => boolean;
  actionsDisabled: boolean;
}): DataTableColumn<DocumentSummary>[] {
  return [
    {
      key: "select",
      header: (
        <input
          type="checkbox"
          checked={allSelected}
          onChange={onToggleAll}
          aria-label={t("fileList.selectAllAria")}
          className="cursor-pointer accent-[var(--color-accent-emphasis)]"
        />
      ),
      headerClassName: "w-10",
      render: (doc) => (
        <input
          type="checkbox"
          checked={isSelected(doc)}
          onChange={() => onToggle(doc)}
          aria-label={t("fileList.selectRowAria")}
          className="cursor-pointer accent-[var(--color-accent-emphasis)]"
        />
      ),
    },
    {
      key: "fileName",
      header: t("fileList.col.fileName"),
      rowHeader: true,
      className: "max-w-[260px]",
      render: (doc) => (
        <Link
          to={`${APP_ROUTES.documents}/${doc.id}`}
          className="block truncate font-medium text-accent-fg hover:underline"
          title={doc.file_name}
        >
          {doc.file_name}
        </Link>
      ),
    },
    {
      key: "knowledgeBases",
      header: t("fileList.col.knowledgeBases"),
      className: "max-w-[240px]",
      render: (doc) => <KnowledgeBaseChips knowledgeBases={doc.knowledge_bases ?? []} />,
    },
    {
      key: "category",
      header: t("fileList.col.category"),
      className: "text-fg-muted",
      render: (doc) => doc.category_name ?? "—",
    },
    {
      key: "status",
      header: t("fileList.col.status"),
      render: (doc) => <StatusBadge status={doc.status} />,
    },
    {
      key: "size",
      header: t("fileList.col.size"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (doc) => formatBytes(doc.file_size_bytes),
    },
    {
      key: "uploadedAt",
      header: t("fileList.col.uploadedAt"),
      className: "tnum text-fg-muted",
      render: (doc) => formatDateTime(doc.uploaded_at),
    },
    {
      key: "actions",
      header: t("fileList.col.actions"),
      align: "right",
      render: (doc) => (
        <RowActionMenu
          actions={documentActions(doc, { onIngest, onDelete, isIngesting, isDeleting })}
          ariaLabel={t("common.objectActions.aria", { name: doc.file_name })}
          loading={isIngesting(doc) || isDeleting(doc)}
          disabled={actionsDisabled}
          testId={`file-list-row-actions-${doc.id}`}
        />
      ),
    },
  ];
}

/**
 * 文書 1 件に対する操作（buttons.md §5.1）。行は RowActionMenu 1 個にまとめ、
 * 削除は danger の項目として確認ダイアログ（runDelete の useConfirm）を通す。
 */
function documentActions(
  doc: DocumentSummary,
  {
    onIngest,
    onDelete,
    isIngesting,
    isDeleting,
  }: {
    onIngest: (doc: DocumentSummary) => void;
    onDelete: (doc: DocumentSummary) => void;
    isIngesting: (doc: DocumentSummary) => boolean;
    isDeleting: (doc: DocumentSummary) => boolean;
  }
): EntityAction[] {
  const ingesting = isIngesting(doc);
  const deleting = isDeleting(doc);
  return [
    {
      id: "ingest",
      label: t(doc.status === "ERROR" ? "flow.retry.preprocess" : "action.enqueueIngestion"),
      icon: doc.status === "ERROR" ? RotateCcw : Sparkles,
      visible: INGESTIBLE.has(doc.status),
      loading: ingesting,
      disabled: deleting,
      onSelect: () => onIngest(doc),
    },
    {
      id: "delete",
      label: t("fileList.delete.action"),
      ariaLabel: t("fileList.delete.aria", { name: doc.file_name }),
      icon: Trash2,
      tone: "danger",
      loading: deleting,
      disabled: ingesting,
      onSelect: () => onDelete(doc),
    },
  ];
}

function KnowledgeBaseChips({ knowledgeBases }: { knowledgeBases: KnowledgeBaseRef[] }) {
  if (knowledgeBases.length === 0) {
    return <span className="text-fg-muted">—</span>;
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {knowledgeBases.map((knowledgeBase) => (
        <span
          key={knowledgeBase.id}
          className="max-w-[12rem] truncate rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs font-medium text-fg"
          title={knowledgeBase.name}
        >
          {knowledgeBase.name}
        </span>
      ))}
    </div>
  );
}
