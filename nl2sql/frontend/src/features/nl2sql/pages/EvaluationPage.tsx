import { useState } from "react";
import { FileText, FlaskConical, GitCompare, ShieldCheck, Wand2 } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { DEFAULT_ANALYZE_SQL, sqlAnalyzePayload } from "../analysisState";
import { engineLabel } from "../labels";
import type {
  AnalyzeData,
  CompareData,
  CompareExecutionData,
  EvaluateData,
  Nl2SqlEngine,
  PreviewData,
  ReverseSqlData,
  SyntheticCasesData,
} from "../types";
import { formatElapsed } from "../useOperationTimer";

const DEFAULT_CASES = [
  { question: "請求金額を一覧で見たい", expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES" },
  { question: "顧客の地域を確認したい", expected_sql: "SELECT REGION FROM CUSTOMERS" },
];

export function EvaluationPage() {
  const [question, setQuestion] = useState("今月の請求金額が大きい取引先を表示して");
  const [engine, setEngine] = useState<Nl2SqlEngine>("auto");
  const [evaluation, setEvaluation] = useState<EvaluateData | null>(null);
  const [comparison, setComparison] = useState<CompareData | null>(null);
  const [synthetic, setSynthetic] = useState<SyntheticCasesData | null>(null);
  const [analysisSql, setAnalysisSql] = useState(DEFAULT_ANALYZE_SQL);
  const [analysisRowLimit, setAnalysisRowLimit] = useState(100);
  const [analysis, setAnalysis] = useState<AnalyzeData | null>(null);
  const [reverseSql, setReverseSql] = useState("SELECT TOTAL_AMOUNT FROM INVOICES");
  const [reverse, setReverse] = useState<ReverseSqlData | null>(null);
  const [loading, setLoading] = useState(false);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [reverseLoading, setReverseLoading] = useState(false);
  const [message, setMessage] = useState("");

  const runEvaluate = async () => {
    setLoading(true);
    setMessage("");
    try {
      const cases = synthetic?.cases ?? DEFAULT_CASES;
      setEvaluation(await apiPost<EvaluateData>("/api/nl2sql/evaluate", { cases, engine }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.run"));
    } finally {
      setLoading(false);
    }
  };

  const generateSynthetic = async () => {
    setLoading(true);
    setMessage("");
    try {
      setSynthetic(await apiPost<SyntheticCasesData>("/api/nl2sql/synthetic-cases"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.synthetic"));
    } finally {
      setLoading(false);
    }
  };

  const compare = async () => {
    setLoading(true);
    setMessage("");
    try {
      setComparison(await apiPost<CompareData>("/api/nl2sql/compare", { question, execute: true }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.compare"));
    } finally {
      setLoading(false);
    }
  };

  const analyzeSql = async () => {
    const sql = analysisSql.trim();
    if (!sql) return;
    setAnalysisLoading(true);
    setMessage("");
    try {
      setAnalysis(await apiPost<AnalyzeData>("/api/nl2sql/analyze", sqlAnalyzePayload(sql, analysisRowLimit)));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("evaluation.error.analyze"));
    } finally {
      setAnalysisLoading(false);
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
      setMessage(err instanceof Error ? err.message : t("evaluation.error.reverse"));
    } finally {
      setReverseLoading(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.evaluation")} subtitle={t("evaluation.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.runner.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-[1fr_14rem]">
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.question.label")}</span>
              <input
                value={question}
                onChange={(event) => setQuestion(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("nl2sql.engine.label")}</span>
              <select
                value={engine}
                onChange={(event) => setEngine(event.currentTarget.value as Nl2SqlEngine)}
                className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              >
                <option value="auto">{t("nl2sql.engine.auto")}</option>
                <option value="select_ai_agent">{t("nl2sql.engine.agent")}</option>
                <option value="select_ai">{t("nl2sql.engine.selectAi")}</option>
                <option value="enterprise_ai_direct">{t("nl2sql.engine.direct")}</option>
              </select>
            </label>
            <div className="flex flex-wrap gap-2 md:col-span-2">
              <Button type="button" loading={loading} onClick={() => void runEvaluate()}>
                <FlaskConical size={16} aria-hidden="true" />
                <span>{t("evaluation.action.run")}</span>
              </Button>
              <Button type="button" variant="secondary" loading={loading} onClick={() => void compare()}>
                <GitCompare size={16} aria-hidden="true" />
                <span>{t("evaluation.action.compare")}</span>
              </Button>
              <Button type="button" variant="secondary" loading={loading} onClick={() => void generateSynthetic()}>
                <Wand2 size={16} aria-hidden="true" />
                <span>{t("evaluation.action.synthetic")}</span>
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.analyze.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
            <div className="grid content-start gap-4">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("evaluation.analyze.sql")}</span>
                <textarea
                  value={analysisSql}
                  onChange={(event) => setAnalysisSql(event.currentTarget.value)}
                  rows={7}
                  className="min-h-44 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
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
                <Button
                  type="button"
                  loading={analysisLoading}
                  disabled={!analysisSql.trim()}
                  onClick={() => void analyzeSql()}
                >
                  <ShieldCheck size={16} aria-hidden="true" />
                  <span>{t("evaluation.action.analyze")}</span>
                </Button>
              </div>
            </div>

            {analysis && (
              <section className="grid content-start gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <div>
                  <p className="mb-2 text-xs font-medium text-slate-500">
                    {t("evaluation.analyze.result")}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge
                      variant={analysis.safety.is_safe ? "success" : "danger"}
                      label={analysis.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
                    />
                    <StatusBadge
                      variant={analysis.safety.is_select_only ? "success" : "danger"}
                      label={t("evaluation.analyze.selectOnly")}
                    />
                    <StatusBadge
                      variant="neutral"
                      label={t("evaluation.analyze.rowLimit", {
                        count: analysis.safety.row_limit_applied,
                      })}
                    />
                  </div>
                </div>
                <p className="text-slate-700">{analysis.explanation}</p>
                {analysis.safety.blocked_reason && (
                  <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-800">
                    {analysis.safety.blocked_reason}
                  </p>
                )}
                <TextList label={t("evaluation.analyze.warnings")} items={analysis.safety.warnings} />
                <SqlSnippet label={t("evaluation.analyze.executableSql")} sql={analysis.executable_sql} />
                {analysis.repaired_sql && (
                  <SqlSnippet label={t("evaluation.analyze.repairedSql")} sql={analysis.repaired_sql} />
                )}
                <TextList label={t("evaluation.analyze.recommendations")} items={analysis.recommendations} />
                <TextList label={t("evaluation.analyze.optimization")} items={analysis.optimization_hints} />
                <div className="grid gap-3 sm:grid-cols-2">
                  <CompactFact
                    label={t("evaluation.analyze.referencedTables")}
                    value={analysis.safety.referenced_tables.join(", ") || "-"}
                  />
                  <CompactFact
                    label={t("evaluation.analyze.referencedColumns")}
                    value={analysis.safety.referenced_columns.join(", ") || "-"}
                  />
                </div>
              </section>
            )}
          </CardContent>
        </Card>

        {evaluation && (
          <Card>
            <CardHeader>
              <CardTitle>{t("evaluation.result.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-3">
              <Metric label={t("evaluation.metric.cases")} value={String(evaluation.total_cases)} />
              <Metric label={t("evaluation.metric.executable")} value={`${Math.round(evaluation.executable_rate * 100)}%`} />
              <Metric label={t("evaluation.metric.selectOnly")} value={`${Math.round(evaluation.select_only_rate * 100)}%`} />
            </CardContent>
          </Card>
        )}

        {comparison && (
          <Card>
            <CardHeader>
              <CardTitle>{t("evaluation.compare.title")}</CardTitle>
              <p className="text-sm text-slate-600">{comparison.recommendation}</p>
            </CardHeader>
            <CardContent className="grid gap-3">
              <div className="grid gap-3 md:grid-cols-4">
                <Metric
                  label={t("evaluation.compare.engines")}
                  value={String(comparison.results.length)}
                />
                <Metric
                  label={t("evaluation.compare.safe")}
                  value={String(comparison.results.filter((item) => item.is_safe).length)}
                />
                <Metric
                  label={t("evaluation.compare.fastest")}
                  value={fastestCompareLabel(comparison.results)}
                />
                <Metric
                  label={t("evaluation.compare.errorRate")}
                  value={`${Math.round(comparison.error_rate * 100)}%`}
                />
              </div>
              {comparison.results.map((result) => {
                const execution = comparison.execution_results.find(
                  (item) => item.engine === result.engine
                );
                return (
                <div key={result.engine} className="grid gap-3 rounded-md border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge variant="info" label={engineLabel(result.engine)} />
                    <StatusBadge
                      variant={result.is_safe ? "success" : "danger"}
                      label={result.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
                    />
                    <StatusBadge
                      variant="neutral"
                      label={formatElapsed(result.timing?.elapsed_ms)}
                    />
                    <StatusBadge
                      variant="neutral"
                      label={`${t("evaluation.compare.rowLimit")} ${result.row_limit}`}
                    />
                    {result.fallback_reason && (
                      <StatusBadge variant="warning" label={t("evaluation.compare.fallback")} />
                    )}
                    {execution && (
                      <>
                        <StatusBadge
                          variant={execution.executed ? "success" : "danger"}
                          label={
                            execution.executed
                              ? t("evaluation.compare.executed")
                              : t("evaluation.compare.notExecuted")
                          }
                        />
                        <StatusBadge
                          variant="neutral"
                          label={t("evaluation.compare.executionRows", {
                            count: execution.row_count,
                          })}
                        />
                      </>
                    )}
                  </div>
                  <div className="grid gap-3 text-sm text-slate-700 lg:grid-cols-[minmax(0,1fr)_minmax(16rem,0.7fr)]">
                    <SqlSnippet
                      label={t("evaluation.compare.sql")}
                      sql={result.executable_sql || result.sql}
                    />
                    <div className="grid content-start gap-3">
                      <CompactFact
                        label={t("evaluation.compare.rewritten")}
                        value={result.rewritten_question || "-"}
                      />
                      <CompactFact
                        label={t("evaluation.compare.tables")}
                        value={result.safety?.referenced_tables.join(", ") || "-"}
                      />
                      <CompactFact
                        label={t("evaluation.compare.columns")}
                        value={result.safety?.referenced_columns.join(", ") || "-"}
                      />
                    </div>
                  </div>
                  {execution && <ExecutionPreview execution={execution} />}
                  <div className="grid gap-3 md:grid-cols-2">
                    <TextList
                      label={t("evaluation.compare.warnings")}
                      items={result.safety?.warnings ?? []}
                    />
                    <TextList
                      label={t("evaluation.compare.recommendations")}
                      items={result.recommendations}
                    />
                  </div>
                  {(result.fallback_reason || result.safety?.blocked_reason) && (
                    <div className="grid gap-1 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                      {result.fallback_reason && <p>{result.fallback_reason}</p>}
                      {result.safety?.blocked_reason && <p>{result.safety.blocked_reason}</p>}
                    </div>
                  )}
                </div>
                );
              })}
            </CardContent>
          </Card>
        )}

        {synthetic && (
          <Card>
            <CardHeader>
              <CardTitle>{t("evaluation.synthetic.title")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2">
              {synthetic.cases.map((item) => (
                <div key={item.question} className="rounded-md border border-slate-200 p-3 text-sm">
                  <p className="font-medium text-slate-900">{item.question}</p>
                  <code className="mt-1 block text-xs text-slate-600">{item.expected_sql}</code>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>{t("evaluation.reverse.title")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[1fr_minmax(18rem,0.8fr)]">
            <label className="grid gap-1 text-sm font-medium text-slate-800">
              <span>{t("evaluation.reverse.sql")}</span>
              <textarea
                value={reverseSql}
                onChange={(event) => setReverseSql(event.currentTarget.value)}
                rows={6}
                className="min-h-36 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              />
            </label>
            <div className="grid content-start gap-3">
              <Button
                type="button"
                variant="secondary"
                loading={reverseLoading}
                disabled={!reverseSql.trim()}
                onClick={() => void reverseExplain()}
              >
                <FileText size={16} aria-hidden="true" />
                <span>{t("evaluation.action.reverse")}</span>
              </Button>
              {reverse && (
                <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <div>
                    <p className="text-xs font-medium text-slate-500">
                      {t("evaluation.reverse.question")}
                    </p>
                    <p className="mt-1 font-semibold text-slate-900">{reverse.question}</p>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-slate-500">
                      {t("evaluation.reverse.explanation")}
                    </p>
                    <p className="mt-1 text-slate-700">{reverse.explanation}</p>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-slate-500">
                      {t("evaluation.reverse.tables")}
                    </p>
                    <p className="mt-1 font-mono text-xs text-slate-700">
                      {reverse.referenced_tables.join(", ") || "-"}
                    </p>
                  </div>
                </section>
              )}
            </div>
          </CardContent>
        </Card>
      </main>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function fastestCompareLabel(results: PreviewData[]): string {
  const safeResults = results.filter((item) => item.is_safe);
  if (safeResults.length === 0) return "-";
  const fastest = safeResults.reduce((current, candidate) => {
    const currentElapsed = current.timing?.elapsed_ms ?? Number.MAX_SAFE_INTEGER;
    const candidateElapsed = candidate.timing?.elapsed_ms ?? Number.MAX_SAFE_INTEGER;
    return candidateElapsed < currentElapsed ? candidate : current;
  });
  return `${engineLabel(fastest.engine)} / ${formatElapsed(fastest.timing?.elapsed_ms)}`;
}

function SqlSnippet({ label, sql }: { label: string; sql: string }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-500">{label}</p>
      <pre className="max-h-44 overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-50">
        <code>{sql || "-"}</code>
      </pre>
    </div>
  );
}

function ExecutionPreview({ execution }: { execution: CompareExecutionData }) {
  if (!execution.executed) {
    return (
      <section className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
        <p className="text-xs font-medium text-red-700">
          {t("evaluation.compare.executionError")}
        </p>
        <p className="mt-1">{execution.error_message || "-"}</p>
      </section>
    );
  }
  const results = execution.results;
  if (!results || results.rows.length === 0) {
    return (
      <section className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
        <p className="text-xs font-medium text-slate-500">
          {t("evaluation.compare.executionResult")}
        </p>
        <p className="mt-1">-</p>
      </section>
    );
  }
  const visibleRows = results.rows.slice(0, 2);
  return (
    <section className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
      <p className="text-xs font-medium text-slate-500">
        {t("evaluation.compare.executionResult")}
      </p>
      <div className="overflow-auto rounded-md border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-xs">
          <thead className="bg-slate-50">
            <tr>
              {results.columns.map((column) => (
                <th key={column} scope="col" className="whitespace-nowrap px-2 py-1 text-left font-semibold text-slate-700">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visibleRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {results.columns.map((column) => (
                  <td key={column} className="whitespace-nowrap px-2 py-1 text-slate-700">
                    {formatCompareCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatCompareCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function TextList({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-slate-500">{label}</p>
      {items.length > 0 ? (
        <ul className="grid gap-1 text-slate-700">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="text-slate-500">-</p>
      )}
    </div>
  );
}

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 bg-white px-3 py-2">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 break-words font-mono text-xs text-slate-700">{value}</p>
    </div>
  );
}
