import { Check, ChevronDown } from "lucide-react";
import {
  type KeyboardEvent,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { cn } from "@/lib/utils";

export interface SelectFieldOption<T extends string = string> {
  value: T;
  label: string;
  description?: string;
}

interface SelectFieldProps<T extends string> {
  id: string;
  label: string;
  value: T;
  options: readonly SelectFieldOption<T>[];
  onValueChange: (value: T) => void;
  helper?: string;
  error?: string;
  required?: boolean;
  requiredLabel?: string;
  placeholder?: string;
  className?: string;
  buttonClassName?: string;
}

/** 見た目と操作を統一した選択フィールド。 */
export function SelectField<T extends string>({
  id,
  label,
  value,
  options,
  onValueChange,
  helper,
  error,
  required,
  requiredLabel,
  placeholder = "",
  className,
  buttonClassName,
}: SelectFieldProps<T>) {
  const reactId = useId();
  const listboxId = `${id}-${reactId}-listbox`;
  const labelId = `${id}-${reactId}-label`;
  const hintId = `${id}-${reactId}-hint`;
  const errorId = `${id}-${reactId}-error`;
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);

  const selectedIndex = useMemo(
    () => options.findIndex((option) => option.value === value),
    [options, value]
  );
  const selectedOption = selectedIndex >= 0 ? options[selectedIndex] : null;
  const activeIndex = highlightedIndex >= 0 ? highlightedIndex : selectedIndex;
  const describedBy = [
    helper ? hintId : "",
    error ? errorId : "",
  ].filter(Boolean).join(" ") || undefined;

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  function openList(nextIndex = selectedIndex >= 0 ? selectedIndex : 0) {
    if (options.length === 0) return;
    setHighlightedIndex(nextIndex);
    setOpen(true);
  }

  function closeList() {
    setOpen(false);
    setHighlightedIndex(-1);
  }

  function selectOption(option: SelectFieldOption<T>) {
    onValueChange(option.value);
    closeList();
    window.requestAnimationFrame(() => buttonRef.current?.focus());
  }

  function moveHighlight(delta: number) {
    if (options.length === 0) return;
    const base = activeIndex >= 0 ? activeIndex : 0;
    const next = (base + delta + options.length) % options.length;
    setHighlightedIndex(next);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) {
          openList();
        } else {
          moveHighlight(1);
        }
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!open) {
          openList(selectedIndex >= 0 ? selectedIndex : options.length - 1);
        } else {
          moveHighlight(-1);
        }
        break;
      case "Home":
        if (open) {
          event.preventDefault();
          setHighlightedIndex(0);
        }
        break;
      case "End":
        if (open) {
          event.preventDefault();
          setHighlightedIndex(options.length - 1);
        }
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        if (!open) {
          openList();
          return;
        }
        if (activeIndex >= 0) {
          selectOption(options[activeIndex]);
        }
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          closeList();
        }
        break;
      case "Tab":
        closeList();
        break;
      default:
        break;
    }
  }

  return (
    <div ref={rootRef} className={cn("space-y-1.5", className)}>
      <label id={labelId} htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-foreground">
        {label}
        {required && requiredLabel ? (
          <span
            aria-hidden="true"
            className="rounded-full bg-warning-bg px-2 py-0.5 text-[11px] font-semibold text-warning"
          >
            {requiredLabel}
          </span>
        ) : null}
      </label>
      <div className="relative">
        <button
          ref={buttonRef}
          id={id}
          type="button"
          role="combobox"
          aria-controls={listboxId}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-invalid={Boolean(error)}
          aria-required={required}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-activedescendant={open && activeIndex >= 0 ? optionId(id, activeIndex) : undefined}
          onClick={() => (open ? closeList() : openList())}
          onKeyDown={handleKeyDown}
          className={cn(
            "flex h-10 w-full cursor-pointer items-center justify-between gap-3 rounded-md border bg-card px-3 text-left text-sm text-foreground outline-none transition-colors",
            "hover:bg-background focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
            error ? "border-danger" : "border-border",
            buttonClassName
          )}
        >
          <span className={cn("min-w-0 truncate", !selectedOption && !value && "text-muted/70")}>
            {selectedOption?.label ?? (value || placeholder)}
          </span>
          <ChevronDown
            size={16}
            className={cn(
              "shrink-0 text-muted transition-transform duration-150",
              open && "rotate-180 text-primary"
            )}
            aria-hidden
          />
        </button>

        {open ? (
          <ul
            id={listboxId}
            role="listbox"
            aria-labelledby={labelId}
            className="absolute left-0 right-0 top-[calc(100%+0.25rem)] z-50 max-h-64 overflow-auto rounded-md border border-border bg-card p-1 shadow-lg"
          >
            {options.map((option, index) => {
              const selected = option.value === value;
              const highlighted = index === activeIndex;
              return (
                <li
                  key={option.value}
                  id={optionId(id, index)}
                  role="option"
                  aria-selected={selected}
                  onMouseEnter={() => setHighlightedIndex(index)}
                  onPointerDown={(event) => {
                    event.preventDefault();
                    selectOption(option);
                  }}
                  className={cn(
                    "flex min-h-10 cursor-pointer items-center gap-2 rounded px-2.5 py-2 text-sm transition-colors",
                    selected
                      ? "bg-info-bg/70 font-medium text-info"
                      : "text-foreground",
                    highlighted && "bg-background text-foreground",
                    selected && highlighted && "bg-info-bg text-info"
                  )}
                >
                  <Check
                    size={15}
                    className={cn("shrink-0 text-primary", selected ? "opacity-100" : "opacity-0")}
                    aria-hidden
                  />
                  <span className="min-w-0">
                    <span className="block truncate">{option.label}</span>
                    {option.description ? (
                      <span className="mt-0.5 block truncate text-xs font-normal text-muted">
                        {option.description}
                      </span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-muted">
          {helper}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function optionId(id: string, index: number) {
  return `${id}-option-${index}`;
}
