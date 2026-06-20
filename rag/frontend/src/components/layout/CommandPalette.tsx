import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Search, X } from "lucide-react";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { NAV_SECTIONS, type NavItem } from "./nav-config";

/**
 * Cmd/Ctrl+K で開くページ移動コマンドパレット。
 * nav-heavy なサイドナビの skip-link 代替 + 高速移動手段。
 * サイドナビの構成（NAV_SECTIONS）をそのまま単一の source of truth として列挙する。
 * 外部依存（cmdk 等）は導入せず、confirm-dialog と同じ portal/focus-trap パターンで実装。
 */

/** 別コンポーネント（サイドバーの検索ボタン等）から開くためのカスタムイベント名。 */
export const OPEN_COMMAND_PALETTE_EVENT = "commandpalette:open";

interface CommandEntry {
  item: NavItem;
  fullLabel: string;
  shortLabel: string;
  sectionTitle: string;
}

function buildEntries(): CommandEntry[] {
  return NAV_SECTIONS.flatMap((section) => {
    const sectionTitle = t(section.titleKey);
    return section.items.map((item) => {
      const fullLabel = t(item.labelKey);
      const shortLabel = item.sidebarLabelKey ? t(item.sidebarLabelKey) : fullLabel;
      return { item, fullLabel, shortLabel, sectionTitle };
    });
  });
}

function matches(entry: CommandEntry, query: string): boolean {
  const haystack = `${entry.fullLabel} ${entry.shortLabel} ${entry.sectionTitle}`.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => haystack.includes(token));
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);

  // Cmd/Ctrl+K でトグル + カスタムイベントで外部から開く。
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
      }
    }
    function onOpen() {
      setOpen(true);
    }
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, onOpen);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, onOpen);
    };
  }, []);

  if (!open) return null;
  return <CommandPaletteDialog onClose={() => setOpen(false)} />;
}

function CommandPaletteDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const previouslyFocused = useRef<Element | null>(null);

  const entries = useMemo(buildEntries, []);
  const results = useMemo(
    () => (query.trim() ? entries.filter((entry) => matches(entry, query)) : entries),
    [entries, query]
  );

  // クエリ変更で選択位置を先頭に戻す。
  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  // 開いたら入力へフォーカス、閉じたらトリガーへ復帰。
  useEffect(() => {
    previouslyFocused.current = document.activeElement;
    inputRef.current?.focus();
    return () => {
      if (previouslyFocused.current instanceof HTMLElement) {
        previouslyFocused.current.focus();
      }
    };
  }, []);

  // 選択中の要素を可視領域へスクロール。
  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, results.length]);

  function select(index: number) {
    const entry = results[index];
    if (!entry) return;
    onClose();
    navigate(entry.item.href);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((prev) => (results.length ? (prev + 1) % results.length : 0));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((prev) => (results.length ? (prev - 1 + results.length) % results.length : 0));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      select(activeIndex);
    }
  }

  return createPortal(
    <div
      className="animate-overlay-in fixed inset-0 z-[1000] flex items-start justify-center bg-black/50 p-4 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("command.title")}
        className="animate-dialog-in flex max-h-[70vh] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl ring-1 ring-black/5"
        onKeyDown={onKeyDown}
      >
        {/* 検索ヘッダー: パレットの主役。アイコン + 入力 + クリア/Esc。 */}
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search size={18} className="shrink-0 text-muted" aria-hidden />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-autocomplete="list"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("command.search.placeholder")}
            // グローバルのフォーム focus 枠(inset ring)を打ち消し、パレットらしいシームレスな検索にする。
            className="h-14 w-full bg-transparent text-[15px] leading-6 text-foreground caret-primary outline-none placeholder:text-muted/80 focus-visible:shadow-none!"
          />
          {query ? (
            <button
              type="button"
              aria-label={t("command.clear")}
              title={t("command.clear")}
              onClick={() => {
                setQuery("");
                inputRef.current?.focus();
              }}
              className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted transition-colors hover:bg-border/60 hover:text-foreground"
            >
              <X size={15} aria-hidden />
            </button>
          ) : (
            <kbd className="shrink-0 rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted">
              esc
            </kbd>
          )}
        </div>

        {results.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-1 px-4 py-12 text-center">
            <Search size={22} className="text-muted/60" aria-hidden />
            <p className="text-sm font-medium text-foreground">{t("command.empty")}</p>
            <p className="text-xs text-muted">{t("command.empty.hint")}</p>
          </div>
        ) : (
          <ul
            id="command-palette-list"
            ref={listRef}
            role="listbox"
            aria-label={t("command.title")}
            className="min-h-0 flex-1 overflow-y-auto p-2"
          >
            {results.map((entry, index) => {
              const Icon = entry.item.icon;
              const isActive = index === activeIndex;
              return (
                <li key={entry.item.href}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    data-active={isActive}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-colors",
                      isActive
                        ? "bg-primary text-primary-foreground"
                        : "text-foreground hover:bg-border/50"
                    )}
                    onMouseMove={() => setActiveIndex(index)}
                    onClick={() => select(index)}
                  >
                    <span
                      className={cn(
                        "flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
                        isActive ? "bg-white/15" : "bg-border/50"
                      )}
                    >
                      <Icon
                        size={15}
                        className={isActive ? "text-primary-foreground" : "text-muted"}
                        aria-hidden
                      />
                    </span>
                    <span className="min-w-0 flex-1 truncate">{entry.fullLabel}</span>
                    <span
                      className={cn(
                        "shrink-0 rounded px-1.5 py-0.5 text-[11px]",
                        isActive ? "bg-white/15 text-primary-foreground/90" : "text-muted"
                      )}
                    >
                      {entry.sectionTitle}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {/* フッター: キーボードヒント + 件数。キーボード優先 UI の完成度を高める。 */}
        <div className="flex items-center justify-between gap-2 border-t border-border bg-background/40 px-4 py-2 text-xs text-muted">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-border bg-background px-1 py-0.5 text-[10px] font-medium">↑</kbd>
              <kbd className="rounded border border-border bg-background px-1 py-0.5 text-[10px] font-medium">↓</kbd>
              {t("command.hint.navigate")}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="inline-flex items-center rounded border border-border bg-background px-1 py-0.5 text-[10px] font-medium">
                <CornerDownLeft size={11} aria-hidden />
              </kbd>
              {t("command.hint.select")}
            </span>
            <span className="hidden items-center gap-1 sm:flex">
              <kbd className="rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium">esc</kbd>
              {t("command.hint.close")}
            </span>
          </div>
          <span className="tnum shrink-0">{t("command.count", { count: results.length })}</span>
        </div>
      </div>
    </div>,
    document.body
  );
}
