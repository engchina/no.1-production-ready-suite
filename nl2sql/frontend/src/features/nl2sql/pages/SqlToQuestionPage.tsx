import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, BookOpen, Database, FileText, RefreshCw, ShieldCheck } from "lucide-react";

import { Button, EmptyState, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { Banner } from "@/components/ui/banner";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet, apiPost } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  DbObjectManagementStatusBar,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
} from "../components/DbObjectManagementShared";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import type { AnalyzeData, Nl2SqlProfile, ReverseSqlData, SchemaCatalog, SchemaTable } from "../types";

type ReverseMode = "standard" | "deep" | "";

// タブではなく 1 画面スクロール + ステッパー。各工程セクションの共通カード枠。
const PANEL_CLASS = "grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm";

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
  const [reverseMode, setReverseMode] = useState<ReverseMode>("");
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedProfileId) ?? null,
    [profiles, selectedProfileId]
  );

  const schemaTables = useMemo(
    () => filteredSchemaTables(catalog, selectedProfile),
    [catalog, selectedProfile]
  );

  const loadReferenceData = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [profileData, schemaData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setProfiles(profileData);
      setCatalog(schemaData);
      setSelectedProfileId((current) =>
        profileData.some((profile) => profile.id === current) ? current : (profileData[0]?.id ?? "")
      );
    } catch (err) {
      setLoadError(actionableError(err, t("sqlToQuestion.error.load")));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadReferenceData();
  }, [loadReferenceData]);

  const analyzeStructure = async () => {
    const trimmedSql = sql.trim();
    if (!trimmedSql) return;
    setAnalyzeLoading(true);
    setActionError("");
    try {
      const analysis = await apiPost<AnalyzeData>("/api/nl2sql/analyze", {
        sql: trimmedSql,
        use_llm: true,
      });
      setStructureText(analysisToStructureText(analysis));
    } catch (err) {
      setActionError(actionableError(err, t("sqlToQuestion.error.analyze")));
    } finally {
      setAnalyzeLoading(false);
    }
  };

  const generateQuestion = async (deep: boolean) => {
    const trimmedSql = sql.trim();
    if (!trimmedSql) return;
    setReverseMode(deep ? "deep" : "standard");
    setActionError("");
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
      setActionError(actionableError(err, t("sqlToQuestion.error.reverse")));
    } finally {
      setReverseMode("");
    }
  };

  const workflowState = reverse
    ? t("sqlToQuestion.workflow.generated")
    : structureText
      ? t("sqlToQuestion.workflow.analyzed")
      : sql.trim()
        ? t("sqlToQuestion.workflow.ready")
        : t("sqlToQuestion.workflow.waiting");
  const actionBusy = analyzeLoading || Boolean(reverseMode);
  const stepIndex = reverse ? 2 : structureText ? 1 : 0;

  return (
    <>
      <PageHeader title={t("nav.sqlToQuestion")} subtitle={t("sqlToQuestion.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        {loadError && (
          <Banner
            severity="danger"
            action={
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={loading}
                onClick={() => void loadReferenceData()}
              >
                <RefreshCw size={15} aria-hidden="true" />
                <span>{t("sqlToQuestion.action.reload")}</span>
              </Button>
            }
          >
            {loadError}
          </Banner>
        )}

        <DbObjectManagementStatusBar
          ariaLabel={t("sqlToQuestion.status.aria")}
          metrics={[
            {
              label: t("sqlToQuestion.metric.profile"),
              value: selectedProfile?.name ?? t("sqlToQuestion.metric.profileEmpty"),
            },
            {
              label: t("sqlToQuestion.metric.tables"),
              value: formatNumber(schemaTables.length),
              emphasis: true,
              testId: "sql-to-question-table-count",
            },
            { label: t("sqlToQuestion.metric.workflow"), value: workflowState },
          ]}
          actions={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading}
              onClick={() => void loadReferenceData()}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("sqlToQuestion.action.reload")}</span>
            </Button>
          }
        />

        <DbObjectStepIndicator
          steps={[
            t("sqlToQuestion.tabs.input"),
            t("sqlToQuestion.tabs.structure"),
            t("sqlToQuestion.tabs.result"),
          ]}
          activeIndex={stepIndex}
          ariaLabel={t("sqlToQuestion.tabs.label")}
          dataTestId="sql-to-question-steps"
        />

        <section
          id="sql-to-question-panel-input"
          aria-labelledby="sql-to-question-input-heading"
          className={PANEL_CLASS}
        >
          <FixedSplitPane
            splitId="sql-to-question-input"
            preferredWidePane="left"
            left={
              <section className="grid min-w-0 content-start gap-4" aria-labelledby="sql-to-question-input-heading">
              <DbObjectPanelHeader
                headingId="sql-to-question-input-heading"
                icon={ArrowRightLeft}
                title={t("sqlToQuestion.input.title")}
                description={t("sqlToQuestion.input.hint")}
              />

              <label className="grid gap-1 text-sm font-medium text-foreground">
                <span>{t("sqlToQuestion.profile.label")}</span>
                <select
                  value={selectedProfileId}
                  onChange={(event) => {
                    setSelectedProfileId(event.currentTarget.value);
                    setReverse(null);
                    setActionError("");
                  }}
                  className="min-h-11 min-w-0 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
                  disabled={loading || actionBusy || profiles.length === 0}
                >
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="grid gap-1 text-sm font-medium text-foreground">
                <span>{t("sqlToQuestion.sql.label")}</span>
                <textarea
                  value={sql}
                  onChange={(event) => {
                    setSql(event.currentTarget.value);
                    setStructureText("");
                    setReverse(null);
                    setActionError("");
                  }}
                  rows={9}
                  className="min-h-56 min-w-0 resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
                  disabled={actionBusy}
                />
              </label>

              <label className="flex min-h-11 items-center gap-2 rounded-md border border-border px-3 py-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={useGlossary}
                  onChange={(event) => {
                    setUseGlossary(event.currentTarget.checked);
                    setReverse(null);
                    setActionError("");
                  }}
                  disabled={actionBusy}
                  className="h-4 w-4 rounded border-border text-primary focus:ring-ring/40"
                />
                <span>{t("sqlToQuestion.useGlossary")}</span>
              </label>

              <div className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row sm:flex-wrap sm:items-center">
                <Button
                  type="button"
                  variant="primary"
                  size="lg"
                  className="w-full whitespace-nowrap sm:w-auto"
                  loading={reverseMode === "standard"}
                  disabled={!sql.trim() || analyzeLoading || reverseMode === "deep"}
                  onClick={() => void generateQuestion(false)}
                >
                  <ArrowRightLeft size={16} aria-hidden="true" />
                  <span>{t("sqlToQuestion.action.generate")}</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  className="w-full whitespace-nowrap sm:w-auto"
                  loading={analyzeLoading}
                  disabled={!sql.trim() || Boolean(reverseMode)}
                  onClick={() => void analyzeStructure()}
                >
                  <ShieldCheck size={16} aria-hidden="true" />
                  <span>{t("sqlToQuestion.action.analyze")}</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="lg"
                  className="w-full whitespace-nowrap sm:w-auto"
                  loading={reverseMode === "deep"}
                  disabled={!sql.trim() || analyzeLoading || reverseMode === "standard"}
                  onClick={() => void generateQuestion(true)}
                >
                  <ArrowRightLeft size={16} aria-hidden="true" />
                  <span>{t("sqlToQuestion.action.deep")}</span>
                </Button>
                <FormStatus tone="danger" message={actionError} className="sm:ml-auto" />
              </div>
              </section>
            }
            right={
              <SchemaPreview
                loading={loading}
                profile={selectedProfile}
                tables={schemaTables}
              />
            }
          />
        </section>

        <section
          id="sql-to-question-panel-structure"
          aria-labelledby="sql-to-question-structure-heading"
          className={PANEL_CLASS}
        >
          <DbObjectPanelHeader
            headingId="sql-to-question-structure-heading"
            icon={FileText}
            title={t("sqlToQuestion.structure.title")}
            description={t("sqlToQuestion.structure.hint")}
          />
          {structureText ? (
            <pre className="max-h-96 overflow-auto rounded-md border border-border bg-code p-4 text-sm leading-6 text-code-fg">
              <code>{structureText}</code>
            </pre>
          ) : (
            <EmptyState
              title={t("sqlToQuestion.structure.emptyTitle")}
              hint={t("sqlToQuestion.structure.emptyHint")}
            />
          )}
        </section>

        <section
          id="sql-to-question-panel-result"
          aria-labelledby="sql-to-question-result-heading"
          className={PANEL_CLASS}
        >
          <DbObjectPanelHeader
            headingId="sql-to-question-result-heading"
            icon={BookOpen}
            title={t("sqlToQuestion.result.title")}
            description={t("sqlToQuestion.result.hint")}
          />
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
        </section>
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
      <section
        className="grid min-h-56 content-start gap-3 rounded-md border border-border bg-background p-4"
        aria-label={t("sqlToQuestion.schema.loading")}
        aria-busy="true"
        data-testid="sql-to-question-schema-skeleton"
      >
        <Skeleton className="h-5 w-40" aria-hidden="true" />
        <Skeleton className="h-16 w-full" aria-hidden="true" />
        <Skeleton className="h-16 w-full" aria-hidden="true" />
        <span className="sr-only">{t("sqlToQuestion.schema.loading")}</span>
      </section>
    );
  }

  return (
    <section className="grid content-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant="neutral" label={profile?.name ?? "-"} />
        <StatusBadge variant="info" label={t("sqlToQuestion.schema.tableCount", { count: tables.length })} />
      </div>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Database size={16} aria-hidden="true" />
        {t("sqlToQuestion.schema.title")}
      </h3>
      {tables.length === 0 ? (
        <EmptyState
          title={t("sqlToQuestion.schema.emptyTitle")}
          hint={t("sqlToQuestion.schema.empty")}
        />
      ) : (
        <div className="grid max-h-96 gap-2 overflow-auto pr-1">
          {tables.map((table) => (
            <section key={table.table_name} className="rounded-md border border-border bg-card p-3">
              <p className="font-semibold text-foreground">
                {table.logical_name || table.table_name}
                <span className="ml-2 font-mono text-xs text-muted">{table.table_name}</span>
              </p>
              <p className="mt-1 text-xs leading-5 text-muted">{table.comment || "-"}</p>
              <p className="mt-2 break-words font-mono text-xs leading-5 text-foreground">
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

function actionableError(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : fallback;
  return `${message} ${t("sqlToQuestion.error.retryHint")}`;
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
      <p className="mb-1 text-xs font-medium text-muted">{label}</p>
      <ul className="grid gap-1">
        {items.map((item) => (
          <li key={item} className="rounded-md border border-border bg-card px-3 py-2 text-foreground">
            {item}
          </li>
        ))}
      </ul>
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
