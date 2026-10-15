import type { HistoryItem } from "./types";
import { selectedVisibleStringKey } from "../../lib/visible-selection";

export type HistoryFeedbackFilter = "all" | "unrated" | "good" | "bad";
export type HistorySafetyFilter = "all" | "safe" | "blocked";
/** 実行者の絞り込み（すべて / 自分が実行 / 他の人が実行。`GET /api/nl2sql/history` の executor。#1954）。 */
export type HistoryExecutorFilter = "all" | "mine" | "others";
export type HistorySortKey = "question" | "created_at";
export type HistorySortDirection = "asc" | "desc";

/** 実行履歴の並べ替え。サーバーが全件で並べる（`GET /api/nl2sql/history` の sort / direction。#1764）。 */
export interface HistorySortState {
  key: HistorySortKey;
  direction: HistorySortDirection;
}

export function selectedVisibleHistoryId(items: HistoryItem[], selectedId: string) {
  return selectedVisibleStringKey(items, selectedId, (item) => item.id);
}
