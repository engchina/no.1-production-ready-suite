"use client";

import { Button } from "@engchina/production-ready-ui";
import { Check, ChevronDown, ChevronsDown, Search, X } from "lucide-react";
import {
  type FocusEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { useValuesChanged } from "@/lib/render-sync";
import { cn } from "@/lib/utils";

interface MultiSelectComboboxStrings {
  addPlaceholder: string;
  toggleListAria: string;
  removeChip: (name: string) => string;
  count: (shown: number, total: number) => string;
  noMatch: (query: string) => string;
  emptyList: string;
  selectedCount: (count: number) => string;
  selectAllVisible: string;
  clear: string;
  /** 一覧の下部の「完了」。選択は選ぶたびに反映済みのため、一覧を閉じて入力欄へ戻るだけ（#316）。 */
  done: string;
  hideEmpty?: string;
  hiddenEmptyCount?: (count: number) => string;
}

/**
 * サーバー側で検索・ページングするときの設定（#302）。渡すと、`items` を画面側で絞り込み・
 * 並べ替え・空項目の抑制をせず、サーバーが返した順のまま出す。
 */
export interface MultiSelectComboboxRemote {
  /** 入力した検索語（debounce は呼び出し側で行う）。 */
  onFilterChange: (filter: string) => void;
  /** 条件に一致する全件数。 */
  total: number;
  hasMore: boolean;
  loadingMore: boolean;
  /** 検索語を変えて取り直している間。 */
  searching: boolean;
  onLoadMore: () => void;
  loadMoreLabel: string;
  searchingLabel: string;
}

/** 入力欄をフォームの欄として扱うときの設定（外の FieldLabel と結ぶ・必須・エラー。#531）。 */
export interface MultiSelectComboboxFieldProps {
  /** 入力欄の id。外の FieldLabel（htmlFor）と結ぶときに渡す。 */
  inputId?: string;
  /** 1 件以上の選択が必須。入力欄（role=combobox）の aria-required で伝える。 */
  required?: boolean;
  /** 未選択などのエラー中。入力欄の aria-invalid で伝える。 */
  invalid?: boolean;
  /** 入力欄の aria-describedby（エラー・補足の id）。 */
  describedBy?: string;
}

export function MultiSelectCombobox<T>({
  items,
  selectedIds,
  onChange,
  disabled = false,
  ariaLabel,
  getId,
  getName,
  getSearchText,
  getMetaText,
  sortItems,
  isEmptyItem,
  getChipBadge,
  getOptionBadge,
  strings,
  triggerClassName,
  remote,
  selectedItems,
  inputId,
  required = false,
  invalid = false,
  describedBy,
}: {
  items: T[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  ariaLabel: string;
  getId: (item: T) => string;
  getName: (item: T) => string;
  getSearchText?: (item: T) => string;
  getMetaText?: (item: T) => string;
  sortItems?: (items: T[]) => T[];
  isEmptyItem?: (item: T) => boolean;
  getChipBadge?: (item: T) => string | null;
  getOptionBadge?: (item: T) => string | null;
  strings: MultiSelectComboboxStrings;
  triggerClassName?: string;
  remote?: MultiSelectComboboxRemote;
  /** チップに出す選択済みの項目。`items`（検索結果のページ）に無い選択済みも名前で出すために渡す。 */
  selectedItems?: T[];
} & MultiSelectComboboxFieldProps) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<Array<HTMLLIElement | null>>([]);
  // 閉じて入力欄へフォーカスを戻すとき、入力欄の onFocus で開き直さないための印。
  const suppressOpenOnFocusRef = useRef(false);

  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [hideEmpty, setHideEmpty] = useState(true);
  const [activeIndex, setActiveIndex] = useState(0);

  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  // サーバー側で検索するときは、画面側で絞り込まない（返った結果をそのまま出す）。
  const normalized = remote ? "" : filter.trim().toLowerCase();
  // 空項目の抑制と並べ替えは全件を持つときだけ意味がある（ページの途中では誤解を招く）。
  const localIsEmptyItem = remote ? undefined : isEmptyItem;

  const hasScoped = localIsEmptyItem ? items.some((item) => !localIsEmptyItem(item)) : false;
  const emptyCount = localIsEmptyItem ? items.filter(localIsEmptyItem).length : 0;
  const showEmptyToggle = Boolean(
    localIsEmptyItem && strings.hideEmpty && hasScoped && emptyCount > 0
  );
  const effectiveHideEmpty = Boolean(localIsEmptyItem && hideEmpty && hasScoped);

  const sorted = useMemo(() => {
    return sortItems && !remote ? sortItems(items) : [...items];
  }, [items, sortItems, remote]);

  const filtered = useMemo(() => {
    let next = sorted;
    if (effectiveHideEmpty && localIsEmptyItem) {
      next = next.filter((item) => !localIsEmptyItem(item) || selected.has(getId(item)));
    }
    if (normalized) {
      next = next.filter((item) => {
        const searchText = getSearchText?.(item) ?? getName(item);
        return searchText.toLowerCase().includes(normalized);
      });
    }
    return next;
  }, [
    sorted,
    effectiveHideEmpty,
    localIsEmptyItem,
    normalized,
    selected,
    getId,
    getName,
    getSearchText,
  ]);

  const hiddenEmptyCount =
    effectiveHideEmpty && localIsEmptyItem
      ? items.filter((item) => localIsEmptyItem(item) && !selected.has(getId(item))).length
      : 0;

  const chips = selectedIds
    .map(
      (id) =>
        selectedItems?.find((item) => getId(item) === id) ??
        items.find((item) => getId(item) === id)
    )
    .filter((item): item is T => Boolean(item));

  const updateFilter = (value: string) => {
    setFilter(value);
    remote?.onFilterChange(value);
  };

  // 候補数が変わったレンダーで、選択位置を候補の範囲に収める。
  const filteredCountChanged = useValuesChanged([filtered.length]);
  if (filteredCountChanged) {
    setActiveIndex((index) => Math.min(index, Math.max(0, filtered.length - 1)));
  }

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    optionRefs.current[activeIndex]?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  const toggle = useCallback(
    (id: string) => {
      onChange(
        selected.has(id)
          ? selectedIds.filter((current) => current !== id)
          : [...selectedIds, id]
      );
    },
    [onChange, selected, selectedIds]
  );

  const selectAllVisible = () => {
    const next = new Set(selectedIds);
    for (const item of filtered) next.add(getId(item));
    onChange([...next]);
  };

  const openList = () => {
    if (disabled || suppressOpenOnFocusRef.current) return;
    setOpen(true);
  };

  /** 一覧を閉じ、入力欄へフォーカスを戻す（完了・Esc・開閉ボタン。#316）。 */
  const closeAndFocusInput = () => {
    setOpen(false);
    suppressOpenOnFocusRef.current = true;
    try {
      inputRef.current?.focus();
    } finally {
      suppressOpenOnFocusRef.current = false;
    }
  };

  // Esc は入力欄だけでなく、一覧の中の操作（さらに表示・完了など）にフォーカスがあるときも効かせる。
  const onRootKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    // 外側のダイアログなどまで Esc を伝えない（一覧だけを閉じる）。
    event.stopPropagation();
    closeAndFocusInput();
  };

  // Tab / Shift+Tab などでフォーカスが入力欄と一覧の外へ移ったら閉じる。移り先が無い
  // （ウィンドウの切り替え・フォーカスできない所のクリック）ときは閉じない。外側のクリックは
  // pointerdown の処理が閉じる。
  const onRootBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (!open) return;
    const next = event.relatedTarget;
    if (next instanceof Node && !rootRef.current?.contains(next)) {
      setOpen(false);
    }
  };

  const onInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        setActiveIndex((index) => Math.min(index + 1, filtered.length - 1));
        return;
      case "ArrowUp":
        event.preventDefault();
        setActiveIndex((index) => Math.max(index - 1, 0));
        return;
      case "Enter": {
        if (!open) return;
        event.preventDefault();
        const target = filtered[activeIndex];
        if (target) toggle(getId(target));
        return;
      }
      case "Backspace":
        if (filter === "" && selectedIds.length > 0) {
          onChange(selectedIds.slice(0, -1));
        }
        return;
      default:
        return;
    }
  };

  const activeOptionId =
    open && filtered[activeIndex]
      ? `${listId}-opt-${getId(filtered[activeIndex])}`
      : undefined;

  return (
    <div ref={rootRef} className="space-y-2" onKeyDown={onRootKeyDown} onBlur={onRootBlur}>
      <div
        className={cn(
          "group flex min-h-11 w-full flex-wrap items-center gap-1.5 rounded-md border border-border/80 bg-surface px-2 py-2 shadow-sm transition-[background-color,border-color,box-shadow] duration-150 focus-within:border-focus-ring focus-within:bg-surface-hover focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-focus-ring",
          triggerClassName,
          disabled && "cursor-not-allowed opacity-60"
        )}
        onClick={() => {
          if (!disabled) {
            inputRef.current?.focus();
            setOpen(true);
          }
        }}
      >
        <span
          className="flex size-8 shrink-0 items-center justify-center rounded-md bg-info-subtle text-accent-fg transition-colors group-focus-within:bg-accent-emphasis group-focus-within:text-fg-on-accent"
          aria-hidden
        >
          <Search size={16} />
        </span>
        {chips.map((item) => {
          const id = getId(item);
          const name = getName(item);
          const badge = getChipBadge?.(item);
          return (
            <span
              key={id}
              className="inline-flex h-7 max-w-full min-w-0 items-center gap-1.5 rounded-md border border-accent-emphasis bg-info-subtle px-2 text-xs font-medium text-fg sm:max-w-[14rem]"
            >
              <span className="min-w-0 truncate">{name}</span>
              {badge ? (
                <span className="shrink-0 rounded-sm bg-surface-sunken px-1 py-0.5 text-xs font-medium text-accent-fg">
                  {badge}
                </span>
              ) : null}
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  toggle(id);
                }}
                disabled={disabled}
                aria-label={strings.removeChip(name)}
                className="relative flex size-5 shrink-0 items-center justify-center rounded-sm text-fg-muted transition-colors before:absolute before:-inset-1 before:content-[''] hover:bg-surface-hover hover:text-fg disabled:cursor-not-allowed"
              >
                <X size={14} aria-hidden />
              </button>
            </span>
          );
        })}
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          role="combobox"
          aria-required={required || undefined}
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy}
          aria-expanded={open}
          aria-controls={listId}
          aria-haspopup="listbox"
          aria-activedescendant={activeOptionId}
          aria-autocomplete="list"
          aria-label={ariaLabel}
          value={filter}
          onChange={(event) => {
            updateFilter(event.target.value);
            setOpen(true);
          }}
          onFocus={openList}
          onKeyDown={onInputKeyDown}
          placeholder={chips.length === 0 ? strings.addPlaceholder : ""}
          disabled={disabled}
          className="h-8 min-w-[7.5rem] flex-1 appearance-none border-0 bg-transparent px-1 text-sm leading-8 text-fg shadow-none placeholder:text-fg-muted focus-visible:border-transparent! focus-visible:shadow-none! disabled:cursor-not-allowed sm:min-w-[12rem]"
        />
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            if (disabled) return;
            if (open) {
              closeAndFocusInput();
            } else {
              setOpen(true);
              inputRef.current?.focus();
            }
          }}
          disabled={disabled}
          aria-label={strings.toggleListAria}
          className="relative ml-auto flex size-9 shrink-0 items-center justify-center rounded-md text-fg-muted transition-colors before:absolute before:-inset-1 before:content-[''] hover:bg-info-subtle hover:text-fg disabled:cursor-not-allowed"
        >
          <ChevronDown
            size={16}
            className={cn("transition-transform", open && "rotate-180")}
            aria-hidden
          />
        </button>
      </div>

      {open ? (
        <div className="overflow-hidden rounded-md border border-border bg-surface shadow-sm">
          {remote ? (
            <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5">
              <span className="tnum text-xs text-fg-muted" aria-live="polite">
                {remote.searching ? remote.searchingLabel : strings.count(items.length, remote.total)}
              </span>
            </div>
          ) : showEmptyToggle ? (
            <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
              <span className="text-xs text-fg-muted">
                {strings.count(filtered.length, items.length)}
              </span>
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-fg-muted">
                <input
                  type="checkbox"
                  checked={hideEmpty}
                  onChange={(event) => setHideEmpty(event.target.checked)}
                  className="cursor-pointer accent-accent-emphasis"
                />
                {strings.hideEmpty}
              </label>
            </div>
          ) : null}

          {filtered.length > 0 ? (
            <ul
              id={listId}
              role="listbox"
              aria-label={ariaLabel}
              aria-multiselectable
              aria-busy={remote?.searching || undefined}
              className="bounded-scroll-area py-1"
            >
              {filtered.map((item, index) => {
                const id = getId(item);
                const name = getName(item);
                const isSelected = selected.has(id);
                const isActive = index === activeIndex;
                const badge = getOptionBadge?.(item);
                const metaText = getMetaText?.(item);
                return (
                  <li
                    key={id}
                    id={`${listId}-opt-${id}`}
                    ref={(node) => {
                      optionRefs.current[index] = node;
                    }}
                    role="option"
                    aria-selected={isSelected}
                    onPointerDown={(event) => {
                      event.preventDefault();
                      toggle(id);
                      inputRef.current?.focus();
                    }}
                    onMouseEnter={() => setActiveIndex(index)}
                    className={cn(
                      "flex min-h-[var(--control-height-touch)] cursor-pointer items-center gap-2.5 px-3 py-2 text-sm",
                      isActive && "bg-info-subtle",
                      isSelected && "bg-info-subtle"
                    )}
                  >
                    <span
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded-sm border",
                        isSelected
                          ? "border-accent-emphasis bg-accent-emphasis text-fg-on-accent"
                          : "border-border"
                      )}
                      aria-hidden
                    >
                      {isSelected ? <Check size={14} strokeWidth={3} /> : null}
                    </span>
                    <span className="min-w-0 max-w-[24rem] truncate font-medium text-fg">
                      {name}
                      {badge ? (
                        <span className="ml-1.5 rounded-sm bg-info-subtle px-1 py-0.5 align-middle text-xs font-medium text-accent-fg">
                          {badge}
                        </span>
                      ) : null}
                    </span>
                    {metaText ? (
                      <span className="tnum shrink-0 text-xs text-fg-muted">{metaText}</span>
                    ) : null}
                    <span className="flex-1" aria-hidden />
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-3 py-6 text-center text-xs text-fg-muted" role="status">
              {remote?.searching
                ? remote.searchingLabel
                : filter.trim()
                  ? strings.noMatch(filter.trim())
                  : strings.emptyList}
            </p>
          )}

          {remote?.hasMore ? (
            <div className="border-t border-border px-3 py-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                icon={ChevronsDown}
                loading={remote.loadingMore}
                disabled={disabled}
                onClick={remote.onLoadMore}
                className="w-full"
              >
                {remote.loadMoreLabel}
              </Button>
            </div>
          ) : null}

          <div className="flex flex-col gap-1.5 border-t border-border bg-surface px-3 py-2 sm:flex-row sm:items-center sm:justify-between sm:py-1.5">
            <span className="tnum text-xs text-fg-muted">
              {hiddenEmptyCount > 0 && strings.hiddenEmptyCount
                ? strings.hiddenEmptyCount(hiddenEmptyCount)
                : strings.selectedCount(selectedIds.length)}
            </span>
            <span className="flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-1.5 sm:w-auto sm:justify-start">
              <span className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <button
                  type="button"
                  onClick={selectAllVisible}
                  // 検索語を変えている間は古い結果が出ているため、まとめて選ばせない。
                  disabled={disabled || filtered.length === 0 || Boolean(remote?.searching)}
                  className="whitespace-nowrap text-xs font-medium text-accent-fg transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-50 disabled:no-underline"
                >
                  {strings.selectAllVisible}
                </button>
                <button
                  type="button"
                  onClick={() => onChange([])}
                  disabled={disabled || selectedIds.length === 0}
                  className="whitespace-nowrap text-xs text-fg-muted transition-colors hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {strings.clear}
                </button>
              </span>
              {/* 選択は選ぶたびに反映済み。「完了」は一覧を閉じて入力欄へ戻る、目に見える閉じ方（#316）。
                  ページの主操作と競わないよう secondary にする。 */}
              <Button
                type="button"
                size="sm"
                variant="secondary"
                icon={Check}
                onClick={closeAndFocusInput}
                // 狭い幅で折り返したときも右端に置く。
                className="ml-auto"
              >
                {strings.done}
              </Button>
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
