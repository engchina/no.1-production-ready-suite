import type { PaginationLabels } from "@production-ready/ui";

import { formatNumber } from "./format";
import { t } from "./i18n";

/**
 * 共通の `Pagination` / `OffsetPagination` / `PagedDataTable` に渡す RAG の文言（件数・ページ・前へ / 次へ。#1266）。
 * 文言は i18n（`pager.*`）、数値の書式は `formatNumber` で、画面ごとの aria-label は呼び出し側の `ariaLabel` で上書きする。
 */
export function paginationLabels(): PaginationLabels {
  return {
    summary: (range) =>
      t("pager.range", {
        start: formatNumber(range.start),
        end: formatNumber(range.end),
        total: formatNumber(range.total),
      }),
    pageIndicator: (page, total) => t("pager.page", { page, total }),
    prev: t("pager.prev"),
    next: t("pager.next"),
    ariaLabel: t("pager.label"),
  };
}
