import { Check, ChevronDown, ChevronsDown, Plus, X } from "lucide-react";
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

import { CONTROL_HEIGHT_CLASS, type FieldWidth, fieldWidthClass } from "./control-size";
import { useFieldWidthScope } from "./field-width-scope";
import { Button } from "./button";
import { FieldError } from "./field-error";
import { DEFAULT_REQUIRED_LABEL, RequiredBadge } from "./required-badge";
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
import { SearchablePopover, useSearchablePopoverPosition } from "./searchable-popover";
import { isInsideAny, nearestScrollTop, selectPortalContainer } from "./select-field";
import { SelectListEmpty } from "./select-list-empty";

/** ボタンの要約に名前を出す件数。残りは「他 N 件」にする（#5267）。 */
export const SEARCHABLE_MULTI_SUMMARY_NAMES = 2;

/** popover の中のフォーカスできる要素（Tab で最後の要素から出るときに閉じるため）。 */
const FOCUSABLE_SELECTOR = 'input:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

export interface SearchableMultiSelectProps {
  /** 開閉ボタンの id（外のラベル・説明と結ぶとき用）。 */
  id: string;
  /** 翻訳済みラベル。ボタンの名前・候補の一覧の名前・選択済みの一覧の名前に使う。 */
  label: string;
  /** ラベルを画面に出さず読み上げだけにする（見出しなどで目的が分かる場所だけ）。 */
  labelHidden?: boolean;
  /**
   * 画面に出している見出し（カードの題名の FieldLabel など）の id。`labelHidden` と一緒に渡すと、
   * ボタンの名前をその見出しにする（同じ名前を二重に読み上げない）。
   */
  labelledBy?: string;
  /** 翻訳済みの補足（任意）。 */
  helper?: ReactNode;
  /** 翻訳済みのエラー（任意）。 */
  error?: string;
  /**
   * 1 件以上の選択が必須。ラベルに「必須」のタグを出す。ボタン（role=button）は aria-required を持てないので、
   * タグをラベルの一部として読み上げる（SearchableSelectField と同じ。検証はアプリ側）。
   */
  required?: boolean;
  requiredLabel?: string;
  /** 外に出しているエラー・補足と結ぶ（ボタンの aria-describedby に足す）。 */
  describedBy?: string;
  /** 外に出しているエラー中（ボタンを aria-invalid にする）。`error` を渡すときは不要。 */
  invalid?: boolean;
  /** 候補（画面側で絞り込むときは全件。サーバー側で検索するときは返った分）。 */
  options: readonly SearchableSelectOption[];
  /** 選択中の値（選んだ順）。 */
  value: readonly string[];
  onValueChange: (value: string[]) => void;
  /**
   * chip とボタンの要約に出す選択済みの候補（任意）。`options`（検索結果のページ）に無い選択済みも名前と状態で
   * 出すために渡す。どちらにも無い値は値そのものを名前として出す（外せるように）。
   */
  selectedOptions?: readonly SearchableSelectOption[];
  /** 検索語が確定したとき（SearchField と同じ: 300ms・Enter・消去。IME の変換中は呼ばない）。 */
  onQueryChange?: (query: string) => void;
  /** サーバー側で検索するとき（#578）。渡さなければ `options` を画面側で絞り込む。 */
  remote?: SearchableSelectRemote;
  /** 候補に無い検索語を値として足せるようにする（画面側で絞り込むときだけ）。足した値は選択の末尾に入る。 */
  creatable?: boolean;
  disabled?: boolean;
  labels?: Partial<SearchableMultiSelectLabels>;
  /**
   * 幅（既定は親の幅いっぱい）。対象の選択（ナレッジベース・プロファイルなど）は `lg`（#1891）。
   * ボタン・選択済みの chip がこの幅に収まる。sm（640px）未満は全幅。開いた popover はボタンの幅に合わせ、
   * 20〜40rem に収める（SearchableSelectField と同じ。#5267）。
   */
  width?: FieldWidth;
  className?: string;
}

/**
 * 検索できる複数選択（#578・#5267）。単一選択（SearchableSelectField）と同じ形で、「ボタン（選択の 1 行の要約）＋
 * 上の層の popover（最上部に検索欄、候補の一覧、下部の操作）＋ ボタンの下の選択済みの chip」。
 *
 * - ボタンは開いても大きさ・位置・見た目が変わらない。popover は Portal で上の層に描き、下の内容（chip など）を押さない。
 * - 開くと検索欄へフォーカスが移る。検索欄は SearchField（debounce 300ms・Enter はすぐ・IME の変換中は絞り込まない）。
 * - ↑↓ / PageUp / PageDown で候補を強調（aria-activedescendant）、Enter で選択を切り替える（選んでも閉じない）。
 *   Esc・「完了」で閉じてボタンへ戻る。外側のクリック・popover の外へのフォーカスの移動で閉じる。Tab は popover の中の
 *   操作を順に巡り、最後の操作からの Tab で閉じてボタンの次へ進む（Shift+Tab は検索欄からボタンへ戻る）。
 * - 候補が多いとき（数百件）は `remote` でサーバー側の検索に切り替え、「さらに表示」で続きを読む。
 * - 選択済みはボタンの下の chip で、名前を切らずに出す。chip の「×」で外す。
 */
export function SearchableMultiSelect({
  id,
  label,
  labelHidden = false,
  labelledBy,
  helper,
  error,
  required = false,
  requiredLabel = DEFAULT_REQUIRED_LABEL,
  describedBy: externalDescribedBy,
  invalid = false,
  options,
  value,
  onValueChange,
  selectedOptions,
  onQueryChange,
  remote,
  creatable = false,
  disabled = false,
  labels: labelOverrides,
  width,
  className,
}: SearchableMultiSelectProps) {
  const widthScope = useFieldWidthScope();
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
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listboxRef = useRef<HTMLUListElement | null>(null);
  // キーボードで動かしたときだけ強調中の候補へスクロールする（マウスの移動では動かさない）。
  const revealActiveRef = useRef(false);
  // 閉じた後に、外れた検索欄が待っていた検索語を確定しても（SearchField の unmount）受け取らない。
  const openRef = useRef(false);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // 外側のクリック（document の pointerdown。開いたときに登録する）から閉じるときも、今の検索語を消す。
  const queryRef = useRef(query);
  queryRef.current = query;
  const [draft, setDraft] = useState("");
  const [hideHideable, setHideHideable] = useState(true);
  const [activeIndex, setActiveIndex] = useState(0);
  const [portalContainer, setPortalContainer] = useState<HTMLElement | null>(null);
  const position = useSearchablePopoverPosition({ open, triggerRef: buttonRef, popoverRef });

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
  const summaryNames = chips.slice(0, SEARCHABLE_MULTI_SUMMARY_NAMES).map((option) => option.label);
  const summaryRest = chips.length - summaryNames.length;

  const canCreate =
    creatable &&
    !remote &&
    Boolean(query) &&
    !selected.has(query) &&
    !options.some((option) => option.value === query);

  const countText = remote
    ? labels.count(options.length, remote.total)
    : labels.count(visible.length, options.length);
  const searching = Boolean(remote?.searching);
  const footerText =
    hiddenCount > 0 && labels.hiddenCount ? labels.hiddenCount(hiddenCount) : labels.selectedCount(value.length);
  const describedBy =
    [helper ? hintId : "", error ? errorId : "", externalDescribedBy ?? ""].filter(Boolean).join(" ") || undefined;
  // 見出し（labelledBy）があればその見出しで、無ければ自分のラベル（labelHidden なら読み上げだけ）で名前を付ける。
  const ownLabel = !(labelHidden && labelledBy);
  const nameId = ownLabel ? labelId : labelledBy;

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!isInsideAny(event.target as Node | null, [rootRef.current, popoverRef.current])) close(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // 開いているあいだに無効になったら閉じる（SearchableSelectField と同じ）。
  useEffect(() => {
    if (disabled && open) close(false);
  }, [disabled, open]);

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
    revealActiveRef.current = false;
    setActiveIndex(0);
    setPortalContainer(selectPortalContainer(buttonRef.current));
    openRef.current = true;
    setOpen(true);
  }

  /** popover を閉じる。検索語は次に開いたとき残さない（SearchableSelectField と同じ）。 */
  function close(returnFocus: boolean) {
    openRef.current = false;
    setOpen(false);
    setActiveIndex(0);
    setDraft("");
    if (queryRef.current) {
      setQuery("");
      onQueryChange?.("");
    }
    if (returnFocus) buttonRef.current?.focus({ preventScroll: true });
  }

  function toggle(optionValue: string) {
    if (disabled) return;
    onValueChange(
      selected.has(optionValue)
        ? value.filter((current) => current !== optionValue)
        : [...value, optionValue]
    );
  }

  function createFromQuery() {
    if (disabled || !canCreate) return;
    onValueChange([...value, query]);
    setQuery("");
    setActiveIndex(0);
    inputRef.current?.focus();
  }

  function selectAllVisible() {
    const next = [...value];
    for (const option of visible) if (!selected.has(option.value)) next.push(option.value);
    onValueChange(next);
  }

  function loadMoreIfNeeded() {
    if (remote?.hasMore && !remote.loadingMore) remote.onLoadMore();
  }

  function moveActive(delta: number) {
    revealActiveRef.current = true;
    const next = moveActiveIndex(active, delta, visible.length);
    setActiveIndex(next < 0 ? 0 : next);
    // 最後の候補から ↓ で、続きのページを読む（キーボードでも「さらに表示」まで行かずに続けられる）。
    if (delta > 0 && active === visible.length - 1) loadMoreIfNeeded();
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Tab") {
      // 検索欄からの Shift+Tab はボタンへ戻る（popover は閉じる）。Tab は popover の中の操作へ進む。
      if (event.shiftKey) {
        event.preventDefault();
        close(true);
      }
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
        if (searching) return;
        // 確定前の検索語では古い候補を選ばない（この Enter で SearchField が確定する）。
        if (creatable && draft.trim() !== query) return;
        if (active < 0 && canCreate) {
          event.preventDefault();
          createFromQuery();
          return;
        }
        const option = active >= 0 ? visible[active] : undefined;
        if (!option) return; // SearchField が検索語をすぐ確定する
        // SearchField の Enter（検索語の確定）とフォームの送信をさせない。
        event.preventDefault();
        toggle(option.value);
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

  // popover の中の操作（さらに表示・完了など）にフォーカスがあるときの Esc と、最後の操作からの Tab。
  function handlePopoverKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.target === inputRef.current) return;
    if (event.target === event.currentTarget || event.target === listboxRef.current) {
      handleSearchKeyDown(event);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    if (event.key === "Tab" && !event.shiftKey) {
      const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      // 最後の操作からの Tab: 閉じてボタンへフォーカスを戻し、既定の Tab でボタンの次の要素へ進む。
      if (focusable[focusable.length - 1] === event.target) close(true);
    }
  }

  // フォーカスが部品（ボタン・chip）と popover の外へ移ったら閉じる。移り先が無い（ウィンドウの切り替え）ときは閉じない。
  function handlePopoverBlur(event: FocusEvent<HTMLDivElement>) {
    const next = event.relatedTarget;
    if (next instanceof Node && !isInsideAny(next, [rootRef.current, popoverRef.current])) close(false);
  }

  function handleButtonKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openPopover();
    } else if (event.key === "Escape" && open) {
      // 開いたままボタンへフォーカスを戻したとき（ボタンを押した後など）も Esc で閉じる。囲むモーダルへは伝えない。
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }
  }

  const popover =
    open && portalContainer ? (
      <SearchablePopover
        popoverRef={popoverRef}
        id={popoverId}
        label={label}
        portalContainer={portalContainer}
        position={position}
        onKeyDown={handlePopoverKeyDown}
        onBlur={handlePopoverBlur}
        search={{
          inputRef,
          id: searchId,
          label: labels.searchLabel(label),
          value: query,
          onSearch: (next) => {
            if (!openRef.current) return;
            setQuery(next);
            setActiveIndex(0);
            onQueryChange?.(next);
          },
          onDraftChange: setDraft,
          clearLabel: labels.clearSearch,
          placeholder: labels.searchPlaceholder,
          resultCountLabel: searching ? labels.searching : countText,
          listboxId,
          activeDescendant: active >= 0 ? searchableOptionId(listboxId, active) : undefined,
          onKeyDown: handleSearchKeyDown,
        }}
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
            tabIndex={-1}
            aria-label={label}
            aria-multiselectable
            aria-busy={searching || undefined}
            aria-activedescendant={active >= 0 ? searchableOptionId(listboxId, active) : undefined}
            className="bounded-scroll-area min-h-0 overscroll-contain py-1"
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
          <SelectListEmpty searching={searching} searchingLabel={labels.searching}>
            {query ? labels.noMatch(query) : labels.empty}
          </SelectListEmpty>
        )}

        {canCreate ? (
          <div className="border-t border-border px-3 py-1.5">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              icon={Plus}
              disabled={disabled}
              onClick={createFromQuery}
              className="w-full"
            >
              {labels.create?.(query)}
            </Button>
          </div>
        ) : null}

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

        {/* 下部の行: 左に「N 件選択中」（折り返さない）、右に操作の組（表示中をすべて選択・クリア・完了）。
            幅が足りない（375px など）ときは件数の文と操作の組を 2 段にし、操作の組は分けない（#2091）。 */}
        <div
          className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 border-t border-border px-3 py-1.5"
          data-searchable-panel-footer=""
        >
          <span className="tnum shrink-0 whitespace-nowrap text-xs text-fg-muted">{footerText}</span>
          {/* 操作の間隔はボタンの余白に任せ（gap なし）、欄の幅（lg 28rem）の popover で件数の文と 1 行に入れる。 */}
          <span className="ml-auto flex flex-wrap items-center justify-end" data-searchable-panel-actions="">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              // 検索語を変えている間は古い結果が出ているため、まとめて選ばせない。
              disabled={disabled || visible.length === 0 || searching}
              onClick={selectAllVisible}
              className="shrink-0"
            >
              {labels.selectAllVisible}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={disabled || value.length === 0}
              onClick={() => onValueChange([])}
              className="shrink-0"
            >
              {labels.clear}
            </Button>
            {/* 選択は選ぶたびに反映済み。「完了」は popover を閉じてボタンへ戻る、目に見える閉じ方。 */}
            <Button
              type="button"
              size="sm"
              variant="secondary"
              icon={Check}
              onClick={() => close(true)}
              className="shrink-0"
            >
              {labels.done}
            </Button>
          </span>
        </div>
      </SearchablePopover>
    ) : null;

  return (
    <div
      ref={rootRef}
      // 親が grid / flex でも親の幅に収める（chip の名前は折り返すため、min-content を親の幅に縛る。#1794）。
      className={cn("min-w-0 space-y-1.5", fieldWidthClass(width, widthScope), className)}
      data-searchable-multi-select=""
    >
      {ownLabel ? (
        <label
          id={labelId}
          htmlFor={id}
          className={cn("flex items-center gap-2 text-sm font-medium text-fg", labelHidden && "sr-only")}
        >
          {label}
          {/* ボタンは aria-required を持てない（axe の aria-allowed-attr）ので、タグを読み上げ対象に残す（#1595）。 */}
          {required && requiredLabel ? <RequiredBadge label={requiredLabel} /> : null}
        </label>
      ) : null}
      <button
        ref={buttonRef}
        id={id}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        aria-labelledby={nameId ? `${nameId} ${valueId}` : valueId}
        aria-describedby={describedBy}
        aria-invalid={Boolean(error) || invalid || undefined}
        disabled={disabled}
        onClick={() => (open ? close(false) : openPopover())}
        onKeyDown={handleButtonKeyDown}
        className={cn(
          CONTROL_HEIGHT_CLASS.md,
          "flex w-full cursor-pointer items-center justify-between gap-3 rounded-control border bg-surface px-3 text-left text-sm text-fg transition-colors",
          "enabled:hover:bg-surface-hover forced-colors:border-[CanvasText] focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring",
          "disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled",
          error || invalid ? "border-danger-fg" : "border-border-control"
        )}
      >
        {/* 選択の 1 行の要約（「A、B 他 3 件」）。名前が入らなければ名前を省き、「他 N 件」は残す（全文は title と chip）。
            高さはボタンの段で固定する。w-0 flex-1 で要約の長さを欄の幅（親の grid・flex の列）に数えない（長い名前で列を広げない）。 */}
        <span
          id={valueId}
          className={cn("flex w-0 min-w-0 flex-1 items-center gap-1", chips.length === 0 && !disabled && "text-fg-muted")}
          title={chips.length > 0 ? chips.map((option) => option.label).join(labels.summarySeparator) : undefined}
          data-searchable-summary=""
        >
          {chips.length === 0 ? (
            <span className="truncate">{labels.noneSelected}</span>
          ) : (
            <>
              <span className="min-w-0 truncate">{summaryNames.join(labels.summarySeparator)}</span>
              {summaryRest > 0 ? (
                <span className="shrink-0 whitespace-nowrap">{labels.summaryRest(summaryRest)}</span>
              ) : null}
            </>
          )}
        </span>
        <ChevronDown
          size={16}
          aria-hidden
          className={cn(
            "shrink-0 transition-transform duration-150",
            disabled ? "text-fg-disabled" : "text-fg-muted",
            open && "rotate-180 text-accent-fg"
          )}
        />
      </button>
      {popover}
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />

      {chips.length > 0 ? (
        <ul
          aria-label={labels.selectedList(label)}
          className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto [scrollbar-gutter:stable]"
          data-searchable-chips=""
        >
          {chips.map((option) => (
            <li
              key={option.value}
              data-surface-tint="info"
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
