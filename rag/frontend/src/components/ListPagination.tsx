import { Pagination, type PaginationRange } from "@engchina/production-ready-ui";

import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";

/**
 * 一覧の直下に置く共通の Pagination に、RAG の文言（件数・ページ・前へ / 次へ）を渡す（#265）。
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
  return (
    <Pagination
      page={page}
      totalPages={totalPages}
      onPageChange={onPageChange}
      summary={t("pager.range", {
        start: formatNumber(range.start),
        end: formatNumber(range.end),
        total: formatNumber(range.total),
      })}
      pageIndicator={t("pager.page", { page, total: totalPages })}
      prevLabel={t("pager.prev")}
      nextLabel={t("pager.next")}
      ariaLabel={ariaLabel ?? t("pager.label")}
      testId={testId}
    />
  );
}
