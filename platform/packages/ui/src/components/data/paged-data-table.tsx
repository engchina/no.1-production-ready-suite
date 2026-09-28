import { INFORMATION_TABLE_ROW_CLASS, INFORMATION_TABLE_VISIBLE_ROWS } from "../../lib/list-density";
import { cn } from "../../lib/utils";
import { DataTable, type DataTableProps } from "./data-table";
import { DEFAULT_PAGE_SIZE, Pagination, usePagination, type PaginationRange } from "./pagination";

/**
 * Pagination の文言（パッケージは i18n に依存しないため、製品が翻訳済みの文字列と整形関数を渡す）。
 * 件数とページ番号は製品の数値の書式（桁区切り等）で整形する。
 */
export interface PaginationLabels {
  /** 件数のサマリ（例: 「1 - 10 / 42 件」）。 */
  summary: (range: PaginationRange) => string;
  /** 「N / M ページ」（任意）。 */
  pageIndicator?: (page: number, totalPages: number) => string;
  prev: string;
  next: string;
  /** nav の aria-label（任意。一覧ごとに `paginationAriaLabel` で上書きできる）。 */
  ariaLabel?: string;
}

export type PagedDataTableProps<T> = Omit<DataTableProps<T>, "stickyHeader" | "visibleRows"> & {
  /** Pagination の文言。 */
  paginationLabels: PaginationLabels;
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
        onPageChange={setPage}
        summary={paginationLabels.summary(range)}
        pageIndicator={paginationLabels.pageIndicator?.(page, totalPages)}
        prevLabel={paginationLabels.prev}
        nextLabel={paginationLabels.next}
        ariaLabel={paginationAriaLabel ?? paginationLabels.ariaLabel}
        testId={paginationTestId}
      />
    </div>
  );
}
