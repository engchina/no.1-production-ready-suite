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

  const range: PaginationRange = {
    start: total === 0 ? 0 : start + 1,
    end: Math.min(start + pageSize, total),
    total,
  };

  return { page: currentPage, setPage, totalPages, pageItems, range };
}

/** 制御式で resetKey が無いときの、変わらない契機。 */
const CONTROLLED_WITHOUT_RESET = Symbol("controlled-without-reset");

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

export interface PaginationProps {
  /** 1-based の現在ページ。 */
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  /** 翻訳済みの件数サマリ（例: 「1–10 / 42 件」）。caller が t() で用意。 */
  summary: string;
  /** 翻訳済み「前へ」ラベル。 */
  prevLabel: string;
  /** 翻訳済み「次へ」ラベル。 */
  nextLabel: string;
  /** 翻訳済み「N / M ページ」ラベル（任意）。 */
  pageIndicator?: string;
  /** nav の aria-label（任意）。省略時は pageIndicator / summary。 */
  ariaLabel?: string;
  /** テスト用 data-testid（任意）。 */
  testId?: string;
  className?: string;
}

/**
 * 共通ページネーション（presentational）。件数サマリ + 前/次。
 * ラベルは i18n 済み文字列を props で受ける（パッケージは i18n 非依存）。
 */
export function Pagination({
  page,
  totalPages,
  onPageChange,
  summary,
  prevLabel,
  nextLabel,
  pageIndicator,
  ariaLabel,
  testId,
  className,
}: PaginationProps) {
  if (totalPages <= 1) return null;
  return (
    <nav
      className={cn(
        "flex flex-wrap items-center justify-between gap-2 text-xs text-fg-muted",
        className
      )}
      aria-label={ariaLabel ?? pageIndicator ?? summary}
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
          <span>{prevLabel}</span>
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
          <span>{nextLabel}</span>
        </Button>
      </div>
    </nav>
  );
}
