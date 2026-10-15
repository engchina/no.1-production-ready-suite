import {
  PageBody,
  Tabs,
  PageHeader,
  Banner,
  Button,
  Card,
  CardContent,
  DataTable,
  type EntityAction,
  FormStatus,
  ObjectActionBar,
  ListToolbar,
  OffsetPagination,
  offsetForPage,
  RowTitleButton,
  FilterSelect,
  isFilterActive,
  SelectField,
  type SelectFieldOption,
  StatusBadge,
  Skeleton,
  TableSkeleton,
  SearchField,
  TimedLoadingState,
  useConfirm,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
} from "@production-ready/ui";
import {
  BookmarkPlus,
  ClipboardList,
  ExternalLink,
  FileText,
  MessageSquareText,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState, ErrorState } from "@/components/StateViews";
import { RagSplitPane } from "@/components/layout/EntityLayout";
import { useAuth } from "@/components/security/AuthProvider";
import {
  api,
  ApiError,
  type CitationFeedbackRating,
  type FeedbackDetail,
  type FeedbackItem,
  type FeedbackSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { listCountProps } from "@/lib/list-paging";
import { listToolbarLabels, paginationLabels } from "@/lib/pagination-labels";
import { CAPABILITY_PERMISSIONS } from "@/lib/permissions";
import {
  useSearchAnswerProfiles,
  useFeedbackDashboard,
  useFeedbackDetail,
  useFeedbackEvaluationCase,
  usePromoteFeedbackToApprovedFaq,
  useListTotal,
} from "@/lib/queries";
import { saveDownloadedFile } from "@/lib/download";
import { toast } from "@/lib/toast";
import { useValuesChanged } from "@/lib/render-sync";
import { canOpenDocumentDetail } from "@/lib/route-permissions";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

import {
  FEEDBACK_ALL_PARAMS,
  FEEDBACK_PAGE_SIZES,
  FEEDBACK_PERIODS,
  FEEDBACK_REASON_LABEL_KEYS,
  FEEDBACK_REASONS,
  defaultFeedbackSearch,
  feedbackListParams,
  isFeedbackFiltered,
  parseFeedbackUrl,
} from "./FeedbackClient.logic";

type DetailTab = "content" | "evidence" | "execution";

/** 期間の既定（URL に無いときの 30 日。`parseFeedbackUrl` と同じ）。 */
const DEFAULT_PERIOD = "30";

export function FeedbackClient() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlState = useMemo(() => parseFeedbackUrl(searchParams), [searchParams]);
  // 利用者が行を選んだ直後だけ、縦積み（xl 未満）の詳細へフォーカスを移す（URL からの復元では動かさない）。
  const revealDetailRef = useRef(false);
  const params = useMemo(() => feedbackListParams(urlState), [urlState]);
  const query = useFeedbackDashboard(params);
  // 件数の「N 件（全 M 件）」の M（条件を外した、見える範囲の全件）。絞り込み中だけ 1 件ぶんの取得で数える（#1891）。
  const filtered = isFeedbackFiltered(urlState);
  const allTotal = useListTotal(
    "feedback",
    {},
    async () => (await api.listFeedback(FEEDBACK_ALL_PARAMS)).items.total,
    filtered
  );
  const searchAnswerProfilesQuery = useSearchAnswerProfiles({ status: "ACTIVE", limit: 100, offset: 0 });
  // 見える範囲は backend が決める（SYSTEM_ADMIN は全員の分、ほかのロールは自分が送った分だけ。#408）。
  // 画面はその範囲を案内するだけ。
  const seesAllUsers = useAuth().user?.is_system_admin === true;
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

  // 検索語は SearchField が確定した値（入力が止まって 300ms・Enter・消去。IME の変換中は確定しない）で URL に入れる（#535）。
  function applySearch(value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set("q", value.slice(0, 200));
    else next.delete("q");
    next.set("page", "1");
    setSearchParams(next, { replace: true });
  }

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

  // 検索語と絞り込みを既定に戻し 1 ページ目へ（並び順と表示件数は条件ではないので残す。#1933）。
  function clearFilters() {
    const next = new URLSearchParams(defaultFeedbackSearch());
    next.set("sort", urlState.sortOrder);
    next.set("size", String(urlState.pageSize));
    setSearchParams(next);
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

  const periodValue = urlState.periodDays == null ? "all" : String(urlState.periodDays);
  // 「すべて」を先頭に、短い期間から並べる（page-archetypes.md「一覧の絞り込みの部品」規則 8）。
  const periodOptions: SelectFieldOption[] = [
    { value: "all", label: t("feedback.filters.all") },
    ...FEEDBACK_PERIODS.filter((days) => days != null).map((days) => ({
      value: String(days),
      label: t("feedback.filters.days", { count: days }),
    })),
  ];
  const searchAnswerProfileOptions: SelectFieldOption[] = [
    { value: "", label: t("feedback.filters.allSearchAnswerProfiles") },
    ...(searchAnswerProfilesQuery.data?.items ?? []).map((view) => ({ value: view.id, label: view.name })),
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
        <Banner
          severity="info"
          title={t(seesAllUsers ? "feedback.scope.allTitle" : "feedback.scope.ownTitle")}
        >
          {t(seesAllUsers ? "feedback.scope.allDescription" : "feedback.scope.ownDescription")}
        </Banner>
        {/* 一覧のツールバー: 左に検索、その右に期間・対象・評価・理由の絞り込み（#1933）。条件は集計にも明細にも効き、
            選んだらすぐ URL に入れて適用する（「適用」のボタンを持たない）ので、分析の条件のフォームではなく一覧の
            絞り込みの型にそろえる。絞り込みが 5 つあるので、狭い置き場所では「絞り込み（N）」のシートにまとめる。 */}
        <ListToolbar
          search={
            <SearchField
              id="feedback-search"
              label={t("feedback.filters.search")}
              labelHidden
              value={urlState.q}
              maxLength={200}
              placeholder={t("feedback.filters.search")}
              onSearch={applySearch}
              clearLabel={t("common.clearSearch")}
              resultCountLabel={
                page ? t("common.searchResultCount", { count: formatNumber(page.total) }) : ""
              }
            />
          }
          filters={
            <>
              <FilterSelect
                id="feedback-period"
                label={t("feedback.filters.period")}
                value={periodValue}
                options={periodOptions}
                defaultValue={DEFAULT_PERIOD}
                onValueChange={(value) => setParam("period", value)}
              />
              <FilterSelect
                id="feedback-search-answer-profile"
                label={t("feedback.filters.searchAnswerProfile")}
                value={urlState.searchAnswerProfileId}
                options={searchAnswerProfileOptions}
                onValueChange={(value) => setParam("search_answer_profile", value)}
                // 対象の選択（長い名前）。
                width="lg"
              />
              <FilterSelect
                id="feedback-target"
                label={t("feedback.filters.target")}
                value={urlState.targetType}
                options={targetOptions}
                onValueChange={(value) => setParam("target", value)}
              />
              <FilterSelect
                id="feedback-rating"
                label={t("feedback.filters.rating")}
                value={urlState.rating}
                options={ratingOptions}
                onValueChange={(value) => setParam("rating", value)}
                // 「評価: 役に立たなかった」が切れない幅。
                width="md"
              />
              <FilterSelect
                id="feedback-reason"
                label={t("feedback.filters.reason")}
                value={urlState.reason}
                options={reasonOptions}
                onValueChange={(value) => setParam("reason", value)}
                width="md"
              />
            </>
          }
          filtersLayout="sheet"
          activeFilterCount={
            [
              isFilterActive(periodValue, DEFAULT_PERIOD),
              Boolean(urlState.searchAnswerProfileId),
              Boolean(urlState.targetType),
              Boolean(urlState.rating),
              Boolean(urlState.reason),
            ].filter(Boolean).length
          }
          searchActive={urlState.q !== ""}
          onClearFilters={clearFilters}
          labels={listToolbarLabels()}
          // 件数はここ 1 か所（狭い置き場所のシートの「N 件を表示」にも使う。#1891・#1933）。
          count={listCountProps({ matchedTotal: page?.total, filtered, allTotal: allTotal.data })}
          testId="feedback-filters-toolbar"
        />

        {query.isLoading ? (
          <TimedLoadingState
            label={t("feedback.page.loading")}
            operationKey="feedback-list-load"
            testId="feedback-loading"
          >
            {/* 集計パネル（4 指標）と一覧の表の寸法を予約する。 */}
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-24" />
              ))}
            </div>
            <TableSkeleton columns={4} />
          </TimedLoadingState>
        ) : query.isError ? (
          <ErrorState message={t("feedback.page.loadError")} onRetry={() => void query.refetch()} />
        ) : data ? (
          <>
            <SummaryPanel summary={data.summary} previous={data.previous_summary} />
            <section aria-labelledby="feedback-list-heading">
              {/* 見出しの右に明細の並び順（集計には効かず明細だけを並べ替えるので、絞り込みとは分けて明細の上に置く。#1933）。
                  件数は上のツールバーの ListCount 1 か所。今のページの範囲と表示件数はページ送り（一覧の直下）が出す。 */}
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <h2 id="feedback-list-heading" className="text-base font-semibold text-fg">
                  {t("feedback.list.title")}
                </h2>
                <ListToolbar
                  actions={
                    // 並べ替えは絞り込みではないので、既定以外でも強調しない（FilterSelect にしない）。
                    <SelectField
                      id="feedback-sort"
                      label={t("feedback.filters.sort")}
                      labelPlacement="inline"
                      value={urlState.sortOrder}
                      options={sortOptions}
                      onValueChange={(value) => setParam("sort", value)}
                      width="sm"
                    />
                  }
                  // 見出しの残りの幅を持たせる（ListToolbar は container query の箱なので、幅を内容から決めない）。
                  className="min-w-0 flex-1"
                  testId="feedback-list-toolbar"
                />
              </div>

              {page?.items.length ? (
                <RagSplitPane
                  splitId="feedback-list"
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
                      <OffsetPagination
                        className="mt-3"
                        offset={offsetForPage(urlState.page, urlState.pageSize)}
                        limit={urlState.pageSize}
                        total={page.total}
                        count={page.items.length}
                        onPageChange={(nextPage) => setParam("page", String(nextPage), false)}
                        pageSizeOptions={FEEDBACK_PAGE_SIZES}
                        onPageSizeChange={(size) => setParam("size", String(size))}
                        labels={paginationLabels()}
                        ariaLabel={t("feedback.pager.label")}
                        testId="feedback-pagination"
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

/** 問題の概要の補足（評価時間・検索・回答プロファイル・コメントの有無）。 */
function QuestionMeta({ item }: { item: FeedbackItem }) {
  return (
    <>
      <span className="tabular-nums">{formatDateTime(item.created_at)}</span>
      {" · "}
      <span>{item.search_answer_profile_name ?? t("feedback.list.unknownSearchAnswerProfile")}</span>
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
                    <span data-surface-tint="neutral" className="h-1.5 overflow-hidden rounded-full bg-surface-hover" aria-hidden>
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
              current={item.feedback_id === selectedId}
              aria-label={t("feedback.list.selectNamed", { name: questionSummary(item) })}
              onClick={() => onSelect(item.feedback_id)}
              data-feedback-row-button={item.feedback_id}
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
      rowProps={(item) => ({
        className: cn(INFORMATION_TABLE_ROW_CLASS, "align-top"),
        "data-testid": `feedback-row-${item.feedback_id}`,
      })}
      stickyHeader
      visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
      scrollAriaLabel={t("feedback.list.scrollLabel")}
      scrollTestId="feedback-scroll-region"
      className="hidden md:block"
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
            data-surface-tint={current ? "accent" : undefined}
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
                current={current}
                aria-label={t("feedback.list.selectNamed", { name: questionSummary(item) })}
                onClick={() => onSelect(item.feedback_id)}
                data-feedback-row-button={item.feedback_id}
              />
            </div>
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <Metadata label={t("feedback.filters.reason")} value={item.reason ? t(FEEDBACK_REASON_LABEL_KEYS[item.reason]) : "—"} />
              <Metadata label={t("feedback.filters.searchAnswerProfile")} value={item.search_answer_profile_name ?? t("feedback.list.unknownSearchAnswerProfile")} />
              <Metadata label={t("feedback.table.targetSource")} value={targetSource(item)} />
              <Metadata label={t("feedback.list.model")} value={item.model ?? "—"} />
            </dl>
          </li>
        );
      })}
    </ul>
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
            <TimedLoadingState
              label={t("feedback.detail.loading")}
              operationKey={`feedback-detail-${feedbackId ?? ""}`}
              framed={false}
              testId="feedback-detail-loading"
            >
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="h-24" />
              <Skeleton className="h-40" />
            </TimedLoadingState>
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
  // 承認済み FAQ への反映は rag.feedback.manage を持つ利用者だけ（評価ケースの作成はフィードバックの閲覧権限で使える。#214）。
  const canPromoteFaq = useAuth().hasPermission(CAPABILITY_PERMISSIONS.feedbackManage);
  const promote = usePromoteFeedbackToApprovedFaq();
  const evaluationCase = useFeedbackEvaluationCase();
  const confirm = useConfirm();
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

  // 品質評価のケースは、評価リクエストの Excel の 1 行（テンプレートと同じ列）で書き出す（#2065）。
  function handleEvaluationCase() {
    setError("");
    evaluationCase.mutate(detail.feedback_id, {
      onSuccess: (file) => {
        saveDownloadedFile(file);
        toast.success(t("feedback.promote.evaluationDone"));
      },
      onError: (caught) =>
        setError(caught instanceof ApiError ? caught.message : t("feedback.promote.error")),
    });
  }

  // 押した操作だけを回す。要求中に別のフィードバックを開いても、そのフィードバックの操作は回さず無効にする（#819）。
  const promotePending = promote.isPending && promote.variables === detail.feedback_id;
  const evaluationCasePending =
    evaluationCase.isPending && evaluationCase.variables === detail.feedback_id;
  const promotionBusy = promote.isPending || evaluationCase.isPending;
  const actions: EntityAction[] = [
    {
      id: "approved-faq",
      label: t("feedback.promote.faq"),
      icon: BookmarkPlus,
      visible: canPromoteFaq,
      disabled: promotionBusy && !promotePending,
      loading: promotePending,
      testId: "feedback-promote-faq",
      onSelect: () => void handlePromote(),
    },
    {
      id: "evaluation-case",
      label: t("feedback.promote.evaluation"),
      icon: ClipboardList,
      disabled: promotionBusy && !evaluationCasePending,
      loading: evaluationCasePending,
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
  // 文書の詳細を開けない利用者には、引用元（文書の詳細）へのリンクを出さない（#303）。
  const canOpenDetail = canOpenDocumentDetail(useAuth().hasPermission);
  return (
    <div id="feedback-panel-evidence" role="tabpanel" aria-labelledby="feedback-tab-evidence" className="space-y-3">
      {detail.citations.length ? detail.citations.map((citation, index) => {
        const targeted = detail.target_type === "citation" && citation.chunk_id === detail.chunk_id;
        const link = `${APP_ROUTES.documents}/${encodeURIComponent(citation.document_id)}?chunk_id=${encodeURIComponent(citation.chunk_id)}`;
        return (
          <article key={`${citation.chunk_id}-${index}`} data-surface-tint={targeted ? "accent" : undefined} className={cn("rounded-lg border border-border p-3", targeted && "border-accent-emphasis bg-accent-subtle")}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-fg">{citation.file_name ?? citation.document_id}</p>
                <p className="mt-0.5 text-xs text-fg-muted">{[citation.section_title, citation.page_number ? t("feedback.detail.page", { count: citation.page_number }) : null].filter(Boolean).join(" / ") || "—"}</p>
              </div>
              {targeted ? <StatusBadge variant="info" icon={false} label={t("feedback.detail.targetCitation")} className="shrink-0" /> : null}
            </div>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-fg/90">{citation.content_preview ?? t("feedback.detail.noCitationPreview")}</p>
            {canOpenDetail ? <Link to={link} className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-accent-fg hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"><ExternalLink size={14} aria-hidden />{t("feedback.list.openCitation")}</Link> : null}
          </article>
        );
      }) : (
        <EmptyState title={t("feedback.detail.noCitations")} hint={t("feedback.detail.noCitationsHint")} />
      )}
    </div>
  );
}

function ExecutionTab({ detail }: { detail: FeedbackDetail }) {
  const chatLink = detail.conversation_id && detail.search_answer_profile_id
    ? `${APP_ROUTES.chat}?search_answer_profile_id=${encodeURIComponent(detail.search_answer_profile_id)}&conversation_id=${encodeURIComponent(detail.conversation_id)}${detail.message_id ? `#message-${encodeURIComponent(detail.message_id)}` : ""}`
    : null;
  const rows = [
    [t("feedback.filters.searchAnswerProfile"), detail.search_answer_profile_name ?? "—"],
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
      <span className="text-xs text-fg-muted">{detail.search_answer_profile_name ?? t("feedback.list.unknownSearchAnswerProfile")}</span>
    </div>
  );
}

function LegacyNotice() {
  return <div data-surface-tint="warning" className="rounded-lg border border-warning-border bg-warning-subtle p-3 text-sm text-warning-fg" role="status"><div className="flex items-start gap-2"><FileText size={16} className="mt-0.5 shrink-0" aria-hidden /><div><p className="font-medium">{t("feedback.detail.legacyTitle")}</p><p className="mt-1 text-xs leading-5">{t("feedback.detail.legacyHint")}</p></div></div></div>;
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
