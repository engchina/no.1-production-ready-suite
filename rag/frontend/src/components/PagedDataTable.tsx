import {
  PagedDataTable as SharedPagedDataTable,
  type PagedDataTableProps as SharedPagedDataTableProps,
} from "@engchina/production-ready-ui";

import { listPaginationLabels } from "@/components/ListPagination";

export type PagedDataTableProps<T> = Omit<SharedPagedDataTableProps<T>, "paginationLabels">;

/**
 * クライアント側で全件を持つ一覧の標準形（#265、NL2SQL が基準）。
 * 共有の PagedDataTable（表頭の固定・md 未満 5 行 / md 以上 8 行の縦スクロール・10 件/ページ）に RAG の文言を渡す。
 */
export function PagedDataTable<T>(props: PagedDataTableProps<T>) {
  return <SharedPagedDataTable<T> {...props} paginationLabels={listPaginationLabels()} />;
}
