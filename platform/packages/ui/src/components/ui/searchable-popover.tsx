import { type FocusEvent, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

import { cn } from "../../lib/utils";

import { useFloatingMenuPosition } from "./floating-menu";
import { SearchField } from "./search-field";

/**
 * 検索つきの選択（`SearchableSelectField` / `SearchableMultiSelect`）の開いた popover の幅の範囲（rem。#5267）。
 * popover はトリガーと同じ幅にし、この範囲に収める（40rem を超えるトリガーではトリガーの左端にそろえて 40rem）。
 * sm（640px）未満の画面では、画面の幅から左右の余白（1rem）を引いた幅にする。
 */
export const SEARCHABLE_POPOVER_WIDTH_REM = { min: 20, max: 40 } as const;

/** 開いた popover の位置と幅（トリガーの下、入らなければ上。幅は `SEARCHABLE_POPOVER_WIDTH_REM`）。 */
export function useSearchablePopoverPosition({
  open,
  triggerRef,
  popoverRef,
}: {
  open: boolean;
  triggerRef: RefObject<HTMLElement | null>;
  popoverRef: RefObject<HTMLElement | null>;
}) {
  return useFloatingMenuPosition({
    align: "start",
    boundary: "viewport",
    widthRangeRem: SEARCHABLE_POPOVER_WIDTH_REM,
    menuRef: popoverRef,
    open,
    triggerRef,
  });
}

export interface SearchablePopoverSearch {
  inputRef: RefObject<HTMLInputElement | null>;
  id: string;
  /** 検索欄の読み上げ名（「〈欄の名前〉を検索」）。 */
  label: string;
  value: string;
  onSearch: (query: string) => void;
  onDraftChange?: (draft: string) => void;
  clearLabel: string;
  placeholder: string;
  /** 件数・検索中の読み上げ（SearchField の resultCountLabel）。 */
  resultCountLabel: string;
  listboxId: string;
  activeDescendant?: string;
  onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
}

/**
 * 検索つきの選択の開いた popover（#578・#5267）。トリガー（ボタン）に付属する一時的な層で、body（モーダルの中なら
 * そのモーダル）へ Portal で描き、下の内容を押さない。最上部に検索欄（SearchField。開くとここへフォーカスが移る）、
 * その下に `children`（候補の一覧・下部の行）を置く。単一選択と複数選択が同じ枠・幅・位置の規則を使う。
 */
export function SearchablePopover({
  popoverRef,
  id,
  label,
  portalContainer,
  position,
  onKeyDown,
  onBlur,
  search,
  children,
}: {
  popoverRef: RefObject<HTMLDivElement | null>;
  id: string;
  /** popover（role=dialog）の読み上げ名（欄の名前）。 */
  label: string;
  portalContainer: HTMLElement;
  position: ReturnType<typeof useSearchablePopoverPosition>;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  onBlur?: (event: FocusEvent<HTMLDivElement>) => void;
  search: SearchablePopoverSearch;
  children: ReactNode;
}) {
  return createPortal(
    <div
      ref={popoverRef}
      id={id}
      role="dialog"
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
      aria-label={label}
      data-floating-menu-placement={position?.placement}
      data-searchable-select-popover=""
      className={cn(
        // 候補の一覧は自分の高さの上限（bounded-scroll-area）の中でスクロールする。画面の空きが足りないときだけ
        // 全体を縮めてスクロールにする（高さは中身の実寸で測るので、全体は overflow-y-auto にしておく）。
        // 縦は flex の列にし、画面の空きが足りないときは候補の一覧（min-h-0）だけを縮め、検索欄と下部の行は残す。
        // 全体のスクロールは最後の手段なので、スクロールバーの溝（scrollbar-gutter）は取らない（幅を下部の行に使う）。
        // 幅はトリガーに合わせて useSearchablePopoverPosition が決める（#5267）。
        "fixed z-[var(--z-dropdown)] flex flex-col overflow-y-auto overscroll-contain rounded-md border border-border bg-surface-raised shadow-[var(--shadow-popover)]",
        !position && "opacity-0"
      )}
      style={position?.style ?? { left: -9999, top: -9999 }}
      // Portal でも React のイベントは祖先（表の行など）へ伝わるため、ここで止める。
      onClick={(event) => event.stopPropagation()}
    >
      <div className="border-b border-border p-2">
        <SearchField
          ref={search.inputRef}
          id={search.id}
          label={search.label}
          labelHidden
          value={search.value}
          onSearch={search.onSearch}
          onDraftChange={search.onDraftChange}
          clearLabel={search.clearLabel}
          resultCountLabel={search.resultCountLabel}
          placeholder={search.placeholder}
          role="combobox"
          aria-expanded
          aria-controls={search.listboxId}
          aria-autocomplete="list"
          aria-activedescendant={search.activeDescendant}
          onKeyDown={search.onKeyDown}
        />
      </div>
      {children}
    </div>,
    portalContainer
  );
}
