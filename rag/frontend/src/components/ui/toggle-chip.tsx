import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * ToggleChip（セグメント化トグル / フィルタチップ）。docs/frontend-button-spec.md §5。
 * フィルタ・モード切替などの連動トグルに使う。状態は色だけでなく `aria-pressed` で伝える。
 * グループは呼び出し側で `role="group"` + `aria-label` を付け、`flex gap-1` 等で並べる。
 */
export function ToggleChip({
  selected,
  children,
  className,
  ...props
}: {
  selected: boolean;
  children: ReactNode;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-pressed">) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={cn(
        "cursor-pointer rounded-full px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
        selected
          ? "bg-primary text-primary-foreground"
          : "border border-border bg-card text-muted hover:bg-background hover:text-foreground",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}
