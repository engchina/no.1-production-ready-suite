import { ChevronDown } from "lucide-react";
import {
  type KeyboardEvent,
  type UIEvent,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { isImeComposing } from "../../lib/keyboard";
import { cn } from "../../lib/utils";

import { CONTROL_MIN_HEIGHT_CLASS, type ControlSize, type FieldWidth, fieldWidthClass } from "./control-size";
import { FieldError } from "./field-error";
import { useFloatingMenuPosition } from "./floating-menu";
import { DEFAULT_REQUIRED_LABEL, RequiredBadge } from "./required-badge";
import { SearchField } from "./search-field";
import {
  DEFAULT_SEARCHABLE_SELECT_LABELS,
  filterSearchableOptions,
  moveActiveIndex,
  SearchableOptionRow,
  searchableOptionId,
  type SearchableSelectLabels,
  type SearchableSelectOption,
  type SearchableSelectRemote,
} from "./searchable-options";
import { Spinner } from "./spinner";
import { isInsideAny, nearestScrollTop, selectPortalContainer } from "./select-field";

/** 一覧の下端からこの距離（px）まで来たら続きを読む。 */
const LOAD_MORE_THRESHOLD_PX = 48;

export interface SearchableSelectFieldProps {
  /** 開閉ボタンの id（外の要素と結ぶとき用）。 */
  id: string;
  /** 翻訳済みラベル。 */
  label: string;
  /** 選択中の値。 */
  value: string;
  /** 候補（画面側で絞り込むときは全件。サーバー側で検索するときは返った分）。 */
  options: readonly SearchableSelectOption[];
  onValueChange: (value: string) => void;
  /**
   * 選択中の候補（任意）。サーバー側で検索していて `options` に選択中が無いときも、ボタンに名前を出すために渡す。
   */
  selectedOption?: SearchableSelectOption | null;
  /** 検索語が確定したとき（SearchField と同じ: 300ms・Enter・消去。IME の変換中は呼ばない）。 */
  onQueryChange?: (query: string) => void;
  /** サーバー側で検索するとき（#578）。渡さなければ `options` を画面側で絞り込む。 */
  remote?: SearchableSelectRemote;
  helper?: string;
  error?: string;
  required?: boolean;
  requiredLabel?: string;
  /** 何も選んでいないときのボタンの文言。 */
  placeholder?: string;
  disabled?: boolean;
  labels?: Partial<SearchableSelectLabels>;
  /** 高さの最小（既定 md = 36px）。選択中の名前は折り返すので、長い名前では高くなる。同じ行の Button と同じ size にする（#613）。 */
  size?: ControlSize;
  /** 幅（候補の名前の長さで選ぶ。既定は親の幅いっぱい）。sm（640px）未満は全幅（#613）。 */
  width?: FieldWidth;
  className?: string;
  buttonClassName?: string;
}

/**
 * 検索できる単一選択（#578）。ボタン（選択中の名前を切らずに出す）を押すと、検索欄と候補の一覧を重ねて開く。
 * 選択肢が数十〜数百件の選択に使う（十数件までの固定の選択肢は SelectField）。
 *
 * - 開くと検索欄へフォーカスが移る。検索欄は SearchField（debounce 300ms・Enter はすぐ・IME の変換中は絞り込まない）。
 * - ↑↓ / PageUp / PageDown で候補を強調（aria-activedescendant）、Enter で選んで閉じ、ボタンへ戻る。
 *   Esc・Tab・外側のクリックで閉じる（Esc と Tab はボタンへ戻る）。IME の変換中の Enter・矢印は候補の操作に使わない。
 * - 候補が多いとき（数百件）は `remote` でサーバー側の検索に切り替え、一覧の下端までスクロールするか
 *   最後の候補から ↓ で続きを読む。
 */
export function SearchableSelectField({
  id,
  label,
  value,
  options,
  onValueChange,
  selectedOption,
  onQueryChange,
  remote,
  helper,
  error,
  required,
  requiredLabel = DEFAULT_REQUIRED_LABEL,
  placeholder = "",
  disabled = false,
  labels: labelOverrides,
  size = "md",
  width,
  className,
  buttonClassName,
}: SearchableSelectFieldProps) {
  const labels = { ...DEFAULT_SEARCHABLE_SELECT_LABELS, ...labelOverrides };
  const reactId = useId();
  const labelId = `${id}-${reactId}-label`;
  const valueId = `${id}-${reactId}-value`;
  const hintId = `${id}-${reactId}-hint`;
  const errorId = `${id}-${reactId}-error`;
  const popoverId = `${id}-${reactId}-popover`;
  const listboxId = `${id}-${reactId}-listbox`;
  const searchId = `${id}-${reactId}-search`;
  const rootRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const listboxRef = useRef<HTMLUListElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const revealActiveRef = useRef(false);
  // 閉じた後に、外れた検索欄が待っていた検索語を確定しても（SearchField の unmount）受け取らない。
  const openRef = useRef(false);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const [portalContainer, setPortalContainer] = useState<HTMLElement | null>(null);
  const [minWidth, setMinWidth] = useState(0);
  const position = useFloatingMenuPosition({
    align: "start",
    boundary: "viewport",
    menuRef: popoverRef,
    open,
    triggerRef: buttonRef,
  });

  const visible = useMemo(
    () => (remote ? options : filterSearchableOptions(options, query)),
    [options, query, remote]
  );
  const selectedIndex = visible.findIndex((option) => option.value === value);
  const active =
    visible.length === 0
      ? -1
      : activeIndex >= 0
        ? Math.min(activeIndex, visible.length - 1)
        : Math.max(0, selectedIndex);
  const current = options.find((option) => option.value === value) ?? selectedOption ?? null;
  const searching = Boolean(remote?.searching);
  const countText = remote
    ? labels.count(options.length, remote.total)
    : labels.count(visible.length, options.length);
  const describedBy = [helper ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined;

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!isInsideAny(event.target as Node | null, [rootRef.current, popoverRef.current])) close(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // 開いたら検索欄へ（位置が決まってから。決まる前は画面外にあり、フォーカスでスクロールさせない）。
  const positioned = Boolean(position);
  useEffect(() => {
    if (open && positioned) inputRef.current?.focus({ preventScroll: true });
  }, [open, positioned]);

  const popoverTop = position?.style.top;
  useLayoutEffect(() => {
    const listbox = listboxRef.current;
    if (!open || !listbox || active < 0 || !revealActiveRef.current) return;
    const item = listbox.children.item(active);
    if (!(item instanceof HTMLElement)) return;
    listbox.scrollTop = nearestScrollTop({
      scrollTop: listbox.scrollTop,
      viewportHeight: listbox.clientHeight,
      itemTop: item.offsetTop,
      itemHeight: item.offsetHeight,
    });
  }, [open, active, popoverTop]);

  function openPopover() {
    if (disabled) return;
    revealActiveRef.current = true;
    setActiveIndex(-1);
    setPortalContainer(selectPortalContainer(buttonRef.current));
    setMinWidth(Math.round(buttonRef.current?.getBoundingClientRect().width ?? 0));
    openRef.current = true;
    setOpen(true);
  }

  function close(returnFocus: boolean) {
    openRef.current = false;
    setOpen(false);
    setActiveIndex(-1);
    if (query) {
      setQuery("");
      onQueryChange?.("");
    }
    if (returnFocus) buttonRef.current?.focus({ preventScroll: true });
  }

  function choose(option: SearchableSelectOption) {
    onValueChange(option.value);
    close(true);
  }

  function loadMoreIfNeeded() {
    if (remote?.hasMore && !remote.loadingMore) remote.onLoadMore();
  }

  function moveActive(delta: number) {
    revealActiveRef.current = true;
    const next = moveActiveIndex(active, delta, visible.length);
    setActiveIndex(next);
    if (delta > 0 && active === visible.length - 1) loadMoreIfNeeded();
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Tab") {
      // 開いた選択はボタンに付属する一時的な層。Tab では閉じてボタンへ戻り、そこから次へ進む
      // （Shift+Tab はボタンに止まる）。
      if (event.shiftKey) event.preventDefault();
      close(true);
      return;
    }
    // IME の変換中の Enter・矢印は変換の確定・候補の移動なので、一覧の操作に使わない（#535）。
    if (isImeComposing(event)) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        moveActive(1);
        return;
      case "ArrowUp":
        event.preventDefault();
        moveActive(-1);
        return;
      case "PageDown":
        event.preventDefault();
        moveActive(10);
        return;
      case "PageUp":
        event.preventDefault();
        moveActive(-10);
        return;
      case "Enter": {
        const option = active >= 0 && !searching ? visible[active] : undefined;
        if (!option) return; // SearchField が検索語をすぐ確定する
        event.preventDefault();
        choose(option);
        return;
      }
      case "Escape":
        event.preventDefault();
        // 開いた選択だけを閉じる。囲むモーダルの Esc まで届かせない。
        event.stopPropagation();
        close(true);
        return;
      default:
        return;
    }
  }

  function handleButtonKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openPopover();
    }
  }

  function handleListScroll(event: UIEvent<HTMLUListElement>) {
    const list = event.currentTarget;
    if (list.scrollHeight - list.scrollTop - list.clientHeight <= LOAD_MORE_THRESHOLD_PX) loadMoreIfNeeded();
  }

  const popover =
    open && portalContainer
      ? createPortal(
          <div
            ref={popoverRef}
            id={popoverId}
            role="dialog"
            aria-label={label}
            data-floating-menu-placement={position?.placement}
            data-searchable-select-popover=""
            className={cn(
              // 候補の一覧は自分の高さの上限（bounded-scroll-area）の中でスクロールする。画面の空きが足りないときだけ
              // 全体を縮めてスクロールにする（高さは中身の実寸で測るので、全体は overflow-y-auto にしておく）。
              "fixed z-[var(--z-dropdown)] w-[min(24rem,calc(100vw-2rem))] overflow-y-auto overscroll-contain rounded-md border border-border bg-surface-raised shadow-[var(--shadow-popover)]",
              !position && "opacity-0"
            )}
            style={{ minWidth: minWidth || undefined, ...(position?.style ?? { left: -9999, top: -9999 }) }}
            // Portal でも React のイベントは祖先（表の行など）へ伝わるため、ここで止める。
            onClick={(event) => event.stopPropagation()}
          >
            <div className="border-b border-border p-2">
              <SearchField
                ref={inputRef}
                id={searchId}
                label={labels.searchLabel(label)}
                labelHidden
                value={query}
                onSearch={(next) => {
                  if (!openRef.current) return;
                  setQuery(next);
                  setActiveIndex(-1);
                  onQueryChange?.(next);
                }}
                clearLabel={labels.clearSearch}
                resultCountLabel={searching ? labels.searching : countText}
                placeholder={labels.searchPlaceholder}
                role="combobox"
                aria-expanded
                aria-controls={listboxId}
                aria-autocomplete="list"
                aria-activedescendant={active >= 0 ? searchableOptionId(listboxId, active) : undefined}
                onKeyDown={handleSearchKeyDown}
              />
            </div>
            {visible.length > 0 ? (
              <ul
                ref={listboxRef}
                id={listboxId}
                role="listbox"
                aria-label={label}
                aria-busy={searching || undefined}
                onScroll={handleListScroll}
                className="bounded-scroll-area overscroll-contain py-1"
              >
                {visible.map((option, index) => (
                  <SearchableOptionRow
                    key={option.value}
                    id={searchableOptionId(listboxId, index)}
                    option={option}
                    selected={option.value === value}
                    active={index === active}
                    multiple={false}
                    onSelect={() => choose(option)}
                    onHover={() => {
                      revealActiveRef.current = false;
                      setActiveIndex(index);
                    }}
                  />
                ))}
              </ul>
            ) : (
              <p className="px-3 py-6 text-center text-xs text-fg-muted">
                {searching ? labels.searching : query ? labels.noMatch(query) : labels.empty}
              </p>
            )}
            <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-1.5 text-xs text-fg-muted">
              <span className="tnum">{searching ? labels.searching : countText}</span>
              {remote?.loadingMore ? (
                <span className="inline-flex items-center gap-1.5">
                  <Spinner size={14} aria-hidden />
                  {labels.loadMore}
                </span>
              ) : null}
            </div>
          </div>,
          portalContainer
        )
      : null;

  return (
    <div ref={rootRef} className={cn("space-y-1.5", fieldWidthClass(width), className)}>
      <label id={labelId} htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-fg">
        {label}
        {required && requiredLabel ? <RequiredBadge label={requiredLabel} aria-hidden /> : null}
      </label>
      <button
        ref={buttonRef}
        id={id}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        aria-labelledby={`${labelId} ${valueId}`}
        aria-describedby={describedBy}
        aria-invalid={Boolean(error) || undefined}
        aria-required={required || undefined}
        disabled={disabled}
        onClick={() => (open ? close(false) : openPopover())}
        onKeyDown={handleButtonKeyDown}
        className={cn(
          CONTROL_MIN_HEIGHT_CLASS[size],
          "flex w-full cursor-pointer items-center justify-between gap-3 rounded-control border bg-surface px-3 py-1.5 text-left text-sm text-fg outline-none transition-colors",
          "hover:bg-surface-hover forced-colors:border-[CanvasText] focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring",
          "disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled",
          error ? "border-danger-fg" : "border-border-control",
          buttonClassName
        )}
      >
        {/* 選択中の名前は切らずに折り返す（長い名前も全体を読める）。 */}
        <span id={valueId} className={cn("min-w-0 [overflow-wrap:anywhere]", !current && "text-fg-muted")}>
          {current?.label ?? (value || placeholder)}
        </span>
        <ChevronDown
          size={16}
          aria-hidden
          className={cn("shrink-0 text-fg-muted transition-transform duration-150", open && "rotate-180 text-accent-fg")}
        />
      </button>
      {popover}
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
    </div>
  );
}
