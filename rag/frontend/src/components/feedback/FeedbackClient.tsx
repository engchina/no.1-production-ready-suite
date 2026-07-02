import {
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Eye,
  FileText,
  FilterX,
  MessageSquareText,
  Search,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import type {
  CitationFeedbackRating,
  CitationFeedbackReason,
  FeedbackDetail,
  FeedbackItem,
  FeedbackSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useBusinessViews,
  useFeedbackDashboard,
  useFeedbackDetail,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

import {
  FEEDBACK_PAGE_SIZES,
  FEEDBACK_PERIODS,
  feedbackListParams,
  pageWindow,
  parseFeedbackUrl,
} from "./FeedbackClient.logic";

const REASONS: CitationFeedbackReason[] = [
  "incorrect",
  "incomplete",
  "missing_evidence",
  "not_relevant",
  "answer_untrusted",
];
const REASON_LABEL_KEYS: Record<CitationFeedbackReason, I18nKey> = {
  incorrect: "feedback.reason.incorrect",
  incomplete: "feedback.reason.incomplete",
  missing_evidence: "feedback.reason.missing_evidence",
  not_relevant: "feedback.reason.not_relevant",
  answer_untrusted: "feedback.reason.answer_untrusted",
};
type DetailTab = "content" | "evidence" | "execution";

export function FeedbackClient() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlState = useMemo(() => parseFeedbackUrl(searchParams), [searchParams]);
  const [searchDraft, setSearchDraft] = useState(urlState.q);
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null);
  const params = useMemo(() => feedbackListParams(urlState), [urlState]);
  const query = useFeedbackDashboard(params);
  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 100, offset: 0 });
  const data = query.data;
  const page = data?.items;
  const totalPages = Math.max(1, Math.ceil((page?.total ?? 0) / urlState.pageSize));

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let changed = false;
    for (const [key, value] of [
      ["period", urlState.periodDays == null ? "all" : String(urlState.periodDays)],
      ["sort", urlState.sortOrder],
      ["size", String(urlState.pageSize)],
      ["page", String(urlState.page)],
    ] as const) {
      if (!next.has(key)) {
        next.set(key, value);
        changed = true;
      }
    }
    if (changed) setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, urlState]);

  useEffect(() => setSearchDraft(urlState.q), [urlState.q]);

  useEffect(() => {
    if (searchDraft === urlState.q) return;
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams(searchParams);
      if (searchDraft.trim()) next.set("q", searchDraft.trim().slice(0, 200));
      else next.delete("q");
      next.set("page", "1");
      setSearchParams(next, { replace: true });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchDraft, searchParams, setSearchParams, urlState.q]);

  useEffect(() => {
    if (!page || page.total === 0 || urlState.page <= totalPages) return;
    const next = new URLSearchParams(searchParams);
    next.set("page", String(totalPages));
    setSearchParams(next, { replace: true });
  }, [page, searchParams, setSearchParams, totalPages, urlState.page]);

  function setParam(name: string, value: string | null, resetPage = true) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(name, value);
    else next.delete(name);
    if (resetPage) next.set("page", "1");
    setSearchParams(next);
  }

  function clearFilters() {
    setSearchDraft("");
    setSearchParams(new URLSearchParams("period=30&sort=newest&size=50&page=1"));
  }

  function openDetail(id: string, trigger: HTMLButtonElement) {
    lastTriggerRef.current = trigger;
    setParam("feedback", id, false);
  }

  function closeDetail() {
    setParam("feedback", null, false);
    window.requestAnimationFrame(() => lastTriggerRef.current?.focus());
  }

  const businessViewOptions: SelectFieldOption[] = [
    { value: "", label: t("feedback.filters.allBusinessViews") },
    ...(businessViewsQuery.data?.items ?? []).map((view) => ({ value: view.id, label: view.name })),
  ];
  const targetOptions: SelectFieldOption[] = [
    { value: "", label: t("feedback.filters.allTargets") },
    { value: "answer", label: t("feedback.target.answer") },
    { value: "citation", label: t("feedback.target.citation") },
  ];
  const ratingOptions: SelectFieldOption[] = [
    { value: "", label: t("feedback.filters.allRatings") },
    { value: "helpful", label: t("feedback.rating.helpful") },
    { value: "not_helpful", label: t("feedback.rating.notHelpful") },
  ];
  const reasonOptions: SelectFieldOption[] = [
    { value: "", label: t("feedback.filters.allReasons") },
    ...REASONS.map((value) => ({ value, label: t(REASON_LABEL_KEYS[value]) })),
  ];
  const sortOptions: SelectFieldOption[] = [
    { value: "newest", label: t("feedback.filters.newest") },
    { value: "oldest", label: t("feedback.filters.oldest") },
  ];

  return (
    <div>
      <PageHeader title={t("feedback.page.title")} subtitle={t("feedback.page.subtitle")} />
      <div className="space-y-4 p-4 sm:p-6 lg:p-8">
        <Card>
          <CardContent className="space-y-4 pt-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <fieldset>
                <legend className="mb-2 text-xs font-medium text-muted">
                  {t("feedback.filters.period")}
                </legend>
                <div className="flex flex-wrap gap-1" role="group" aria-label={t("feedback.filters.period")}>
                  {FEEDBACK_PERIODS.map((days) => (
                    <ToggleChip
                      key={days ?? "all"}
                      selected={urlState.periodDays === days}
                      onClick={() => setParam("period", days == null ? "all" : String(days))}
                    >
                      {days == null
                        ? t("feedback.filters.all")
                        : t("feedback.filters.days", { count: days })}
                    </ToggleChip>
                  ))}
                </div>
              </fieldset>
              <Button type="button" variant="ghost" size="sm" onClick={clearFilters}>
                <FilterX size={14} aria-hidden />
                {t("feedback.filters.clear")}
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-7">
              <label className="sm:col-span-2 xl:col-span-2">
                <span className="mb-1 block text-xs font-medium text-foreground">
                  {t("feedback.filters.search")}
                </span>
                <span className="relative block">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
                  <input
                    type="search"
                    value={searchDraft}
                    maxLength={200}
                    placeholder={t("feedback.filters.searchPlaceholder")}
                    className="h-9 w-full rounded-md border border-border bg-card pl-9 pr-3 text-sm text-foreground outline-none placeholder:text-muted focus-visible:ring-2 focus-visible:ring-ring"
                    onChange={(event) => setSearchDraft(event.target.value)}
                  />
                </span>
              </label>
              <SelectField
                id="feedback-business-view"
                label={t("feedback.filters.businessView")}
                value={urlState.businessViewId}
                options={businessViewOptions}
                onValueChange={(value) => setParam("business_view", value)}
              />
              <SelectField
                id="feedback-target"
                label={t("feedback.filters.target")}
                value={urlState.targetType}
                options={targetOptions}
                onValueChange={(value) => setParam("target", value)}
              />
              <SelectField
                id="feedback-rating"
                label={t("feedback.filters.rating")}
                value={urlState.rating}
                options={ratingOptions}
                onValueChange={(value) => setParam("rating", value)}
              />
              <SelectField
                id="feedback-reason"
                label={t("feedback.filters.reason")}
                value={urlState.reason}
                options={reasonOptions}
                onValueChange={(value) => setParam("reason", value)}
              />
              <SelectField
                id="feedback-sort"
                label={t("feedback.filters.sort")}
                value={urlState.sortOrder}
                options={sortOptions}
                onValueChange={(value) => setParam("sort", value)}
              />
            </div>
          </CardContent>
        </Card>

        {query.isLoading ? (
          <LoadingState rows={8} label={t("feedback.page.title")} />
        ) : query.isError ? (
          <ErrorState message={t("feedback.page.loadError")} onRetry={() => void query.refetch()} />
        ) : data ? (
          <>
            <SummaryPanel summary={data.summary} previous={data.previous_summary} />
            <section aria-labelledby="feedback-list-heading">
              <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
                <div>
                  <h2 id="feedback-list-heading" className="text-base font-semibold text-foreground">
                    {t("feedback.list.title")}
                  </h2>
                  <p className="text-xs tabular-nums text-muted">
                    {t("feedback.list.range", {
                      start: page?.total ? (urlState.page - 1) * urlState.pageSize + 1 : 0,
                      end: Math.min(urlState.page * urlState.pageSize, page?.total ?? 0),
                      total: page?.total ?? 0,
                    })}
                  </p>
                </div>
                <SelectField
                  id="feedback-page-size"
                  label={t("feedback.pager.pageSize")}
                  value={String(urlState.pageSize)}
                  options={FEEDBACK_PAGE_SIZES.map((size) => ({
                    value: String(size),
                    label: t("feedback.pager.pageSizeValue", { count: size }),
                  }))}
                  onValueChange={(value) => setParam("size", value)}
                />
              </div>

              {page?.items.length ? (
                <>
                  <FeedbackTable items={page.items} onOpen={openDetail} />
                  <FeedbackCards items={page.items} onOpen={openDetail} />
                  <Pagination
                    current={urlState.page}
                    total={totalPages}
                    onChange={(nextPage) => setParam("page", String(nextPage), false)}
                  />
                </>
              ) : (
                <Card>
                  <CardContent className="pt-5">
                    <EmptyState title={t("feedback.list.empty")} hint={t("feedback.list.emptyHint")} />
                  </CardContent>
                </Card>
              )}
            </section>
          </>
        ) : null}
      </div>
      <FeedbackDetailDialog feedbackId={urlState.feedbackId || null} onClose={closeDetail} />
    </div>
  );
}

function SummaryPanel({ summary, previous }: { summary: FeedbackSummary; previous: FeedbackSummary | null }) {
  return (
    <Card>
      <CardContent className="grid gap-4 pt-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <section aria-labelledby="feedback-summary-heading">
          <h2 id="feedback-summary-heading" className="mb-3 text-sm font-semibold text-foreground">
            {t("feedback.summary.title")}
          </h2>
          <div className="grid grid-cols-2 gap-x-5 gap-y-3 sm:grid-cols-4 lg:grid-cols-2 xl:grid-cols-4">
            <Metric label={t("feedback.summary.total")} value={formatNumber(summary.total)} delta={countDelta(summary.total, previous?.total)} />
            <Metric label={t("feedback.summary.helpfulRate")} value={formatRate(summary.helpful_rate)} delta={rateDelta(summary.helpful_rate, previous?.helpful_rate)} />
            <Metric label={t("feedback.summary.answerRate")} value={formatRate(summary.answer_helpful_rate)} detail={t("feedback.summary.count", { count: summary.answer_total })} delta={rateDelta(summary.answer_helpful_rate, previous?.answer_helpful_rate)} />
            <Metric label={t("feedback.summary.citationRate")} value={formatRate(summary.citation_helpful_rate)} detail={t("feedback.summary.count", { count: summary.citation_total })} delta={rateDelta(summary.citation_helpful_rate, previous?.citation_helpful_rate)} />
          </div>
        </section>
        <section aria-labelledby="feedback-reasons-heading">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 id="feedback-reasons-heading" className="text-sm font-semibold text-foreground">
              {t("feedback.reasons.title")}
            </h2>
            <span className="text-xs tabular-nums text-muted">
              {t("feedback.summary.lowCount", { count: summary.not_helpful_count })}
            </span>
          </div>
          {summary.reason_counts.length ? (
            <div className="space-y-2">
              {summary.reason_counts.map((item) => {
                const ratio = summary.not_helpful_count ? item.count / summary.not_helpful_count : 0;
                return (
                  <div key={item.reason} className="grid grid-cols-[minmax(8rem,1fr)_minmax(5rem,1fr)_auto] items-center gap-2 text-xs">
                    <span className="truncate text-foreground" title={t(REASON_LABEL_KEYS[item.reason])}>{t(REASON_LABEL_KEYS[item.reason])}</span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-muted/20" aria-hidden>
                      <span className="block h-full rounded-full bg-danger" style={{ width: `${Math.round(ratio * 100)}%` }} />
                    </span>
                    <span className="w-20 text-right tabular-nums text-muted">{formatNumber(item.count)} / {formatRate(ratio)}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-muted">{t("feedback.reasons.empty")}</p>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value, detail, delta }: { label: string; value: string; detail?: string; delta: string | null }) {
  return (
    <div className="min-w-0 border-l-2 border-primary/30 pl-3">
      <p className="truncate text-xs text-muted" title={label}>{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-foreground">{value}</p>
      <p className="mt-0.5 min-h-4 text-[11px] tabular-nums text-muted">{detail ?? delta ?? "\u00a0"}</p>
      {detail && delta ? <p className="text-[11px] tabular-nums text-muted">{delta}</p> : null}
    </div>
  );
}

function FeedbackTable({ items, onOpen }: { items: FeedbackItem[]; onOpen: (id: string, trigger: HTMLButtonElement) => void }) {
  return (
    <div className="hidden max-h-[60vh] overflow-auto rounded-lg border border-border bg-card md:block">
      <table className="w-full min-w-[1260px] table-fixed border-collapse text-left text-xs">
        <thead className="sticky top-0 z-20 bg-background/95 text-muted shadow-[0_1px_0_var(--color-border)] backdrop-blur">
          <tr>
            <TableHead className="w-28">{t("feedback.table.time")}</TableHead>
            <TableHead className="w-32">{t("feedback.filters.rating")}</TableHead>
            <TableHead className="w-44">{t("feedback.filters.reason")}</TableHead>
            <TableHead className="w-40">{t("feedback.filters.businessView")}</TableHead>
            <TableHead className="w-32">{t("feedback.table.targetSource")}</TableHead>
            <TableHead className="w-44">{t("feedback.list.model")}</TableHead>
            <TableHead>{t("feedback.table.question")}</TableHead>
            <TableHead className="w-28 whitespace-nowrap text-right">{t("feedback.table.actions")}</TableHead>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((item) => (
            <tr key={item.feedback_id} className="align-top transition-colors hover:bg-background/70">
              <TableCell className="whitespace-nowrap tabular-nums text-muted">{formatDateTime(item.created_at)}</TableCell>
              <TableCell><RatingBadge rating={item.rating} /></TableCell>
              <TableCell className="max-w-44"><span className="line-clamp-2">{item.reason ? t(REASON_LABEL_KEYS[item.reason]) : "—"}</span></TableCell>
              <TableCell className="max-w-40"><span className="line-clamp-2">{item.business_view_name ?? t("feedback.list.unknownBusinessView")}</span></TableCell>
              <TableCell className="whitespace-nowrap">{targetSource(item)}</TableCell>
              <TableCell className="max-w-36"><span className="block truncate" title={item.model ?? undefined}>{item.model ?? "—"}</span></TableCell>
              <TableCell>
                <p className="line-clamp-2 max-w-xl text-sm leading-5 text-foreground">{item.question_preview ?? item.conversation_title ?? item.comment_preview ?? t("feedback.list.legacyPreview")}</p>
                {item.has_comment ? <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted"><MessageSquareText size={11} aria-hidden />{t("feedback.list.hasComment")}</span> : null}
              </TableCell>
              <TableCell className="text-right">
                <Button type="button" variant="secondary" size="sm" className="whitespace-nowrap" onClick={(event) => onOpen(item.feedback_id, event.currentTarget)}>
                  <Eye size={14} aria-hidden />{t("feedback.list.openDetail")}
                </Button>
              </TableCell>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FeedbackCards({ items, onOpen }: { items: FeedbackItem[]; onOpen: (id: string, trigger: HTMLButtonElement) => void }) {
  return (
    <ul className="space-y-2 md:hidden">
      {items.map((item) => (
        <li key={item.feedback_id} className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2"><RatingBadge rating={item.rating} /><span className="text-xs tabular-nums text-muted">{formatDateTime(item.created_at)}</span></div>
              <p className="mt-2 line-clamp-2 text-sm font-medium leading-5 text-foreground">{item.question_preview ?? item.conversation_title ?? item.comment_preview ?? t("feedback.list.legacyPreview")}</p>
            </div>
            <Button type="button" variant="secondary" size="sm" aria-label={t("feedback.list.openDetail")} onClick={(event) => onOpen(item.feedback_id, event.currentTarget)}><Eye size={14} aria-hidden /></Button>
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <Metadata label={t("feedback.filters.reason")} value={item.reason ? t(REASON_LABEL_KEYS[item.reason]) : "—"} />
            <Metadata label={t("feedback.filters.businessView")} value={item.business_view_name ?? t("feedback.list.unknownBusinessView")} />
            <Metadata label={t("feedback.table.targetSource")} value={targetSource(item)} />
            <Metadata label={t("feedback.list.model")} value={item.model ?? "—"} />
          </dl>
        </li>
      ))}
    </ul>
  );
}

function Pagination({ current, total, onChange }: { current: number; total: number; onChange: (page: number) => void }) {
  return (
    <nav className="mt-3 flex flex-wrap items-center justify-center gap-1" aria-label={t("feedback.pager.label")}>
      <Button type="button" variant="secondary" size="sm" disabled={current <= 1} onClick={() => onChange(current - 1)}><ChevronLeft size={14} aria-hidden /><span className="sr-only sm:not-sr-only">{t("pager.prev")}</span></Button>
      {pageWindow(current, total).map((item, index) => item === "ellipsis" ? (
        <span key={`ellipsis-${index}`} className="flex h-8 min-w-8 items-center justify-center text-sm text-muted" aria-hidden>…</span>
      ) : (
        <Button key={item} type="button" variant={item === current ? "primary" : "ghost"} size="sm" className="min-w-8 px-2 tabular-nums" aria-current={item === current ? "page" : undefined} aria-label={t("feedback.pager.page", { count: item })} onClick={() => onChange(item)}>{item}</Button>
      ))}
      <Button type="button" variant="secondary" size="sm" disabled={current >= total} onClick={() => onChange(current + 1)}><span className="sr-only sm:not-sr-only">{t("pager.next")}</span><ChevronRight size={14} aria-hidden /></Button>
    </nav>
  );
}

function FeedbackDetailDialog({ feedbackId, onClose }: { feedbackId: string | null; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<DetailTab>("content");
  const query = useFeedbackDetail(feedbackId);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (feedbackId && !dialog.open) dialog.showModal();
    if (!feedbackId && dialog.open) dialog.close();
  }, [feedbackId]);

  useEffect(() => setTab("content"), [feedbackId]);

  function close() {
    dialogRef.current?.close();
  }

  function handleTabKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const tabs: DetailTab[] = ["content", "evidence", "execution"];
    const current = tabs.indexOf(tab);
    const delta = event.key === "ArrowRight" ? 1 : -1;
    setTab(tabs[(current + delta + tabs.length) % tabs.length]);
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="feedback-detail-title"
      className="m-0 ml-auto h-dvh max-h-dvh w-full max-w-none overflow-hidden border-0 border-l border-border bg-card p-0 text-foreground shadow-2xl backdrop:bg-black/45 md:w-[min(42rem,92vw)]"
      onClose={onClose}
      onClick={(event) => { if (event.target === dialogRef.current) close(); }}
    >
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2 id="feedback-detail-title" className="text-base font-semibold text-foreground">{t("feedback.detail.title")}</h2>
            <p className="mt-0.5 truncate text-xs font-mono text-muted" title={feedbackId ?? undefined}>{feedbackId ? shortId(feedbackId) : "—"}</p>
          </div>
          <Button type="button" variant="ghost" size="md" className="size-11 px-0" aria-label={t("feedback.detail.close")} onClick={close}><X size={16} aria-hidden /></Button>
        </header>
        <div className="border-b border-border px-4 pt-2 sm:px-5" role="tablist" aria-label={t("feedback.detail.tabsLabel")} onKeyDown={handleTabKey}>
          <div className="flex gap-1 overflow-x-auto">
            {(["content", "evidence", "execution"] as DetailTab[]).map((value) => (
              <Button
                key={value}
                id={`feedback-tab-${value}`}
                type="button"
                role="tab"
                size="sm"
                variant="ghost"
                className={cn("rounded-b-none border-b-2 border-transparent", tab === value && "border-primary text-primary")}
                aria-selected={tab === value}
                aria-controls={`feedback-panel-${value}`}
                tabIndex={tab === value ? 0 : -1}
                onClick={() => setTab(value)}
              >
                {t(`feedback.detail.tab.${value}` as I18nKey)}
              </Button>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
          {query.isLoading ? (
            <LoadingState rows={6} label={t("feedback.detail.title")} />
          ) : query.isError ? (
            <ErrorState message={t("feedback.detail.loadError")} onRetry={() => void query.refetch()} />
          ) : query.data ? (
            <DetailPanel detail={query.data} tab={tab} />
          ) : null}
        </div>
      </div>
    </dialog>
  );
}

function DetailPanel({ detail, tab }: { detail: FeedbackDetail; tab: DetailTab }) {
  if (tab === "content") return <ContentTab detail={detail} />;
  if (tab === "evidence") return <EvidenceTab detail={detail} />;
  return <ExecutionTab detail={detail} />;
}

function ContentTab({ detail }: { detail: FeedbackDetail }) {
  const hasSavedText = Boolean(detail.question || detail.answer || detail.comment);
  return (
    <div id="feedback-panel-content" role="tabpanel" aria-labelledby="feedback-tab-content" className="space-y-4">
      <DetailSummary detail={detail} />
      {!hasSavedText ? <LegacyNotice /> : null}
      <TextSection title={t("feedback.detail.question")} value={detail.question} />
      <TextSection title={t("feedback.detail.answer")} value={detail.answer} />
      <section>
        <h3 className="text-xs font-semibold text-muted">{t("feedback.detail.reason")}</h3>
        <p className="mt-1 text-sm text-foreground">{detail.reason ? t(REASON_LABEL_KEYS[detail.reason]) : "—"}</p>
      </section>
      <TextSection title={t("feedback.detail.comment")} value={detail.comment} empty={t("feedback.detail.noComment")} />
    </div>
  );
}

function EvidenceTab({ detail }: { detail: FeedbackDetail }) {
  return (
    <div id="feedback-panel-evidence" role="tabpanel" aria-labelledby="feedback-tab-evidence" className="space-y-3">
      {detail.citations.length ? detail.citations.map((citation, index) => {
        const targeted = detail.target_type === "citation" && citation.chunk_id === detail.chunk_id;
        const link = `${APP_ROUTES.documents}/${encodeURIComponent(citation.document_id)}?chunk_id=${encodeURIComponent(citation.chunk_id)}`;
        return (
          <article key={`${citation.chunk_id}-${index}`} className={cn("rounded-lg border border-border p-3", targeted && "border-primary bg-primary/5")}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-foreground">{citation.file_name ?? citation.document_id}</p>
                <p className="mt-0.5 text-xs text-muted">{[citation.section_title, citation.page_number ? t("feedback.detail.page", { count: citation.page_number }) : null].filter(Boolean).join(" / ") || "—"}</p>
              </div>
              {targeted ? <span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary">{t("feedback.detail.targetCitation")}</span> : null}
            </div>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-foreground/90">{citation.content_preview ?? t("feedback.detail.noCitationPreview")}</p>
            <Link to={link} className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"><ExternalLink size={13} aria-hidden />{t("feedback.list.openCitation")}</Link>
          </article>
        );
      }) : (
        <EmptyState title={t("feedback.detail.noCitations")} hint={t("feedback.detail.noCitationsHint")} />
      )}
    </div>
  );
}

function ExecutionTab({ detail }: { detail: FeedbackDetail }) {
  const chatLink = detail.conversation_id && detail.business_view_id
    ? `${APP_ROUTES.chat}?business_view_id=${encodeURIComponent(detail.business_view_id)}&conversation_id=${encodeURIComponent(detail.conversation_id)}${detail.message_id ? `#message-${encodeURIComponent(detail.message_id)}` : ""}`
    : null;
  const rows = [
    [t("feedback.filters.businessView"), detail.business_view_name ?? "—"],
    [t("feedback.list.model"), detail.model ?? "—"],
    [t("feedback.list.trace"), detail.trace_id],
    [t("feedback.detail.outcome"), detail.execution.outcome ?? "—"],
    [t("feedback.detail.searchMode"), detail.execution.search_mode ?? "—"],
    [t("feedback.detail.elapsed"), detail.execution.elapsed_ms == null ? "—" : t("feedback.detail.elapsedValue", { count: Math.round(detail.execution.elapsed_ms) })],
    [t("feedback.detail.retrieved"), numberOrDash(detail.execution.retrieved_count)],
    [t("feedback.detail.reranked"), numberOrDash(detail.execution.reranked_count)],
    [t("feedback.detail.citationCount"), numberOrDash(detail.execution.citation_count)],
    [t("feedback.detail.guardrail"), detail.execution.guardrail_codes.join(" / ") || "—"],
    [t("feedback.detail.fingerprint"), detail.execution.config_fingerprint ?? "—"],
  ];
  return (
    <div id="feedback-panel-execution" role="tabpanel" aria-labelledby="feedback-tab-execution" className="space-y-4">
      <dl className="divide-y divide-border rounded-lg border border-border">
        {rows.map(([label, value]) => <div key={label} className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 px-3 py-2.5 text-sm"><dt className="text-muted">{label}</dt><dd className="break-all font-mono text-xs leading-5 text-foreground">{value}</dd></div>)}
      </dl>
      {chatLink ? <Link to={chatLink} className="inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"><MessageSquareText size={14} aria-hidden />{t("feedback.list.openConversation")}</Link> : null}
    </div>
  );
}

function DetailSummary({ detail }: { detail: FeedbackDetail }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-background p-3">
      <RatingBadge rating={detail.rating} />
      <span className="text-xs text-muted">{formatDateTime(detail.created_at)}</span>
      <span className="text-xs text-muted">{detail.business_view_name ?? t("feedback.list.unknownBusinessView")}</span>
    </div>
  );
}

function LegacyNotice() {
  return <div className="rounded-lg border border-warning/30 bg-warning-bg p-3 text-sm text-warning" role="status"><div className="flex items-start gap-2"><FileText size={16} className="mt-0.5 shrink-0" aria-hidden /><div><p className="font-medium">{t("feedback.detail.legacyTitle")}</p><p className="mt-1 text-xs leading-5">{t("feedback.detail.legacyHint")}</p></div></div></div>;
}

function TextSection({ title, value, empty = "—" }: { title: string; value: string | null; empty?: string }) {
  return <section><h3 className="text-xs font-semibold text-muted">{title}</h3><p className="mt-1 whitespace-pre-wrap break-words rounded-lg border border-border bg-background p-3 text-sm leading-6 text-foreground">{value ?? empty}</p></section>;
}

function RatingBadge({ rating }: { rating: CitationFeedbackRating }) {
  const helpful = rating === "helpful";
  return <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-1 text-[11px] font-medium", helpful ? "bg-success-bg text-success" : "bg-danger-bg text-danger")}>{helpful ? <ThumbsUp size={12} aria-hidden /> : <ThumbsDown size={12} aria-hidden />}{helpful ? t("feedback.rating.helpful") : t("feedback.rating.notHelpful")}</span>;
}

function TableHead({ children, className }: { children: React.ReactNode; className?: string }) {
  return <th scope="col" className={cn("px-3 py-2 font-medium", className)}>{children}</th>;
}

function TableCell({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={cn("px-3 py-2.5", className)}>{children}</td>;
}

function Metadata({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><dt className="text-muted">{label}</dt><dd className="mt-0.5 line-clamp-2 text-foreground">{value}</dd></div>;
}

function targetSource(item: FeedbackItem) {
  const target = item.target_type === "answer" ? t("feedback.target.answer") : t("feedback.target.citation");
  return `${target} / ${t(`feedback.surface.${item.source_surface ?? "unknown"}` as I18nKey)}`;
}

function formatRate(value: number) {
  return `${Math.round(value * 100)}%`;
}

function countDelta(current: number, previous: number | undefined) {
  if (previous == null) return null;
  const delta = current - previous;
  return t("feedback.summary.previousCount", { value: `${delta >= 0 ? "+" : ""}${formatNumber(delta)}` });
}

function rateDelta(current: number, previous: number | undefined) {
  if (previous == null) return null;
  const delta = Math.round((current - previous) * 100);
  return t("feedback.summary.previousPoints", { value: `${delta >= 0 ? "+" : ""}${delta}` });
}

function numberOrDash(value: number | null) {
  return value == null ? "—" : formatNumber(value);
}

function shortId(value: string) {
  return value.length > 20 ? `${value.slice(0, 20)}…` : value;
}
