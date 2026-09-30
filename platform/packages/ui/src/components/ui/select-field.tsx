import { Check, ChevronDown } from "lucide-react";
import {
  type KeyboardEvent,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { cn } from "../../lib/utils";

import { CONTROL_HEIGHT_CLASS, type ControlSize, type FieldWidth, fieldWidthClass } from "./control-size";
import { FieldError } from "./field-error";
import { useFloatingMenuPosition } from "./floating-menu";
import { DEFAULT_REQUIRED_LABEL, RequiredBadge } from "./required-badge";

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
  /** 必須であることを aria-required と中立色の RequiredBadge「必須」で伝える（検証はアプリ側）。 */
  required?: boolean;
  /** 必須バッジの文言。既定「必須」。条件付きの必須だけ上書きする。 */
  requiredLabel?: string;
  placeholder?: string;
  /**
   * 無効にする（#631）。ボタンはネイティブの disabled（Tab で止まらない・押せない・typeahead も効かない）で、
   * 地と文字は TextField の disabled と同じ --color-surface-disabled / --color-fg-disabled。開いているときに無効になったら閉じる。
   */
  disabled?: boolean;
  /**
   * ラベルを画面に出さず読み上げだけにする（`sr-only`）。表の行・一覧のツールバーのように、周り（列の見出し・隣の文言）で
   * 目的が分かる場所だけに使う。フォームの欄では使わない（TextField の labelHidden と同じ）。
   */
  labelHidden?: boolean;
  /** ボタン（combobox）に付ける `data-testid`（e2e 用）。 */
  "data-testid"?: string;
  /** 高さ（既定 md = 36px）。同じ行に並べる Button・入力欄と同じ size にする（#613）。 */
  size?: ControlSize;
  /**
   * 幅（選択肢のラベルの長さで選ぶ。既定は親の幅いっぱい）。sm（640px）未満は全幅。
   * フォームの grid のセルに置くときは指定しない。grid の外に単独で置く選択欄は必ず指定する（#613）。
   */
  width?: FieldWidth;
  className?: string;
  buttonClassName?: string;
}

/** 入力が途切れてからこの時間で typeahead の入力をリセットする（W3C APG の select-only combobox と同じ 500ms）。 */
export const SELECT_TYPEAHEAD_RESET_MS = 500;
/** 空きが十分でも超えない一覧の高さ（rem）。`max-h-64` と同じ。 */
const LISTBOX_MAX_HEIGHT_REM = 16;
/** PageUp / PageDown で動かす件数。 */
const PAGE_STEP = 10;

function normalizeForTypeahead(text: string) {
  return text.normalize("NFKC").trimStart().toLocaleLowerCase();
}

/**
 * typeahead で強調する選択肢の位置。W3C APG の select-only combobox に合わせる。
 * - `startIndex` から末尾、先頭から `startIndex` の手前の順に、`query` で始まる最初の選択肢を返す
 * - 同じ文字の繰り返し（"aaa"）は、その文字で始まる選択肢を順に巡る
 * - 見つからなければ -1
 */
export function findTypeaheadIndex(labels: readonly string[], query: string, startIndex: number) {
  const count = labels.length;
  const normalizedQuery = normalizeForTypeahead(query);
  if (count === 0 || !normalizedQuery) return -1;
  const start = ((startIndex % count) + count) % count;
  const ordered = Array.from({ length: count }, (_, offset) => (start + offset) % count);
  const normalizedLabels = labels.map(normalizeForTypeahead);
  const match = ordered.find((index) => normalizedLabels[index].startsWith(normalizedQuery));
  if (match !== undefined) return match;
  const chars = Array.from(normalizedQuery);
  if (chars.every((char) => char === chars[0])) {
    return ordered.find((index) => normalizedLabels[index].startsWith(chars[0])) ?? -1;
  }
  return -1;
}

/**
 * typeahead で探し始める位置。1 文字目と同じ文字の繰り返しは次の選択肢から（同じ文字で巡る）、
 * 続けて入力した文字は今の選択肢から（前方一致が続くあいだは動かさない）。
 */
export function typeaheadStartIndex(query: string, activeIndex: number) {
  if (activeIndex < 0) return 0;
  const chars = Array.from(query);
  return chars.every((char) => char === chars[0]) ? activeIndex + 1 : activeIndex;
}

/**
 * 強調中の選択肢を一覧の中で見せるための scrollTop（`scrollIntoView({ block: "nearest" })` と同じ動き）。
 * `scrollIntoView` は一覧の外（ページ本体）までスクロールさせるため、一覧の scrollTop だけを動かす。
 */
export function nearestScrollTop({
  scrollTop,
  viewportHeight,
  itemTop,
  itemHeight,
  padding = 0,
}: {
  scrollTop: number;
  viewportHeight: number;
  itemTop: number;
  itemHeight: number;
  padding?: number;
}) {
  const top = itemTop - padding;
  const bottom = itemTop + itemHeight + padding;
  if (top < scrollTop) return Math.max(0, top);
  if (bottom > scrollTop + viewportHeight) return bottom - viewportHeight;
  return scrollTop;
}

/** 外側クリックの判定。フィールド本体と、Portal で body に出した一覧の両方を内側とみなす。 */
export function isInsideAny(
  target: Node | null,
  containers: readonly (Pick<Node, "contains"> | null | undefined)[]
) {
  if (!target) return false;
  return containers.some((container) => container?.contains(target) ?? false);
}

/**
 * 一覧を描く先。モーダル（`aria-modal="true"` / `<dialog>`）の中なら、そのモーダルの中に描く。
 * モーダルの外に出すと、支援技術がモーダルの外として読まない（aria-modal）・top layer の下に隠れる（`<dialog>`）ため。
 */
export function selectPortalContainer(trigger: Pick<Element, "closest"> | null): HTMLElement | null {
  const modal = trigger?.closest<HTMLElement>('[aria-modal="true"], dialog[open]');
  if (modal) return modal;
  return typeof document === "undefined" ? null : document.body;
}

/**
 * 見た目と操作を統一した選択フィールド（W3C APG の select-only combobox）。
 * 一覧は body へ Portal で描き、親の overflow に切られない。画面の下端では上に反転する。
 */
export function SelectField<T extends string>({
  id,
  label,
  value,
  options,
  onValueChange,
  helper,
  error,
  required,
  requiredLabel = DEFAULT_REQUIRED_LABEL,
  placeholder = "",
  disabled = false,
  labelHidden = false,
  "data-testid": testId,
  size = "md",
  width,
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
  const listboxRef = useRef<HTMLUListElement | null>(null);
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const [portalContainer, setPortalContainer] = useState<HTMLElement | null>(null);
  // キーボード・typeahead・開いた直後だけ強調中の選択肢へスクロールする（マウスの移動では動かさない）。
  const revealHighlightRef = useRef(false);
  const typeaheadRef = useRef<{ query: string; timer: ReturnType<typeof setTimeout> | undefined }>({
    query: "",
    timer: undefined,
  });
  const position = useFloatingMenuPosition({
    align: "stretch",
    boundary: "viewport",
    maxHeightRem: LISTBOX_MAX_HEIGHT_REM,
    menuRef: listboxRef,
    open,
    triggerRef: buttonRef,
  });

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
      if (!isInsideAny(event.target as Node | null, [rootRef.current, listboxRef.current])) {
        closeList();
      }
    };

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  // 開いているあいだに無効になったら閉じる（無効な欄の一覧を残さない）。
  useEffect(() => {
    if (disabled && open) closeList();
  }, [disabled, open]);

  useEffect(() => {
    const typeahead = typeaheadRef.current;
    return () => clearTimeout(typeahead.timer);
  }, []);

  // 強調中の選択肢を一覧の表示範囲に入れる。位置計算で一覧の高さが変わったときもやり直す。
  const listboxMaxHeight = position?.style.maxHeight;
  useLayoutEffect(() => {
    const listbox = listboxRef.current;
    if (!open || !listbox || activeIndex < 0 || !revealHighlightRef.current) return;
    const item = listbox.children.item(activeIndex);
    if (!(item instanceof HTMLElement)) return;
    listbox.scrollTop = nearestScrollTop({
      scrollTop: listbox.scrollTop,
      viewportHeight: listbox.clientHeight,
      itemTop: item.offsetTop,
      itemHeight: item.offsetHeight,
      padding: Number.parseFloat(window.getComputedStyle(listbox).paddingTop) || 0,
    });
  }, [open, activeIndex, listboxMaxHeight]);

  function highlight(index: number) {
    revealHighlightRef.current = true;
    setHighlightedIndex(index);
  }

  function openList(nextIndex = selectedIndex >= 0 ? selectedIndex : 0) {
    if (disabled || options.length === 0) return;
    highlight(nextIndex);
    setPortalContainer(selectPortalContainer(buttonRef.current));
    setOpen(true);
  }

  function closeList() {
    setOpen(false);
    setHighlightedIndex(-1);
    resetTypeahead();
  }

  function resetTypeahead() {
    clearTimeout(typeaheadRef.current.timer);
    typeaheadRef.current = { query: "", timer: undefined };
  }

  function typeahead(char: string) {
    const typeaheadState = typeaheadRef.current;
    clearTimeout(typeaheadState.timer);
    const query = typeaheadState.query + char;
    const match = findTypeaheadIndex(
      options.map((option) => option.label),
      query,
      typeaheadStartIndex(query, activeIndex)
    );
    typeaheadRef.current = {
      query: match >= 0 ? query : "",
      timer: setTimeout(resetTypeahead, SELECT_TYPEAHEAD_RESET_MS),
    };
    if (!open) {
      openList(match >= 0 ? match : undefined);
    } else if (match >= 0) {
      highlight(match);
    }
  }

  function selectOption(option: SelectFieldOption<T>) {
    onValueChange(option.value);
    closeList();
    window.requestAnimationFrame(() => buttonRef.current?.focus({ preventScroll: true }));
  }

  function moveHighlight(delta: number) {
    if (options.length === 0) return;
    const base = activeIndex >= 0 ? activeIndex : 0;
    const next = (base + delta + options.length) % options.length;
    highlight(next);
  }

  function moveHighlightBy(delta: number) {
    if (options.length === 0) return;
    const base = activeIndex >= 0 ? activeIndex : 0;
    highlight(Math.min(options.length - 1, Math.max(0, base + delta)));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    // 無効なボタンにはフォーカスが来ないが、プログラムで focus された場合も開かず typeahead もしない。
    if (disabled) return;
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
          highlight(0);
        }
        break;
      case "End":
        if (open) {
          event.preventDefault();
          highlight(options.length - 1);
        }
        break;
      case "PageUp":
        if (open) {
          event.preventDefault();
          moveHighlightBy(-PAGE_STEP);
        }
        break;
      case "PageDown":
        if (open) {
          event.preventDefault();
          moveHighlightBy(PAGE_STEP);
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
          // 一覧だけを閉じる。囲むモーダルの Esc（document の keydown）まで届かせない。
          event.stopPropagation();
          closeList();
        }
        break;
      case "Tab":
        closeList();
        break;
      default:
        if (
          event.key.length === 1 &&
          event.key !== " " &&
          !event.altKey &&
          !event.ctrlKey &&
          !event.metaKey
        ) {
          event.preventDefault();
          typeahead(event.key);
        }
        break;
    }
  }

  // 一覧は body（モーダルの中ならそのモーダル）へ Portal で描く。親の overflow に切られず、
  // z-index を持つ層の中では floating-menu が z-index をその層より 1 段上げる。
  const listbox =
    open && portalContainer
      ? createPortal(
          <ul
            ref={listboxRef}
            id={listboxId}
            role="listbox"
            aria-labelledby={labelId}
            data-floating-menu-placement={position?.placement}
            className={cn(
              "fixed z-[var(--z-dropdown)] max-h-64 overflow-auto overscroll-contain rounded-md border border-border bg-surface-raised p-1 shadow-[var(--shadow-popover)]",
              !position && "opacity-0"
            )}
            style={position?.style ?? { left: -9999, top: -9999 }}
            // 一覧を押してもフォーカスはボタンに残す（aria-activedescendant で強調を伝える）。
            onMouseDown={(event) => event.preventDefault()}
            // Portal でも React のイベントは祖先（表の行など）へ伝わるため、選択のクリックはここで止める。
            onClick={(event) => event.stopPropagation()}
          >
            {options.map((option, index) => {
              const selected = option.value === value;
              const highlighted = index === activeIndex;
              return (
                <li
                  key={option.value}
                  id={optionId(id, index)}
                  role="option"
                  // e2e が値で選択肢を選べるように、値を data 属性に出す（表示には使わない）。
                  data-value={option.value}
                  aria-selected={selected}
                  onMouseEnter={() => {
                    revealHighlightRef.current = false;
                    setHighlightedIndex(index);
                  }}
                  // pointerdown で選ぶとタッチで一覧をスクロールしただけで選ばれるため、click で選ぶ。
                  onClick={() => selectOption(option)}
                  className={cn(
                    "flex min-h-10 cursor-pointer items-center gap-2 rounded px-2.5 py-2 text-sm transition-colors",
                    selected
                      ? "bg-accent-muted font-medium text-accent-fg-strong"
                      : "text-fg",
                    highlighted && "bg-accent-subtle text-fg",
                    selected && highlighted && "bg-accent-muted text-accent-fg-strong"
                  )}
                >
                  <Check
                    size={16}
                    className={cn("shrink-0 text-accent-fg-strong", selected ? "opacity-100" : "opacity-0")}
                    aria-hidden
                  />
                  <span className="min-w-0">
                    <span className="block truncate">{option.label}</span>
                    {option.description ? (
                      <span className="mt-0.5 block truncate text-xs font-normal text-fg-muted">
                        {option.description}
                      </span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>,
          portalContainer
        )
      : null;

  return (
    <div ref={rootRef} className={cn("space-y-1.5", fieldWidthClass(width), className)}>
      <label
        id={labelId}
        htmlFor={id}
        className={cn("flex items-center gap-2 text-sm font-medium text-fg", labelHidden && "sr-only")}
      >
        {label}
        {required && requiredLabel ? (
          <RequiredBadge label={requiredLabel} aria-hidden />
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
          aria-required={required || undefined}
          aria-labelledby={labelId}
          aria-describedby={describedBy}
          aria-activedescendant={open && activeIndex >= 0 ? optionId(id, activeIndex) : undefined}
          disabled={disabled}
          data-testid={testId}
          // e2e が選択中の値を確かめられるように、値を data 属性に出す（表示には使わない）。
          data-value={value}
          onClick={() => (open ? closeList() : openList())}
          onKeyDown={handleKeyDown}
          className={cn(
            CONTROL_HEIGHT_CLASS[size],
            "flex w-full cursor-pointer items-center justify-between gap-3 rounded-control border bg-surface px-3 text-left text-sm text-fg outline-none transition-colors",
            "enabled:hover:bg-surface-hover forced-colors:border-[CanvasText] focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring",
            // TextField・SearchableSelectField の disabled と同じ地・文字（枠線は残して欄の形を見せる）。
            "disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled",
            error ? "border-danger-fg" : "border-border-control",
            buttonClassName
          )}
        >
          <span className={cn("min-w-0 truncate", !selectedOption && !value && !disabled && "text-fg-muted")}>
            {selectedOption?.label ?? (value || placeholder)}
          </span>
          <ChevronDown
            size={16}
            className={cn(
              "shrink-0 transition-transform duration-150",
              disabled ? "text-fg-disabled" : "text-fg-muted",
              open && "rotate-180 text-accent-fg"
            )}
            aria-hidden
          />
        </button>

        {listbox}
      </div>
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
    </div>
  );
}

function optionId(id: string, index: number) {
  return `${id}-option-${index}`;
}
