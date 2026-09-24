import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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
 * 一覧のページング状態（slice・totalPages・clamp）を 1 箇所に集約するフック。
 * 各ページで重複していた「PAGE_SIZE / setPage / slice / clamp」を置き換える。
 * items が変わったら 1 ページ目へ戻す。
 */
export function usePagination<T>(items: readonly T[], pageSize: number = DEFAULT_PAGE_SIZE) {
  const [page, setPage] = useState(1);
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, totalPages);

  useEffect(() => {
    setPage(1);
  }, [items]);

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
          <span className="tnum inline-flex min-h-[var(--button-height-sm)] items-center rounded-[var(--button-radius)] border border-border-control px-3 text-fg">
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
