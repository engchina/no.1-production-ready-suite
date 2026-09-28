import { Pagination, type PaginationLabels, type PaginationRange } from "@engchina/production-ready-ui";

import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";

/** 共通の Pagination に渡す RAG の文言（件数・ページ・前へ / 次へ）。 */
export function listPaginationLabels(): PaginationLabels {
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

/**
 * 一覧の直下に置く共通の Pagination に、RAG の文言を渡す（#265）。
 * クライアント側は `usePagination`、サーバー側（offset / limit / total）は `offsetPagination` の結果を渡す。
 * 1 ページしかないときは何も出さない。
 */
export function ListPagination({
  page,
  totalPages,
  range,
  onPageChange,
  ariaLabel,
  testId,
}: {
  page: number;
  totalPages: number;
  range: PaginationRange;
  onPageChange: (page: number) => void;
  ariaLabel?: string;
  testId?: string;
}) {
  const labels = listPaginationLabels();
  return (
    <Pagination
      page={page}
      totalPages={totalPages}
      onPageChange={onPageChange}
      summary={labels.summary(range)}
      pageIndicator={labels.pageIndicator?.(page, totalPages)}
      prevLabel={labels.prev}
      nextLabel={labels.next}
      ariaLabel={ariaLabel ?? labels.ariaLabel}
      testId={testId}
    />
  );
}
