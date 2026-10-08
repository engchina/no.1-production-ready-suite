import { formatPaginationNumber, type PaginationLabels } from "@production-ready/ui";

import { t } from "./i18n";

/**
 * 共通の `Pagination` / `CursorPagination` / `PagedDataTable` に渡す文言（#1266。3 製品で同じ形）。
 * 件数・ページ番号は共通の数値の書式（桁区切り）で整形する。一覧ごとの aria-label は `ariaLabel` で上書きする。
 */
export function paginationLabels(): PaginationLabels {
  return {
    summary: ({ start, end, total }) =>
      t("pager.range", {
        start: formatPaginationNumber(start),
        end: formatPaginationNumber(end),
        total: formatPaginationNumber(total),
      }),
    pageIndicator: (page, totalPages) =>
      t("pager.page", {
        page: formatPaginationNumber(page),
        total: formatPaginationNumber(totalPages),
      }),
    prev: t("pager.prev"),
    next: t("pager.next"),
    ariaLabel: t("pager.label"),
  };
}
