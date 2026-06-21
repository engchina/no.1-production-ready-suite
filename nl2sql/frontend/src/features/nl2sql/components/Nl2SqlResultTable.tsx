import { Card, CardContent, CardHeader, CardTitle } from "@engchina/production-ready-ui";

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
          <p className="rounded-md border border-dashed border-slate-300 p-6 text-sm text-slate-600">
            {t("nl2sql.results.empty")}
          </p>
        ) : (
          <div className="overflow-auto rounded-md border border-slate-200">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50">
                <tr>
                  {results.columns.map((column) => (
                    <th
                      key={column}
                      scope="col"
                      className="whitespace-nowrap px-3 py-2 text-left font-semibold text-slate-800"
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {results.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {results.columns.map((column) => (
                      <td key={column} className="whitespace-nowrap px-3 py-2 text-slate-700">
                        {formatCell(row[column])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
