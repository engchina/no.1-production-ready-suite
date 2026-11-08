import type { HTMLAttributes, ReactNode } from "react";

import {
  INFORMATION_CARD_LIST_SCROLL_CLASS,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
} from "../../lib/list-density";
import { cn } from "../../lib/utils";

/**
 * 表ではない行リスト・カードの一覧の縦スクロール領域（#4636）。
 *
 * 件数が増える行リストは、ページ全体を伸ばさず、この領域の中だけを縦にスクロールする（`page-archetypes.md`
 * 「一覧の型と、基準から外す例外」）。高さは共通の定数で決め、製品で `max-h-*` を書かない。
 *
 * - `size="list"`（既定）: 通常の行（`INFORMATION_LIST_SCROLL_CLASS`。md 未満 17.5rem・md 以上 28rem）。
 * - `size="card"`: 本文・差分・入れ子の一覧を持つ高い行（`INFORMATION_CARD_LIST_SCROLL_CLASS`。26.25rem / 42rem）。
 *
 * 領域は名前付きの `region` で、Tab でフォーカスして矢印キーで動かせる（キーボードでもスクロールできる）。
 * 短い一覧は伸ばさない（高さは上限だけ）。中の `ul` / `ol` は呼び出し側が置く（一覧の意味を保つ）。
 */
export interface ListScrollRegionProps extends Omit<HTMLAttributes<HTMLDivElement>, "role" | "tabIndex"> {
  /** 領域の読み上げ名（翻訳済み。例「文の結果の一覧（スクロール）」）。 */
  label: string;
  size?: "list" | "card";
  children: ReactNode;
  testId?: string;
}

export function ListScrollRegion({ label, size = "list", className, children, testId, ...rest }: ListScrollRegionProps) {
  return (
    <div
      {...rest}
      role="region"
      aria-label={label}
      tabIndex={0}
      className={cn(
        "min-w-0",
        size === "card" ? INFORMATION_CARD_LIST_SCROLL_CLASS : INFORMATION_LIST_SCROLL_CLASS,
        INFORMATION_TABLE_FOCUS_CLASS,
        className,
      )}
      data-list-scroll-region={size}
      data-testid={testId}
    >
      {children}
    </div>
  );
}
