import { useMemo, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  MessageText,
  ListToolbar,
  OffsetPagination,
  PageBody,
  PageHeader,
  PAGE_SIZE_OPTIONS,
  RowTitleButton,
  SelectField,
  SideSheet,
  Skeleton,
  StatusBadge,
  TableSkeleton,
  TabPanel,
  Tabs,
  type DataTableColumn,
  useActionPending,
} from "@production-ready/ui";

import { listCount } from "@/components/ListFilters";
import { agentPaginationLabels, listScrollLabel, QueryState, usePersistedServerPaging } from "@/components/ListViews";
import {
  agentApi,
  FEEDBACK_REASONS,
  REPORT_PERIOD_DAYS,
  SIGNAL_KINDS,
  type FeedbackItem,
  type FeedbackPeriodDays,
  type FeedbackRating,
  type FeedbackReason,
  type FeedbackReport,
  type FeedbackSummary,
  type RunFeedback,
  type RunState,
  type SignalItem,
  type SignalKind,
} from "@/lib/api";
import { AnswerFeedback } from "@/components/chat/AnswerFeedback";
import { AddToEvaluationCase, useCanEditEvaluationSets } from "@/components/evaluation/AddToEvaluationCase";
import { ReportSourceNote } from "@/components/ReportSourceNote";
import { useCapabilities } from "@/lib/permissions";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { isOneOf, isString, useWorkspaceState, type WorkspaceValidator } from "@/lib/workspace-state";
import { countDelta, formatRate, Metric, rateDelta } from "@/pages/feedbackMetrics";
import { SignalDetail, signalLabel, SignalsContent, SignalsSkeleton } from "@/pages/FeedbackSignals";

// フィードバック（#774）。2 つの見方を `Tabs` で切り替える（#1822）。
// - 確かめるべき実行（既定）: 評価の付いていない実行も、回答の検証・回答の対応・ツール・承認・実行の状態の
//   暗黙のシグナルから集める（`FeedbackSignals.tsx`）。明示の評価は回答のごく一部にしか付かないため既定にする。
// - 利用者の評価: チャットの回答への利用者の評価を集計し、役に立たなかった回答と理由を確かめる。
//   画面の形は RAG のフィードバック（集計 → 理由 → 一覧 → 詳細）にそろえる。
// 期間・業務 Agent は 2 つの見方で共通。期間は 365 日まで。Oracle の構成は保存した Run の履歴を集計し、
// 一覧はサーバー側でページングする（#794）。

const PERIODS: readonly FeedbackPeriodDays[] = REPORT_PERIOD_DAYS;
const RATINGS: readonly FeedbackRating[] = ["helpful", "not_helpful"];
const VIEWS = ["signals", "ratings"] as const;
type FeedbackView = (typeof VIEWS)[number];

const isPeriod: WorkspaceValidator<FeedbackPeriodDays> = (value): value is FeedbackPeriodDays =>
  PERIODS.includes(value as FeedbackPeriodDays);
const isRatingFilter: WorkspaceValidator<string> = (value): value is string =>
  value === "" || RATINGS.includes(value as FeedbackRating);
const isReasonFilter: WorkspaceValidator<string> = (value): value is string =>
  value === "" || FEEDBACK_REASONS.includes(value as FeedbackReason);
const isSignalFilter: WorkspaceValidator<string> = (value): value is string =>
  value === "" || SIGNAL_KINDS.includes(value as SignalKind);

export function reasonLabel(reason: FeedbackReason): string {
  return t(`feedback.reason.${reason}` as I18nKey);
}

function ratingLabel(rating: FeedbackRating): string {
  return t(`feedback.rating.${rating}` as I18nKey);
}

export function FeedbackPage() {
  const [view, setView] = useWorkspaceState("feedback", "view", "signals" as FeedbackView, isOneOf(VIEWS));
  const [days, setDays] = useWorkspaceState("feedback", "days", 30 as FeedbackPeriodDays, isPeriod);
  const [agentId, setAgentId] = useWorkspaceState("feedback", "agentId", "", isString);
  const [rating, setRating] = useWorkspaceState("feedback", "rating", "", isRatingFilter);
  const [reason, setReason] = useWorkspaceState("feedback", "reason", "", isReasonFilter);
  const [signalKind, setSignalKind] = useWorkspaceState("feedback", "signalKind", "", isSignalFilter);
  // 一覧のページ（作業状態。絞り込みを変えたら 1 ページ目へ戻す）。
  // 表示件数も作業状態に残す（#1891）。
  const ratingPaging = usePersistedServerPaging("feedback");
  const signalPaging = usePersistedServerPaging("feedbackSignals");
  const setPage = ratingPaging.setPage;
  const setSignalPage = signalPaging.setPage;
  const [selected, setSelected] = useState<FeedbackItem | null>(null);
  const [selectedSignal, setSelectedSignal] = useState<SignalItem | null>(null);
  const queryClient = useQueryClient();

  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const filters = useMemo(
    () => ({
      days,
      agentId: agentId || undefined,
      rating: (rating || undefined) as FeedbackRating | undefined,
      reason: (reason || undefined) as FeedbackReason | undefined,
      offset: ratingPaging.offset,
      limit: ratingPaging.limit,
    }),
    [days, agentId, rating, reason, ratingPaging.offset, ratingPaging.limit]
  );
  const signalFilters = useMemo(
    () => ({
      days,
      agentId: agentId || undefined,
      kind: (signalKind || undefined) as SignalKind | undefined,
      offset: signalPaging.offset,
      limit: signalPaging.limit,
    }),
    [days, agentId, signalKind, signalPaging.offset, signalPaging.limit]
  );
  // 見ている見方だけを取得する。
  const report = useQuery({
    queryKey: ["feedback", "ratings", filters],
    queryFn: () => agentApi.getFeedbackReport(filters),
    enabled: view === "ratings",
    // 絞り込みを変えている間は前の結果を出したまま取り直す（Skeleton に戻さない）。
    placeholderData: keepPreviousData,
  });
  const signals = useQuery({
    queryKey: ["feedback", "signals", signalFilters],
    queryFn: () => agentApi.getFeedbackSignals(signalFilters),
    enabled: view === "signals",
    placeholderData: keepPreviousData,
  });
  // 「最新情報を取得」は押した取り直しの間だけ回す（定期の取り直し・他の操作の後の invalidate・条件の切り替えでは回さない。#819）。
  const manualRefresh = useActionPending();
  const filtered = Boolean(rating || reason);

  // 期間・業務 Agent は 2 つの見方で共通。変えたら両方の一覧を 1 ページ目から読む（UX 契約 workspace-state.md）。
  function changeSharedFilter<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      setPage(1);
      setSignalPage(1);
    };
  }

  function changeRatingFilter(set: (value: string) => void) {
    return (value: string) => {
      set(value);
      setPage(1);
    };
  }

  function clearListFilters() {
    setRating("");
    setReason("");
    setPage(1);
  }

  function changeSignalKind(value: string) {
    setSignalKind(value);
    setSignalPage(1);
  }

  // 2 つの見方の絞り込みの先頭（タブの中は 1 つだけ描くので、id は同じでよい）。
  const sharedFilters = (
    <>
      <SelectField<string>
        id="feedback-period"
        width="sm"
        label={t("feedback.filter.period")}
        value={String(days)}
        options={PERIODS.map((period) => ({ value: String(period), label: t(`feedback.period.${period}` as I18nKey) }))}
        onValueChange={(value) => changeSharedFilter(setDays)(Number(value) as FeedbackPeriodDays)}
      />
      <SelectField<string>
        id="feedback-agent"
        width="lg"
        label={t("feedback.filter.agent")}
        value={agentId}
        options={[
          { value: "", label: t("feedback.filter.all") },
          ...(agents.data?.agents ?? []).map((agent) => ({ value: agent.id, label: agent.name })),
        ]}
        onValueChange={changeSharedFilter(setAgentId)}
      />
    </>
  );

  return (
    <>
      <PageHeader
        wide
        title={t("nav.feedback")}
        subtitle={t("page.feedback.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () =>
              void manualRefresh.track(async () => {
                await (view === "signals" ? signals.refetch() : report.refetch());
              }),
          },
        ]}
        tabs={
          <Tabs
            idPrefix="feedback"
            ariaLabel={t("feedback.view.label")}
            value={view}
            onChange={(id) => setView(id as FeedbackView)}
            items={[
              { id: "signals", label: t("feedback.view.signals") },
              { id: "ratings", label: t("feedback.view.ratings") },
            ]}
          />
        }
      />
      <PageBody wide>
        <TabPanel idPrefix="feedback" id="signals" value={view} className="space-y-4">
          <div className="flex flex-wrap items-end gap-x-4 gap-y-3" data-testid="signals-filters">
            {sharedFilters}
            <SelectField<string>
              id="feedback-signal"
              width="md"
              label={t("feedback.filter.signal")}
              value={signalKind}
              options={[
                { value: "", label: t("feedback.filter.all") },
                ...SIGNAL_KINDS.map((item) => ({ value: item, label: signalLabel(item) })),
              ]}
              onValueChange={changeSignalKind}
            />
          </div>
          <QueryState
            query={signals}
            loadingLabel={t("loading.feedbackSignals")}
            testId="signals-loading"
            skeleton={<SignalsSkeleton />}
          >
            {signals.data ? (
              <SignalsContent
                report={signals.data}
                filtered={Boolean(signalKind)}
                onClearFilters={() => changeSignalKind("")}
                onOpen={setSelectedSignal}
                onPageChange={setSignalPage}
                onPageSizeChange={signalPaging.setPageSize}
              />
            ) : null}
          </QueryState>
        </TabPanel>
        <TabPanel idPrefix="feedback" id="ratings" value={view} className="space-y-4">
          <div className="flex flex-wrap items-end gap-x-4 gap-y-3" data-testid="feedback-filters">
            {sharedFilters}
            <SelectField<string>
              id="feedback-rating"
              width="sm"
              label={t("feedback.filter.rating")}
              value={rating}
              options={[
                { value: "", label: t("feedback.filter.all") },
                ...RATINGS.map((item) => ({ value: item, label: ratingLabel(item) })),
              ]}
              onValueChange={changeRatingFilter(setRating)}
            />
            <SelectField<string>
              id="feedback-reason"
              width="md"
              label={t("feedback.filter.reason")}
              value={reason}
              options={[
                { value: "", label: t("feedback.filter.all") },
                ...FEEDBACK_REASONS.map((item) => ({ value: item, label: reasonLabel(item) })),
              ]}
              onValueChange={changeRatingFilter(setReason)}
            />
          </div>
          <QueryState
            query={report}
            loadingLabel={t("loading.feedback")}
            testId="feedback-loading"
            skeleton={<FeedbackSkeleton />}
          >
            {report.data ? (
              <FeedbackContent
                report={report.data}
                filtered={filtered}
                onClearFilters={clearListFilters}
                onOpen={setSelected}
                onPageChange={setPage}
                onPageSizeChange={ratingPaging.setPageSize}
              />
            ) : null}
          </QueryState>
        </TabPanel>
        <SideSheet
          open={selected !== null}
          onClose={() => setSelected(null)}
          title={t("feedback.detail.title")}
          closeLabel={t("feedback.detail.close")}
          id="feedback-detail"
          // 詳細は一覧の右から出す（会話の履歴などのナビは左）。
          side="right"
          data-testid="feedback-detail"
        >
          {selected ? (
            <FeedbackDetail
              item={selected}
              onReviewed={(run) => {
                setSelected((current) => (current ? { ...current, admin_review: run.admin_review ?? null } : current));
                void queryClient.invalidateQueries({ queryKey: ["feedback"] });
                // 評価ケースの下書きは管理者のコメントを使う（#810）。
                void queryClient.invalidateQueries({ queryKey: ["evaluation-case-draft", run.id] });
              }}
            />
          ) : null}
        </SideSheet>
        <SideSheet
          open={selectedSignal !== null}
          onClose={() => setSelectedSignal(null)}
          title={t("feedback.signals.detail.title")}
          closeLabel={t("feedback.detail.close")}
          id="signals-detail"
          side="right"
          data-testid="signals-detail"
        >
          {selectedSignal ? <SignalDetail item={selectedSignal} /> : null}
        </SideSheet>
      </PageBody>
    </>
  );
}

function FeedbackSkeleton() {
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="grid grid-cols-2 gap-4 pt-4 xl:grid-cols-4">
          {Array.from({ length: 4 }, (_, index) => (
            <Skeleton key={index} className="h-16" />
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4">
          <TableSkeleton columns={6} />
        </CardContent>
      </Card>
    </div>
  );
}

function FeedbackContent({
  report,
  filtered,
  onClearFilters,
  onOpen,
  onPageChange,
  onPageSizeChange,
}: {
  report: FeedbackReport;
  filtered: boolean;
  onClearFilters: () => void;
  onOpen: (item: FeedbackItem) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}) {
  const { summary, previous } = report;
  if (summary.total === 0 && summary.admin_reviewed === 0) {
    return (
      <Card>
        <CardContent className="pt-4">
          <EmptyState title={t("feedback.empty.title")} hint={t("feedback.empty.hint")} />
          <ReportSourceNote source={report.source} />
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>{t("feedback.summary.title")}</CardTitle>
          <CardDescription>{t("feedback.summary.description")}</CardDescription>
          <ReportSourceNote source={report.source} />
        </CardHeader>
        <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          <div className="grid grid-cols-2 gap-x-5 gap-y-4 self-start sm:grid-cols-4 lg:grid-cols-2" data-testid="feedback-summary">
            <Metric label={t("feedback.summary.total")} value={formatNumber(summary.total)} delta={countDelta(summary.total, previous.total)} />
            <Metric
              label={t("feedback.summary.helpfulRate")}
              value={formatRate(summary.helpful_rate)}
              delta={rateDelta(summary.helpful_rate, previous.helpful_rate)}
            />
            <Metric label={t("feedback.summary.helpful")} value={formatNumber(summary.helpful)} delta={countDelta(summary.helpful, previous.helpful)} />
            <Metric
              label={t("feedback.summary.notHelpful")}
              value={formatNumber(summary.not_helpful)}
              delta={countDelta(summary.not_helpful, previous.not_helpful)}
            />
          </div>
          <ReasonCounts summary={summary} />
          <p className="text-xs text-fg-muted lg:col-span-2" data-testid="feedback-admin-summary">
            {t("feedback.summary.admin", {
              count: formatNumber(summary.admin_reviewed),
              notHelpful: formatNumber(summary.admin_not_helpful),
            })}
          </p>
        </CardContent>
      </Card>

      <Card className="min-w-0">
        <CardHeader>
          <CardTitle>{t("feedback.list.title")}</CardTitle>
          <CardDescription>{t("feedback.list.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {report.total === 0 ? (
            <EmptyState
              title={t("feedback.noMatch.title")}
              action={
                filtered ? (
                  <Button variant="secondary" size="sm" onClick={onClearFilters}>
                    {t("feedback.noMatch.clear")}
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="grid min-w-0 gap-2">
              {/* 件数: 絞り込み（評価・理由）中は「N 件（全 M 件）」。M は期間・業務 Agent の評価の全件（#1891）。 */}
              <ListToolbar count={listCount(summary.total, report.total, filtered)} testId="feedback-list-toolbar" />
              <FeedbackTable items={report.items} onOpen={onOpen} />
              <OffsetPagination
                offset={report.offset}
                limit={report.limit}
                total={report.total}
                count={report.items.length}
                onPageChange={(next) => onPageChange(next)}
                onPageSizeChange={onPageSizeChange}
                pageSizeOptions={PAGE_SIZE_OPTIONS}
                labels={agentPaginationLabels()}
                ariaLabel={t("feedback.pagerLabel")}
                testId="feedback-pagination"
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function ReasonCounts({ summary }: { summary: FeedbackSummary }) {
  return (
    <section aria-labelledby="feedback-reasons-heading" className="min-w-0" data-testid="feedback-reasons">
      <h3 id="feedback-reasons-heading" className="mb-3 text-sm font-semibold text-fg">
        {t("feedback.reasons.title")}
      </h3>
      {summary.reason_counts.length ? (
        <div className="space-y-2">
          {summary.reason_counts.map((item) => {
            const ratio = summary.not_helpful ? item.count / summary.not_helpful : 0;
            return (
              <div key={item.reason} className="grid grid-cols-[minmax(8rem,1fr)_minmax(4rem,1fr)_auto] items-center gap-2 text-xs">
                <span className="truncate text-fg" title={reasonLabel(item.reason)}>
                  {reasonLabel(item.reason)}
                </span>
                <span data-surface-tint="neutral" className="h-1.5 overflow-hidden rounded-full bg-surface-hover" aria-hidden>
                  <span className="block h-full rounded-full bg-danger-emphasis" style={{ width: `${Math.round(ratio * 100)}%` }} />
                </span>
                <span className="w-20 text-right tabular-nums text-fg-muted">
                  {formatNumber(item.count)} / {formatRate(ratio)}
                </span>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-fg-muted">{t("feedback.reasons.empty")}</p>
      )}
    </section>
  );
}

function FeedbackTable({ items, onOpen }: { items: FeedbackItem[]; onOpen: (item: FeedbackItem) => void }) {
  const columns: DataTableColumn<FeedbackItem>[] = [
    {
      key: "question",
      header: t("feedback.column.question"),
      rowHeader: true,
      className: "min-w-56 max-w-96",
      render: (item) => <RowTitleButton title={item.question} subtitle={item.run_id} onClick={() => onOpen(item)} />,
    },
    {
      key: "rating",
      header: t("feedback.column.rating"),
      render: (item) => (item.feedback ? <RatingBadge rating={item.feedback.rating} /> : <NoRating />),
    },
    {
      key: "reason",
      header: t("feedback.column.reason"),
      className: "text-xs text-fg",
      render: (item) => (item.feedback?.reason ? reasonLabel(item.feedback.reason) : "—"),
    },
    {
      key: "admin_review",
      header: t("feedback.column.adminReview"),
      render: (item) => (item.admin_review ? <RatingBadge rating={item.admin_review.rating} /> : <NoRating />),
    },
    {
      key: "agent",
      header: t("feedback.column.agent"),
      className: "text-xs text-fg",
      render: (item) => item.agent_name || t("feedback.agentDeleted"),
    },
    {
      key: "user",
      header: t("feedback.column.user"),
      className: "text-xs text-fg",
      render: (item) => item.display_name || t("feedback.userUnknown"),
    },
    {
      key: "updated_at",
      header: t("feedback.column.ratedAt"),
      className: "whitespace-nowrap text-xs tabular-nums text-fg-muted",
      render: (item) => formatDateTime(item.updated_at),
    },
  ];
  return (
    <DataTable<FeedbackItem>
      rows={items}
      columns={columns}
      getRowKey={(item) => item.run_id}
      rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
      ariaLabel={t("feedback.list.label")}
      scrollAriaLabel={listScrollLabel(t("feedback.list.label"))}
      tableClassName="w-full min-w-[59rem]"
      stickyHeader
      visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
    />
  );
}

function NoRating() {
  return <span className="text-xs text-fg-muted">—</span>;
}

function RatingBadge({ rating }: { rating: FeedbackRating }) {
  return (
    <StatusBadge variant={rating === "helpful" ? "success" : "danger"} label={ratingLabel(rating)} />
  );
}

/** 役に立たなかった回答（本人の 👎 か管理者の評価）。評価ケースに追加できる（#810）。 */
function notHelpful(item: FeedbackItem): boolean {
  return item.feedback?.rating === "not_helpful" || item.admin_review?.rating === "not_helpful";
}

function FeedbackDetail({ item, onReviewed }: { item: FeedbackItem; onReviewed: (run: RunState) => void }) {
  const capabilities = useCapabilities();
  const canAddCase = useCanEditEvaluationSets();
  return (
    <div className="space-y-4 text-sm">
      <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
        <dt className="text-fg-muted">{t("feedback.column.agent")}</dt>
        <dd className="break-words text-fg">{item.agent_name || t("feedback.agentDeleted")}</dd>
        <dt className="text-fg-muted">{t("feedback.column.user")}</dt>
        <dd className="break-words text-fg">{item.display_name || t("feedback.userUnknown")}</dd>
        <dt className="text-fg-muted">{t("feedback.column.ratedAt")}</dt>
        <dd className="tabular-nums text-fg">{formatDateTime(item.updated_at)}</dd>
        <dt className="text-fg-muted">{t("feedback.detail.run")}</dt>
        <dd className="break-all font-mono text-fg">{item.run_id}</dd>
      </dl>
      <section className="space-y-1">
        <h3 className="text-xs font-semibold text-fg-muted">{t("feedback.detail.question")}</h3>
        <p data-surface-tint="accent" className="whitespace-pre-wrap break-words rounded-md bg-accent-subtle px-3 py-2 text-fg">{item.question}</p>
      </section>
      <section className="space-y-1">
        <h3 className="text-xs font-semibold text-fg-muted">{t("feedback.detail.answer")}</h3>
        <div className="rounded-md border border-border p-3">
          <MessageText text={item.answer} className="text-sm text-fg" />
        </div>
      </section>
      <RatingSection title={t("feedback.detail.ownerRating")} rating={item.feedback} testId="feedback-detail-owner" />
      <RatingSection
        title={t("feedback.detail.adminReview")}
        rating={item.admin_review}
        by={item.reviewer_display_name}
        testId="feedback-detail-admin"
      />
      {/* 管理者の評価は Agent 管理の権限だけ（本人の評価とは別に残す。#774）。 */}
      {capabilities.admin ? (
        <AnswerFeedback runId={item.run_id} current={item.admin_review} mode="admin" onSaved={onReviewed} />
      ) : null}
      {/* 役に立たなかった回答を品質評価の評価ケースにする（品質評価の権限。#810）。 */}
      {canAddCase && notHelpful(item) ? (
        <AddToEvaluationCase key={item.run_id} runId={item.run_id} testId="feedback-add-case" />
      ) : null}
    </div>
  );
}

function RatingSection({
  title,
  rating,
  by,
  testId,
}: {
  title: string;
  rating: RunFeedback | null;
  by?: string;
  testId: string;
}) {
  return (
    <section className="space-y-1" data-testid={testId}>
      <h3 className="text-xs font-semibold text-fg-muted">{title}</h3>
      {rating ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <RatingBadge rating={rating.rating} />
            {rating.reason ? <span className="text-fg">{reasonLabel(rating.reason)}</span> : null}
            {by ? <span className="text-xs text-fg-muted">{by}</span> : null}
          </div>
          {rating.comment ? <p className="whitespace-pre-wrap break-words text-fg">{rating.comment}</p> : null}
        </>
      ) : (
        <p className="text-fg-muted">{t("feedback.detail.notRated")}</p>
      )}
    </section>
  );
}
