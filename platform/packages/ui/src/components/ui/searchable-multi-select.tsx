import { Check, ChevronDown, ChevronsDown, ChevronUp, X } from "lucide-react";
import {
  type FocusEvent,
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { isImeComposing } from "../../lib/keyboard";
import { cn } from "../../lib/utils";

import { Button } from "./button";
import { FieldLabel } from "./field-label";
import { SearchField } from "./search-field";
import {
  DEFAULT_SEARCHABLE_SELECT_LABELS,
  filterSearchableOptions,
  moveActiveIndex,
  SearchableOptionRow,
  searchableOptionId,
  type SearchableMultiSelectLabels,
  type SearchableSelectOption,
  type SearchableSelectRemote,
} from "./searchable-options";
import { nearestScrollTop } from "./select-field";

export interface SearchableMultiSelectProps {
  /** 検索欄（role=combobox）の id。 */
  id: string;
  /** 翻訳済みラベル。検索欄の名前・候補の一覧の名前・選択済みの一覧の名前に使う。 */
  label: string;
  /** ラベルを画面に出さず読み上げだけにする（見出しなどで目的が分かる場所だけ）。 */
  labelHidden?: boolean;
  /**
   * 画面に出している見出し（カードの題名の FieldLabel など）の id。`labelHidden` と一緒に渡すと、
   * 検索欄の名前をその見出しにする（同じ名前を二重に読み上げない）。
   */
  labelledBy?: string;
  /** 翻訳済みの補足（任意）。 */
  helper?: ReactNode;
  /** 翻訳済みのエラー（任意）。 */
  error?: string;
  /** 1 件以上の選択が必須。ラベルに「必須」、検索欄に aria-required（検証はアプリ側）。 */
  required?: boolean;
  requiredLabel?: string;
  /** 外に出しているエラー・補足と結ぶ（検索欄の aria-describedby に足す）。 */
  describedBy?: string;
  /** 外に出しているエラー中（検索欄を aria-invalid にする）。`error` を渡すときは不要。 */
  invalid?: boolean;
  /** 候補（画面側で絞り込むときは全件。サーバー側で検索するときは返った分）。 */
  options: readonly SearchableSelectOption[];
  /** 選択中の値（選んだ順）。 */
  value: readonly string[];
  onValueChange: (value: string[]) => void;
  /**
   * chip に出す選択済みの候補（任意）。`options`（検索結果のページ）に無い選択済みも名前と状態で出すために渡す。
   * どちらにも無い値は値そのものを名前として出す（外せるように）。
   */
  selectedOptions?: readonly SearchableSelectOption[];
  /** 検索語が確定したとき（SearchField と同じ: 300ms・Enter・消去。IME の変換中は呼ばない）。 */
  onQueryChange?: (query: string) => void;
  /** サーバー側で検索するとき（#578）。渡さなければ `options` を画面側で絞り込む。 */
  remote?: SearchableSelectRemote;
  disabled?: boolean;
  labels?: Partial<SearchableMultiSelectLabels>;
  className?: string;
}

/**
 * 検索できる複数選択（#578）。「検索欄（combobox）＋ 候補の一覧（listbox、開いている間だけ）＋ 選択済みの chip」。
 *
 * - 検索欄は SearchField（debounce 300ms・Enter はすぐ・IME の変換中は絞り込まない・消去）。
 * - ↑↓ で候補を強調（aria-activedescendant）、Enter で選択を切り替える（IME の変換中の Enter・矢印は候補の操作に使わない）。
 *   Esc で一覧を閉じ、もう一度の Esc で検索語を消す。フォーカスが部品の外へ出たら閉じる。
 * - 選んでも一覧は閉じない（続けて選べる）。「完了」で閉じて検索欄へ戻る。
 * - 候補が多いとき（数百件）は `remote` でサーバー側の検索に切り替え、「さらに表示」で続きを読む。
 * - 選択済みは chip で、名前を切らずに出す。chip の「×」で外す。
 */
export function SearchableMultiSelect({
  id,
  label,
  labelHidden = false,
  labelledBy,
  helper,
  error,
  required = false,
  requiredLabel,
  describedBy,
  invalid = false,
  options,
  value,
  onValueChange,
  selectedOptions,
  onQueryChange,
  remote,
  disabled = false,
  labels: labelOverrides,
  className,
}: SearchableMultiSelectProps) {
  const labels = { ...DEFAULT_SEARCHABLE_SELECT_LABELS, ...labelOverrides };
  const reactId = useId();
  const listboxId = `${id}-${reactId}-listbox`;
  const labelId = `${id}-${reactId}-label`;
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listboxRef = useRef<HTMLUListElement | null>(null);
  // 閉じて検索欄へフォーカスを戻すとき、検索欄の onFocus で開き直さないための印。
  const suppressOpenRef = useRef(false);
  // キーボードで動かしたときだけ強調中の候補へスクロールする（マウスの移動では動かさない）。
  const revealActiveRef = useRef(false);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [hideHideable, setHideHideable] = useState(true);
  const [activeIndex, setActiveIndex] = useState(0);

  const selected = useMemo(() => new Set(value), [value]);
  const matched = useMemo(
    () => (remote ? options : filterSearchableOptions(options, query)),
    [options, query, remote]
  );
  const canHide =
    Boolean(labels.hideHideable) &&
    !remote &&
    options.some((option) => option.hideable) &&
    options.some((option) => !option.hideable);
  const hiding = canHide && hideHideable;
  const visible = useMemo(
    () => (hiding ? matched.filter((option) => !option.hideable || selected.has(option.value)) : matched),
    [hiding, matched, selected]
  );
  const hiddenCount = hiding ? matched.length - visible.length : 0;
  // 候補が減ったときも強調を範囲に収める（state を同期させず、描画のたびに求める）。
  const active = visible.length === 0 ? -1 : Math.min(activeIndex, visible.length - 1);

  const chips = value.map(
    (current) =>
      selectedOptions?.find((option) => option.value === current) ??
      options.find((option) => option.value === current) ?? { value: current, label: current }
  );

  const countText = remote
    ? labels.count(options.length, remote.total)
    : labels.count(visible.length, options.length);
  const searching = Boolean(remote?.searching);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

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
  }, [open, active]);

  function openList() {
    if (disabled) return;
    setOpen(true);
  }

  /** 一覧を閉じ、検索欄へフォーカスを戻す（完了・Esc・開閉ボタン）。 */
  function closeAndFocusInput() {
    setOpen(false);
    suppressOpenRef.current = true;
    try {
      inputRef.current?.focus();
    } finally {
      suppressOpenRef.current = false;
    }
  }

  function toggle(optionValue: string) {
    if (disabled) return;
    onValueChange(
      selected.has(optionValue)
        ? value.filter((current) => current !== optionValue)
        : [...value, optionValue]
    );
  }

  function selectAllVisible() {
    const next = [...value];
    for (const option of visible) if (!selected.has(option.value)) next.push(option.value);
    onValueChange(next);
  }

  function moveActive(delta: number) {
    revealActiveRef.current = true;
    const next = moveActiveIndex(active, delta, visible.length);
    setActiveIndex(next < 0 ? 0 : next);
    // 最後の候補から ↓ で、続きのページを読む（キーボードでも「さらに表示」まで行かずに続けられる）。
    if (delta > 0 && active === visible.length - 1 && remote?.hasMore && !remote.loadingMore) {
      remote.onLoadMore();
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // IME の変換中の Enter・矢印は変換の確定・候補の移動なので、一覧の操作に使わない（#535）。
    if (disabled || isImeComposing(event)) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) {
          openList();
          return;
        }
        moveActive(1);
        return;
      case "ArrowUp":
        event.preventDefault();
        if (open) moveActive(-1);
        return;
      case "Enter": {
        if (!open || active < 0 || searching) return;
        const option = visible[active];
        if (!option) return;
        // SearchField の Enter（検索語の確定）とフォームの送信をさせない。
        event.preventDefault();
        toggle(option.value);
        return;
      }
      case "Escape":
        if (!open) return; // 閉じているときは SearchField が検索語を消す
        event.preventDefault();
        // 囲むダイアログまで Esc を伝えない（一覧だけを閉じる）。
        event.stopPropagation();
        setOpen(false);
        return;
      default:
        return;
    }
  }

  // 一覧の中の操作（さらに表示・完了など）にフォーカスがあるときも Esc で閉じて検索欄へ戻る。
  function handleRootKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Escape" || !open || event.defaultPrevented) return;
    if (event.target === inputRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    closeAndFocusInput();
  }

  // Tab / Shift+Tab でフォーカスが部品の外へ移ったら閉じる。移り先が無い（ウィンドウの切り替え）ときは閉じない。
  function handleRootBlur(event: FocusEvent<HTMLDivElement>) {
    if (!open) return;
    const next = event.relatedTarget;
    if (next instanceof Node && !rootRef.current?.contains(next)) setOpen(false);
  }

  const toggleButton = (
    <Button
      type="button"
      variant="ghost"
      iconOnly
      icon={open ? ChevronUp : ChevronDown}
      aria-label={labels.toggleList}
      aria-expanded={open}
      aria-controls={listboxId}
      disabled={disabled}
      // 押しても検索欄からフォーカスを外さない。
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        if (open) {
          closeAndFocusInput();
        } else {
          setOpen(true);
          inputRef.current?.focus();
        }
      }}
      className={cn(
        "h-full min-h-0 rounded-none",
        !draft && "rounded-r-[calc(var(--radius-control)-1px)]",
        "forced-colors:border-y-[Canvas] forced-colors:border-r-[Canvas]"
      )}
    />
  );

  return (
    <div
      ref={rootRef}
      className={cn("space-y-2", className)}
      onKeyDown={handleRootKeyDown}
      onBlur={handleRootBlur}
      data-searchable-multi-select=""
    >
      {/* 必須のタグを出すため、見えるラベルは FieldLabel で出す（SearchField は必須を持たない）。 */}
      {labelHidden ? null : (
        <FieldLabel
          id={labelId}
          htmlFor={id}
          label={label}
          required={required}
          requiredLabel={requiredLabel}
          className="block"
        />
      )}
      <SearchField
        ref={inputRef}
        id={id}
        label={label}
        labelHidden
        aria-labelledby={labelHidden ? labelledBy : labelId}
        aria-required={required || undefined}
        helper={helper}
        error={error}
        value={query}
        onSearch={(next) => {
          setQuery(next);
          setActiveIndex(0);
          onQueryChange?.(next);
        }}
        onDraftChange={(next) => {
          setDraft(next);
          if (next) openList();
        }}
        clearLabel={labels.clearSearch}
        resultCountLabel={searching ? labels.searching : countText}
        placeholder={labels.searchPlaceholder}
        disabled={disabled}
        trailing={toggleButton}
        role="combobox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? searchableOptionId(listboxId, active) : undefined}
        aria-describedby={describedBy}
        aria-invalid={Boolean(error) || invalid || undefined}
        onFocus={() => {
          if (!suppressOpenRef.current) openList();
        }}
        onKeyDown={handleKeyDown}
      />

      {open ? (
        <div
          className="overflow-hidden rounded-md border border-border bg-surface shadow-sm"
          data-searchable-panel=""
        >
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-border px-3 py-1.5">
            <span className="tnum text-xs text-fg-muted">{searching ? labels.searching : countText}</span>
            {canHide && labels.hideHideable ? (
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-fg-muted">
                <input
                  type="checkbox"
                  checked={hideHideable}
                  onChange={(event) => setHideHideable(event.target.checked)}
                  className="cursor-pointer accent-accent-emphasis"
                />
                {labels.hideHideable}
              </label>
            ) : null}
          </div>

          {visible.length > 0 ? (
            <ul
              ref={listboxRef}
              id={listboxId}
              role="listbox"
              aria-label={label}
              aria-multiselectable
              aria-busy={searching || undefined}
              className="bounded-scroll-area py-1"
            >
              {visible.map((option, index) => (
                <SearchableOptionRow
                  key={option.value}
                  id={searchableOptionId(listboxId, index)}
                  option={option}
                  selected={selected.has(option.value)}
                  active={index === active}
                  multiple
                  onSelect={() => {
                    toggle(option.value);
                    setActiveIndex(index);
                  }}
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

          {remote?.hasMore ? (
            <div className="border-t border-border px-3 py-1.5">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                icon={ChevronsDown}
                loading={remote.loadingMore}
                disabled={disabled}
                onClick={remote.onLoadMore}
                className="w-full"
              >
                {labels.loadMore}
              </Button>
            </div>
          ) : null}

          <div className="flex flex-col gap-1.5 border-t border-border px-3 py-2 sm:flex-row sm:items-center sm:justify-between sm:py-1.5">
            <span className="tnum text-xs text-fg-muted">
              {hiddenCount > 0 && labels.hiddenCount
                ? labels.hiddenCount(hiddenCount)
                : labels.selectedCount(value.length)}
            </span>
            <span className="flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-1.5 sm:w-auto sm:justify-start">
              <span className="flex flex-wrap items-center gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  // 検索語を変えている間は古い結果が出ているため、まとめて選ばせない。
                  disabled={disabled || visible.length === 0 || searching}
                  onClick={selectAllVisible}
                >
                  {labels.selectAllVisible}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={disabled || value.length === 0}
                  onClick={() => onValueChange([])}
                >
                  {labels.clear}
                </Button>
              </span>
              {/* 選択は選ぶたびに反映済み。「完了」は一覧を閉じて検索欄へ戻る、目に見える閉じ方。 */}
              <Button
                type="button"
                size="sm"
                variant="secondary"
                icon={Check}
                onClick={closeAndFocusInput}
                className="ml-auto"
              >
                {labels.done}
              </Button>
            </span>
          </div>
        </div>
      ) : null}

      {chips.length > 0 ? (
        <ul
          aria-label={labels.selectedList(label)}
          className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto [scrollbar-gutter:stable]"
          data-searchable-chips=""
        >
          {chips.map((option) => (
            <li
              key={option.value}
              className="inline-flex min-h-7 max-w-full items-center gap-1.5 rounded-md border border-accent-emphasis bg-info-subtle py-0.5 pl-2 pr-0.5 text-xs font-medium text-fg"
            >
              {/* 名前は切らずに折り返す（選択の完全な表示）。 */}
              <span className="min-w-0 [overflow-wrap:anywhere]">{option.label}</span>
              {option.badge ? (
                <span className="shrink-0 rounded-sm bg-surface-sunken px-1 py-0.5 text-xs font-medium text-accent-fg">
                  {option.badge}
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => toggle(option.value)}
                disabled={disabled}
                aria-label={labels.removeChip(option.label)}
                className="pr-touch-target relative flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-sm text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring"
              >
                <X size={14} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
