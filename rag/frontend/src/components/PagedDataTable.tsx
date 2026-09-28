import {
  DataTable,
  DEFAULT_PAGE_SIZE,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  usePagination,
  type DataTableProps,
} from "@engchina/production-ready-ui";

import { ListPagination } from "@/components/ListPagination";
import { cn } from "@/lib/utils";

export type PagedDataTableProps<T> = Omit<DataTableProps<T>, "stickyHeader" | "visibleRows"> & {
  /**
   * 1 ページ目へ戻す契機（業務ビューの切り替えなど）。再取得で行が変わってもページは保ち、
   * 行が減って範囲外になったら表示だけ末尾のページに寄せる。
   */
  resetKey?: unknown;
  paginationTestId?: string;
  paginationAriaLabel?: string;
};

/**
 * クライアント側で全件を持つ一覧の標準形（#265、NL2SQL が基準）。
 * 表頭を固定し md 未満 5 行・md 以上 8 行で表の中を縦スクロールにして、直下に共通の Pagination（10 件/ページ）を置く。
 */
export function PagedDataTable<T>({
  rows,
  rowProps,
  resetKey,
  paginationTestId,
  paginationAriaLabel,
  ...props
}: PagedDataTableProps<T>) {
  const { page, setPage, totalPages, pageItems, range } = usePagination(rows, DEFAULT_PAGE_SIZE, { resetKey });
  return (
    <div className="grid gap-2">
      <DataTable<T>
        {...props}
        rows={pageItems}
        rowProps={(row, index) => {
          const extra = rowProps?.(row, index);
          return { ...extra, className: cn(INFORMATION_TABLE_ROW_CLASS, extra?.className) };
        }}
        stickyHeader
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
      />
      <ListPagination
        page={page}
        totalPages={totalPages}
        range={range}
        onPageChange={setPage}
        ariaLabel={paginationAriaLabel}
        testId={paginationTestId}
      />
    </div>
  );
}
