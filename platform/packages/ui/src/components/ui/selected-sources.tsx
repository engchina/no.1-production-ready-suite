import { BookOpen, X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { Button } from "./button";
import { InfoTip } from "./info-tip";

export interface SelectedSource {
  id: string;
  name: string;
  location?: string;
  fullLocation?: string;
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
        className="flex min-w-0 flex-1 basis-48 flex-wrap content-start gap-1 overflow-y-auto overscroll-contain [--source-row:calc(var(--control-height-sm)+2*var(--space-1)+2px)] max-h-[calc(2*var(--source-row)+var(--space-1))]"
      >
        {items.map((item, index) => {
          const text = [item.name, item.location].filter(Boolean).join(" · ");
          return (
            <li
              key={item.id}
              className="flex h-[var(--source-row)] min-w-0 max-w-full items-center gap-1 rounded-control border border-border bg-surface p-1 text-xs"
            >
              <InfoTip
                label={text}
                content={[item.name, item.fullLocation ?? item.location].filter(Boolean).join("\n")}
                className="min-h-[var(--control-height-sm)] gap-1 px-1 text-left"
              >
                <BookOpen size={14} className="shrink-0" aria-hidden />
                <span className="min-w-0 truncate">{item.name}</span>
                {item.location && (
                  <span className="flex min-w-0 shrink-[0.5] items-center gap-1 text-fg-muted">
                    <span aria-hidden>·</span>
                    <span dir="rtl" className="min-w-0 truncate text-left"><bdi>{item.location}</bdi></span>
                  </span>
                )}
              </InfoTip>
              <Button
                className="shrink-0"
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
