import { useState } from "react";
import { FileSearch, Play, ShieldCheck, Wrench } from "lucide-react";

import { Button, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { FormStatus } from "@/components/ui/form-status";
import { apiPost } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { QueryResultsTable } from "../components/DbAdminShared";
import {
  DbObjectManagementStatusBar,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
} from "../components/DbObjectManagementShared";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import type { AnalyzeData, QueryResults, RepairData } from "../types";

const DEFAULT_ANALYZE_SQL = "";

// タブではなく 1 画面スクロール + ステッパー。各工程セクションの共通カード枠。
const PANEL_CLASS = "grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm";

export function SqlAnalysisPage() {
  const [analysisSql, setAnalysisSql] = useState(DEFAULT_ANALYZE_SQL);
  const [analysisUseLlm, setAnalysisUseLlm] = useState(false);
  const [analysis, setAnalysis] = useState<AnalyzeData | null>(null);
  const [execution, setExecution] = useState<QueryResults | null>(null);
  const [repairSql, setRepairSql] = useState("");
  const [repairErrorMessage, setRepairErrorMessage] = useState("");
  const [repair, setRepair] = useState<RepairData | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [executeLoading, setExecuteLoading] = useState(false);
  const [repairLoading, setRepairLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [executeError, setExecuteError] = useState("");
  const [repairActionError, setRepairActionError] = useState("");

  const invalidateAnalysis = () => {
    setAnalysis(null);
    setExecution(null);
    setAnalysisError("");
    setExecuteError("");
  };

  const invalidateRepair = () => {
    setRepair(null);
    setRepairActionError("");
  };

  const analyzeSql = async () => {
    const sql = analysisSql.trim();
    if (!sql) return;
    setAnalysisLoading(true);
    setAnalysisError("");
    setExecuteError("");
    setExecution(null);
    try {
      setAnalysis(
        await apiPost<AnalyzeData>("/api/nl2sql/analyze", {
          sql,
          use_llm: analysisUseLlm,
        })
      );
    } catch (err) {
      setAnalysisError(actionableError(err, t("sqlAnalysis.error.analyze")));
    } finally {
      setAnalysisLoading(false);
    }
  };

  const executeAnalyzedSql = async () => {
    const sql = analysis?.executable_sql.trim();
    if (!sql || !analysis?.safety.is_safe) return;
    setExecuteLoading(true);
    setExecuteError("");
    try {
      const data = await apiPost<QueryResults>("/api/nl2sql/execute", { sql });
      setExecution(data);
    } catch (err) {
      setExecuteError(actionableError(err, t("sqlAnalysis.error.execute")));
    } finally {
      setExecuteLoading(false);
    }
  };

  const repairOracleError = async () => {
    const sql = repairSql.trim();
    const errorMessage = repairErrorMessage.trim();
    if (!sql || !errorMessage) return;
    setRepairLoading(true);
    setRepairActionError("");
    try {
      setRepair(
        await apiPost<RepairData>("/api/nl2sql/repair", {
          sql,
          error_message: errorMessage,
        })
      );
    } catch (err) {
      setRepairActionError(actionableError(err, t("sqlAnalysis.error.repair")));
    } finally {
      setRepairLoading(false);
    }
  };

  const workflowState = resolveWorkflowState({
    analysisSql,
    repairSql,
    repairErrorMessage,
    analysis,
    execution,
    repair,
    analysisLoading,
    executeLoading,
    repairLoading,
  });
  const safety = repair ? repair.safety : analysis?.safety;
  const safetyState = safety
    ? safety.is_safe
      ? t("nl2sql.safety.safe")
      : t("nl2sql.safety.blocked")
    : t("sqlAnalysis.safety.unverified");
  const stepIndex = repair ? 3 : execution ? 1 : 0;

  return (
    <>
      <PageHeader title={t("nav.sqlAnalysis")} subtitle={t("sqlAnalysis.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        <DbObjectManagementStatusBar
          ariaLabel={t("sqlAnalysis.status.aria")}
          metrics={[
            { label: t("sqlAnalysis.metric.workflow"), value: workflowState },
            { label: t("sqlAnalysis.metric.safety"), value: safetyState },
            {
              label: t("sqlAnalysis.metric.resultRows"),
              value: execution ? formatNumber(execution.total) : "-",
              emphasis: Boolean(execution),
              testId: "sql-analysis-result-count",
            },
          ]}
        />

        <DbObjectStepIndicator
          steps={[
            t("sqlAnalysis.tabs.analysis"),
            t("sqlAnalysis.tabs.execution"),
            t("sqlAnalysis.tabs.repair"),
          ]}
          activeIndex={stepIndex}
          ariaLabel={t("sqlAnalysis.tabs.label")}
          dataTestId="sql-analysis-steps"
        />

        <section
          id="sql-analysis-panel-analysis"
          aria-labelledby="sql-analysis-input-heading"
          className={PANEL_CLASS}
        >
          <FixedSplitPane
            splitId="sql-analysis-workspace"
            preferredWidePane="right"
            left={
              <section className="grid min-w-0 content-start gap-4" aria-labelledby="sql-analysis-input-heading">
              <DbObjectPanelHeader
                headingId="sql-analysis-input-heading"
                icon={ShieldCheck}
                title={t("sqlAnalysis.analyze.title")}
                description={t("sqlAnalysis.analyze.hint")}
              />

              <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                <span>{t("sqlAnalysis.sql.label")}</span>
                <textarea
                  value={analysisSql}
                  onChange={(event) => {
                    setAnalysisSql(event.currentTarget.value);
                    invalidateAnalysis();
                  }}
                  disabled={analysisLoading || executeLoading}
                  rows={10}
                  className="min-h-64 min-w-0 resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
                />
              </label>

              <label className="flex min-h-11 items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={analysisUseLlm}
                  onChange={(event) => {
                    setAnalysisUseLlm(event.currentTarget.checked);
                    invalidateAnalysis();
                  }}
                  disabled={analysisLoading || executeLoading}
                  className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
                />
                <span>{t("sqlAnalysis.useLlm")}</span>
              </label>

              <div className="grid gap-2 border-t border-border pt-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <Button
                    type="button"
                    variant="primary"
                    size="lg"
                    className="w-full whitespace-nowrap sm:w-auto"
                    loading={analysisLoading}
                    disabled={!analysisSql.trim() || executeLoading}
                    onClick={() => void analyzeSql()}
                  >
                    <ShieldCheck size={16} aria-hidden="true" />
                    <span>{t("sqlAnalysis.action.analyze")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="lg"
                    className="w-full whitespace-nowrap sm:w-auto"
                    loading={executeLoading}
                    disabled={analysisLoading || !analysis?.safety.is_safe || !analysis.executable_sql}
                    onClick={() => void executeAnalyzedSql()}
                  >
                    <Play size={16} aria-hidden="true" />
                    <span>{t("sqlAnalysis.action.execute")}</span>
                  </Button>
                </div>
                <FormStatus tone="danger" message={analysisError} className="w-full" />
                <FormStatus tone="danger" message={executeError} className="w-full" />
              </div>
              </section>
            }
            right={
              <section className="grid min-w-0 content-start gap-4" aria-labelledby="sql-analysis-result-heading">
              <DbObjectPanelHeader
                headingId="sql-analysis-result-heading"
                icon={FileSearch}
                title={t("sqlAnalysis.result.title")}
                description={t("sqlAnalysis.result.hint")}
              />
              <AnalysisResult analysis={analysis} loading={analysisLoading} />
              </section>
            }
          />
        </section>

        <section
          id="sql-analysis-panel-execution"
          aria-labelledby="sql-analysis-execution-heading"
          className={PANEL_CLASS}
        >
          <section className="grid min-w-0 content-start gap-4" aria-labelledby="sql-analysis-execution-heading">
              <DbObjectPanelHeader
                headingId="sql-analysis-execution-heading"
                icon={Play}
                title={t("sqlAnalysis.execution.title")}
                description={t("sqlAnalysis.execution.hint")}
              />
              {execution ? (
                <div className="grid min-w-0 gap-3">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge
                      variant="info"
                      label={t("sqlAnalysis.execution.rows", { count: execution.total })}
                    />
                  </div>
                  <QueryResultsTable results={execution} />
                </div>
              ) : (
                <EmptyState
                  title={t("sqlAnalysis.execution.emptyTitle")}
                  hint={t("sqlAnalysis.execution.emptyHint")}
                />
              )}
          </section>
        </section>

        <section
          id="sql-analysis-panel-repair"
          aria-labelledby="sql-analysis-repair-heading"
          className={PANEL_CLASS}
        >
          <FixedSplitPane
            splitId="sql-analysis-repair"
            preferredWidePane="right"
            left={
              <section className="grid min-w-0 content-start gap-4" aria-labelledby="sql-analysis-repair-heading">
              <DbObjectPanelHeader
                headingId="sql-analysis-repair-heading"
                icon={Wrench}
                title={t("sqlAnalysis.repair.title")}
                description={t("sqlAnalysis.repair.hint")}
              />

              <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                <span>{t("sqlAnalysis.repair.sql")}</span>
                <textarea
                  value={repairSql}
                  onChange={(event) => {
                    setRepairSql(event.currentTarget.value);
                    invalidateRepair();
                  }}
                  disabled={repairLoading}
                  rows={6}
                  className="min-h-40 min-w-0 resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
                />
              </label>
              <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
                <span>{t("sqlAnalysis.repair.error")}</span>
                <textarea
                  value={repairErrorMessage}
                  onChange={(event) => {
                    setRepairErrorMessage(event.currentTarget.value);
                    invalidateRepair();
                  }}
                  disabled={repairLoading}
                  rows={4}
                  className="min-h-28 min-w-0 resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
                />
              </label>

              <div className="grid gap-2 border-t border-border pt-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <Button
                    type="button"
                    variant="primary"
                    size="lg"
                    className="w-full whitespace-nowrap sm:w-auto"
                    loading={repairLoading}
                    disabled={!repairSql.trim() || !repairErrorMessage.trim()}
                    onClick={() => void repairOracleError()}
                  >
                    <Wrench size={16} aria-hidden="true" />
                    <span>{t("sqlAnalysis.action.repair")}</span>
                  </Button>
                </div>
                <FormStatus tone="danger" message={repairActionError} className="w-full" />
              </div>
              </section>
            }
            right={
              <section className="grid min-w-0 content-start gap-4" aria-labelledby="sql-analysis-repair-result-heading">
              <DbObjectPanelHeader
                headingId="sql-analysis-repair-result-heading"
                icon={FileSearch}
                title={t("sqlAnalysis.repair.resultTitle")}
                description={t("sqlAnalysis.repair.resultHint")}
              />
              <RepairResult repair={repair} loading={repairLoading} />
              </section>
            }
          />
        </section>
      </main>
    </>
  );
}

function AnalysisResult({ analysis, loading }: { analysis: AnalyzeData | null; loading: boolean }) {
  if (loading) {
    return <ResultSkeleton ariaLabel={t("sqlAnalysis.result.loading")} />;
  }
  if (!analysis) {
    return (
      <EmptyState
        title={t("sqlAnalysis.result.emptyTitle")}
        hint={t("sqlAnalysis.result.emptyHint")}
      />
    );
  }

  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
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
          label={
            analysis.safety.row_limit_applied > 0
              ? t("sqlAnalysis.rowLimit", { count: analysis.safety.row_limit_applied })
              : t("sqlAnalysis.rowLimitUnlimited")
          }
        />
        <StatusBadge
          variant={analysis.llm_enhanced ? "success" : "neutral"}
          label={analysis.llm_enhanced ? "OCI Enterprise AI" : "deterministic"}
        />
        {analysis.risk_level && (
          <StatusBadge
            variant={
              analysis.risk_level === "high"
                ? "danger"
                : analysis.risk_level === "medium"
                  ? "warning"
                  : "success"
            }
            label={analysis.risk_level}
          />
        )}
      </div>
      <p className="text-foreground">{analysis.explanation}</p>
      {analysis.safety.blocked_reason && (
        <p className="rounded-md border border-danger/30 bg-danger-bg px-3 py-2 text-danger">
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
        <CompactFact label={t("sqlAnalysis.statementType")} value={analysis.statement_type || "-"} />
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

function RepairResult({ repair, loading }: { repair: RepairData | null; loading: boolean }) {
  if (loading) {
    return <ResultSkeleton ariaLabel={t("sqlAnalysis.repair.loading")} />;
  }
  if (!repair) {
    return (
      <EmptyState
        title={t("sqlAnalysis.repair.emptyTitle")}
        hint={t("sqlAnalysis.repair.emptyHint")}
      />
    );
  }
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant="info" label={repair.error_code || "-"} />
        <StatusBadge
          variant={repair.safety.is_safe ? "success" : "warning"}
          label={repair.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
        />
      </div>
      <p className="text-foreground">{repair.explanation}</p>
      <SqlSnippet label={t("sqlAnalysis.repair.repairedSql")} sql={repair.repaired_sql} />
      <TextList label={t("sqlAnalysis.recommendations")} items={repair.recommendations} />
    </section>
  );
}

function ResultSkeleton({ ariaLabel }: { ariaLabel: string }) {
  return (
    <div className="grid min-h-48 gap-3" aria-label={ariaLabel} data-testid="sql-analysis-result-skeleton">
      <div className="h-8 animate-pulse rounded-md bg-muted/30" aria-hidden="true" />
      <div className="h-20 animate-pulse rounded-md bg-muted/30" aria-hidden="true" />
      <div className="h-32 animate-pulse rounded-md bg-muted/30" aria-hidden="true" />
    </div>
  );
}

function TextList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs font-medium text-muted">{label}</p>
      <ul className="grid gap-1">
        {items.map((item) => (
          <li key={item} className="break-words rounded-md border border-border bg-card px-3 py-2 text-foreground">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SqlSnippet({ label, sql }: { label: string; sql: string }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs font-medium text-muted">{label}</p>
      <pre className="max-w-full overflow-auto rounded-md border border-border bg-code p-3 text-sm leading-6 text-code-fg">
        <code>{sql || "-"}</code>
      </pre>
    </div>
  );
}

function CompactFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-card p-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 break-words text-sm font-semibold text-foreground">{value}</p>
    </div>
  );
}

function actionableError(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : fallback;
  return `${message} ${t("sqlAnalysis.error.retryHint")}`;
}

function resolveWorkflowState({
  analysisSql,
  repairSql,
  repairErrorMessage,
  analysis,
  execution,
  repair,
  analysisLoading,
  executeLoading,
  repairLoading,
}: {
  analysisSql: string;
  repairSql: string;
  repairErrorMessage: string;
  analysis: AnalyzeData | null;
  execution: QueryResults | null;
  repair: RepairData | null;
  analysisLoading: boolean;
  executeLoading: boolean;
  repairLoading: boolean;
}) {
  // 1 画面化に伴い activeView 非依存の単一状態解決へ。進行が進んだものを優先表示する。
  if (analysisLoading) return t("sqlAnalysis.workflow.analyzing");
  if (executeLoading) return t("sqlAnalysis.workflow.executing");
  if (repairLoading) return t("sqlAnalysis.workflow.repairing");
  if (repair) return t("sqlAnalysis.workflow.repaired");
  if (execution) return t("sqlAnalysis.workflow.executed");
  if (analysis) return t("sqlAnalysis.workflow.analyzed");
  if (repairSql.trim() && repairErrorMessage.trim()) return t("sqlAnalysis.workflow.repairReady");
  if (analysisSql.trim()) return t("sqlAnalysis.workflow.analysisReady");
  return t("sqlAnalysis.workflow.analysisWaiting");
}
