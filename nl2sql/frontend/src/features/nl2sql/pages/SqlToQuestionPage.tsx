import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRightLeft, BookOpen, Database, FileText, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator, TimedLoadingState } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { FormStatus } from "@/components/ui/form-status";
import { FieldLabel } from "@/components/ui/required-field";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiGet, apiPost, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import { useRequestScope } from "@/lib/useRequestScope";
import { DbObjectPanelHeader, DbObjectStepIndicator } from "../components/DbObjectManagementShared";
import { QuestionText } from "../components/QuestionText";
import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { profileDisplayLabel } from "../profileDisplay";
import type {
  ProfileSummary,
  ProfileSummaryPage,
  ProfileUsageContext,
  ReverseSqlData,
  SchemaObjectDetail,
  SchemaObjectPage,
  SchemaTable,
} from "../types";

// タブではなく 1 画面スクロール + ステッパー。各工程セクションの共通カード枠。
const PANEL_CLASS = "grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm";

export function SqlToQuestionPage() {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [selectedProfile, setSelectedProfile] = useState<ProfileUsageContext | null>(null);
  const [schemaTables, setSchemaTables] = useState<SchemaTable[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [sql, setSql] = useState("");
  const [useGlossary, setUseGlossary] = useState(true);
  const [structureText, setStructureText] = useState("");
  const [reverse, setReverse] = useState<ReverseSqlData | null>(null);
  const [loading, setLoading] = useState(false);
  const [reverseLoading, setReverseLoading] = useState(false);
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
          profilePage.items.some((profile) => profile.id === current)
            ? current
            : (profilePage.items[0]?.id ?? "")
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
          page.items.slice(0, 8).map((item) =>
            apiGet<SchemaObjectDetail>(
              `/api/schema/objects/${encodeURIComponent(item.owner)}/${encodeURIComponent(item.object_name)}`,
              { signal, timeoutMs: API_TIMEOUT_MS.interactiveDetail }
            )
          )
        );
        if (signal.aborted || sequence !== detailSequence.current) return;
        const detailsByName = new Map(
          visibleDetails.map((detail) => [detail.table.table_name.toUpperCase(), detail.table])
        );
        setSelectedProfile(profile);
        setSchemaTables(
          page.items.map(
            (item) =>
              detailsByName.get(item.object_name.toUpperCase()) ?? schemaSummaryTable(item)
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

  useEffect(() => {
    void loadReferenceData();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, [abortAll, loadReferenceData]);

  const generateQuestion = async () => {
    const trimmedSql = sql.trim();
    if (!trimmedSql) return;
    setReverseLoading(true);
    setActionError("");
    try {
      const data = await apiPost<ReverseSqlData>("/api/nl2sql/reverse/deep", {
        sql: trimmedSql,
        profile_id: selectedProfileId || undefined,
        use_glossary: useGlossary,
      });
      setReverse(data);
      if (data.logical_structure) setStructureText(data.logical_structure);
    } catch (err) {
      setActionError(actionableError(err, t("sqlToQuestion.error.reverse")));
    } finally {
      setReverseLoading(false);
    }
  };

  const actionBusy = reverseLoading;
  const stepIndex = reverse ? 3 : structureText ? 1 : 0;

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
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={loadError ? { tone: "danger", message: loadError } : null}
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
                    setReverse(null);
                    setActionError("");
                  }}
                  rows={9}
                  required
                  aria-required="true"
                  className="min-h-56 min-w-0 resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
                  disabled={actionBusy}
                />
              </div>

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
                  loading={reverseLoading}
                  disabled={!sql.trim()}
                  onClick={() => void generateQuestion()}
                >
                  <ArrowRightLeft size={16} aria-hidden="true" />
                  <span>{t("sqlToQuestion.action.generate")}</span>
                </Button>
                <FormStatus tone="danger" message={actionError} className="sm:ml-auto" />
              </div>
              {actionBusy ? (
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
              <div className="min-w-0 rounded-md border border-border bg-card p-3">
                <p className="text-xs font-medium text-muted">{t("sqlToQuestion.result.question")}</p>
                <QuestionText
                  value={reverse.question}
                  variant="detail"
                  maxLines={3}
                  expandable
                  className="mt-1 font-semibold"
                />
              </div>
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
    <section className="grid content-start gap-3 rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <StatusBadge variant="neutral" label={profile ? profileDisplayLabel(profile) : "-"} />
        <span data-testid="sql-to-question-table-count">
          <StatusBadge variant="info" label={t("sqlToQuestion.schema.tableCount", { count: tables.length })} />
        </span>
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
