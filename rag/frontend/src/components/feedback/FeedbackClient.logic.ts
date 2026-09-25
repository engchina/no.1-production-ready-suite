import type {
  CitationFeedbackRating,
  CitationFeedbackReason,
  FeedbackListParams,
  FeedbackTargetType,
} from "@/lib/api";

export const FEEDBACK_PAGE_SIZES = [25, 50, 100] as const;
export const FEEDBACK_PERIODS = [7, 30, 90, null] as const;

export interface FeedbackUrlState {
  periodDays: number | null;
  businessViewId: string;
  targetType: FeedbackTargetType | "";
  rating: CitationFeedbackRating | "";
  reason: CitationFeedbackReason | "";
  q: string;
  sortOrder: "newest" | "oldest";
  pageSize: (typeof FEEDBACK_PAGE_SIZES)[number];
  page: number;
  feedbackId: string;
}

const TARGETS = new Set<FeedbackTargetType>(["answer", "citation"]);
const RATINGS = new Set<CitationFeedbackRating>(["helpful", "not_helpful"]);
const REASONS = new Set<CitationFeedbackReason>([
  "incorrect",
  "incomplete",
  "missing_evidence",
  "not_relevant",
  "answer_untrusted",
]);

export function parseFeedbackUrl(params: URLSearchParams): FeedbackUrlState {
  const period = params.get("period");
  const periodDays = period === "all" ? null : validNumber(period, [7, 30, 90], 30);
  const pageSize = validNumber(params.get("size"), [...FEEDBACK_PAGE_SIZES], 50) as FeedbackUrlState["pageSize"];
  const target = params.get("target") ?? "";
  const rating = params.get("rating") ?? "";
  const reason = params.get("reason") ?? "";
  return {
    periodDays,
    businessViewId: params.get("business_view") ?? "",
    targetType: TARGETS.has(target as FeedbackTargetType) ? (target as FeedbackTargetType) : "",
    rating: RATINGS.has(rating as CitationFeedbackRating)
      ? (rating as CitationFeedbackRating)
      : "",
    reason: REASONS.has(reason as CitationFeedbackReason)
      ? (reason as CitationFeedbackReason)
      : "",
    q: (params.get("q") ?? "").slice(0, 200),
    sortOrder: params.get("sort") === "oldest" ? "oldest" : "newest",
    pageSize,
    page: Math.max(1, integer(params.get("page"), 1)),
    feedbackId: params.get("feedback") ?? "",
  };
}

export function feedbackListParams(state: FeedbackUrlState): FeedbackListParams {
  return {
    business_view_id: state.businessViewId || undefined,
    target_type: state.targetType || undefined,
    rating: state.rating || undefined,
    reason: state.reason || undefined,
    period_days: state.periodDays,
    q: state.q || undefined,
    sort_order: state.sortOrder,
    limit: state.pageSize,
    offset: (state.page - 1) * state.pageSize,
  };
}

export type PageWindowItem = number | "ellipsis";

export function pageWindow(current: number, total: number): PageWindowItem[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = new Set([1, total, current - 1, current, current + 1]);
  if (current <= 3) [2, 3, 4].forEach((page) => pages.add(page));
  if (current >= total - 2) [total - 3, total - 2, total - 1].forEach((page) => pages.add(page));
  const sorted = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b);
  const result: PageWindowItem[] = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1] > 1) result.push("ellipsis");
    result.push(page);
  });
  return result;
}

function validNumber(value: string | null, allowed: readonly number[], fallback: number): number {
  const parsed = integer(value, fallback);
  return allowed.includes(parsed) ? parsed : fallback;
}

function integer(value: string | null, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : fallback;
}
