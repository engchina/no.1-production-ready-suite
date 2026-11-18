import { Banner, Button, Card, CardContent, CardHeader, CardTitle, DataTable, Disclosure, FormSkeleton, FormStatus, INFORMATION_TABLE_ROW_CLASS, INFORMATION_TABLE_VISIBLE_ROWS, OffsetPagination, PAGE_SIZE_OPTIONS, SaveErrorBanner, StatusBadge, Switch, TimedLoadingState, type StatusVariant } from "@production-ready/ui";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CircleCheck, Clock, FileText, History, Hourglass, RotateCcw, type LucideIcon } from "lucide-react";
import { useState } from "react";

import { DataLoadFailure } from "@/components/DataLoadStates";
import { api, type KnowledgeBaseWikiData, type KnowledgeBaseWikiDocument, type Page } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";

/** 構築状態の並び（凡例と一覧の表示の順）と、バッジの形。色だけにしないよう状態ごとにアイコンも変える。 */
const WIKI_STATUSES: { status: string; variant: StatusVariant; icon: LucideIcon | true }[] = [
  { status: "READY", variant: "success", icon: CircleCheck },
  { status: "RUNNING", variant: "info", icon: Hourglass },
  { status: "QUEUED", variant: "neutral", icon: Clock },
  { status: "STALE", variant: "warning", icon: History },
  { status: "NOT_BUILT", variant: "neutral", icon: true },
  { status: "FAILED", variant: "danger", icon: true },
];

function WikiStatusBadge({ status, count }: { status: string; count?: number }) {
  const badge = WIKI_STATUSES.find((item) => item.status === status) ?? WIKI_STATUSES[4];
  const label = t(`wiki.status.${badge.status}` as I18nKey);
  return <StatusBadge variant={badge.variant} icon={badge.icon} label={count == null ? label : t("wiki.legendItem", { label, count })} />;
}

function failureReason(code: string | null | undefined): string {
  if (!code) return "—";
  const key = `wiki.failure.${code}`;
  // 未知のコードは汎用の説明にする（backend が理由を足しても画面が壊れない）。
  return t((["timeout", "build_failed", "authorization_changed"].includes(code) ? key : "wiki.failure.build_failed") as I18nKey);
}

/** KB ごとの Wiki の背景構築の指定と、所属文書の構築状態（#4941・#5466）。本文は表示しない。 */
export function KnowledgeBaseWiki({ id, editable }: { id: string; editable: boolean }) {
  const cache = useQueryClient();
  // 文書ごとの状態は server 側で名前の順にページングする。表示件数とページは KB ごとに保つ。
  const paging = useClientListPaging("knowledgeBases.wikiDocumentsPaging", id);
  const limit = paging.pageSize;
  const offset = (paging.page - 1) * limit;
  const key = ["knowledge-base-wiki", id, offset, limit];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.getKnowledgeBaseWiki(id, offset, limit),
    placeholderData: keepPreviousData,
    // 構築は背景で進むので、有効な間だけ状態を読み直す。
    refetchInterval: (current) => (current.state.data?.enabled ? 5000 : false),
  });
  const [retryStatus, setRetryStatus] = useState<{ tone: "success" | "danger"; message: string } | null>(null);
  const saved = (data: KnowledgeBaseWikiData) => cache.setQueryData(key, data);
  const update = useMutation({ mutationFn: (enabled: boolean) => api.setKnowledgeBaseWiki(id, enabled, offset, limit), onSuccess: saved });
  const retry = useMutation({
    mutationFn: () => api.retryKnowledgeBaseWiki(id, offset, limit),
    // 応答は待機へ戻した後の状態。押した直後に一覧と件数が変わり、結果を起点のボタンの直下に出す。
    onSuccess: (data, failed: number) => {
      saved(data);
      setRetryStatus({ tone: "success", message: failed > 0 ? t("wiki.retryQueued", { count: failed }) : t("wiki.retryResumed") });
    },
    onError: () => setRetryStatus({ tone: "danger", message: t("wiki.retryError") }),
  });
  const data = query.data;
  const counts = data?.counts ?? {};
  const failed = counts.FAILED ?? 0;
  const ready = counts.READY ?? 0;
  const total = data?.documents?.total ?? 0;
  const blocked = data?.blocked_reason === "actor_unavailable";
  const showRetry = Boolean(data?.enabled) && (failed > 0 || blocked || retry.isPending || retryStatus != null);
  const progressLabel = failed > 0 ? t("wiki.progressFailed", { ready, total, failed }) : t("wiki.progress", { ready, total });
  return <Card data-testid="knowledge-base-wiki">
    <CardHeader><CardTitle className="flex items-center gap-2 text-base"><BookOpen className="size-4 text-fg-muted" aria-hidden />{t("wiki.title")}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      {!data && query.isPending ? <TimedLoadingState label={t("wiki.loading")}><FormSkeleton fields={1} actions={false} /></TimedLoadingState> : null}
      {!data && query.isError ? <DataLoadFailure error={query.error} fallback={t("wiki.loadError")} onRetry={() => void query.refetch()} retrying={query.isFetching} /> : null}
      {data ? <>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <p className="text-sm font-medium">{t("wiki.build")}</p>
            <p id={`wiki-build-help-${id}`} className="text-xs text-fg-muted">{t("wiki.buildHelp")}</p>
          </div>
          <Switch aria-label={t("wiki.build")} aria-describedby={`wiki-build-help-${id}`} checked={data.enabled} onCheckedChange={(enabled) => { retry.reset(); setRetryStatus(null); update.mutate(enabled); }} disabled={!editable || update.isPending} className="shrink-0" />
        </div>
        <p aria-live="polite" className="text-sm text-fg-muted">{t(data.enabled ? "wiki.enabled" : "wiki.disabled")}</p>
        {data.enabled && blocked ? <Banner severity="warning" title={t("wiki.blocked.title")}>{t("wiki.blocked.actor")}</Banner> : null}
        {total === 0 ? <p className="text-sm text-fg-muted">{t("wiki.empty")}</p> : (
          <div className="space-y-2 rounded-md border border-border bg-surface-sunken px-3 py-2" data-testid="knowledge-base-wiki-progress">
            <p aria-live="polite" className="tnum text-xs font-medium text-fg">{progressLabel}</p>
            <progress className="h-2 w-full" value={ready} max={total} aria-label={progressLabel} />
            <ul aria-label={t("wiki.legend")} className="flex flex-wrap gap-1.5">
              {WIKI_STATUSES.filter(({ status }) => (counts[status] ?? 0) > 0).map(({ status }) => <li key={status}><WikiStatusBadge status={status} count={counts[status]} /></li>)}
            </ul>
          </div>
        )}
        {showRetry ? <div className="min-w-0 space-y-2" data-testid="knowledge-base-wiki-retry">
          <p className="text-xs leading-relaxed text-fg-muted">{t(blocked ? "wiki.retryHelpBlocked" : "wiki.retryHelp")}</p>
          <div>
            <Button variant="secondary" size="sm" icon={RotateCcw} loading={retry.isPending} disabled={!editable || (failed === 0 && !blocked && !retry.isPending)} onClick={() => { update.reset(); setRetryStatus(null); retry.mutate(failed); }}>{t("wiki.retry")}</Button>
          </div>
          {retryStatus ? <FormStatus tone={retryStatus.tone} message={retryStatus.message} /> : null}
        </div> : null}
        {total > 0 && data.documents ? <WikiDocuments page={data.documents} paging={paging} expanded={failed > 0 || retryStatus != null} /> : null}
        <SaveErrorBanner message={update.error ? t("wiki.saveError") : null} attemptKey={update.submittedAt} />
      </> : null}
    </CardContent>
  </Card>;
}

/**
 * 文書ごとの構築状態。失敗があるとき・再試行の結果を出しているときは開いた状態で出す（再試行で失敗が待機に
 * 変わっても畳まず、変わった行を見せ続ける）。表は状態色の面に置かず、失敗は行の状態と理由・進捗の件数で示す。
 */
function WikiDocuments({ page, paging, expanded }: { page: Page<KnowledgeBaseWikiDocument>; paging: ReturnType<typeof useClientListPaging>; expanded: boolean }) {
  return <Disclosure
    key={expanded ? "expanded" : "collapsed"}
    defaultOpen={expanded}
    surface="sunken"
    icon={FileText}
    summary={t("wiki.documents.title")}
    description={t("wiki.documents.hint")}
    meta={<span className="tnum text-xs text-fg-muted">{t("wiki.documents.count", { count: page.total })}</span>}
    data-testid="knowledge-base-wiki-documents"
  >
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2">
      <DataTable<KnowledgeBaseWikiDocument>
        stickyHeader
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
        rows={page.items}
        getRowKey={(row) => row.document_id}
        ariaLabel={t("wiki.documents.title")}
        rowProps={() => ({ "data-testid": "knowledge-base-wiki-document", className: INFORMATION_TABLE_ROW_CLASS })}
        columns={[
          { key: "file_name", header: t("wiki.documents.column.file"), rowHeader: true, className: "min-w-[10rem] break-all font-medium text-fg", render: (row) => row.file_name || row.document_id },
          { key: "status", header: t("wiki.documents.column.status"), className: "whitespace-nowrap", render: (row) => <WikiStatusBadge status={row.status} /> },
          { key: "failure", header: t("wiki.documents.column.failure"), className: "min-w-[12rem] break-words text-xs text-fg-muted", render: (row) => failureReason(row.failure_code) },
          { key: "updated_at", header: t("wiki.documents.column.updated"), className: "tnum whitespace-nowrap text-xs text-fg-muted", render: (row) => (row.updated_at ? formatDateTime(row.updated_at) : "—") },
        ]}
      />
      <OffsetPagination
        total={page.total}
        offset={page.offset}
        limit={page.limit}
        count={page.items.length}
        onPageChange={(next) => paging.onPageChange(next)}
        onPageSizeChange={paging.onPageSizeChange}
        pageSizeOptions={PAGE_SIZE_OPTIONS}
        ariaLabel={t("wiki.documents.pagination")}
        labels={paginationLabels()}
      />
    </div>
  </Disclosure>;
}
