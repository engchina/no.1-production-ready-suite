import type { HistoryItem } from "./types";

export type HistoryFeedbackFilter = "all" | "unrated" | "good" | "bad" | "needs_review";
export type HistorySafetyFilter = "all" | "safe" | "blocked";
export type HistorySortKey = "question" | "created_at";
export type HistorySortDirection = "asc" | "desc";

export interface HistorySortState {
  key: HistorySortKey;
  direction: HistorySortDirection;
}

export interface HistoryManagementQuery {
  search: string;
  feedback: HistoryFeedbackFilter;
  safety: HistorySafetyFilter;
  sort: HistorySortState;
}

function normalizedSearchText(item: HistoryItem) {
  return [
    item.question,
    item.rewritten_question,
    item.generated_sql,
    item.executable_sql,
    item.profile_id,
    item.profile_name,
    item.feedback_comment,
  ]
    .join("\n")
    .toLocaleLowerCase("ja-JP");
}

function matchesFeedback(item: HistoryItem, feedback: HistoryFeedbackFilter) {
  if (feedback === "all") return true;
  if (feedback === "unrated") return !item.feedback_rating;
  return item.feedback_rating === feedback;
}

function matchesSafety(item: HistoryItem, safety: HistorySafetyFilter) {
  if (safety === "all") return true;
  return safety === "safe" ? item.safety_is_safe : !item.safety_is_safe;
}

function createdAtValue(item: HistoryItem) {
  const value = Date.parse(item.created_at);
  return Number.isNaN(value) ? 0 : value;
}

export function filterAndSortHistory(items: HistoryItem[], query: HistoryManagementQuery) {
  const search = query.search.trim().toLocaleLowerCase("ja-JP");
  return items
    .filter((item) => {
      if (!matchesFeedback(item, query.feedback) || !matchesSafety(item, query.safety)) return false;
      return !search || normalizedSearchText(item).includes(search);
    })
    .sort((left, right) => {
      const comparison =
        query.sort.key === "question"
          ? left.question.localeCompare(right.question, "ja")
          : createdAtValue(left) - createdAtValue(right);
      return query.sort.direction === "asc" ? comparison : -comparison;
    });
}

export function selectedVisibleHistoryId(items: HistoryItem[], selectedId: string) {
  if (items.some((item) => item.id === selectedId)) return selectedId;
  return items[0]?.id ?? "";
}
