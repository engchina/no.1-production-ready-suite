import type {
  CitationFeedbackRating,
  CitationFeedbackReason,
  FeedbackListParams,
  FeedbackTargetType,
} from "@/lib/api";
import type { I18nKey } from "@/lib/i18n";

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
/** 理由の表示順（絞り込み・入力・表示で共通）。回答だけの理由は rag_poc の分類を含む。 */
export const FEEDBACK_ANSWER_REASONS: CitationFeedbackReason[] = [
  "incorrect",
  "incomplete",
  "missing_knowledge",
  "outdated_source",
  "ambiguous_question",
  "not_relevant",
  "answer_untrusted",
];
export const FEEDBACK_CITATION_REASONS: CitationFeedbackReason[] = [
  "missing_evidence",
  "not_relevant",
  "answer_untrusted",
];
export const FEEDBACK_REASONS: CitationFeedbackReason[] = [
  ...new Set([...FEEDBACK_ANSWER_REASONS, ...FEEDBACK_CITATION_REASONS]),
];
export const FEEDBACK_REASON_LABEL_KEYS: Record<CitationFeedbackReason, I18nKey> = {
  incorrect: "feedback.reason.incorrect",
  incomplete: "feedback.reason.incomplete",
  missing_evidence: "feedback.reason.missing_evidence",
  not_relevant: "feedback.reason.not_relevant",
  answer_untrusted: "feedback.reason.answer_untrusted",
  missing_knowledge: "feedback.reason.missing_knowledge",
  outdated_source: "feedback.reason.outdated_source",
  ambiguous_question: "feedback.reason.ambiguous_question",
};
const REASONS = new Set<CitationFeedbackReason>(FEEDBACK_REASONS);

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

/** 品質評価の要求（EvaluationClient の `evaluation.requestJson`）の既定。 */
const EMPTY_EVALUATION_REQUEST = { cases: [], top_k: 10, rerank_top_n: 5, mode: "hybrid" };

/**
 * 品質評価の要求 JSON へ評価ケースを追記する（同じ id のケースは置き換える）。
 * 未編集（null）なら新しい要求を作る。利用者が編集中の JSON が壊れている場合は上書きしない。
 */
export function appendEvaluationCase(
  rawRequestJson: string | null,
  evaluationCase: { id: string }
): { ok: true; json: string } | { ok: false } {
  let request: Record<string, unknown> = { ...EMPTY_EVALUATION_REQUEST };
  if (rawRequestJson !== null) {
    try {
      const parsed: unknown = JSON.parse(rawRequestJson);
      if (!parsed || typeof parsed !== "object" || !Array.isArray((parsed as { cases?: unknown }).cases)) {
        return { ok: false };
      }
      request = parsed as Record<string, unknown>;
    } catch {
      return { ok: false };
    }
  }
  const cases = (request.cases as { id?: unknown }[]).filter((item) => item?.id !== evaluationCase.id);
  return { ok: true, json: JSON.stringify({ ...request, cases: [...cases, evaluationCase] }, null, 2) };
}
