import { useState } from "react";
import { ArrowRightLeft, Play, ShieldCheck, Wrench } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { DEFAULT_ANALYZE_SQL, sqlAnalyzePayload } from "../analysisState";
import { Nl2SqlResultTable } from "../components/Nl2SqlResultTable";
import type { AnalyzeData, QueryResults, RepairData, ReverseSqlData } from "../types";

export function SqlAnalysisPage() {
  const [analysisSql, setAnalysisSql] = useState(DEFAULT_ANALYZE_SQL);
  const [analysisRowLimit, setAnalysisRowLimit] = useState(100);
  const [analysisUseLlm, setAnalysisUseLlm] = useState(false);
  const [analysis, setAnalysis] = useState<AnalyzeData | null>(null);
  const [execution, setExecution] = useState<QueryResults | null>(null);
  const [repairSql, setRepairSql] = useState("SELECT BAD_COLUMN FROM INVOICES");
  const [repairError, setRepairError] = useState('ORA-00904: "BAD_COLUMN": invalid identifier');
  const [repair, setRepair] = useState<RepairData | null>(null);
  const [reverseSql, setReverseSql] = useState("SELECT TOTAL_AMOUNT FROM INVOICES");
  const [reverse, setReverse] = useState<ReverseSqlData | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [executeLoading, setExecuteLoading] = useState(false);
  const [repairLoading, setRepairLoading] = useState(false);
  const [reverseLoading, setReverseLoading] = useState(false);
  const [message, setMessage] = useState("");

  const analyzeSql = async () => {
    const sql = analysisSql.trim();
    if (!sql) return;
    setAnalysisLoading(true);
    setMessage("");
    setExecution(null);
    try {
      setAnalysis(
        await apiPost<AnalyzeData>("/api/nl2sql/analyze", {
          ...sqlAnalyzePayload(sql, analysisRowLimit),
          use_llm: analysisUseLlm,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("sqlAnalysis.error.analyze"));
    } finally {
      setAnalysisLoading(false);
    }
  };

  const executeAnalyzedSql = async () => {
    const sql = analysis?.executable_sql.trim();
    if (!sql || !analysis?.safety.is_safe) return;
    setExecuteLoading(true);
    setMessage("");
    try {
      setExecution(
        await apiPost<QueryResults>("/api/nl2sql/execute", {
          sql,
          row_limit: analysisRowLimit,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("sqlAnalysis.error.execute"));
    } finally {
      setExecuteLoading(false);
    }
  };

  const repairOracleError = async () => {
    const sql = repairSql.trim();
    const errorMessage = repairError.trim();
    if (!sql || !errorMessage) return;
    setRepairLoading(true);
    setMessage("");
    try {
      setRepair(
        await apiPost<RepairData>("/api/nl2sql/repair", {
          sql,
          error_message: errorMessage,
          row_limit: analysisRowLimit,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("sqlAnalysis.error.repair"));
    } finally {
      setRepairLoading(false);
    }
  };

  const reverseExplain = async () => {
    const sql = reverseSql.trim();
    if (!sql) return;
    setReverseLoading(true);
    setMessage("");
    try {
      setReverse(await apiPost<ReverseSqlData>("/api/nl2sql/reverse", { sql }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("sqlAnalysis.error.reverse"));
    } finally {
      setReverseLoading(false);
    }
  };

  const reverseDeepExplain = async () => {
    const sql = reverseSql.trim();
    if (!sql) return;
    setReverseLoading(true);
    setMessage("");
    try {
      setReverse(await apiPost<ReverseSqlData>("/api/nl2sql/reverse/deep", { sql }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("sqlAnalysis.error.reverse"));
    } finally {
      setReverseLoading(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.sqlAnalysis")} subtitle={t("sqlAnalysis.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck size={18} aria-hidden="true" />
              {t("sqlAnalysis.analyze.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
            <div className="grid content-start gap-4">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("sqlAnalysis.sql.label")}</span>
                <textarea
                  value={analysisSql}
                  onChange={(event) => setAnalysisSql(event.currentTarget.value)}
                  rows={8}
                  className="min-h-48 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <div className="grid gap-3 sm:grid-cols-[minmax(9rem,12rem)_auto] sm:items-end">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("nl2sql.rowLimit.label")}</span>
                  <input
                    type="number"
                    min={1}
                    max={5000}
                    value={analysisRowLimit}
                    onChange={(event) => setAnalysisRowLimit(Number(event.currentTarget.value))}
                    className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  <label className="flex min-h-11 items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-800">
                    <input
                      type="checkbox"
                      checked={analysisUseLlm}
                      onChange={(event) => setAnalysisUseLlm(event.currentTarget.checked)}
                      className="h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                    />
                    <span>{t("sqlAnalysis.useLlm")}</span>
                  </label>
                  <Button
                    type="button"
                    loading={analysisLoading}
                    disabled={!analysisSql.trim()}
                    onClick={() => void analyzeSql()}
                  >
                    <ShieldCheck size={16} aria-hidden="true" />
                    <span>{t("sqlAnalysis.action.analyze")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    loading={executeLoading}
                    disabled={!analysis?.safety.is_safe || !analysis.executable_sql}
                    onClick={() => void executeAnalyzedSql()}
                  >
                    <Play size={16} aria-hidden="true" />
                    <span>{t("sqlAnalysis.action.execute")}</span>
                  </Button>
                </div>
              </div>
            </div>

            <AnalysisResult analysis={analysis} />
          </CardContent>
        </Card>

        <Nl2SqlResultTable results={execution} />

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Wrench size={18} aria-hidden="true" />
              {t("sqlAnalysis.repair.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
            <div className="grid content-start gap-4">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("sqlAnalysis.repair.sql")}</span>
                <textarea
                  value={repairSql}
                  onChange={(event) => setRepairSql(event.currentTarget.value)}
                  rows={5}
                  className="min-h-32 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("sqlAnalysis.repair.error")}</span>
                <textarea
                  value={repairError}
                  onChange={(event) => setRepairError(event.currentTarget.value)}
                  rows={3}
                  className="min-h-24 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <Button
                type="button"
                loading={repairLoading}
                disabled={!repairSql.trim() || !repairError.trim()}
                onClick={() => void repairOracleError()}
              >
                <Wrench size={16} aria-hidden="true" />
                <span>{t("sqlAnalysis.action.repair")}</span>
              </Button>
            </div>

            {repair ? (
              <section className="grid content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <StatusBadge variant="info" label={repair.error_code || "-"} />
                  <StatusBadge
                    variant={repair.safety.is_safe ? "success" : "warning"}
                    label={repair.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
                  />
                </div>
                <p className="text-slate-700">{repair.explanation}</p>
                <SqlSnippet label={t("sqlAnalysis.repair.repairedSql")} sql={repair.repaired_sql} />
                <TextList label={t("sqlAnalysis.recommendations")} items={repair.recommendations} />
              </section>
            ) : (
              <section className="grid min-h-48 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                {t("sqlAnalysis.repair.empty")}
              </section>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ArrowRightLeft size={18} aria-hidden="true" />
              {t("sqlAnalysis.reverse.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
            <div className="grid content-start gap-4">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("sqlAnalysis.reverse.sql")}</span>
                <textarea
                  value={reverseSql}
                  onChange={(event) => setReverseSql(event.currentTarget.value)}
                  rows={6}
                  className="min-h-36 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  loading={reverseLoading}
                  disabled={!reverseSql.trim()}
                  onClick={() => void reverseExplain()}
                >
                  <ArrowRightLeft size={16} aria-hidden="true" />
                  <span>{t("sqlAnalysis.action.reverse")}</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  loading={reverseLoading}
                  disabled={!reverseSql.trim()}
                  onClick={() => void reverseDeepExplain()}
                >
                  <ArrowRightLeft size={16} aria-hidden="true" />
                  <span>{t("sqlAnalysis.action.reverseDeep")}</span>
                </Button>
              </div>
            </div>

            {reverse && (
              <section className="grid content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <div className="flex flex-wrap gap-2">
                  <StatusBadge variant="neutral" label={reverse.source ?? "deterministic"} />
                </div>
                <CompactFact label={t("sqlAnalysis.reverse.question")} value={reverse.question} />
                <CompactFact label={t("sqlAnalysis.reverse.explanation")} value={reverse.explanation} />
                <CompactFact
                  label={t("sqlAnalysis.reverse.tables")}
                  value={reverse.referenced_tables.join(", ") || "-"}
                />
                <TextList label={t("sqlAnalysis.reverse.steps")} items={reverse.logical_steps ?? []} />
                <TextList label={t("sqlAnalysis.warnings")} items={reverse.warnings ?? []} />
              </section>
            )}
          </CardContent>
        </Card>
      </main>
    </>
  );
}

function AnalysisResult({ analysis }: { analysis: AnalyzeData | null }) {
  if (!analysis) {
    return (
      <section className="grid min-h-48 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
        {t("sqlAnalysis.empty")}
      </section>
    );
  }

  return (
    <section className="grid content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge
          variant={analysis.safety.is_safe ? "success" : "danger"}
          label={analysis.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
        />
        <StatusBadge
          variant={analysis.safety.is_select_only ? "success" : "danger"}
          label={t("sqlAnalysis.selectOnly")}
        />
        <StatusBadge
          variant="neutral"
          label={t("sqlAnalysis.rowLimit", { count: analysis.safety.row_limit_applied })}
        />
        <StatusBadge variant={analysis.llm_enhanced ? "success" : "neutral"} label={analysis.llm_enhanced ? "OCI Enterprise AI" : "deterministic"} />
        {analysis.risk_level && <StatusBadge variant={analysis.risk_level === "high" ? "danger" : analysis.risk_level === "medium" ? "warning" : "success"} label={analysis.risk_level} />}
      </div>
      <p className="text-slate-700">{analysis.explanation}</p>
      {analysis.safety.blocked_reason && (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800">
          {analysis.safety.blocked_reason}
        </p>
      )}
      <TextList label={t("sqlAnalysis.warnings")} items={analysis.safety.warnings} />
      <SqlSnippet label={t("sqlAnalysis.executableSql")} sql={analysis.executable_sql} />
      {analysis.repaired_sql && <SqlSnippet label={t("sqlAnalysis.repairedSql")} sql={analysis.repaired_sql} />}
      {analysis.structure_summary && (
        <CompactFact label={t("sqlAnalysis.structure")} value={analysis.structure_summary} />
      )}
      <TextList label={t("sqlAnalysis.recommendations")} items={analysis.recommendations} />
      <TextList label={t("sqlAnalysis.optimization")} items={analysis.optimization_hints} />
      <TextList label={t("sqlAnalysis.riskFindings")} items={analysis.risk_findings ?? []} />
      <TextList label={t("sqlAnalysis.repairCandidates")} items={analysis.repair_candidates ?? []} />
      <TextList label={t("sqlAnalysis.operations")} items={analysis.operations ?? []} />
      <TextList label={t("sqlAnalysis.filters")} items={analysis.conditions ?? analysis.filters ?? []} />
      <TextList label={t("sqlAnalysis.groupBy")} items={analysis.group_by ?? []} />
      <TextList label={t("sqlAnalysis.orderBy")} items={analysis.order_by ?? []} />
      <TextList label={t("sqlAnalysis.joins")} items={analysis.joins ?? []} />
      <TextList label={t("sqlAnalysis.aggregations")} items={analysis.aggregations ?? []} />
      <div className="grid gap-3 sm:grid-cols-2">
        <CompactFact
          label={t("sqlAnalysis.statementType")}
          value={analysis.statement_type || "-"}
        />
        <CompactFact
          label={t("sqlAnalysis.referencedTables")}
          value={(analysis.object_names ?? analysis.safety.referenced_tables).join(", ") || "-"}
        />
        <CompactFact
          label={t("sqlAnalysis.referencedColumns")}
          value={(analysis.column_names ?? analysis.safety.referenced_columns).join(", ") || "-"}
        />
      </div>
      <TextList label={t("sqlAnalysis.warnings")} items={analysis.llm_warnings ?? []} />
    </section>
  );
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

function SqlSnippet({ label, sql }: { label: string; sql: string }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-500">{label}</p>
      <pre className="overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
        <code>{sql || "-"}</code>
      </pre>
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
