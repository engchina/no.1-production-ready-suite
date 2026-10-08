import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

/** 一覧のページング既定サイズ。手書き PAGE_SIZE を廃し本定数へ統一する。 */
export const DEFAULT_PAGE_SIZE = 10;

export interface PaginationRange {
  /** 1-based の表示開始行（空なら 0）。 */
  start: number;
  /** 表示終了行（含む）。 */
  end: number;
  /** 全件数。 */
  total: number;
}

/**
 * Pagination の文言。パッケージは i18n に依存しないため、既定は日本語（`DEFAULT_PAGINATION_LABELS`）で、
 * 製品は i18n の文字列と数値の書式で上書きできる（`labels` に部分だけ渡してもよい）。
 */
export interface PaginationLabels {
  /** 件数のサマリ（例: 「1 - 10 / 42 件」）。 */
  summary: (range: PaginationRange) => string;
  /** 「N / M ページ」（任意）。 */
  pageIndicator?: (page: number, totalPages: number) => string;
  prev: string;
  next: string;
  /** nav の aria-label（任意。一覧ごとに `ariaLabel` で上書きできる）。 */
  ariaLabel?: string;
}

const numberFormat = new Intl.NumberFormat("ja-JP");

/** 共通の数値の書式（桁区切り）。製品の `formatNumber` と同じ結果になる。 */
export function formatPaginationNumber(value: number) {
  return numberFormat.format(value);
}

/** Pagination の既定の文言（日本語。3 製品で同じ形。#1266）。 */
export const DEFAULT_PAGINATION_LABELS: PaginationLabels = {
  summary: ({ start, end, total }) =>
    `${formatPaginationNumber(start)} - ${formatPaginationNumber(end)} / ${formatPaginationNumber(total)} 件`,
  pageIndicator: (page, totalPages) => `${page} / ${totalPages} ページ`,
  prev: "前へ",
  next: "次へ",
  ariaLabel: "ページ送り",
};

export interface UsePaginationOptions {
  /**
   * 制御式の現在ページ（1-based）。ページ番号を作業状態（sessionStorage・URL 等）に保持するときに渡す
   * （UX 契約 workspace-state.md）。渡すと、再取得で items が変わってもページを戻さない（範囲外は表示だけ末尾に寄せる）。
   */
  page?: number;
  /** 制御式のときのページ変更。`setPage` と Pagination の操作から呼ばれる。 */
  onPageChange?: (page: number) => void;
  /**
   * 1 ページ目へ戻す契機（検索語・絞り込み・並べ替え等）。この値が変わったときだけ 1 ページ目へ戻す。
   * 省略時: 非制御なら items が変わるたびに戻す（従来どおり）、制御式なら戻さない。
   */
  resetKey?: unknown;
}

/**
 * 一覧のページング状態（slice・totalPages・clamp）を 1 箇所に集約するフック。
 * 各ページで重複していた「PAGE_SIZE / setPage / slice / clamp」を置き換える。
 * 既定では items が変わったら 1 ページ目へ戻す。ページを保持する一覧は `options.page` / `onPageChange` /
 * `resetKey` を渡す。
 */
export function usePagination<T>(
  items: readonly T[],
  pageSize: number = DEFAULT_PAGE_SIZE,
  options: UsePaginationOptions = {}
) {
  const controlled = options.page !== undefined;
  const [innerPage, setInnerPage] = useState(1);
  const requestedPage = controlled ? Math.max(1, Math.floor(options.page ?? 1)) : innerPage;
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(requestedPage, totalPages);

  const onPageChangeRef = useRef(options.onPageChange);
  useLayoutEffect(() => {
    onPageChangeRef.current = options.onPageChange;
  });
  const setPage = useCallback(
    (next: number) => {
      if (controlled) onPageChangeRef.current?.(next);
      else setInnerPage(next);
    },
    [controlled]
  );

  // 前回の契機と比べて変わったときだけ戻す（StrictMode の effect の再実行や復元直後の初回で戻さない）。
  const resetTrigger = "resetKey" in options ? options.resetKey : controlled ? CONTROLLED_WITHOUT_RESET : items;
  const previousResetTrigger = useRef(resetTrigger);
  useEffect(() => {
    if (Object.is(previousResetTrigger.current, resetTrigger)) return;
    previousResetTrigger.current = resetTrigger;
    setPage(1);
  }, [resetTrigger, setPage]);

  const start = total === 0 ? 0 : (currentPage - 1) * pageSize;
  const pageItems = useMemo(
    () => items.slice(start, start + pageSize),
    [items, start, pageSize]
  );

  const range = paginationRange(currentPage, pageSize, pageItems.length, total);

  return { page: currentPage, setPage, totalPages, pageItems, range };
}

/** 制御式で resetKey が無いときの、変わらない契機。 */
const CONTROLLED_WITHOUT_RESET = Symbol("controlled-without-reset");

/**
 * 件数のサマリの範囲（「a - b / n 件」の a・b・n）。`count` は今のページで出している件数。
 * 0 件のときは start / end とも 0。
 */
export function paginationRange(page: number, pageSize: number, count: number, total: number): PaginationRange {
  const size = Math.max(1, pageSize);
  const offset = (Math.max(1, Math.floor(page)) - 1) * size;
  const safeTotal = Math.max(0, total);
  return {
    start: count === 0 || safeTotal === 0 ? 0 : offset + 1,
    end: count === 0 ? 0 : offset + count,
    total: safeTotal,
  };
}

/**
 * サーバー側のページング（offset / limit / total）を Pagination の page / totalPages / range に直す。
 * `count` は今のページで返った件数。ページの移動は `offsetForPage(page, limit)` で offset に戻す。
 */
export function offsetPagination({
  offset,
  limit,
  total,
  count,
}: {
  offset: number;
  limit: number;
  total: number;
  count: number;
}) {
  const size = Math.max(1, limit);
  const page = Math.floor(Math.max(0, offset) / size) + 1;
  const totalPages = Math.max(1, Math.ceil(Math.max(0, total) / size));
  const range: PaginationRange = {
    start: total === 0 || count === 0 ? 0 : offset + 1,
    end: count === 0 ? 0 : offset + count,
    total,
  };
  return { page, totalPages, range };
}

/** 1-based のページ番号を offset に直す。 */
export function offsetForPage(page: number, limit: number) {
  return (Math.max(1, Math.floor(page)) - 1) * Math.max(1, limit);
}

/** 最後のページの offset（0 件なら 0）。 */
export function lastPageOffset(total: number, limit: number) {
  if (total <= 0) return 0;
  const size = Math.max(1, limit);
  return Math.floor((total - 1) / size) * size;
}

/**
 * 表示中のページが範囲外になったとき（最後のページの最後の 1 件を削除した・保存していたページが
 * 他の操作で無くなった）に移るべき offset を返す。範囲内なら null。
 * 1 件もない（total=0）ときは先頭（0）へ戻す。DB の縮退で空になったときも 0 に戻るだけで、回復後は先頭から表示する。
 */
export function outOfRangeOffset({
  offset,
  total,
  limit,
}: {
  offset: number;
  total: number;
  limit: number;
}): number | null {
  if (offset === 0 || offset < total) return null;
  return lastPageOffset(total, limit);
}

/**
 * カーソル型の API（`next_cursor` と `total` を返す）のページを、共通の Pagination の
 * page / totalPages / range に直す（#403。NL2SQL から共通にした）。前へ戻るためのカーソルは画面が積んで持つ
 * （`useCursorPages`）。
 * - `depth`: 今までに「次へ」で進んだ回数（積んだカーソルの数）。
 * - `total` がカーソルの位置と合わない（取得の間に件数が変わった）ときも、次のカーソルがあれば次のページを数える。
 */
export function cursorPagination({
  depth,
  limit,
  total,
  count,
  hasNext,
}: {
  depth: number;
  limit: number;
  total: number;
  count: number;
  hasNext: boolean;
}) {
  const size = Math.max(1, limit);
  const page = Math.max(0, depth) + 1;
  const offset = (page - 1) * size;
  const totalPages = Math.max(page + (hasNext ? 1 : 0), Math.ceil(Math.max(0, total) / size), 1);
  return {
    page,
    totalPages,
    range: {
      start: count === 0 ? 0 : offset + 1,
      end: count === 0 ? 0 : offset + count,
      total: Math.max(total, offset + count),
    },
  };
}

interface CursorPagesState {
  cursor: string | null;
  stack: Array<string | null>;
}

const EMPTY_CURSOR_PAGES: CursorPagesState = { cursor: null, stack: [] };

/**
 * カーソル型の API の「前へ / 次へ」の状態（今のカーソルと、前へ戻るために積んだカーソル）。
 * `next(nextCursor)` で進み、`prev()` で 1 つ戻り、`reset()`（または `resetKey` の変化）で先頭へ戻す。
 * `cursor` は最初のページで `null`（API には送らない）。
 */
export function useCursorPages(options: { resetKey?: unknown } = {}) {
  const [state, setState] = useState<CursorPagesState>(EMPTY_CURSOR_PAGES);
  const next = useCallback((nextCursor: string) => {
    setState((current) => ({ cursor: nextCursor, stack: [...current.stack, current.cursor] }));
  }, []);
  const prev = useCallback(() => {
    setState((current) => {
      if (current.stack.length === 0) return current;
      const stack = current.stack.slice(0, -1);
      return { cursor: current.stack[current.stack.length - 1] ?? null, stack };
    });
  }, []);
  const reset = useCallback(() => {
    setState((current) => (current.cursor === null && current.stack.length === 0 ? current : EMPTY_CURSOR_PAGES));
  }, []);

  // 検索語・絞り込みが変わったら先頭へ（usePagination の resetKey と同じ規則）。
  const resetTrigger = "resetKey" in options ? options.resetKey : CONTROLLED_WITHOUT_RESET;
  const previousResetTrigger = useRef(resetTrigger);
  useEffect(() => {
    if (Object.is(previousResetTrigger.current, resetTrigger)) return;
    previousResetTrigger.current = resetTrigger;
    reset();
  }, [resetTrigger, reset]);

  return {
    cursor: state.cursor,
    depth: state.stack.length,
    page: state.stack.length + 1,
    canGoPrevious: state.stack.length > 0,
    next,
    prev,
    reset,
  };
}

function resolveLabels(labels: Partial<PaginationLabels> | undefined): PaginationLabels {
  return labels ? { ...DEFAULT_PAGINATION_LABELS, ...labels } : DEFAULT_PAGINATION_LABELS;
}

export interface PaginationProps {
  /** 1-based の現在ページ。 */
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  /**
   * 文言（部分だけでもよい。省略は `DEFAULT_PAGINATION_LABELS`）。`summary` / `pageIndicator` は `range` から作る。
   * 製品は i18n の文字列と数値の書式をここで渡す。
   */
  labels?: Partial<PaginationLabels>;
  /** 件数のサマリの範囲（`usePagination` / `offsetPagination` / `cursorPagination` の `range`）。 */
  range?: PaginationRange;
  /** 翻訳済みの件数サマリ（`range` + `labels` の代わりに直接渡すとき）。 */
  summary?: string;
  /** 翻訳済み「前へ」ラベル（`labels.prev` の代わり）。 */
  prevLabel?: string;
  /** 翻訳済み「次へ」ラベル（`labels.next` の代わり）。 */
  nextLabel?: string;
  /** 翻訳済み「N / M ページ」ラベル（`labels.pageIndicator` の代わり）。 */
  pageIndicator?: string;
  /** nav の aria-label（任意）。省略時は labels.ariaLabel / pageIndicator / summary。 */
  ariaLabel?: string;
  /** テスト用 data-testid（任意）。 */
  testId?: string;
  className?: string;
}

/**
 * 共通ページネーション（presentational）。件数サマリ + 前/次。
 * 文言は `labels`（既定は日本語）と `range` から作る。1 ページしかないときは何も出さない。
 */
export function Pagination({
  page,
  totalPages,
  onPageChange,
  labels: labelOverrides,
  range,
  summary: summaryText,
  prevLabel,
  nextLabel,
  pageIndicator: pageIndicatorText,
  ariaLabel,
  testId,
  className,
}: PaginationProps) {
  if (totalPages <= 1) return null;
  const labels = resolveLabels(labelOverrides);
  const summary = summaryText ?? (range ? labels.summary(range) : "");
  const pageIndicator = pageIndicatorText ?? labels.pageIndicator?.(page, totalPages);
  return (
    <nav
      className={cn(
        "flex flex-wrap items-center justify-between gap-2 text-xs text-fg-muted",
        className
      )}
      aria-label={ariaLabel ?? labels.ariaLabel ?? pageIndicator ?? summary}
      data-testid={testId}
    >
      <span className="tnum">{summary}</span>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          icon={ChevronLeft}
          disabled={page <= 1}
          onClick={() => onPageChange(Math.max(1, page - 1))}
        >
          <span>{prevLabel ?? labels.prev}</span>
        </Button>
        {pageIndicator ? (
          <span className="tnum inline-flex min-h-[var(--button-height-sm)] items-center rounded-control border border-border-control px-3 text-fg">
            {pageIndicator}
          </span>
        ) : null}
        <Button
          type="button"
          variant="secondary"
          size="sm"
          trailingIcon={ChevronRight}
          disabled={page >= totalPages}
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
        >
          <span>{nextLabel ?? labels.next}</span>
        </Button>
      </div>
    </nav>
  );
}

export interface OffsetPaginationProps {
  offset: number;
  limit: number;
  total: number;
  /** 今のページで返った件数。 */
  count: number;
  /** ページの移動（1-based のページ番号と、その offset）。 */
  onPageChange: (page: number, offset: number) => void;
  labels?: Partial<PaginationLabels>;
  ariaLabel?: string;
  testId?: string;
  className?: string;
}

/**
 * サーバー側（offset / limit / total）でページングする一覧の直下に置く Pagination（#794 / #1266。Agent の
 * `ServerPagination` と RAG の `outOfRangeOffset` を共通にした）。
 * 1 ページしかないときは出さない。表示中のページが範囲外（削除・期間の変更・保存していたページが無くなった）に
 * なって 0 件で返ったら、最後のページへ寄せる（`onPageChange` を呼ぶ）。
 */
export function OffsetPagination({
  offset,
  limit,
  total,
  count,
  onPageChange,
  labels,
  ariaLabel,
  testId,
  className,
}: OffsetPaginationProps) {
  const paging = offsetPagination({ offset, limit, total, count });
  const onPageChangeRef = useRef(onPageChange);
  useLayoutEffect(() => {
    onPageChangeRef.current = onPageChange;
  });
  const fallbackOffset = count === 0 ? outOfRangeOffset({ offset, total, limit }) : null;
  useEffect(() => {
    if (fallbackOffset === null) return;
    onPageChangeRef.current(Math.floor(fallbackOffset / Math.max(1, limit)) + 1, fallbackOffset);
  }, [fallbackOffset, limit]);
  return (
    <Pagination
      page={paging.page}
      totalPages={paging.totalPages}
      range={paging.range}
      onPageChange={(page) => onPageChange(page, offsetForPage(page, limit))}
      labels={labels}
      ariaLabel={ariaLabel}
      testId={testId}
      className={className}
    />
  );
}

export interface CursorPaginationProps {
  /** 「次へ」で進んだ回数（`useCursorPages().depth`）。 */
  depth: number;
  /** 1 ページの件数（既定 10 件）。 */
  limit?: number;
  total: number;
  /** 今のページで返った件数。 */
  count: number;
  /** API が返した次のカーソル（無ければ null / undefined / 空文字）。 */
  nextCursor: string | null | undefined;
  onPrevious: () => void;
  onNext: (nextCursor: string) => void;
  labels?: Partial<PaginationLabels>;
  ariaLabel?: string;
  testId?: string;
  className?: string;
}

/**
 * カーソル型の API（`next_cursor` と `total`）のページ送りを、共通の Pagination で出す（#403 / #1266）。
 * 前へ戻るカーソルは `useCursorPages` が積んで持つので、移動は隣のページだけ（前へ / 次へ）。
 */
export function CursorPagination({
  depth,
  limit = DEFAULT_PAGE_SIZE,
  total,
  count,
  nextCursor,
  onPrevious,
  onNext,
  labels,
  ariaLabel,
  testId,
  className,
}: CursorPaginationProps) {
  const next = nextCursor || null;
  const { page, totalPages, range } = cursorPagination({ depth, limit, total, count, hasNext: next !== null });
  return (
    <Pagination
      page={page}
      totalPages={totalPages}
      range={range}
      onPageChange={(target) => {
        if (target < page) onPrevious();
        else if (target > page && next !== null) onNext(next);
      }}
      labels={labels}
      ariaLabel={ariaLabel}
      testId={testId}
      className={className}
    />
  );
}
