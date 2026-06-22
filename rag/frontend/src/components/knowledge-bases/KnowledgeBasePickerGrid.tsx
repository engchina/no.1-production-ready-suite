"use client";

import { Check, ChevronDown, X } from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import type { KnowledgeBaseSummary } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * 知識ベースの複数選択コンボボックス。
 *
 * 旧来の「全件を平置きするチェックボックスの壁」を廃し、Dify / RAGFlow など
 * 主要 RAG プロダクト同様の「選択済みチップ + 検索付きリスト」へ刷新した。
 * - 選択済みは削除可能なチップで常に可視化(スクロールしても見失わない)。
 * - 文書数の多い順に並べ、空(0 文書)の KB は既定で畳んで雑音を抑える。
 * - キーボードのみで開閉・移動・選択・解除が完結する(combobox / listbox ロール)。
 *
 * props 互換を保つため、アップロード/検索/評価/業務ビューで共用できる。
 */
export function KnowledgeBasePickerGrid({
  items,
  selectedIds,
  onChange,
  disabled = false,
  ariaLabel,
}: {
  items: KnowledgeBaseSummary[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  ariaLabel: string;
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

  const hasNonEmpty = items.some((kb) => kb.document_count > 0);
  const emptyCount = items.filter((kb) => kb.document_count === 0).length;
  const showEmptyToggle = hasNonEmpty && emptyCount > 0;
  // すべて空の場合は隠すと何も残らないため、抑制を無効化する。
  const effectiveHideEmpty = hideEmpty && hasNonEmpty;

  /** 文書数の多い順 → 名前順。役立つ KB を上位へ。 */
  const sorted = useMemo(() => {
    return [...items].sort((a, b) => {
      if (b.document_count !== a.document_count) {
        return b.document_count - a.document_count;
      }
      return a.name.localeCompare(b.name, "ja");
    });
  }, [items]);

  const topId = sorted.length > 1 && sorted[0]?.document_count > 0 ? sorted[0].id : null;

  const filtered = useMemo(() => {
    let next = sorted;
    if (effectiveHideEmpty) {
      // 選択済みは空でも残し、リストから解除できるようにする。
      next = next.filter((kb) => kb.document_count > 0 || selected.has(kb.id));
    }
    if (normalized) {
      next = next.filter((kb) => kb.name.toLowerCase().includes(normalized));
    }
    return next;
  }, [sorted, effectiveHideEmpty, normalized, selected]);

  const hiddenEmptyCount = effectiveHideEmpty
    ? items.filter((kb) => kb.document_count === 0 && !selected.has(kb.id)).length
    : 0;

  // 表示中の選択済みチップ(順序は選択順を維持)。
  const chips = selectedIds
    .map((id) => items.find((kb) => kb.id === id))
    .filter((kb): kb is KnowledgeBaseSummary => Boolean(kb));

  useEffect(() => {
    setActiveIndex((index) => Math.min(index, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  // 外側クリックで閉じる。
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

  // ハイライト項目をスクロール内に保つ。
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
    for (const kb of filtered) next.add(kb.id);
    onChange([...next]);
  };

  const openList = () => {
    if (disabled) return;
    setOpen(true);
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
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
        if (target) toggle(target.id);
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
          // 入力が空のとき Backspace で末尾のチップを外す。
          onChange(selectedIds.slice(0, -1));
        }
        return;
      default:
        return;
    }
  };

  const activeOptionId =
    open && filtered[activeIndex] ? `${listId}-opt-${filtered[activeIndex].id}` : undefined;

  return (
    <div ref={rootRef} className="space-y-1.5">
      {/* トリガー: 選択済みチップ + 検索入力 */}
      <div
        className={cn(
          "flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1.5 transition-colors focus-within:border-primary focus-within:outline-2 focus-within:outline-offset-1 focus-within:outline-ring",
          disabled && "cursor-not-allowed opacity-60"
        )}
        onClick={() => {
          if (!disabled) {
            inputRef.current?.focus();
            setOpen(true);
          }
        }}
      >
        {chips.map((kb) => (
          <span
            key={kb.id}
            className="inline-flex max-w-[12rem] items-center gap-1 rounded-md bg-info-bg px-2 py-0.5 text-xs font-medium text-foreground"
          >
            <span className="truncate">{kb.name}</span>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                toggle(kb.id);
              }}
              disabled={disabled}
              aria-label={t("knowledgeBasePicker.removeChip", { name: kb.name })}
              className="relative shrink-0 rounded-sm p-0.5 text-muted transition-colors before:absolute before:-inset-2 before:content-[''] hover:text-foreground disabled:cursor-not-allowed"
            >
              <X size={12} aria-hidden />
            </button>
          </span>
        ))}
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
          placeholder={chips.length === 0 ? t("knowledgeBasePicker.addPlaceholder") : ""}
          disabled={disabled}
          className="h-6 min-w-[8rem] flex-1 bg-transparent px-1 text-sm text-foreground outline-none placeholder:text-muted/70 disabled:cursor-not-allowed"
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
          aria-label={t("knowledgeBasePicker.toggleListAria")}
          className="relative shrink-0 rounded-sm p-1 text-muted transition-colors before:absolute before:-inset-1.5 before:content-[''] hover:text-foreground disabled:cursor-not-allowed"
        >
          <ChevronDown
            size={16}
            className={cn("transition-transform", open && "rotate-180")}
            aria-hidden
          />
        </button>
      </div>

      {/* リスト本体 */}
      {open ? (
        <div className="overflow-hidden rounded-md border border-border bg-card shadow-sm">
          {showEmptyToggle ? (
            <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
              <span className="text-xs text-muted">
                {t("knowledgeBasePicker.count", {
                  shown: filtered.length,
                  total: items.length,
                })}
              </span>
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={hideEmpty}
                  onChange={(event) => setHideEmpty(event.target.checked)}
                  className="cursor-pointer accent-[var(--primary)]"
                />
                {t("knowledgeBasePicker.hideEmpty")}
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
              {filtered.map((kb, index) => {
                const isSelected = selected.has(kb.id);
                const isActive = index === activeIndex;
                return (
                  <li
                    key={kb.id}
                    id={`${listId}-opt-${kb.id}`}
                    ref={(node) => {
                      optionRefs.current[index] = node;
                    }}
                    role="option"
                    aria-selected={isSelected}
                    onPointerDown={(event) => {
                      event.preventDefault();
                      toggle(kb.id);
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
                      {kb.name}
                      {kb.id === topId ? (
                        <span className="ml-1.5 rounded-sm bg-info-bg px-1 py-0.5 align-middle text-[10px] font-medium text-primary">
                          {t("knowledgeBasePicker.mostDocs")}
                        </span>
                      ) : null}
                    </span>
                    <span className="tnum shrink-0 text-xs text-muted">
                      {t("knowledgeBasePicker.documentCount", {
                        count: kb.document_count,
                      })}
                    </span>
                    <span className="flex-1" aria-hidden />
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-3 py-6 text-center text-xs text-muted">
              {normalized
                ? t("knowledgeBasePicker.noMatch", { query: filter.trim() })
                : t("knowledgeBasePicker.emptyList")}
            </p>
          )}

          {/* フッター: 非表示件数 + 一括操作 */}
          <div className="flex items-center justify-between border-t border-border bg-card px-3 py-1.5">
            <span className="tnum text-xs text-muted">
              {hiddenEmptyCount > 0
                ? t("knowledgeBasePicker.hiddenEmptyCount", { count: hiddenEmptyCount })
                : t("knowledgeBasePicker.selectedCount", { count: selectedIds.length })}
            </span>
            <span className="flex items-center gap-3">
              <button
                type="button"
                onClick={selectAllVisible}
                disabled={disabled || filtered.length === 0}
                className="text-xs font-medium text-primary transition-colors hover:underline disabled:cursor-not-allowed disabled:opacity-50 disabled:no-underline"
              >
                {t("knowledgeBasePicker.selectAllVisible")}
              </button>
              <button
                type="button"
                onClick={() => onChange([])}
                disabled={disabled || selectedIds.length === 0}
                className="text-xs text-muted transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              >
                {t("knowledgeBasePicker.clear")}
              </button>
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
