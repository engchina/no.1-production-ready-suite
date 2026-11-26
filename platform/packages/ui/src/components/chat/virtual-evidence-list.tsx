import { defaultRangeExtractor, useVirtualizer, type Range } from "@tanstack/react-virtual";
import { useCallback, useRef, useState, type ReactNode } from "react";

import type { EvidenceListItem } from "./answer-evidence";

/** 可変高の根拠カードを実測し、スクロール位置の前後だけを描く。 */
export function VirtualEvidenceList({
  items,
  renderCard,
  testId,
}: {
  items: readonly EvidenceListItem[];
  renderCard: (index: number) => ReactNode;
  testId?: string;
}) {
  const list = useRef<HTMLUListElement>(null);
  const [focused, setFocused] = useState<number | null>(null);
  const itemKey = useCallback((index: number) => `${items[index].key}-${index}`, [items]);
  const rangeExtractor = useCallback((range: Range) => {
    const rows = defaultRangeExtractor(range);
    if (focused === null || focused >= range.count) return rows;
    // 原文から戻る操作元と、Tab / Shift+Tab で次に移る行を残す。
    const neighbors = [focused - 1, focused, focused + 1].filter(i => i >= 0 && i < range.count);
    return [...new Set([...rows, ...neighbors])].sort((a, b) => a - b);
  }, [focused]);
  const virtual = useVirtualizer({
    count: items.length,
    getScrollElement: () => list.current,
    getItemKey: itemKey,
    estimateSize: () => 176, // 未表示のカードだけに使う概算。表示時に余白込みの実寸で更新する。
    overscan: 2,
    rangeExtractor,
  });

  return (
    <ul
      ref={list}
      tabIndex={0}
      data-testid={testId}
      className="bounded-scroll-area-lg pr-1 [overflow-anchor:none]"
      onFocusCapture={(event) => {
        const row = event.target.closest<HTMLLIElement>("li[data-index]");
        setFocused(row ? Number(row.dataset.index) : null);
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget || (event.key !== "Home" && event.key !== "End")) return;
        event.preventDefault();
        const end = event.key === "End";
        virtual.scrollToIndex(end ? items.length - 1 : 0, { align: end ? "end" : "start" });
      }}
    >
      <li role="presentation" aria-hidden="true" style={{ height: virtual.getTotalSize() }} />
      {virtual.getVirtualItems().map(row => (
        <li
          key={row.key}
          ref={virtual.measureElement}
          data-index={row.index}
          aria-posinset={row.index + 1}
          aria-setsize={items.length}
          className={`absolute inset-x-0 top-0 pr-1${row.index < items.length - 1 ? " pb-2" : ""}`}
          style={{ transform: `translateY(${row.start}px)` }}
        >
          {renderCard(row.index)}
        </li>
      ))}
    </ul>
  );
}
