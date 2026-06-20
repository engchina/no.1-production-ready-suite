"use client";

import { CheckCheck, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import type { KnowledgeBaseSummary } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** これ以上の件数になったら絞り込み入力とスクロール領域を表示する。 */
const TOOLBAR_THRESHOLD = 9;

/**
 * 知識ベースの複数選択グリッド。
 * 件数が多い場合でもレイアウトが崩れないよう、絞り込み・全選択/クリア・
 * 高さ固定のスクロール領域を備える。アップロード/検索/評価で共用する。
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
  const [filter, setFilter] = useState("");
  const normalized = filter.trim().toLowerCase();

  const filtered = useMemo(
    () =>
      normalized
        ? items.filter((kb) => kb.name.toLowerCase().includes(normalized))
        : items,
    [items, normalized]
  );

  const showToolbar = items.length > TOOLBAR_THRESHOLD;
  const selected = new Set(selectedIds);

  const toggle = (id: string) => {
    onChange(
      selected.has(id)
        ? selectedIds.filter((current) => current !== id)
        : [...selectedIds, id]
    );
  };

  const selectAllVisible = () => {
    const next = new Set(selectedIds);
    for (const kb of filtered) next.add(kb.id);
    onChange([...next]);
  };

  return (
    <div className="space-y-2">
      {showToolbar ? (
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-0 flex-1 sm:max-w-xs">
            <Search
              size={14}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
              aria-hidden
            />
            <input
              type="text"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder={t("knowledgeBasePicker.filterPlaceholder")}
              aria-label={t("knowledgeBasePicker.filterAria")}
              disabled={disabled}
              className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-8 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60"
            />
            {filter ? (
              <button
                type="button"
                onClick={() => setFilter("")}
                aria-label={t("knowledgeBasePicker.filterClear")}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-muted transition-colors hover:bg-info-bg hover:text-foreground"
              >
                <X size={14} aria-hidden />
              </button>
            ) : null}
          </div>
          <span className="tnum ml-auto text-xs text-muted" aria-live="polite">
            {t("knowledgeBasePicker.count", {
              shown: filtered.length,
              total: items.length,
            })}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={selectAllVisible}
            disabled={disabled || filtered.length === 0}
          >
            <CheckCheck size={14} aria-hidden />
            {t("knowledgeBasePicker.selectAll")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onChange([])}
            disabled={disabled || selectedIds.length === 0}
          >
            <X size={14} aria-hidden />
            {t("knowledgeBasePicker.clear")}
          </Button>
        </div>
      ) : null}

      {filtered.length > 0 ? (
        <div
          role="group"
          aria-label={ariaLabel}
          className={cn(
            "grid gap-2 sm:grid-cols-2 xl:grid-cols-3",
            showToolbar &&
              "bounded-scroll-area rounded-md border border-border bg-card/40 p-2"
          )}
        >
          {filtered.map((knowledgeBase) => (
            <label
              key={knowledgeBase.id}
              className={cn(
                "flex min-w-0 cursor-pointer items-start gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm transition-colors hover:bg-info-bg/40",
                selected.has(knowledgeBase.id) && "border-primary/40 bg-info-bg/50",
                disabled && "cursor-not-allowed opacity-60"
              )}
            >
              <input
                type="checkbox"
                checked={selected.has(knowledgeBase.id)}
                onChange={() => toggle(knowledgeBase.id)}
                disabled={disabled}
                className="mt-0.5 cursor-pointer accent-[var(--primary)] disabled:cursor-not-allowed"
              />
              <span className="min-w-0">
                <span className="block truncate font-medium text-foreground">
                  {knowledgeBase.name}
                </span>
                <span className="tnum block text-xs text-muted">
                  {t("knowledgeBasePicker.documentCount", {
                    count: knowledgeBase.document_count,
                  })}
                </span>
              </span>
            </label>
          ))}
        </div>
      ) : (
        <p className="rounded-md border border-dashed border-border bg-background px-3 py-6 text-center text-xs text-muted">
          {t("knowledgeBasePicker.noMatch", { query: filter.trim() })}
        </p>
      )}
    </div>
  );
}
