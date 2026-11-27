import type { ReactNode } from "react";

import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

/** 凡例の種類の色の印。 */
export function GraphLegendSwatch({ fill }: { fill: string }) {
  return <span className="h-2.5 w-2.5 shrink-0 rounded-sm border border-current/20" style={{ background: fill }} aria-hidden="true" />;
}

/**
 * 凡例（押すと種類を隠す・出す）。`placement="overlay"`（既定）は画布の左下に重ね、
 * `"inline"` は画布の外（上か下）に置く行で、見出し（`label`）を出す。項目が多く狭い画面で画布を覆う図は `inline` にする（#5520）。
 */
export function GraphLegend({
  label, items, disabledIds, onToggle, children, testId, placement = "overlay",
}: {
  label: string;
  items: { id: string; label: string; marker?: ReactNode; hint?: string }[];
  disabledIds: ReadonlySet<string>;
  onToggle: (id: string) => void;
  children?: ReactNode;
  testId?: string;
  placement?: "overlay" | "inline";
}) {
  const inline = placement === "inline";
  return (
    <div className={cn("rounded-md border border-border bg-surface px-2 py-1.5", inline ? "min-w-0" : "absolute bottom-3 left-3 z-10 max-w-[calc(100%-1.5rem)] shadow-sm")} role="group" aria-label={label} data-testid={testId}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-4 text-fg-muted">
        {inline ? <span aria-hidden="true">{label}</span> : null}
        {items.map((item) => <Button type="button" size="sm" variant="ghost" key={item.id}
          aria-pressed={!disabledIds.has(item.id)} tooltip={item.hint} onClick={() => onToggle(item.id)}
          className={cn("text-xs", disabledIds.has(item.id) && "opacity-40 line-through")}
          data-testid={testId ? `${testId}-${item.id}` : undefined}>
          {item.marker}{item.label}
        </Button>)}
        {children}
      </div>
    </div>
  );
}
