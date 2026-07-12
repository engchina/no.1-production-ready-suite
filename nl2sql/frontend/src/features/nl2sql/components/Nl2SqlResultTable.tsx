import { Card, CardContent, CardHeader, CardTitle, DataTable } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import type { QueryResults } from "../types";

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function Nl2SqlResultTable({ results }: { results: QueryResults | null }) {
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
          <DataTable
            columns={results.columns.map((column) => ({
              key: column,
              header: column,
              render: (row: Record<string, unknown>) => formatCell(row[column]),
            }))}
            rows={results.rows}
            getRowKey={(_, index) => index}
            ariaLabel={t("nl2sql.results.title", { count: results.total })}
          />
        )}
      </CardContent>
    </Card>
  );
}
