import { useWorkspaceState, useWorkspaceRevalidation, useWorkspaceActivation, useWorkspaceActive, WorkspaceResultNotice } from "@/components/WorkspaceState";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRightLeft, BookOpen, Database, FileText, RefreshCw } from "lucide-react";

import {
  Button,
  EmptyState,
  StatusBadge,
  PageHeader,
  FormStatus,
  Skeleton,
  PageBody,
} from "@engchina/production-ready-ui";

import { ProcessingIndicator, TimedLoadingState } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { FieldLabel } from "@/components/ui/required-field";
import { apiGet, apiPost, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  DbObjectStepIndicator,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { QuestionText } from "../components/QuestionText";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { profileDisplayLabel } from "../profileDisplay";
import type {
  Nl2SqlLogicalStructureItem,
  ProfileSummary,
  ProfileSummaryPage,
  ProfileUsageContext,
  ReverseSqlData,
  SchemaObjectDetail,
  SchemaObjectPage,
  SchemaTable,
} from "../types";

type SqlToQuestionPanel = "input" | "structure";
type GeneratedSql = { sql: string; explanation: string; warnings: string[]; at: string };
// 質問の再利用と旧結果の識別に必要な項目だけを同一タブに保存する。
const EMPTY_QUESTION_SNAPSHOT = {
  question: "", logicalStructure: "", sourceSql: "", warnings: [] as string[], generatedAt: "",
};

export function SqlToQuestionPage() {
  useWorkspaceRevalidation();
  const [savedPanel, setActivePanel] = useWorkspaceState<SqlToQuestionPanel | "result">("activePanel", "input");
  // 旧3タブ版で保存した質問候補の選択を、草稿を維持して統合先へ移行する。
  const activePanel: SqlToQuestionPanel = savedPanel === "input" ? "input" : "structure";
  useEffect(() => {
    if (savedPanel === "result") setActivePanel("structure");
  }, [savedPanel, setActivePanel]);
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<ProfileUsageContext | null>(null);
  const [schemaTables, setSchemaTables] = useState<SchemaTable[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useWorkspaceState("selectedProfileId", "");
  const [sql, setSql] = useWorkspaceState(`sql:${selectedProfileId}`, "");
  const [structureText, setStructureText] = useWorkspaceState(`structureText:${selectedProfileId}`, "");
  const [questionSnapshot, setQuestionSnapshot] = useWorkspaceState(`questionSnapshot:${selectedProfileId}`, EMPTY_QUESTION_SNAPSHOT);
  const reverse = questionSnapshot.generatedAt && questionSnapshot.sourceSql === sql ? questionSnapshot : null;
  const generatedThisVisit = useRef<typeof EMPTY_QUESTION_SNAPSHOT | null>(null);
  const [editingStructure, setEditingStructure] = useState(false);
  const [sqlGenerationLoading, setSqlGenerationLoading] = useState(false);
  const [sqlGenerationError, setSqlGenerationError] = useState("");
  const [regenerated, setRegenerated] = useState<{ sql: string; explanation: string; warnings: string[]; signature: string; at: string } | null>(null);
  const [questionSql, setQuestionSql] = useState<GeneratedSql | null>(null);
  const [questionSqlLoading, setQuestionSqlLoading] = useState(false);
  const [questionSqlError, setQuestionSqlError] = useState("");
  const questionSqlInFlight = useRef(false);
  const structureSignature = JSON.stringify([selectedProfileId, structureText, false]);
  const workspaceActive = useWorkspaceActive();
  const activeRef = useRef(workspaceActive);
  activeRef.current = workspaceActive;
  const focusStructure = useRef(false);
  useEffect(() => {
    if (activePanel === "structure" && focusStructure.current && workspaceActive) {
      focusStructure.current = false;
      document.getElementById("sql-to-question-tab-structure")?.focus();
    }
  }, [activePanel, workspaceActive]);
  const [structureItems, setStructureItems] = useState<Nl2SqlLogicalStructureItem[]>([]);
  useEffect(() => { setQuestionSql(null); setQuestionSqlError(""); }, [selectedProfileId, sql, reverse]);
  useEffect(() => { setStructureItems([]); setEditingStructure(false); setRegenerated(null); setSqlGenerationError(""); }, [selectedProfileId]);
  const [loading, setLoading] = useState(false);
  const [reverseLoading, setReverseLoading] = useState(false);
  const reverseInFlight = useRef(false);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [referenceRefreshVersion, setReferenceRefreshVersion] = useState(0);
  const loadSequence = useRef(0);
  const detailSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const loadReferenceData = useCallback(async () => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    try {
      await runScopedRequest(async (signal) => {
        const profilePage = await apiGet<ProfileSummaryPage>(
          "/api/nl2sql/profiles/search?limit=100",
          { signal, timeoutMs: API_TIMEOUT_MS.interactiveList }
        );
        if (signal.aborted || sequence !== loadSequence.current) return;
        setProfiles(profilePage.items);
        setSelectedProfileId((current) =>
          current || profilePage.items[0]?.id || ""
        );
        setReferenceRefreshVersion((current) => current + 1);
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setLoadError(actionableError(err, t("sqlToQuestion.error.load")));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [abortAll, runScopedRequest]);

  useEffect(() => {
    if (!selectedProfileId) {
      setSelectedProfile(null);
      setSchemaTables([]);
      return;
    }
    const sequence = detailSequence.current + 1;
    detailSequence.current = sequence;
    void runScopedRequest(async (signal) => {
      setLoading(true);
      setLoadError("");
      try {
        const params = new URLSearchParams({ limit: "100", profile_id: selectedProfileId });
        const [profile, page] = await Promise.all([
          apiGet<ProfileUsageContext>(
            `/api/nl2sql/profiles/${encodeURIComponent(selectedProfileId)}/usage-context`,
            {
              signal,
              timeoutMs: API_TIMEOUT_MS.interactiveList,
            }
          ),
          apiGet<SchemaObjectPage>(`/api/schema/objects?${params}`, {
            signal,
            timeoutMs: API_TIMEOUT_MS.interactiveList,
          }),
        ]);
        const visibleDetails = await Promise.all(
          page.items.slice(0, 8).map(async (item) => {
            const detail = await apiGet<SchemaObjectDetail>(
              `/api/schema/objects/${encodeURIComponent(item.owner)}/${encodeURIComponent(item.object_name)}`,
              { signal, timeoutMs: API_TIMEOUT_MS.interactiveDetail }
            );
            return [JSON.stringify([item.owner, item.object_name]), detail.table] as const;
          })
        );
        if (signal.aborted || sequence !== detailSequence.current) return;
        const detailsByName = new Map(visibleDetails);
        setSelectedProfile(profile);
        setSchemaTables(
          page.items.map(
            (item) =>
              detailsByName.get(JSON.stringify([item.owner, item.object_name])) ?? schemaSummaryTable(item)
          )
        );
      } catch (error) {
        if (!isAbortError(error) && sequence === detailSequence.current) {
          setLoadError(actionableError(error, t("sqlToQuestion.error.load")));
        }
      } finally {
        if (sequence === detailSequence.current) setLoading(false);
      }
    });
  }, [referenceRefreshVersion, runScopedRequest, selectedProfileId]);

  const referenceVisited = useRef(false);
  useWorkspaceActivation(() => {
    if (referenceVisited.current) void loadReferenceData();
    referenceVisited.current = true;
  });
  useEffect(() => {
    void loadReferenceData();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, [abortAll, loadReferenceData]);

  const generateQuestion = async () => {
    const trimmedSql = sql.trim();
    if (!trimmedSql || actionBusy || reverseInFlight.current) return;
    reverseInFlight.current = true;
    setReverseLoading(true);
    focusStructure.current = activeRef.current;
    setActivePanel("structure");
    setActionError("");
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiPost<ReverseSqlData>("/api/nl2sql/reverse/deep", {
          sql: trimmedSql,
          profile_id: selectedProfileId || undefined,
          use_glossary: false,
        }, { signal, timeoutMs: API_TIMEOUT_MS.longRunningJob });
        if (signal.aborted) return;
        const snapshot = {
          question: data.question,
          logicalStructure: data.logical_structure || "",
          sourceSql: sql,
          warnings: data.warnings ?? [],
          generatedAt: new Date().toISOString(),
        };
        generatedThisVisit.current = snapshot;
        setQuestionSnapshot(snapshot);
        setStructureText(data.logical_structure || "");
        setRegenerated(null);
        setSqlGenerationError("");
        setStructureItems(data.logical_structure_items ?? []);
        focusStructure.current = activeRef.current;
        setActivePanel("structure");
      });
    } catch (err) {
      if (isAbortError(err)) return;
      setActionError(actionableError(err, t("sqlToQuestion.error.reverse")));
      if (activeRef.current) setActivePanel("input");
    } finally {
      reverseInFlight.current = false;
      setReverseLoading(false);
    }
  };

  const generateSql = async () => {
    if (!structureText.trim() || actionBusy) return;
    setSqlGenerationLoading(true);
    setSqlGenerationError("");
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiPost<{ sql: string; explanation: string; warnings: string[] }>(
          "/api/nl2sql/reverse/sql",
          { logical_structure: structureText, profile_id: selectedProfileId || undefined, use_glossary: false },
          { signal, timeoutMs: API_TIMEOUT_MS.longRunningJob }
        );
        if (signal.aborted) return;
        setRegenerated({ ...data, signature: structureSignature, at: new Date().toLocaleString("ja-JP") });
      });
    } catch (err) {
      if (!isAbortError(err)) setSqlGenerationError(actionableError(err, t("sqlToQuestion.error.regenerate")));
    } finally {
      setSqlGenerationLoading(false);
    }
  };

  const generateQuestionSql = async () => {
    const question = reverse?.question.trim();
    if (!question || actionBusy || questionSqlInFlight.current) return;
    questionSqlInFlight.current = true;
    setQuestionSqlLoading(true);
    setQuestionSqlError("");
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiPost<Omit<GeneratedSql, "at">>(
          "/api/nl2sql/reverse/question-sql",
          { question, profile_id: selectedProfileId || undefined },
          { signal, timeoutMs: API_TIMEOUT_MS.longRunningJob }
        );
        if (signal.aborted) return;
        setQuestionSql({ ...data, at: new Date().toLocaleString("ja-JP") });
      });
    } catch (err) {
      if (!isAbortError(err)) setQuestionSqlError(actionableError(err, t("sqlToQuestion.error.questionSql")));
    } finally {
      questionSqlInFlight.current = false;
      setQuestionSqlLoading(false);
    }
  };

  const actionBusy = reverseLoading || sqlGenerationLoading || questionSqlLoading;
  const panels = useMemo(
    () =>
      [
        { id: "input", label: t("sqlToQuestion.tabs.input"), icon: ArrowRightLeft },
        { id: "structure", label: t("sqlToQuestion.tabs.structure"), icon: FileText },
      ] satisfies Array<DbObjectTab<SqlToQuestionPanel>>,
    []
  );
  const activePanelIndex = Math.max(
    0,
    panels.findIndex((panel) => panel.id === activePanel)
  );
  const renderStepIndicator = (panel: SqlToQuestionPanel) =>
    activePanel === panel ? (
      <DbObjectStepIndicator
        steps={panels.map((item) => item.label)}
        activeIndex={activePanelIndex}
        ariaLabel={t("sqlToQuestion.tabs.label")}
        dataTestId="sql-to-question-steps"
      />
    ) : null;

  return (
    <>
      <PageHeader
        title={t("nav.sqlToQuestion")}
        subtitle={t("sqlToQuestion.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: loadReferenceData,
            loading,
          },
        ]}
      />
      <PageBody className="grid gap-4">
        <PageNotice
          notice={loadError ? { tone: "danger", message: loadError } : null}
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading}
              onClick={() => void loadReferenceData()} icon={RefreshCw}>
              <span>{t("sqlToQuestion.action.reload")}</span>
            </Button>
          }
        />

        <DbObjectManagementTabs
          activeView={activePanel}
          tabs={panels}
          idPrefix="sql-to-question"
          ariaLabel={t("sqlToQuestion.tabs.label")}
          onViewChange={setActivePanel}
        />

        <DbObjectManagementPanelShell
          id="sql-to-question-panel-input"
          labelledBy="sql-to-question-tab-input"
          idPrefix="sql-to-question"
          ariaLabel={t("sqlToQuestion.input.title")}
          className={activePanel === "input" ? "" : "hidden"}
          topContent={renderStepIndicator("input")}
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

              <label className="grid gap-1 text-sm font-medium text-fg">
                <span>{t("sqlToQuestion.profile.label")}</span>
                <select
                  value={selectedProfileId}
                  onChange={(event) => {
                    setSelectedProfileId(event.currentTarget.value);
                    setActionError("");
                    setActivePanel("input");
                  }}
                  className="min-h-11 min-w-0 rounded-md border border-border-control bg-surface px-3 py-2 focus:border-focus-ring focus:outline-none focus:ring-2 focus:ring-focus-ring disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-fg-disabled"
                  disabled={loading || actionBusy || profiles.length === 0}
                >
                  {profiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profileDisplayLabel(profile)}
                    </option>
                  ))}
                </select>
              </label>

              <div className="grid gap-1">
                <FieldLabel
                  htmlFor="sql-to-question-sql-input"
                  label={t("sqlToQuestion.sql.label")}
                  required
                />
                <textarea
                  id="sql-to-question-sql-input"
                  value={sql}
                  onChange={(event) => {
                    setSql(event.currentTarget.value);
                    setStructureText("");
                    setEditingStructure(false);
                    setStructureItems([]);
                    setRegenerated(null);
                    setQuestionSnapshot(EMPTY_QUESTION_SNAPSHOT);
                    setActionError("");
                    setActivePanel("input");
                  }}
                  rows={9}
                  required
                  aria-required="true"
                  className="min-h-56 min-w-0 resize-y rounded-md border border-border-control bg-surface px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-focus-ring focus:ring-2 focus:ring-focus-ring disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-fg-disabled"
                  disabled={actionBusy}
                />
              </div>

              <div className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row sm:flex-wrap sm:items-center">
                <Button
                  type="button"
                  variant="primary"
                  size="lg"
                  className="w-full whitespace-nowrap sm:w-auto"
                  loading={reverseLoading}
                  disabled={!sql.trim() || actionBusy || loading || !!loadError || !selectedProfile}
                  onClick={() => void generateQuestion()} icon={ArrowRightLeft}>
                  <span>{t("sqlToQuestion.action.generate")}</span>
                </Button>
                <FormStatus tone="danger" message={actionError} className="sm:ml-auto" />
              </div>
              {reverseLoading ? (
                <ProcessingIndicator
                  active
                  label={t("sqlToQuestion.action.generate")}
                  operationKey="reverse-deep"
                  placement="action"
                  testId="sql-to-question-processing"
                  activityIcon="none"
                />
              ) : null}
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
        </DbObjectManagementPanelShell>

        <DbObjectManagementPanelShell
          id="sql-to-question-panel-structure"
          labelledBy="sql-to-question-tab-structure"
          idPrefix="sql-to-question"
          ariaLabel={t("sqlToQuestion.tabs.structure")}
          className={activePanel === "structure" ? "" : "hidden"}
          topContent={renderStepIndicator("structure")}
        >
          <div aria-busy={reverseLoading} className="grid min-w-0 gap-4" data-testid="sql-to-question-analysis-content">
          {reverseLoading && (
            <TimedLoadingState label={t("sqlToQuestion.analysis.loading")} placement="result" framed={false} testId="sql-to-question-analysis-loading">
              <Skeleton className="h-5 w-40" aria-hidden="true" />
              <Skeleton className="h-8 w-full" aria-hidden="true" />
              <Skeleton className="h-80 w-full" aria-hidden="true" />
              <Skeleton className="h-11 w-56 max-w-full" aria-hidden="true" />
              <Skeleton className="h-5 w-32" aria-hidden="true" />
              <Skeleton className="h-28 w-full" aria-hidden="true" />
            </TimedLoadingState>
          )}
          <div hidden={reverseLoading} className="grid min-w-0 gap-4">
          <DbObjectPanelHeader
            headingId="sql-to-question-structure-heading"
            icon={FileText}
            title={t("sqlToQuestion.structure.title")}
            description={t("sqlToQuestion.structure.hint")}
          />
          {structureText || reverse || structureItems.length > 0 || regenerated || editingStructure ? (
            <section className="grid min-w-0 gap-4">
              <TextList label={t("sqlToQuestion.result.warnings")} items={reverse?.warnings ?? []} />
              <div className="grid min-w-0 gap-1">
                <FieldLabel htmlFor="sql-to-question-structure-input" label={t("sqlToQuestion.structure.editor")} required />
                <textarea
                  id="sql-to-question-structure-input"
                  value={structureText}
                  onChange={(event) => { setEditingStructure(true); setStructureText(event.currentTarget.value); setStructureItems([]); setSqlGenerationError(""); }}
                  rows={16}
                  required
                  aria-required="true"
                  disabled={actionBusy}
                  className="min-h-64 min-w-0 w-full resize-y rounded-md border border-border-control bg-surface px-3 py-2 font-mono text-sm leading-6 focus:outline-none focus:ring-2 focus:ring-focus-ring"
                />
              </div>
              {structureItems.length > 0 && <LogicalStructureList items={structureItems} />}
              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button type="button" size="lg" loading={sqlGenerationLoading} disabled={actionBusy || !structureText.trim() || loading || !!loadError || !selectedProfile} onClick={() => void generateSql()} icon={ArrowRightLeft}>
                  {t("sqlToQuestion.actions.regenerateSql")}
                </Button>
              </div>
              <FormStatus tone="danger" message={sqlGenerationError} />
              {sqlGenerationLoading && <ProcessingIndicator active label={t("sqlToQuestion.actions.regenerateSql")} operationKey="structure-to-sql" placement="action" activityIcon="none" />}
              {regenerated && (
                <section className="grid min-w-0 gap-2" aria-label={t("sqlToQuestion.regenerated.title")}>
                  <h3 className="font-semibold">{t("sqlToQuestion.regenerated.title")}</h3>
                  <p className="text-sm text-fg-muted">{t("sqlToQuestion.regenerated.at", { at: regenerated.at })}</p>
                  {regenerated.signature !== structureSignature && <FormStatus tone="warning" message={t("sqlToQuestion.regenerated.stale")} />}
                  <pre data-surface="code" className="max-h-96 overflow-auto rounded-md border border-border bg-surface p-4 text-sm leading-6 text-fg"><code>{regenerated.sql}</code></pre>
                  <p className="text-sm">{regenerated.explanation}</p>
                  <TextList label={t("sqlToQuestion.result.warnings")} items={regenerated.warnings ?? []} />
                </section>
              )}
            </section>
          ) : (
            <EmptyState title={t("sqlToQuestion.structure.emptyTitle")} hint={t("sqlToQuestion.structure.emptyHint")} />
          )}
          <section
            id="sql-to-question-results"
            aria-labelledby="sql-to-question-result-heading"
            className="grid min-w-0 gap-4 border-t border-border pt-4"
          >
            <DbObjectPanelHeader
              headingId="sql-to-question-result-heading"
              icon={BookOpen}
              title={t("sqlToQuestion.result.title")}
              description={t("sqlToQuestion.result.hint")}
            />
            {reverse ? (
              <section className="grid content-start gap-3 text-sm">
                {structureText !== reverse.logicalStructure && <FormStatus tone="warning" message={t("sqlToQuestion.result.staleStructure")} />}
                <WorkspaceResultNotice result={reverse} inputSignature={JSON.stringify([selectedProfileId, sql, false])} finishedAt={reverse.generatedAt} restored={reverse !== generatedThisVisit.current} />
                <div className="min-w-0 rounded-md border border-border bg-surface p-3">
                  <p className="text-xs font-medium text-fg-muted">{t("sqlToQuestion.result.question")}</p>
                  <QuestionText
                    value={reverse.question}
                    variant="detail"
                    maxLines={0}
                    className="mt-1 font-semibold"
                  />
                </div>
                <div className="flex flex-wrap items-center gap-[8px] border-t border-border pt-4">
                  <Button type="button" size="lg" loading={questionSqlLoading} disabled={actionBusy || !reverse.question.trim() || loading || !!loadError || !selectedProfile} onClick={() => void generateQuestionSql()} icon={ArrowRightLeft}>
                    {t("sqlToQuestion.actions.questionSql")}
                  </Button>
                </div>
                <FormStatus tone="danger" message={questionSqlError} />
                {questionSqlLoading && <ProcessingIndicator active label={t("sqlToQuestion.actions.questionSql")} operationKey="question-to-sql" placement="action" activityIcon="none" />}
                {questionSql && (
                  <section className="grid min-w-0 gap-2" aria-label={t("sqlToQuestion.questionSql.title")}>
                    <h3 className="font-semibold">{t("sqlToQuestion.questionSql.title")}</h3>
                    <p className="text-sm text-fg-muted">{t("sqlToQuestion.regenerated.at", { at: questionSql.at })}</p>
                    <pre data-surface="code" className="max-h-96 overflow-auto rounded-md border border-border bg-surface p-4 text-sm leading-6 text-fg"><code>{questionSql.sql}</code></pre>
                    <p className="text-sm">{questionSql.explanation}</p>
                    <TextList label={t("sqlToQuestion.result.warnings")} items={questionSql.warnings ?? []} />
                  </section>
                )}
              </section>
            ) : (
              <EmptyState
                title={t("sqlToQuestion.result.emptyTitle")}
                hint={t("sqlToQuestion.result.emptyHint")}
              />
            )}
          </section>
          </div>
          </div>
        </DbObjectManagementPanelShell>
      </PageBody>
    </>
  );
}

function SchemaPreview({
  loading,
  profile,
  tables,
}: {
  loading: boolean;
  profile: ProfileUsageContext | null;
  tables: SchemaTable[];
}) {
  if (loading) {
    return (
      <TimedLoadingState
        label={t("sqlToQuestion.schema.loading")}
        operationKey="sql-to-question-schema"
        placement="panel"
        className="min-h-56 content-start"
        testId="sql-to-question-schema-skeleton"
      >
        <Skeleton className="h-5 w-40" aria-hidden="true" />
        <Skeleton className="h-16 w-full" aria-hidden="true" />
        <Skeleton className="h-16 w-full" aria-hidden="true" />
      </TimedLoadingState>
    );
  }

  return (
    <section className="grid content-start gap-3 rounded-md border border-border bg-surface-sunken p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant="neutral" label={profile ? profileDisplayLabel(profile) : "-"} />
        <span data-testid="sql-to-question-table-count">
          <StatusBadge variant="info" label={t("sqlToQuestion.schema.tableCount", { count: tables.length })} />
        </span>
      </div>
      <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
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
            <section key={JSON.stringify([table.owner, table.table_name])} className="rounded-md border border-border bg-surface p-3">
              <p className="font-semibold text-fg">
                {table.logical_name || table.table_name}
                <span className="ml-2 font-mono text-xs text-fg-muted">{table.qualified_name || `${table.owner}.${table.table_name}`}</span>
              </p>
              <p className="mt-1 text-xs leading-5 text-fg-muted">{table.comment || "-"}</p>
              <p className="mt-2 break-words font-sans text-xs leading-5 text-fg">
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

function schemaSummaryTable(item: SchemaObjectPage["items"][number]): SchemaTable {
  return {
    table_name: item.object_name,
    qualified_name: `${item.owner}.${item.object_name}`,
    logical_name: item.logical_name,
    owner: item.owner,
    table_type: item.object_type,
    comment: item.comment,
    row_count: item.row_count,
    columns: [],
    constraints: [],
  };
}

// SQL 論理構造の項目 kind -> 見出し文言。未知の kind は技術詳細だけを見出し無しで出す。
const STRUCTURE_LABEL_KEYS: Record<string, string> = {
  summary: "sqlToQuestion.structure.summary",
  statement: "sqlToQuestion.structure.statement",
  operations: "sqlToQuestion.structure.operations",
  filters: "sqlToQuestion.structure.filters",
  joins: "sqlToQuestion.structure.joins",
  group_by: "sqlToQuestion.structure.groupBy",
  order_by: "sqlToQuestion.structure.orderBy",
  aggregations: "sqlToQuestion.structure.aggregations",
};

/** SQL 論理構造を「見出し + 業務者向け説明 + 技術詳細」で併記する。 */
function LogicalStructureList({ items }: { items: Nl2SqlLogicalStructureItem[] }) {
  return (
    <dl
      className="grid gap-2"
      aria-label={t("sqlToQuestion.structure.listAria")}
      data-testid="sql-to-question-structure-list"
    >
      {items.map((item, index) => {
        const labelKey = STRUCTURE_LABEL_KEYS[item.kind ?? ""];
        return (
          <div
            key={`${index}-${item.kind ?? ""}`}
            className="grid gap-1 rounded-md border border-border bg-surface px-3 py-2"
            data-structure-kind={item.kind || undefined}
          >
            <dt className="text-xs font-medium text-fg-muted">
              {labelKey ? t(labelKey) : (item.kind ?? "")}
            </dt>
            <dd className="grid min-w-0 gap-1">
              <span className="min-w-0 text-sm leading-6 text-fg [overflow-wrap:anywhere]">
                {item.business}
              </span>
              {item.technical && (
                <span className="flex min-w-0 items-start gap-1.5 text-xs leading-5 text-fg-muted">
                  <span className="sr-only">{t("nl2sql.logicalSteps.technicalSrLabel")}</span>
                  <span
                    className="mt-0.5 shrink-0 rounded bg-surface-hover px-1.5 font-medium"
                    aria-hidden="true"
                  >
                    {t("nl2sql.logicalSteps.technicalLabel")}
                  </span>
                  <code className="min-w-0 font-mono [overflow-wrap:anywhere]">
                    {item.technical}
                  </code>
                </span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function TextList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-fg-muted">{label}</p>
      <ul className="grid gap-1">
        {items.map((item, index) => (
          <li
            // 同一文が並ぶことがあるため index を key に含める。
            key={`${index}-${item}`}
            className="flex min-w-0 items-start gap-2 rounded-md border border-border bg-surface px-3 py-2 text-fg"
          >
            <span className="min-w-0 [overflow-wrap:anywhere]">{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
