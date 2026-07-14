import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DataTable,
  DEFAULT_PAGE_SIZE,
  Pagination,
  usePagination,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import type { QueryResults } from "../types";

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// ponytail: hooks 順を保つため guard 前に無条件呼び出し。空配列は安定参照にして usePagination の再初期化ループを避ける
const EMPTY_ROWS: Record<string, unknown>[] = [];

export function Nl2SqlResultTable({ results }: { results: QueryResults | null }) {
  const { page, setPage, totalPages, pageItems, range } = usePagination(
    results?.rows ?? EMPTY_ROWS,
    DEFAULT_PAGE_SIZE,
  );

  if (!results) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("nl2sql.results.title", { count: results.total })}</CardTitle>
      </CardHeader>
      <CardContent>
        {results.rows.length === 0 ? (
          <p className="rounded-md border border-dashed border-border p-6 text-sm text-muted">
            {t("nl2sql.results.empty")}
          </p>
        ) : (
          <div className="grid gap-2">
            <DataTable
              columns={results.columns.map((column) => ({
                key: column,
                header: column,
                render: (row: Record<string, unknown>) => formatCell(row[column]),
              }))}
              rows={pageItems}
              getRowKey={(_, index) => (range.start === 0 ? 0 : range.start - 1) + index}
              ariaLabel={t("nl2sql.results.title", { count: results.total })}
            />
            <Pagination
              page={page}
              totalPages={totalPages}
              onPageChange={setPage}
              summary={t("queryResults.pageSummary", { start: range.start, end: range.end, total: range.total })}
              pageIndicator={t("queryResults.page", { page, total: totalPages })}
              prevLabel={t("queryResults.prev")}
              nextLabel={t("queryResults.next")}
              testId="nl2sql-result-pagination"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
