import {
  CheckCircle2,
  Clipboard,
  FileText,
  MessageSquareText,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import type {
  CitationFeedbackRating,
  CitationFeedbackReason,
  FeedbackItem,
  FeedbackListParams,
  FeedbackTargetType,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useBusinessViews, useFeedbackDashboard } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

const LIMIT = 20;
const PERIODS = [7, 30, 90, null] as const;
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

export function FeedbackClient() {
  const [periodDays, setPeriodDays] = useState<number | null>(30);
  const [businessViewId, setBusinessViewId] = useState("");
  const [targetType, setTargetType] = useState<FeedbackTargetType | "">("");
  const [rating, setRating] = useState<CitationFeedbackRating | "">("");
  const [reason, setReason] = useState<CitationFeedbackReason | "">("");
  const [offset, setOffset] = useState(0);
  const params = useMemo<FeedbackListParams>(
    () => ({
      business_view_id: businessViewId || undefined,
      target_type: targetType || undefined,
      rating: rating || undefined,
      reason: reason || undefined,
      period_days: periodDays,
      limit: LIMIT,
      offset,
    }),
    [businessViewId, offset, periodDays, rating, reason, targetType]
  );
  const query = useFeedbackDashboard(params);
  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 100, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  const data = query.data;
  const page = data?.items;

  const businessViewOptions: SelectFieldOption[] = [
    { value: "", label: t("feedback.filters.allBusinessViews") },
    ...businessViews.map((view) => ({ value: view.id, label: view.name })),
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

  function changeFilter(action: () => void) {
    action();
    setOffset(0);
  }

  return (
    <div>
      <PageHeader title={t("feedback.page.title")} subtitle={t("feedback.page.subtitle")} />
      <div className="space-y-6 p-4 sm:p-6 lg:p-8">
        <Card>
          <CardContent className="space-y-4 pt-5">
            <fieldset>
              <legend className="mb-2 text-sm font-medium text-foreground">
                {t("feedback.filters.period")}
              </legend>
              <div className="flex flex-wrap gap-2">
                {PERIODS.map((days) => (
                  <Button
                    key={days ?? "all"}
                    type="button"
                    variant="secondary"
                    size="sm"
                    className={cn(
                      "min-h-11 touch-manipulation",
                      periodDays === days && "border-primary bg-primary/10"
                    )}
                    aria-pressed={periodDays === days}
                    onClick={() => changeFilter(() => setPeriodDays(days))}
                  >
                    {days ? t("feedback.filters.days", { count: days }) : t("feedback.filters.all")}
                  </Button>
                ))}
              </div>
            </fieldset>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <SelectField
                id="feedback-business-view"
                label={t("feedback.filters.businessView")}
                value={businessViewId}
                options={businessViewOptions}
                onValueChange={(value) => changeFilter(() => setBusinessViewId(value))}
              />
              <SelectField
                id="feedback-target"
                label={t("feedback.filters.target")}
                value={targetType}
                options={targetOptions}
                onValueChange={(value) =>
                  changeFilter(() => setTargetType(value as FeedbackTargetType | ""))
                }
              />
              <SelectField
                id="feedback-rating"
                label={t("feedback.filters.rating")}
                value={rating}
                options={ratingOptions}
                onValueChange={(value) =>
                  changeFilter(() => setRating(value as CitationFeedbackRating | ""))
                }
              />
              <SelectField
                id="feedback-reason"
                label={t("feedback.filters.reason")}
                value={reason}
                options={reasonOptions}
                onValueChange={(value) =>
                  changeFilter(() => setReason(value as CitationFeedbackReason | ""))
                }
              />
            </div>
          </CardContent>
        </Card>

        {query.isLoading ? (
          <LoadingState rows={5} label={t("feedback.page.title")} />
        ) : query.isError ? (
          <ErrorState message={t("feedback.page.loadError")} onRetry={() => void query.refetch()} />
        ) : data ? (
          <>
            <section aria-labelledby="feedback-summary-heading">
              <h2 id="feedback-summary-heading" className="sr-only">
                {t("feedback.summary.title")}
              </h2>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard
                  label={t("feedback.summary.total")}
                  value={data.summary.total.toLocaleString("ja-JP")}
                  icon={MessageSquareText}
                />
                <MetricCard
                  label={t("feedback.summary.helpfulRate")}
                  value={formatRate(data.summary.helpful_rate)}
                  icon={CheckCircle2}
                  positive
                />
                <MetricCard
                  label={t("feedback.summary.answerRate")}
                  value={formatRate(data.summary.answer_helpful_rate)}
                  detail={t("feedback.summary.count", { count: data.summary.answer_total })}
                  icon={ThumbsUp}
                />
                <MetricCard
                  label={t("feedback.summary.citationRate")}
                  value={formatRate(data.summary.citation_helpful_rate)}
                  detail={t("feedback.summary.count", { count: data.summary.citation_total })}
                  icon={FileText}
                />
              </div>
            </section>

            <Card>
              <CardHeader>
                <CardTitle>{t("feedback.reasons.title")}</CardTitle>
              </CardHeader>
              <CardContent>
                {data.summary.reason_counts.length ? (
                  <div className="space-y-3">
                    {data.summary.reason_counts.map((item) => {
                      const ratio = data.summary.not_helpful_count
                        ? item.count / data.summary.not_helpful_count
                        : 0;
                      return (
                        <div key={item.reason} className="space-y-1.5">
                          <div className="flex items-center justify-between gap-3 text-sm">
                            <span className="font-medium text-foreground">
                              {t(REASON_LABEL_KEYS[item.reason])}
                            </span>
                            <span className="shrink-0 tabular-nums text-muted">
                              {item.count.toLocaleString("ja-JP")}（{formatRate(ratio)}）
                            </span>
                          </div>
                          <div className="h-2 overflow-hidden rounded-full bg-muted/20" aria-hidden>
                            <div
                              className="h-full rounded-full bg-danger transition-[width] duration-200 motion-reduce:transition-none"
                              style={{ width: `${Math.round(ratio * 100)}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-sm text-muted">{t("feedback.reasons.empty")}</p>
                )}
              </CardContent>
            </Card>

            <section aria-labelledby="feedback-list-heading">
              <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
                <div>
                  <h2 id="feedback-list-heading" className="text-base font-semibold text-foreground">
                    {t("feedback.list.title")}
                  </h2>
                  <p className="text-sm text-muted">
                    {t("feedback.list.range", {
                      start: page && page.total ? offset + 1 : 0,
                      end: offset + (page?.items.length ?? 0),
                      total: page?.total ?? 0,
                    })}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    className="min-h-11"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                  >
                    {t("pager.prev")}
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    className="min-h-11"
                    disabled={!page?.has_next}
                    onClick={() => setOffset(offset + LIMIT)}
                  >
                    {t("pager.next")}
                  </Button>
                </div>
              </div>
              {page?.items.length ? (
                <ul className="space-y-3">
                  {page.items.map((item) => (
                    <FeedbackRow key={item.feedback_id} item={item} />
                  ))}
                </ul>
              ) : (
                <Card>
                  <CardContent className="pt-5">
                    <EmptyState
                      title={t("feedback.list.empty")}
                      hint={t("feedback.list.emptyHint")}
                    />
                  </CardContent>
                </Card>
              )}
            </section>
          </>
        ) : null}
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  positive = false,
}: {
  label: string;
  value: string;
  detail?: string;
  icon: typeof MessageSquareText;
  positive?: boolean;
}) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-3 pt-5">
        <div>
          <p className="text-sm text-muted">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-foreground">{value}</p>
          {detail ? <p className="mt-1 text-xs text-muted">{detail}</p> : null}
        </div>
        <Icon className={cn("size-5 text-primary", positive && "text-success")} aria-hidden />
      </CardContent>
    </Card>
  );
}

function FeedbackRow({ item }: { item: FeedbackItem }) {
  const helpful = item.rating === "helpful";
  const chatLink =
    item.conversation_id && item.business_view_id
      ? `${APP_ROUTES.chat}?business_view_id=${encodeURIComponent(
          item.business_view_id
        )}&conversation_id=${encodeURIComponent(item.conversation_id)}${
          item.message_id ? `#message-${encodeURIComponent(item.message_id)}` : ""
        }`
      : null;
  const documentLink = item.document_id
    ? `${APP_ROUTES.documents}/${encodeURIComponent(item.document_id)}${
        item.chunk_id ? `?chunk_id=${encodeURIComponent(item.chunk_id)}` : ""
      }`
    : null;

  async function copyTrace() {
    try {
      await navigator.clipboard.writeText(item.trace_id);
      toast.success(t("feedback.list.traceCopied"));
    } catch {
      toast.error(t("feedback.list.traceCopyError"));
    }
  }

  return (
    <li>
      <Card>
        <CardContent className="space-y-3 pt-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs font-medium",
                    helpful ? "bg-success-bg text-success" : "bg-danger-bg text-danger"
                  )}
                >
                  {helpful ? <ThumbsUp size={13} aria-hidden /> : <ThumbsDown size={13} aria-hidden />}
                  {helpful ? t("feedback.rating.helpful") : t("feedback.rating.notHelpful")}
                </span>
                <span className="rounded-full border border-border px-2 py-1 text-xs text-muted">
                  {item.target_type === "answer"
                    ? t("feedback.target.answer")
                    : t("feedback.target.citation")}
                </span>
                <span className="text-xs text-muted">
                  {new Intl.DateTimeFormat("ja-JP", {
                    dateStyle: "medium",
                    timeStyle: "short",
                  }).format(new Date(item.created_at))}
                </span>
              </div>
              <p className="mt-2 break-words text-sm font-medium text-foreground">
                {item.business_view_name ?? t("feedback.list.unknownBusinessView")}
              </p>
              {item.reason ? (
                <p className="mt-1 text-sm text-muted">
                  {t("feedback.list.reason")}: {t(REASON_LABEL_KEYS[item.reason])}
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              {chatLink ? (
                <Link
                  to={chatLink}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <MessageSquareText size={15} aria-hidden />
                  {t("feedback.list.openConversation")}
                </Link>
              ) : null}
              {documentLink ? (
                <Link
                  to={documentLink}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <FileText size={15} aria-hidden />
                  {t("feedback.list.openCitation")}
                </Link>
              ) : null}
            </div>
          </div>
          <dl className="grid min-w-0 gap-2 rounded-md bg-background p-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
            <Metadata label={t("feedback.list.surface")} value={t(`feedback.surface.${item.source_surface ?? "unknown"}` as I18nKey)} />
            <Metadata label={t("feedback.list.model")} value={item.model ?? "—"} />
            <Metadata label={t("feedback.list.document")} value={item.file_name ?? item.document_id ?? "—"} />
            <div className="min-w-0">
              <dt className="text-muted">{t("feedback.list.trace")}</dt>
              <dd className="mt-1 flex min-w-0 items-center gap-1 font-mono text-foreground">
                <span className="truncate" title={item.trace_id}>{shortId(item.trace_id)}</span>
                <button
                  type="button"
                  className="flex size-11 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-card hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  aria-label={t("feedback.list.copyTrace")}
                  onClick={() => void copyTrace()}
                >
                  <Clipboard size={14} aria-hidden />
                </button>
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>
    </li>
  );
}

function Metadata({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted">{label}</dt>
      <dd className="mt-1 break-words text-foreground">{value}</dd>
    </div>
  );
}

function formatRate(value: number) {
  return `${Math.round(value * 100)}%`;
}

function shortId(value: string) {
  return value.length > 12 ? `${value.slice(0, 12)}…` : value;
}
