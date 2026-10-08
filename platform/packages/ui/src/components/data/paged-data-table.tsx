import { INFORMATION_TABLE_ROW_CLASS, INFORMATION_TABLE_VISIBLE_ROWS } from "../../lib/list-density";
import { cn } from "../../lib/utils";
import { DataTable, type DataTableProps } from "./data-table";
import { DEFAULT_PAGE_SIZE, Pagination, usePagination, type PaginationLabels } from "./pagination";

export type { PaginationLabels } from "./pagination";

export type PagedDataTableProps<T> = Omit<DataTableProps<T>, "stickyHeader" | "visibleRows"> & {
  /** Pagination の文言（省略時は共通の既定 `DEFAULT_PAGINATION_LABELS`。製品は i18n の文字列を渡す）。 */
  paginationLabels?: Partial<PaginationLabels>;
  /** 1 ページの件数（既定 10 件）。 */
  pageSize?: number;
  /**
   * 1 ページ目へ戻す契機（検索語・絞り込み・対象の切り替えなど）。
   * 省略時もページを保つ（定期的な再取得で行が変わってもページを戻さない。行が減って範囲外になったら表示だけ末尾に寄せる）。
   */
  resetKey?: unknown;
  /** 制御式のページ番号（作業状態に保持するとき。UX 契約 workspace-state.md）。 */
  page?: number;
  onPageChange?: (page: number) => void;
  paginationTestId?: string;
  paginationAriaLabel?: string;
};

/**
 * クライアント側で全件を持つ一覧の標準形（#265、NL2SQL が基準）。
 * 表頭を固定し、md 未満 5 行・md 以上 8 行で表の中を縦スクロールにして、直下に共通の Pagination（既定 10 件/ページ）を置く。
 * 1 ページしかないときは Pagination を出さない。行の最小高さは `INFORMATION_TABLE_ROW_CLASS`。
 */
export function PagedDataTable<T>({
  rows,
  rowProps,
  paginationLabels,
  pageSize = DEFAULT_PAGE_SIZE,
  resetKey,
  page: controlledPage,
  onPageChange,
  paginationTestId,
  paginationAriaLabel,
  ...props
}: PagedDataTableProps<T>) {
  const { page, setPage, totalPages, pageItems, range } = usePagination(rows, pageSize, {
    page: controlledPage,
    onPageChange,
    // resetKey を省いたときは、行が変わってもページを戻さない（undefined は変わらない契機）。
    resetKey,
  });
  return (
    <div className="grid min-w-0 gap-2">
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
      <Pagination
        page={page}
        totalPages={totalPages}
        range={range}
        onPageChange={setPage}
        labels={paginationLabels}
        ariaLabel={paginationAriaLabel}
        testId={paginationTestId}
      />
    </div>
  );
}
