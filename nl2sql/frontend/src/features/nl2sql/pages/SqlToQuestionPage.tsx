import { useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, BookOpen, Database, FileText, ShieldCheck } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import type { AnalyzeData, Nl2SqlProfile, ReverseSqlData, SchemaCatalog, SchemaTable } from "../types";

export function SqlToQuestionPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [sql, setSql] = useState("");
  const [useGlossary, setUseGlossary] = useState(true);
  const [structureText, setStructureText] = useState("");
  const [reverse, setReverse] = useState<ReverseSqlData | null>(null);
  const [loading, setLoading] = useState(false);
  const [analyzeLoading, setAnalyzeLoading] = useState(false);
  const [reverseLoading, setReverseLoading] = useState(false);
  const [message, setMessage] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedProfileId) ?? null,
    [profiles, selectedProfileId]
  );

  const schemaTables = useMemo(
    () => filteredSchemaTables(catalog, selectedProfile),
    [catalog, selectedProfile]
  );

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setMessage("");
      try {
        const [profileData, schemaData] = await Promise.all([
          apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
          apiGet<SchemaCatalog>("/api/schema/catalog"),
        ]);
        setProfiles(profileData);
        setCatalog(schemaData);
        setSelectedProfileId(profileData[0]?.id ?? "");
      } catch (err) {
        setMessage(err instanceof Error ? err.message : t("sqlToQuestion.error.load"));
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const analyzeStructure = async () => {
    const trimmedSql = sql.trim();
    if (!trimmedSql) return;
    setAnalyzeLoading(true);
    setMessage("");
    try {
      const analysis = await apiPost<AnalyzeData>("/api/nl2sql/analyze", {
        sql: trimmedSql,
        row_limit: selectedProfile?.default_row_limit ?? 100,
        use_llm: true,
      });
      setStructureText(analysisToStructureText(analysis));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("sqlToQuestion.error.analyze"));
    } finally {
      setAnalyzeLoading(false);
    }
  };

  const generateQuestion = async (deep: boolean) => {
    const trimmedSql = sql.trim();
    if (!trimmedSql) return;
    setReverseLoading(true);
    setMessage("");
    try {
      const data = await apiPost<ReverseSqlData>(
        deep ? "/api/nl2sql/reverse/deep" : "/api/nl2sql/reverse",
        {
          sql: trimmedSql,
          profile_id: selectedProfileId || undefined,
          use_glossary: useGlossary,
        }
      );
      setReverse(data);
      if (data.logical_structure) setStructureText(data.logical_structure);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("sqlToQuestion.error.reverse"));
    } finally {
      setReverseLoading(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.sqlToQuestion")} subtitle={t("sqlToQuestion.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ArrowRightLeft size={18} aria-hidden="true" />
              {t("sqlToQuestion.input.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(18rem,0.8fr)]">
            <div className="grid content-start gap-4">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("sqlToQuestion.profile.label")}</span>
                <select
                  value={selectedProfileId}
                  onChange={(event) => {
                    setSelectedProfileId(event.currentTarget.value);
                    setMessage("");
                  }}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  disabled={loading || profiles.length === 0}
                >
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("sqlToQuestion.sql.label")}</span>
                <textarea
                  value={sql}
                  onChange={(event) => setSql(event.currentTarget.value)}
                  rows={9}
                  className="min-h-56 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>

              <div className="flex flex-wrap gap-2">
                <label className="flex min-h-11 items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={useGlossary}
                    onChange={(event) => setUseGlossary(event.currentTarget.checked)}
                    className="h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("sqlToQuestion.useGlossary")}</span>
                </label>
                <Button
                  type="button"
                  variant="secondary"
                  loading={analyzeLoading}
                  disabled={!sql.trim()}
                  onClick={() => void analyzeStructure()}
                >
                  <ShieldCheck size={16} aria-hidden="true" />
                  <span>{t("sqlToQuestion.action.analyze")}</span>
                </Button>
                <Button
                  type="button"
                  loading={reverseLoading}
                  disabled={!sql.trim()}
                  onClick={() => void generateQuestion(false)}
                >
                  <ArrowRightLeft size={16} aria-hidden="true" />
                  <span>{t("sqlToQuestion.action.generate")}</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  loading={reverseLoading}
                  disabled={!sql.trim()}
                  onClick={() => void generateQuestion(true)}
                >
                  <ArrowRightLeft size={16} aria-hidden="true" />
                  <span>{t("sqlToQuestion.action.deep")}</span>
                </Button>
              </div>
            </div>

            <SchemaPreview
              loading={loading}
              profile={selectedProfile}
              tables={schemaTables}
            />
          </CardContent>
        </Card>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileText size={18} aria-hidden="true" />
                {t("sqlToQuestion.structure.title")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {structureText ? (
                <pre className="max-h-96 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-4 text-sm leading-6 text-slate-50">
                  <code>{structureText}</code>
                </pre>
              ) : (
                <EmptyState
                  title={t("sqlToQuestion.structure.emptyTitle")}
                  hint={t("sqlToQuestion.structure.emptyHint")}
                />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BookOpen size={18} aria-hidden="true" />
                {t("sqlToQuestion.result.title")}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {reverse ? (
                <section className="grid content-start gap-3 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge variant="neutral" label={reverse.source ?? "deterministic"} />
                    {useGlossary && <StatusBadge variant="info" label={t("sqlToQuestion.glossaryApplied")} />}
                  </div>
                  <CompactFact label={t("sqlToQuestion.result.question")} value={reverse.question} />
                  <CompactFact label={t("sqlToQuestion.result.explanation")} value={reverse.explanation} />
                  <CompactFact
                    label={t("sqlToQuestion.result.tables")}
                    value={reverse.referenced_tables.join(", ") || "-"}
                  />
                  <TextList label={t("sqlToQuestion.result.steps")} items={reverse.logical_steps ?? []} />
                  <TextList label={t("sqlToQuestion.result.warnings")} items={reverse.warnings ?? []} />
                </section>
              ) : (
                <EmptyState
                  title={t("sqlToQuestion.result.emptyTitle")}
                  hint={t("sqlToQuestion.result.emptyHint")}
                />
              )}
            </CardContent>
          </Card>
        </div>
      </main>
    </>
  );
}

function SchemaPreview({
  loading,
  profile,
  tables,
}: {
  loading: boolean;
  profile: Nl2SqlProfile | null;
  tables: SchemaTable[];
}) {
  if (loading) {
    return (
      <section className="grid min-h-56 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
        {t("sqlToQuestion.schema.loading")}
      </section>
    );
  }

  return (
    <section className="grid content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant="neutral" label={profile?.name ?? "-"} />
        <StatusBadge variant="info" label={t("sqlToQuestion.schema.tableCount", { count: tables.length })} />
      </div>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Database size={16} aria-hidden="true" />
        {t("sqlToQuestion.schema.title")}
      </h3>
      {tables.length === 0 ? (
        <p className="rounded-md border border-dashed border-slate-300 bg-white p-3 text-slate-500">
          {t("sqlToQuestion.schema.empty")}
        </p>
      ) : (
        <div className="grid max-h-96 gap-2 overflow-auto pr-1">
          {tables.map((table) => (
            <section key={table.table_name} className="rounded-md border border-slate-200 bg-white p-3">
              <p className="font-semibold text-slate-900">
                {table.logical_name || table.table_name}
                <span className="ml-2 font-mono text-xs text-slate-500">{table.table_name}</span>
              </p>
              <p className="mt-1 text-xs leading-5 text-slate-600">{table.comment || "-"}</p>
              <p className="mt-2 break-words font-mono text-xs leading-5 text-slate-700">
                {table.columns
                  .slice(0, 8)
                  .map((column) => column.logical_name || column.column_name)
                  .join(", ") || "-"}
              </p>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

function filteredSchemaTables(catalog: SchemaCatalog | null, profile: Nl2SqlProfile | null) {
  if (!catalog) return [];
  const allowed = new Set((profile?.allowed_tables ?? []).map((table) => table.toUpperCase()));
  return catalog.tables.filter(
    (table) => allowed.size === 0 || allowed.has(table.table_name.toUpperCase())
  );
}

function analysisToStructureText(analysis: AnalyzeData) {
  const lines = [
    "SQL 論理構造",
    `- Statement: ${analysis.statement_type || "SELECT"}`,
    `- Summary: ${analysis.structure_summary || analysis.explanation}`,
    `- 参照表: ${(analysis.object_names ?? analysis.safety.referenced_tables).join(", ") || "-"}`,
    `- 参照列: ${(analysis.column_names ?? analysis.safety.referenced_columns).join(", ") || "-"}`,
  ];
  const sections: Array<[string, string[] | undefined]> = [
    [t("sqlAnalysis.operations"), analysis.operations],
    [t("sqlAnalysis.filters"), analysis.conditions ?? analysis.filters],
    [t("sqlAnalysis.joins"), analysis.joins],
    [t("sqlAnalysis.groupBy"), analysis.group_by],
    [t("sqlAnalysis.orderBy"), analysis.order_by],
    [t("sqlAnalysis.aggregations"), analysis.aggregations],
    [t("sqlAnalysis.riskFindings"), analysis.risk_findings],
  ];
  for (const [label, items] of sections) {
    if (items?.length) lines.push(`- ${label}: ${items.join("; ")}`);
  }
  return lines.join("\n");
}

function TextList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-500">{label}</p>
      <ul className="grid gap-1">
        {items.map((item) => (
          <li key={item} className="rounded-md border border-slate-200 bg-white px-3 py-2 text-slate-700">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 bg-white p-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-slate-900">{value}</p>
    </div>
  );
}
