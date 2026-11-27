import { BookOpen, X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { Button } from "./button";

export interface SelectedSource {
  id: string;
  name: string;
  location?: string;
}

interface Props {
  items: SelectedSource[];
  label: string;
  removeLabel: (name: string) => string;
  onRemove: (id: string) => void;
  focusAfterLast?: () => void;
  disabled?: boolean;
  action?: ReactNode;
}

/** 資料の選択は常に全件を表示し、タグの折り返しだけを 2 行までに収める。 */
export function SelectedSources({
  items,
  label,
  removeLabel,
  onRemove,
  focusAfterLast,
  disabled,
  action,
}: Props) {
  const list = useRef<HTMLUListElement>(null);
  if (!items.length) return null;
  function remove(index: number, keyboard: boolean) {
    const next = items[index + 1] ?? items[index - 1];
    onRemove(items[index].id);
    if (keyboard)
      requestAnimationFrame(() => {
        const button =
          next &&
          list.current?.querySelector<HTMLButtonElement>(`button[data-source-id="${CSS.escape(next.id)}"]`);
        if (button) button.focus();
        else focusAfterLast?.();
      });
  }
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="text-xs font-medium text-fg-muted">{label}</span>
      <ul
        ref={list}
        aria-label={label}
        className="flex min-w-0 flex-1 basis-48 flex-wrap content-start gap-1 overflow-y-auto overscroll-contain [--source-row:var(--button-height-sm)] pointer-coarse:[--source-row:var(--control-height-touch)] max-h-[calc(2*var(--source-row)+0.25rem)]"
      >
        {items.map((item, index) => {
          const text = [item.name, item.location].filter(Boolean).join(" · ");
          return (
            <li
              key={item.id}
              className="flex h-[var(--source-row)] min-w-0 max-w-full items-center gap-1 rounded-control border border-border bg-surface pl-2 text-xs"
            >
              <BookOpen size={14} className="shrink-0" aria-hidden="true" />
              <span className="min-w-0 truncate" title={item.name}>{item.name}</span>
              {item.location && <span className="max-w-[45%] shrink-0 truncate text-fg-muted" title={item.location}>· {item.location}</span>}
              <Button
                iconOnly
                size="sm"
                variant="ghost"
                icon={X}
                aria-label={removeLabel(text)}
                data-source-id={item.id}
                disabled={disabled}
                onClick={(event) => remove(index, event.detail === 0)}
              />
            </li>
          );
        })}
      </ul>
      {action}
    </div>
  );
}
