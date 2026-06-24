"use client";

import { Check, ChevronDown, Search, X } from "lucide-react";
import {
  type KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

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
  hideEmpty?: string;
  hiddenEmptyCount?: (count: number) => string;
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
}) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<Array<HTMLLIElement | null>>([]);

  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [hideEmpty, setHideEmpty] = useState(true);
  const [activeIndex, setActiveIndex] = useState(0);

  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const normalized = filter.trim().toLowerCase();

  const hasScoped = isEmptyItem ? items.some((item) => !isEmptyItem(item)) : false;
  const emptyCount = isEmptyItem ? items.filter(isEmptyItem).length : 0;
  const showEmptyToggle = Boolean(isEmptyItem && strings.hideEmpty && hasScoped && emptyCount > 0);
  const effectiveHideEmpty = Boolean(isEmptyItem && hideEmpty && hasScoped);

  const sorted = useMemo(() => {
    return sortItems ? sortItems(items) : [...items];
  }, [items, sortItems]);

  const filtered = useMemo(() => {
    let next = sorted;
    if (effectiveHideEmpty && isEmptyItem) {
      next = next.filter((item) => !isEmptyItem(item) || selected.has(getId(item)));
    }
    if (normalized) {
      next = next.filter((item) => {
        const searchText = getSearchText?.(item) ?? getName(item);
        return searchText.toLowerCase().includes(normalized);
      });
    }
    return next;
  }, [sorted, effectiveHideEmpty, isEmptyItem, normalized, selected, getId, getName, getSearchText]);

  const hiddenEmptyCount =
    effectiveHideEmpty && isEmptyItem
      ? items.filter((item) => isEmptyItem(item) && !selected.has(getId(item))).length
      : 0;

  const chips = selectedIds
    .map((id) => items.find((item) => getId(item) === id))
    .filter((item): item is T => Boolean(item));

  useEffect(() => {
    setActiveIndex((index) => Math.min(index, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

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
    if (disabled) return;
    setOpen(true);
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
      case "Escape":
        if (open) {
          event.preventDefault();
          setOpen(false);
        }
        return;
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
    <div ref={rootRef} className="space-y-2">
      <div
        className={cn(
          "group flex min-h-11 w-full flex-wrap items-center gap-1.5 rounded-md border border-border/80 bg-card px-2 py-2 shadow-sm transition-[background-color,border-color,box-shadow] duration-150 focus-within:border-primary/70 focus-within:bg-background focus-within:ring-2 focus-within:ring-primary/15",
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
          className="flex size-8 shrink-0 items-center justify-center rounded-md bg-info-bg text-primary transition-colors group-focus-within:bg-primary group-focus-within:text-white"
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
              className="inline-flex h-7 max-w-full min-w-0 items-center gap-1.5 rounded-md border border-primary/15 bg-info-bg px-2 text-xs font-medium text-foreground sm:max-w-[14rem]"
            >
              <span className="min-w-0 truncate">{name}</span>
              {badge ? (
                <span className="shrink-0 rounded-sm bg-background px-1 py-0.5 text-[10px] font-medium text-primary">
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
                className="relative flex size-5 shrink-0 items-center justify-center rounded-sm text-muted transition-colors before:absolute before:-inset-1 before:content-[''] hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed"
              >
                <X size={12} aria-hidden />
              </button>
            </span>
          );
        })}
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-haspopup="listbox"
          aria-activedescendant={activeOptionId}
          aria-autocomplete="list"
          aria-label={ariaLabel}
          value={filter}
          onChange={(event) => {
            setFilter(event.target.value);
            setOpen(true);
          }}
          onFocus={openList}
          onKeyDown={onInputKeyDown}
          placeholder={chips.length === 0 ? strings.addPlaceholder : ""}
          disabled={disabled}
          className="h-8 min-w-[7.5rem] flex-1 appearance-none border-0 bg-transparent px-1 text-sm leading-8 text-foreground shadow-none outline-none placeholder:text-muted/70 focus:outline-none focus:ring-0 focus-visible:border-transparent! focus-visible:shadow-none! disabled:cursor-not-allowed sm:min-w-[12rem]"
        />
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            if (disabled) return;
            setOpen((value) => !value);
            inputRef.current?.focus();
          }}
          disabled={disabled}
          aria-label={strings.toggleListAria}
          className="relative ml-auto flex size-9 shrink-0 items-center justify-center rounded-md text-muted transition-colors before:absolute before:-inset-1 before:content-[''] hover:bg-info-bg hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 disabled:cursor-not-allowed"
        >
          <ChevronDown
            size={16}
            className={cn("transition-transform", open && "rotate-180")}
            aria-hidden
          />
        </button>
      </div>

      {open ? (
        <div className="overflow-hidden rounded-md border border-border bg-card shadow-sm">
          {showEmptyToggle ? (
            <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
              <span className="text-xs text-muted">
                {strings.count(filtered.length, items.length)}
              </span>
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={hideEmpty}
                  onChange={(event) => setHideEmpty(event.target.checked)}
                  className="cursor-pointer accent-[var(--primary)]"
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
                      "flex min-h-[44px] cursor-pointer items-center gap-2.5 px-3 py-2 text-sm",
                      isActive && "bg-info-bg/60",
                      isSelected && "bg-info-bg/40"
                    )}
                  >
                    <span
                      className={cn(
                        "flex size-4 shrink-0 items-center justify-center rounded-sm border",
                        isSelected
                          ? "border-primary bg-primary text-white"
                          : "border-border"
                      )}
                      aria-hidden
                    >
                      {isSelected ? <Check size={12} strokeWidth={3} /> : null}
                    </span>
                    <span className="min-w-0 max-w-[24rem] truncate font-medium text-foreground">
                      {name}
                      {badge ? (
                        <span className="ml-1.5 rounded-sm bg-info-bg px-1 py-0.5 align-middle text-[10px] font-medium text-primary">
                          {badge}
                        </span>
                      ) : null}
                    </span>
                    {metaText ? (
                      <span className="tnum shrink-0 text-xs text-muted">{metaText}</span>
                    ) : null}
                    <span className="flex-1" aria-hidden />
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-3 py-6 text-center text-xs text-muted">
              {normalized ? strings.noMatch(filter.trim()) : strings.emptyList}
            </p>
          )}

          <div className="flex flex-col gap-1.5 border-t border-border bg-card px-3 py-2 sm:flex-row sm:items-center sm:justify-between sm:py-1.5">
            <span className="tnum text-xs text-muted">
              {hiddenEmptyCount > 0 && strings.hiddenEmptyCount
                ? strings.hiddenEmptyCount(hiddenEmptyCount)
                : strings.selectedCount(selectedIds.length)}
            </span>
            <span className="flex w-full items-center justify-between gap-2 sm:w-auto sm:justify-start sm:gap-3">
              <button
                type="button"
                onClick={selectAllVisible}
                disabled={disabled || filtered.length === 0}
                className="text-xs font-medium text-primary transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-50 disabled:no-underline"
              >
                {strings.selectAllVisible}
              </button>
              <button
                type="button"
                onClick={() => onChange([])}
                disabled={disabled || selectedIds.length === 0}
                className="text-xs text-muted transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              >
                {strings.clear}
              </button>
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
