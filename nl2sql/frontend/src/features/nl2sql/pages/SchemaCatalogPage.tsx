import { useEffect, useMemo, useState } from "react";
import { Database, KeyRound, RefreshCw, Rows3, Search, Wand2 } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { formatSchemaCount, schemaTableRowLabel } from "../schemaDisplay";
import type { CommentSuggestionData, SchemaCatalog } from "../types";

export function SchemaCatalogPage() {
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [suggestions, setSuggestions] = useState<CommentSuggestionData | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  const load = async (refresh = false) => {
    setLoading(true);
    setMessage("");
    try {
      const data = refresh
        ? await apiPost<SchemaCatalog>("/api/schema/refresh")
        : await apiGet<SchemaCatalog>("/api/schema/catalog");
      setCatalog(data);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("schema.error.load"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const filteredTables = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!catalog) return [];
    if (!q) return catalog.tables;
    return catalog.tables.filter(
      (table) =>
        table.table_name.toLowerCase().includes(q) ||
        table.logical_name.toLowerCase().includes(q) ||
        table.columns.some(
          (column) =>
            column.column_name.toLowerCase().includes(q) ||
            column.logical_name.toLowerCase().includes(q)
        )
    );
  }, [catalog, query]);

  const suggest = async () => {
    setLoading(true);
    try {
      setSuggestions(await apiPost<CommentSuggestionData>("/api/nl2sql/comments/suggest"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("schema.error.comments"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.schema")}
        subtitle={t("schema.subtitle")}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm" loading={loading} onClick={() => void suggest()}>
              <Wand2 size={15} aria-hidden="true" />
              <span>{t("schema.action.suggestComments")}</span>
            </Button>
            <Button type="button" variant="primary" size="sm" loading={loading} onClick={() => void load(true)}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("schema.action.refresh")}</span>
            </Button>
          </div>
        }
      />

      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardContent className="pt-6">
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("schema.search.label")}</span>
              <span className="relative">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.currentTarget.value)}
                  className="min-h-11 w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={t("schema.search.placeholder")}
                />
              </span>
            </label>
          </CardContent>
        </Card>

        <div className="grid gap-4">
          {filteredTables.map((table) => (
            <Card key={table.table_name}>
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <CardTitle>{table.logical_name}</CardTitle>
                    <p className="mt-1 font-mono text-xs text-slate-500">{table.table_name}</p>
                    <p className="mt-1 text-sm text-slate-600">{table.comment}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge variant="info" label={t("schema.table.columns", { count: table.columns.length })} />
                    <StatusBadge variant="neutral" label={schemaTableRowLabel(table)} />
                    <StatusBadge
                      variant={table.constraints.length > 0 ? "success" : "neutral"}
                      label={t("schema.table.constraints", { count: table.constraints.length })}
                    />
                  </div>
                </div>
              </CardHeader>
              <CardContent className="grid gap-4">
                <div className="grid gap-3 md:grid-cols-3">
                  <SchemaFact icon={Database} label={t("schema.fact.owner")} value={table.owner || "-"} />
                  <SchemaFact icon={Rows3} label={t("schema.fact.rows")} value={formatSchemaCount(table.row_count)} />
                  <SchemaFact icon={KeyRound} label={t("schema.fact.constraints")} value={String(table.constraints.length)} />
                </div>
                {table.constraints.length > 0 && (
                  <section className="rounded-md border border-slate-200 bg-slate-50 p-3">
                    <p className="text-xs font-medium text-slate-500">{t("schema.constraints.title")}</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {table.constraints.map((constraint) => (
                        <span
                          key={constraint}
                          className="rounded-md border border-slate-200 bg-white px-2 py-1 font-mono text-xs text-slate-700"
                        >
                          {constraint}
                        </span>
                      ))}
                    </div>
                  </section>
                )}
                <div className="overflow-auto rounded-md border border-slate-200">
                  <table className="min-w-full divide-y divide-slate-200 text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="px-3 py-2 text-left">{t("schema.col.logical")}</th>
                        <th className="px-3 py-2 text-left">{t("schema.col.physical")}</th>
                        <th className="px-3 py-2 text-left">{t("schema.col.type")}</th>
                        <th className="px-3 py-2 text-left">{t("schema.col.sample")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {table.columns.map((column) => (
                        <tr key={column.column_name}>
                          <td className="px-3 py-2 font-medium">{column.logical_name}</td>
                          <td className="px-3 py-2 font-mono text-xs">{column.column_name}</td>
                          <td className="px-3 py-2">{column.data_type}</td>
                          <td className="px-3 py-2">
                            <span className="break-words font-mono text-xs text-slate-700">
                              {column.sample_values.join(", ") || "-"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {suggestions && (
          <Card>
            <CardHeader>
              <CardTitle>{t("schema.comments.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2">
              {suggestions.suggestions.slice(0, 12).map((item) => (
                <div key={item.object_name} className="rounded-md border border-slate-200 p-3 text-sm">
                  <span className="font-mono text-xs text-slate-500">{item.object_name}</span>
                  <p className="mt-1 text-slate-800">{item.suggested_comment}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        )}
      </main>
    </>
  );
}

function SchemaFact({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Database;
  label: string;
  value: string;
}) {
  return (
    <div className="flex min-w-0 items-start gap-2 rounded-md border border-slate-200 bg-white p-3">
      <Icon size={16} className="mt-0.5 shrink-0 text-slate-500" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-slate-500">{label}</p>
        <p className="mt-1 truncate text-sm font-semibold text-slate-900" title={value}>
          {value}
        </p>
      </div>
    </div>
  );
}
