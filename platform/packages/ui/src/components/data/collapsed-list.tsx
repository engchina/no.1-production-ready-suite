import { ChevronDown, ChevronUp } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "../ui/button";

/**
 * 会話・実行の詳細の中で増えるブロックの畳み方（#4636）。
 *
 * 回答の中の文のカード・実行の成果物・処理の経過の段など、会話や詳細の流れの中に並ぶブロックは、入れ子の
 * スクロール領域にせず、`COLLAPSED_LIST_LIMIT` 件を超えたら残りを畳んで「すべて表示（全 N 件）」で開く
 * （回答の本文の節 `ANSWER_SECTION_COLLAPSE_AFTER` と同じ件数・同じ文言）。
 *
 * - `keep: "start"`（既定）は先頭を残し、ボタンを一覧の後ろに置く。
 * - `keep: "end"` は末尾（最新）を残し、ボタンを一覧の前に置く（実行中の経過で最新の段を見せる）。
 * - `pinned` に当たる項目（実行中・失敗の段など）は畳んでも出す。
 */

/** 畳んだときに出す項目の数（回答の本文の節と同じ）。 */
export const COLLAPSED_LIST_LIMIT = 10;

export interface CollapsedListLabels {
  /** 先頭を残して畳んだ一覧を開くボタン（`total` は全件）。 */
  showAll: (total: number) => string;
  /** 末尾を残して畳んだ一覧の、前の項目を開くボタン（`hidden` は畳んだ件数）。 */
  showEarlier: (hidden: number) => string;
  /** 開いた一覧を畳むボタン。 */
  collapse: string;
}

export const DEFAULT_COLLAPSED_LIST_LABELS: CollapsedListLabels = {
  showAll: (total) => `すべて表示（全 ${total} 件）`,
  showEarlier: (hidden) => `前の ${hidden} 件を表示`,
  collapse: "折りたたむ",
};

export interface CollapsedListOptions<T> {
  /** 畳んだときに出す件数。既定 `COLLAPSED_LIST_LIMIT`。 */
  limit?: number;
  /** 残す側。既定 `"start"`。 */
  keep?: "start" | "end";
  /** 畳んでも出す項目。 */
  pinned?: (item: T, index: number) => boolean;
}

export interface CollapsedListState<T> {
  /** 今出す項目と元の位置。 */
  visible: { item: T; index: number }[];
  /** 件数が上限を超え、開閉のボタンを出すか。 */
  collapsible: boolean;
  expanded: boolean;
  setExpanded: (expanded: boolean) => void;
  /** 畳んでいる件数（開いているときは 0）。 */
  hiddenCount: number;
  total: number;
  keep: "start" | "end";
}

export function useCollapsedList<T>(items: readonly T[], options: CollapsedListOptions<T> = {}): CollapsedListState<T> {
  const { limit = COLLAPSED_LIST_LIMIT, keep = "start", pinned } = options;
  const [expanded, setExpanded] = useState(false);
  const collapsible = items.length > limit;
  const visible = useMemo(() => {
    const all = items.map((item, index) => ({ item, index }));
    if (!collapsible || expanded) return all;
    const from = keep === "end" ? items.length - limit : 0;
    const to = keep === "end" ? items.length : limit;
    return all.filter(({ item, index }) => (index >= from && index < to) || Boolean(pinned?.(item, index)));
  }, [items, collapsible, expanded, keep, limit, pinned]);
  return {
    visible,
    collapsible,
    expanded,
    setExpanded,
    hiddenCount: items.length - visible.length,
    total: items.length,
    keep,
  };
}

export interface CollapsedListToggleProps {
  state: Pick<CollapsedListState<unknown>, "collapsible" | "expanded" | "setExpanded" | "hiddenCount" | "total" | "keep">;
  /** 開閉する一覧の id（`aria-controls`）。 */
  controls?: string;
  labels?: Partial<CollapsedListLabels>;
  testId?: string;
}

/** 畳んだ一覧の開閉のボタン。上限を超えないときは何も出さない。 */
export function CollapsedListToggle({ state, controls, labels: labelsInput, testId }: CollapsedListToggleProps) {
  if (!state.collapsible) return null;
  const labels = { ...DEFAULT_COLLAPSED_LIST_LABELS, ...labelsInput };
  const label = state.expanded
    ? labels.collapse
    : state.keep === "end"
      ? labels.showEarlier(state.hiddenCount)
      : labels.showAll(state.total);
  const icon = state.expanded ? (state.keep === "end" ? ChevronDown : ChevronUp) : state.keep === "end" ? ChevronUp : ChevronDown;
  return (
    <div className="flex">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        icon={icon}
        aria-expanded={state.expanded}
        aria-controls={controls}
        onClick={() => state.setExpanded(!state.expanded)}
        data-testid={testId}
      >
        {label}
      </Button>
    </div>
  );
}
