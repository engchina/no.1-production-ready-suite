import {
  PageBody,
  Tabs,
  PageHeader,
  Button,
  Card,
  CardContent,
  DataTable,
  type EntityAction,
  FormStatus,
  ObjectActionBar,
  SelectField,
  type SelectFieldOption,
  StatusBadge,
  ToggleChip,
  useConfirm,
} from "@engchina/production-ready-ui";
import {
  BookmarkPlus,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  ExternalLink,
  FileText,
  FilterX,
  MessageSquareText,
  Search,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { EmptyState, ErrorState, LoadingState } from "@/components/StateViews";
import { RagSplitPane, RowTitleButton } from "@/components/layout/EntityLayout";
import {
  ApiError,
  type CitationFeedbackRating,
  type FeedbackDetail,
  type FeedbackItem,
  type FeedbackSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useBusinessViews,
  useFeedbackDashboard,
  useFeedbackDetail,
  useFeedbackEvaluationCase,
  usePromoteFeedbackToApprovedFaq,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { readWorkspace, writeWorkspace } from "@/lib/workspace-state";
import { useValuesChanged } from "@/lib/render-sync";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

import {
  FEEDBACK_PAGE_SIZES,
  FEEDBACK_PERIODS,
  FEEDBACK_REASON_LABEL_KEYS,
  FEEDBACK_REASONS,
  appendEvaluationCase,
  feedbackListParams,
  pageWindow,
  parseFeedbackUrl,
} from "./FeedbackClient.logic";

type DetailTab = "content" | "evidence" | "execution";

export function FeedbackClient() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlState = useMemo(() => parseFeedbackUrl(searchParams), [searchParams]);
  const [searchDraft, setSearchDraft] = useState(urlState.q);
  // 利用者が行を選んだ直後だけ、縦積み（xl 未満）の詳細へフォーカスを移す（URL からの復元では動かさない）。
  const revealDetailRef = useRef(false);
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

  // URL の検索語が変わったレンダーで、入力欄を URL の値に合わせる。
  const urlQueryChanged = useValuesChanged([urlState.q]);
  if (urlQueryChanged) setSearchDraft(urlState.q);

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

  // B 型の選択。行の操作以外の領域のクリック（キーボードは先頭セルの名前のボタン）で選び、
  // 選んだ行は URL の `feedback` に持つ（再読込・戻る / 進むで同じ行が開く）。
  function selectFeedback(id: string) {
    if (id === urlState.feedbackId) return;
    revealDetailRef.current = true;
    setParam("feedback", id, false);
  }

  function closeDetail() {
    const id = urlState.feedbackId;
    setParam("feedback", null, false);
    window.requestAnimationFrame(() => focusRowButton(id));
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
    ...FEEDBACK_REASONS.map((value) => ({ value, label: t(FEEDBACK_REASON_LABEL_KEYS[value]) })),
  ];
  const sortOptions: SelectFieldOption[] = [
    { value: "newest", label: t("feedback.filters.newest") },
    { value: "oldest", label: t("feedback.filters.oldest") },
  ];

  return (
    <div>
      <PageHeader wide title={t("feedback.page.title")} subtitle={t("feedback.page.subtitle")} />
      <PageBody wide>
        <Card>
          <CardContent className="space-y-4 pt-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <fieldset>
                <legend className="mb-2 text-xs font-medium text-fg-muted">
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
              <Button type="button" variant="ghost" size="sm" onClick={clearFilters} icon={FilterX}>
                {t("feedback.filters.clear")}
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-7">
              <label className="sm:col-span-2 xl:col-span-2">
                <span className="mb-1 block text-xs font-medium text-fg">
                  {t("feedback.filters.search")}
                </span>
                <span className="relative block">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-muted" aria-hidden />
                  <input
                    type="search"
                    value={searchDraft}
                    maxLength={200}
                    placeholder={t("feedback.filters.searchPlaceholder")}
                    className="h-9 w-full rounded-md border border-border-control bg-surface pl-9 pr-3 text-sm text-fg outline-none placeholder:text-fg-muted focus-visible:ring-2 focus-visible:ring-focus-ring"
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
                  <h2 id="feedback-list-heading" className="text-base font-semibold text-fg">
                    {t("feedback.list.title")}
                  </h2>
                  <p className="text-xs tabular-nums text-fg-muted">
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
                <RagSplitPane
                  splitId="feedback-list"
                  // 一覧は情報の密な表なので、一覧側を既定で広くする（詳細は divider で広げられる）。
                  preferredWidePane="left"
                  left={
                    <>
                      <FeedbackTable
                        items={page.items}
                        selectedId={urlState.feedbackId || null}
                        onSelect={selectFeedback}
                      />
                      <FeedbackCards
                        items={page.items}
                        selectedId={urlState.feedbackId || null}
                        onSelect={selectFeedback}
                      />
                      <Pagination
                        current={urlState.page}
                        total={totalPages}
                        onChange={(nextPage) => setParam("page", String(nextPage), false)}
                      />
                    </>
                  }
                  right={
                    <FeedbackDetailPanel
                      feedbackId={urlState.feedbackId || null}
                      revealRef={revealDetailRef}
                      onClose={closeDetail}
                    />
                  }
                />
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
      </PageBody>
    </div>
  );
}

/** 選択を閉じた後、その行の名前のボタンへフォーカスを戻す（表示中の表 / カードのどちらか）。 */
function focusRowButton(id: string) {
  if (!id) return;
  const candidates = Array.from(
    document.querySelectorAll<HTMLElement>(`[data-feedback-row-button="${CSS.escape(id)}"]`)
  );
  candidates.find((element) => element.offsetParent !== null)?.focus();
}

function questionSummary(item: FeedbackItem) {
  return item.question_preview ?? item.conversation_title ?? item.comment_preview ?? t("feedback.list.legacyPreview");
}

/** 問題の概要の補足（評価時間・業務ビュー・コメントの有無）。 */
function QuestionMeta({ item }: { item: FeedbackItem }) {
  return (
    <>
      <span className="tabular-nums">{formatDateTime(item.created_at)}</span>
      {" · "}
      <span>{item.business_view_name ?? t("feedback.list.unknownBusinessView")}</span>
      {item.has_comment ? (
        <span className="ml-2 inline-flex items-center gap-1">
          <MessageSquareText size={14} aria-hidden />
          {t("feedback.list.hasComment")}
        </span>
      ) : null}
    </>
  );
}

function SummaryPanel({ summary, previous }: { summary: FeedbackSummary; previous: FeedbackSummary | null }) {
  return (
    <Card>
      <CardContent className="grid gap-4 pt-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <section aria-labelledby="feedback-summary-heading">
          <h2 id="feedback-summary-heading" className="mb-3 text-sm font-semibold text-fg">
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
            <h2 id="feedback-reasons-heading" className="text-sm font-semibold text-fg">
              {t("feedback.reasons.title")}
            </h2>
            <span className="text-xs tabular-nums text-fg-muted">
              {t("feedback.summary.lowCount", { count: summary.not_helpful_count })}
            </span>
          </div>
          {summary.reason_counts.length ? (
            <div className="space-y-2">
              {summary.reason_counts.map((item) => {
                const ratio = summary.not_helpful_count ? item.count / summary.not_helpful_count : 0;
                return (
                  <div key={item.reason} className="grid grid-cols-[minmax(8rem,1fr)_minmax(5rem,1fr)_auto] items-center gap-2 text-xs">
                    <span className="truncate text-fg" title={t(FEEDBACK_REASON_LABEL_KEYS[item.reason])}>{t(FEEDBACK_REASON_LABEL_KEYS[item.reason])}</span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-surface-hover" aria-hidden>
                      <span className="block h-full rounded-full bg-danger-emphasis" style={{ width: `${Math.round(ratio * 100)}%` }} />
                    </span>
                    <span className="w-20 text-right tabular-nums text-fg-muted">{formatNumber(item.count)} / {formatRate(ratio)}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-fg-muted">{t("feedback.reasons.empty")}</p>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value, detail, delta }: { label: string; value: string; detail?: string; delta: string | null }) {
  return (
    <div className="min-w-0 border-l-2 border-accent-emphasis pl-3">
      <p className="truncate text-xs text-fg-muted" title={label}>{label}</p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-fg">{value}</p>
      <p className="mt-0.5 min-h-4 text-xs tabular-nums text-fg-muted">{detail ?? delta ?? "\u00a0"}</p>
      {detail && delta ? <p className="text-xs tabular-nums text-fg-muted">{delta}</p> : null}
    </div>
  );
}

function FeedbackTable({
  items,
  selectedId,
  onSelect,
}: {
  items: FeedbackItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  /*
    分割ペインの一覧側に置くため、列は 評価・問題の概要・理由・対象 / 送信元 に絞る（モデルと実行情報は詳細の「実行情報」）。
    折り返さない列（評価・対象）は w-px + whitespace-nowrap で内容の幅に縮め、
    理由は min-w で潰れず max-w で広がりすぎないようにし、残りの幅を問題の概要に渡す。
  */
  return (
    <DataTable<FeedbackItem>
      columns={[
        {
          key: "rating",
          header: t("feedback.filters.rating"),
          headerClassName: NOWRAP_COLUMN,
          className: cn(CELL_PAD, NOWRAP_COLUMN),
          render: (item) => <RatingBadge rating={item.rating} />,
        },
        {
          key: "question",
          header: t("feedback.table.question"),
          rowHeader: true,
          className: cn(CELL_PAD, "min-w-48"),
          render: (item) => (
            <RowTitleButton
              title={questionSummary(item)}
              subtitle={<QuestionMeta item={item} />}
              ariaLabel={t("feedback.list.selectNamed", { name: questionSummary(item) })}
              onClick={() => onSelect(item.feedback_id)}
              dataAttributes={{ "data-feedback-row-button": item.feedback_id }}
            />
          ),
        },
        {
          key: "reason",
          header: t("feedback.filters.reason"),
          headerClassName: "min-w-24",
          className: CELL_PAD,
          render: (item) => <span className="line-clamp-2 max-w-40">{item.reason ? t(FEEDBACK_REASON_LABEL_KEYS[item.reason]) : "—"}</span>,
        },
        {
          key: "targetSource",
          header: t("feedback.table.targetSource"),
          headerClassName: NOWRAP_COLUMN,
          className: cn(CELL_PAD, NOWRAP_COLUMN),
          render: (item) => targetSource(item),
        },
      ]}
      rows={items}
      getRowKey={(item) => item.feedback_id}
      onRowClick={(item) => onSelect(item.feedback_id)}
      selectedRowKey={selectedId}
      rowProps={(item) => ({ className: "align-top", "data-testid": `feedback-row-${item.feedback_id}` })}
      stickyHeader
      className="hidden max-h-[60vh] overflow-auto md:block"
      tableClassName="w-full min-w-[36rem] border-collapse"
      ariaLabel={t("feedback.list.title")}
    />
  );
}

function FeedbackCards({
  items,
  selectedId,
  onSelect,
}: {
  items: FeedbackItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="space-y-2 md:hidden" aria-label={t("feedback.list.title")}>
      {items.map((item) => {
        const current = item.feedback_id === selectedId;
        return (
          // カードの操作以外の領域のクリックで選ぶ。キーボードは問題の概要のボタンで選ぶ（page-archetypes.md §0-7）。
          <li
            key={item.feedback_id}
            aria-current={current ? "true" : undefined}
            onClick={(event) => {
              if ((event.target as HTMLElement).closest("button, a")) return;
              onSelect(item.feedback_id);
            }}
            className={cn(
              "cursor-pointer rounded-lg border p-3 transition-colors",
              current ? "border-accent-emphasis bg-accent-subtle" : "border-border bg-surface hover:bg-surface-hover"
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <RatingBadge rating={item.rating} />
              <span className="text-xs tabular-nums text-fg-muted">{formatDateTime(item.created_at)}</span>
            </div>
            <div className="mt-2">
              <RowTitleButton
                title={questionSummary(item)}
                ariaLabel={t("feedback.list.selectNamed", { name: questionSummary(item) })}
                onClick={() => onSelect(item.feedback_id)}
                dataAttributes={{ "data-feedback-row-button": item.feedback_id }}
              />
            </div>
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <Metadata label={t("feedback.filters.reason")} value={item.reason ? t(FEEDBACK_REASON_LABEL_KEYS[item.reason]) : "—"} />
              <Metadata label={t("feedback.filters.businessView")} value={item.business_view_name ?? t("feedback.list.unknownBusinessView")} />
              <Metadata label={t("feedback.table.targetSource")} value={targetSource(item)} />
              <Metadata label={t("feedback.list.model")} value={item.model ?? "—"} />
            </dl>
          </li>
        );
      })}
    </ul>
  );
}

function Pagination({ current, total, onChange }: { current: number; total: number; onChange: (page: number) => void }) {
  return (
    <nav className="mt-3 flex flex-wrap items-center justify-center gap-1" aria-label={t("feedback.pager.label")}>
      <Button type="button" variant="secondary" size="sm" disabled={current <= 1} onClick={() => onChange(current - 1)} icon={ChevronLeft}><span className="sr-only sm:not-sr-only">{t("pager.prev")}</span></Button>
      {pageWindow(current, total).map((item, index) => item === "ellipsis" ? (
        <span key={`ellipsis-${index}`} className="flex h-8 min-w-8 items-center justify-center text-sm text-fg-muted" aria-hidden>…</span>
      ) : (
        <Button key={item} type="button" variant={item === current ? "primary" : "ghost"} size="sm" className="min-w-8 px-2 tabular-nums" aria-current={item === current ? "page" : undefined} aria-label={t("feedback.pager.page", { count: item })} onClick={() => onChange(item)}>{item}</Button>
      ))}
      <Button type="button" variant="secondary" size="sm" disabled={current >= total} onClick={() => onChange(current + 1)} trailingIcon={ChevronRight}><span className="sr-only sm:not-sr-only">{t("pager.next")}</span></Button>
    </nav>
  );
}

/** B 型の詳細側。選んだフィードバックを内容 / 根拠 / 実行情報のタブで見せる。 */
function FeedbackDetailPanel({
  feedbackId,
  revealRef,
  onClose,
}: {
  feedbackId: string | null;
  revealRef: MutableRefObject<boolean>;
  onClose: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [tab, setTab] = useState<DetailTab>("content");
  const query = useFeedbackDetail(feedbackId);

  // 別のフィードバックを選んだレンダーで、タブを「内容」に戻す。
  const feedbackChanged = useValuesChanged([feedbackId]);
  if (feedbackChanged) setTab("content");

  useEffect(() => {
    if (!feedbackId || !revealRef.current) return;
    revealRef.current = false;
    // 縦積み（xl 未満）では詳細が一覧の下にあるため、選んだら詳細の見出しへ移る。横並びではフォーカスを行に残す。
    if (!window.matchMedia("(min-width: 1280px)").matches) headingRef.current?.focus();
  }, [feedbackId, revealRef]);

  if (!feedbackId) {
    return (
      <Card>
        <CardContent className="pt-5">
          <EmptyState title={t("feedback.detail.emptyTitle")} hint={t("feedback.detail.emptyHint")} />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <section aria-labelledby="feedback-detail-title" className="flex min-h-0 flex-col">
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2
              id="feedback-detail-title"
              ref={headingRef}
              tabIndex={-1}
              className="scroll-mt-4 rounded-sm text-base font-semibold text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
            >
              {t("feedback.detail.title")}
            </h2>
            <p className="mt-0.5 truncate font-mono text-xs text-fg-muted" title={feedbackId}>{shortId(feedbackId)}</p>
          </div>
          <Button type="button" variant="ghost" size="md" iconOnly aria-label={t("feedback.detail.close")} onClick={onClose} icon={X} />
        </header>
        {query.data ? <FeedbackPromotionActions detail={query.data} /> : null}
        <div className="px-4 sm:px-5">
          <Tabs
            idPrefix="feedback"
            ariaLabel={t("feedback.detail.tabsLabel")}
            value={tab}
            onChange={(value) => setTab(value as DetailTab)}
            items={(["content", "evidence", "execution"] as DetailTab[]).map((value) => ({
              id: value,
              label: t(`feedback.detail.tab.${value}` as I18nKey),
            }))}
          />
        </div>
        <div className="p-4 sm:p-5">
          {query.isLoading ? (
            <LoadingState rows={6} label={t("feedback.detail.title")} />
          ) : query.isError ? (
            <ErrorState message={t("feedback.detail.loadError")} onRetry={() => void query.refetch()} />
          ) : query.data ? (
            <DetailPanel detail={query.data} tab={tab} />
          ) : null}
        </div>
      </section>
    </Card>
  );
}

/**
 * 回答 feedback の還流（rag_poc の FAQ 昇格・eval case 昇格）。対象の操作なので ObjectActionBar に置く。
 * 結果は toast、失敗は操作の近くの FormStatus で知らせる。
 */
function FeedbackPromotionActions({ detail }: { detail: FeedbackDetail }) {
  const promote = usePromoteFeedbackToApprovedFaq();
  const evaluationCase = useFeedbackEvaluationCase();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  if (detail.target_type !== "answer") return null;

  async function handlePromote() {
    setError("");
    const confirmed = await confirm({
      title: t("feedback.promote.faqConfirmTitle"),
      description: t("feedback.promote.faqConfirmDescription"),
      confirmLabel: t("feedback.promote.faq"),
    });
    if (!confirmed) return;
    promote.mutate(detail.feedback_id, {
      onSuccess: (result) =>
        toast.success(t("feedback.promote.faqDone", { question: result.question })),
      onError: (caught) =>
        setError(caught instanceof ApiError ? caught.message : t("feedback.promote.error")),
    });
  }

  function handleEvaluationCase() {
    setError("");
    evaluationCase.mutate(detail.feedback_id, {
      onSuccess: (created) => {
        const appended = appendEvaluationCase(
          readWorkspace<string | null>("evaluation.requestJson", null, isStringOrNull),
          created
        );
        if (!appended.ok) {
          setError(t("feedback.promote.evaluationInvalidRequest"));
          return;
        }
        writeWorkspace("evaluation.requestJson", appended.json);
        toast.success(t("feedback.promote.evaluationDone"));
        navigate(APP_ROUTES.evaluation);
      },
      onError: (caught) =>
        setError(caught instanceof ApiError ? caught.message : t("feedback.promote.error")),
    });
  }

  const actions: EntityAction[] = [
    {
      id: "approved-faq",
      label: t("feedback.promote.faq"),
      icon: BookmarkPlus,
      disabled: promote.isPending,
      loading: promote.isPending,
      testId: "feedback-promote-faq",
      onSelect: () => void handlePromote(),
    },
    {
      id: "evaluation-case",
      label: t("feedback.promote.evaluation"),
      icon: ClipboardList,
      disabled: evaluationCase.isPending,
      loading: evaluationCase.isPending,
      testId: "feedback-promote-evaluation",
      onSelect: handleEvaluationCase,
    },
  ];
  return (
    <div className="space-y-1 border-b border-border px-4 py-2 sm:px-5">
      <ObjectActionBar
        actions={actions}
        ariaLabel={t("common.objectActions.aria", { name: t("feedback.detail.title") })}
        moreLabel={t("common.objectActions.more")}
        testId="feedback-detail-actions"
      />
      {error ? <FormStatus tone="danger" message={error} /> : null}
    </div>
  );
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
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
        <h3 className="text-xs font-semibold text-fg-muted">{t("feedback.detail.reason")}</h3>
        <p className="mt-1 text-sm text-fg">{detail.reason ? t(FEEDBACK_REASON_LABEL_KEYS[detail.reason]) : "—"}</p>
      </section>
      <TextSection title={t("feedback.detail.comment")} value={detail.comment} empty={t("feedback.detail.noComment")} />
      {detail.target_type === "answer" ? (
        <TextSection
          title={t("feedback.detail.correctedAnswer")}
          value={detail.corrected_answer ?? null}
          empty={t("feedback.detail.noCorrectedAnswer")}
        />
      ) : null}
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
          <article key={`${citation.chunk_id}-${index}`} className={cn("rounded-lg border border-border p-3", targeted && "border-accent-emphasis bg-accent-subtle")}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-fg">{citation.file_name ?? citation.document_id}</p>
                <p className="mt-0.5 text-xs text-fg-muted">{[citation.section_title, citation.page_number ? t("feedback.detail.page", { count: citation.page_number }) : null].filter(Boolean).join(" / ") || "—"}</p>
              </div>
              {targeted ? <StatusBadge variant="info" icon={false} label={t("feedback.detail.targetCitation")} className="shrink-0" /> : null}
            </div>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-fg/90">{citation.content_preview ?? t("feedback.detail.noCitationPreview")}</p>
            <Link to={link} className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-accent-fg hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"><ExternalLink size={14} aria-hidden />{t("feedback.list.openCitation")}</Link>
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
        {rows.map(([label, value]) => <div key={label} className="grid grid-cols-[8rem_minmax(0,1fr)] gap-3 px-3 py-2.5 text-sm"><dt className="text-fg-muted">{label}</dt><dd className="break-all font-mono text-xs leading-5 text-fg">{value}</dd></div>)}
      </dl>
      {chatLink ? <Link to={chatLink} className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-fg hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"><MessageSquareText size={14} aria-hidden />{t("feedback.list.openConversation")}</Link> : null}
    </div>
  );
}

function DetailSummary({ detail }: { detail: FeedbackDetail }) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-surface-sunken p-3">
      <RatingBadge rating={detail.rating} />
      <span className="text-xs text-fg-muted">{formatDateTime(detail.created_at)}</span>
      <span className="text-xs text-fg-muted">{detail.business_view_name ?? t("feedback.list.unknownBusinessView")}</span>
    </div>
  );
}

function LegacyNotice() {
  return <div className="rounded-lg border border-warning-border bg-warning-subtle p-3 text-sm text-warning-fg" role="status"><div className="flex items-start gap-2"><FileText size={16} className="mt-0.5 shrink-0" aria-hidden /><div><p className="font-medium">{t("feedback.detail.legacyTitle")}</p><p className="mt-1 text-xs leading-5">{t("feedback.detail.legacyHint")}</p></div></div></div>;
}

function TextSection({ title, value, empty = "—" }: { title: string; value: string | null; empty?: string }) {
  return <section><h3 className="text-xs font-semibold text-fg-muted">{title}</h3><p className="mt-1 whitespace-pre-wrap break-words rounded-lg border border-border bg-surface-sunken p-3 text-sm leading-6 text-fg">{value ?? empty}</p></section>;
}

/** 利用者の評価は状態（良し悪しの判断）なので、色だけに頼らずサムズアップ / ダウンで冗長に符号化する。 */
function RatingBadge({ rating }: { rating: CitationFeedbackRating }) {
  const helpful = rating === "helpful";
  return (
    <StatusBadge
      variant={helpful ? "success" : "danger"}
      icon={helpful ? ThumbsUp : ThumbsDown}
      label={helpful ? t("feedback.rating.helpful") : t("feedback.rating.notHelpful")}
    />
  );
}

/** 折り返さない値の列。w-px で内容の幅まで縮め、nowrap で次の列へはみ出させない。 */
const NOWRAP_COLUMN = "w-px whitespace-nowrap";
/** 明細行のセル余白（2 行の問題の概要を読みやすくするため DataTable 既定より縦を広げる）。 */
const CELL_PAD = "py-2.5";

function Metadata({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><dt className="text-fg-muted">{label}</dt><dd className="mt-0.5 line-clamp-2 text-fg">{value}</dd></div>;
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
