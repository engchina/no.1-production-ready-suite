import type { LucideIcon } from "lucide-react";
import { type KeyboardEvent, type ReactNode, useRef } from "react";

import { cn } from "../../lib/utils";

export interface TabItem {
  id: string;
  label: string;
  icon?: LucideIcon;
  /** 件数（等幅数字）。 */
  count?: number;
  /** 件数以外の短いバッジ（版数・状態など）。count と同じ見た目で出す。 */
  badge?: string;
  badgeTestId?: string;
  /** 表示ラベルより詳しい読み上げ名が必要な場合だけ指定する。 */
  ariaLabel?: string;
  disabled?: boolean;
}

export interface TabsProps {
  items: TabItem[];
  value: string;
  onChange?: (id: string) => void;
  /** tablist の aria-label（翻訳済み）。 */
  ariaLabel?: string;
  /** 同じ画面に Tabs を複数置くときだけ、TabPanel と揃えて指定する。 */
  idPrefix?: string;
  className?: string;
}

/**
 * ← → / Home / End に応じた移動先の tab id（WAI-ARIA Tabs パターン、無効タブは飛ばし端で循環）。
 * 対象外のキーなら null。
 */
export function nextTabId(items: TabItem[], value: string, key: string): string | null {
  const enabled = items.filter((item) => !item.disabled);
  if (enabled.length === 0) return null;
  const current = enabled.findIndex((item) => item.id === value);
  switch (key) {
    case "ArrowRight":
      return enabled[(current + 1) % enabled.length].id;
    case "ArrowLeft":
      return enabled[(current - 1 + enabled.length) % enabled.length].id;
    case "Home":
      return enabled[0].id;
    case "End":
      return enabled[enabled.length - 1].id;
    default:
      return null;
  }
}

/**
 * ビュー切替。**同じ対象の別の見方**に切り替えるときだけ使う。
 * データを絞り込むだけなら ToggleChip、別の画面に移るなら Sidebar。
 * 下線スタイル固定。PageHeader の `tabs` に渡すとヘッダー下端に吸い付く。
 */
export function Tabs({ items, value, onChange, ariaLabel, idPrefix = "pr", className }: TabsProps) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const next = nextTabId(items, value, event.key);
    if (next === null) return;
    event.preventDefault();
    onChange?.(next);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      onKeyDown={handleKeyDown}
      className={cn(
        // PageHeader 内ではヘッダーの full-bleed な罫線が境界になるため自前の線を出さない
        "flex items-stretch gap-5 overflow-x-auto shadow-[inset_0_-1px_0_var(--color-border)] [scrollbar-width:none] [header_&]:shadow-none [&::-webkit-scrollbar]:hidden",
        className
      )}
    >
      {items.map((item) => {
        const selected = item.id === value;
        const Icon = item.icon;
        const badge = item.count ?? item.badge;
        const badgeId = `${idPrefix}-tab-${item.id}-badge`;
        return (
          <button
            key={item.id}
            ref={(node) => {
              refs.current[item.id] = node;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${item.id}`}
            aria-selected={selected}
            aria-label={item.ariaLabel}
            aria-describedby={badge == null ? undefined : badgeId}
            aria-controls={`${idPrefix}-panel-${item.id}`}
            tabIndex={selected ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onChange?.(item.id)}
            className={cn(
              "group inline-flex h-[var(--tab-height)] shrink-0 cursor-pointer items-center gap-2 whitespace-nowrap border-b-2 border-transparent px-1 text-sm font-medium text-fg-muted transition-colors",
              "hover:enabled:border-border-strong hover:enabled:text-fg",
              "aria-selected:border-accent-emphasis aria-selected:font-semibold aria-selected:text-accent-fg-strong aria-selected:hover:border-accent-emphasis aria-selected:hover:text-accent-fg-strong",
              "disabled:cursor-not-allowed disabled:text-fg-disabled",
              "focus-visible:rounded-sm focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring",
              "forced-colors:aria-selected:border-[Highlight]"
            )}
          >
            {Icon ? <Icon size={16} className="shrink-0" aria-hidden /> : null}
            <span>{item.label}</span>
            {badge == null ? null : (
              <span
                id={badgeId}
                data-testid={item.badgeTestId}
                className="tnum inline-flex min-w-6 items-center justify-center rounded-full bg-surface-hover px-1.5 text-xs font-normal text-fg-muted group-aria-selected:bg-accent-muted group-aria-selected:text-accent-fg-strong"
              >
                {badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** Tabs の中身。`id` は Tabs の item.id と一致させる。選択中のときだけ描画する。 */
export function TabPanel({
  id,
  value,
  idPrefix = "pr",
  className,
  children,
}: {
  id: string;
  value: string;
  idPrefix?: string;
  className?: string;
  children?: ReactNode;
}) {
  if (id !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${id}`}
      aria-labelledby={`${idPrefix}-tab-${id}`}
      tabIndex={0}
      className={cn("min-w-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring", className)}
    >
      {children}
    </div>
  );
}
