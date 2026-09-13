import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@engchina/production-ready-ui";

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
        "flex flex-wrap items-center justify-between gap-[8px] text-xs text-fg-muted",
        className
      )}
      aria-label={ariaLabel ?? pageIndicator ?? summary}
      data-testid={testId}
    >
      <span className="tnum">{summary}</span>
      <div className="flex flex-wrap items-center gap-[8px]">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPageChange(Math.max(1, page - 1))} icon={ChevronLeft}>
          <span>{prevLabel}</span>
        </Button>
        {pageIndicator ? (
          <span className="tnum inline-flex min-h-8 items-center rounded-md border border-border px-3 text-fg">
            {pageIndicator}
          </span>
        ) : null}
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={page >= totalPages}
          onClick={() => onPageChange(Math.min(totalPages, page + 1))} trailingIcon={ChevronRight}>
          <span>{nextLabel}</span>
        </Button>
      </div>
    </nav>
  );
}
