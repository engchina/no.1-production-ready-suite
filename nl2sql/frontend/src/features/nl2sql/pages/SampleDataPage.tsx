import { useEffect, useMemo, useRef, useState } from "react";
import { Database, FileSpreadsheet, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StatusBadge, toast } from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { ProcessingIndicator } from "@/components/ProcessingState";
import { PageNotice } from "@/components/page-notice";
import { apiGet, apiPost, isAbortError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import { DbAdminExecutionResult, ExecutionConfirmationField } from "../components/DbAdminShared";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import { useSchemaRefreshJob, useStartSchemaRefresh } from "../incrementalQueries";
import type { SampleDataInfo, SampleDataMutationData, SchemaRefreshJob } from "../types";

type SampleStep = "tables" | "views" | "data" | "all";
type SampleAction = "import" | "delete";

const SAMPLE_DATA_ID = "sample-data";
const SAMPLE_STEPS: SampleStep[] = ["all", "tables", "views", "data"];

function sampleStepLabel(step: SampleStep) {
  return t(`dataTools.sample.step.${step}`);
}

function joinSql(statements: string[]) {
  return statements.join(";\n\n");
}

function schemaRefreshRequiresFull(job: SchemaRefreshJob | null) {
  if (!job) return false;
  return (
    Boolean(job.requires_full_refresh) ||
    job.error_code === "schema_refresh_full_required" ||
    job.error_code === "schema_refresh_target_unresolved"
  );
}

function schemaRefreshRequiredMessage(reasonCode = "") {
  if (reasonCode === "schema_refresh_target_unresolved") {
    return t("dataMgmt.schemaJob.targetUnresolved");
  }
  return t("dataMgmt.schemaJob.fullRequired");
}

function schemaRefreshErrorMessage(job: SchemaRefreshJob) {
  if (schemaRefreshRequiresFull(job)) {
    return schemaRefreshRequiredMessage(job.error_code);
  }
  return job.error_code
    ? `${t("dataMgmt.schemaJob.error")} (${job.error_code})`
    : t("dataMgmt.schemaJob.error");
}

function schemaRefreshProcessingLabel(job: SchemaRefreshJob | null, fullLabel: string) {
  return job?.mode === "targeted" ? t("common.processing.schemaDeltaSyncing") : fullLabel;
}

function SampleObjectSummary({ sampleInfo }: { sampleInfo: SampleDataInfo | null }) {
  return (
    <section className="grid gap-2 rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-foreground">{t("dataTools.sample.objects")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <span className="sr-only" data-testid="sample-data-object-count">
            {formatNumber(sampleInfo?.objects.length ?? 0)}
          </span>
          <StatusBadge
            variant="neutral"
            label={`${t("dataTools.sample.metric.objects")} ${formatNumber(sampleInfo?.objects.length ?? 0)}`}
          />
          <span className="sr-only" data-testid="sample-data-imported-count">
            {formatNumber(sampleInfo?.imported_objects.length ?? 0)}
          </span>
          <StatusBadge
            variant="success"
            label={`${t("dataTools.sample.metric.imported")} ${formatNumber(sampleInfo?.imported_objects.length ?? 0)}`}
          />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {(sampleInfo?.objects ?? []).map((objectName) => (
          <StatusBadge
            key={objectName}
            variant={sampleInfo?.imported_objects.includes(objectName) ? "success" : "neutral"}
            label={objectName}
          />
        ))}
      </div>
    </section>
  );
}

function SampleSqlPreview({ sql }: { sql: string }) {
  return (
    <section className="grid gap-2">
      <div>
        <p className="font-semibold text-foreground">{t("dataTools.sample.sqlPreview")}</p>
        <p className="mt-1 text-sm text-muted">{t("dataTools.sample.sqlPreviewHint")}</p>
      </div>
      <pre className="max-h-80 overflow-auto rounded-md border border-border bg-code p-3 text-sm leading-6 text-code-fg">
        <code>{sql || "-"}</code>
      </pre>
    </section>
  );
}

export function SampleDataPage() {
  const [sampleInfo, setSampleInfo] = useState<SampleDataInfo | null>(null);
  const [sampleStep, setSampleStep] = useState<SampleStep>("all");
  const [activeAction, setActiveAction] = useState<SampleAction>("import");
  const [sampleConfirmation, setSampleConfirmation] = useState("");
  const [sampleResult, setSampleResult] = useState<SampleDataMutationData | null>(null);
  const [schemaRefreshJobId, setSchemaRefreshJobId] = useState("");
  const [schemaRefreshError, setSchemaRefreshError] = useState("");
  const [schemaRefreshNeedsFull, setSchemaRefreshNeedsFull] = useState(false);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const loadSequence = useRef(0);
  const completedSchemaRefreshJob = useRef("");
  const { abortAll, run: runScopedRequest } = useRequestScope();
  const startSchemaRefresh = useStartSchemaRefresh();
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshJob = schemaRefreshJobQuery.data ?? null;
  const schemaRefreshing =
    !schemaRefreshJobQuery.error &&
    (startSchemaRefresh.isPending ||
      schemaRefreshJob?.status === "pending" ||
      schemaRefreshJob?.status === "running");
  const visibleSchemaRefreshError = schemaRefreshJobQuery.error
    ? schemaRefreshJobQuery.error instanceof Error
      ? schemaRefreshJobQuery.error.message
      : t("dataMgmt.schemaJob.error")
    : schemaRefreshError;

  const expectedConfirmation = sampleInfo?.confirmation ?? "SQL_ASSIST_SAMPLE";
  const confirmationMatched = sampleConfirmation.trim() === expectedConfirmation;
  const isDeleteAction = activeAction === "delete";

  const sampleSqlPreview = useMemo(() => {
    if (!sampleInfo) return "";
    if (activeAction === "delete") return joinSql(sampleInfo.sql.delete ?? []);
    const steps = sampleStep === "all" ? ["tables", "views", "data"] : [sampleStep];
    return joinSql(steps.flatMap((step) => sampleInfo.sql[step] ?? []));
  }, [activeAction, sampleInfo, sampleStep]);

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading("load");
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiGet<SampleDataInfo>("/api/nl2sql/sample-data", { signal });
        if (!signal.aborted && sequence === loadSequence.current) setSampleInfo(data);
      });
      if (announce && sequence === loadSequence.current) {
        toast.success(t("common.action.refreshed"));
      }
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      if (sequence === loadSequence.current) setLoading("");
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, []);

  const reloadSampleState = async () => {
    setSampleInfo(await apiGet<SampleDataInfo>("/api/nl2sql/sample-data"));
  };

  const refreshSchema = async () => {
    completedSchemaRefreshJob.current = "";
    try {
      const job = await startSchemaRefresh.mutateAsync();
      setSchemaRefreshJobId(job.job_id);
      toast.success(t("dataMgmt.schemaJob.accepted"));
      if (!job.job_id && job.status === "done") {
        setSchemaRefreshError("");
        setSchemaRefreshNeedsFull(false);
        await reloadSampleState();
      }
    } catch (err) {
      setSchemaRefreshError(err instanceof Error ? err.message : t("dataMgmt.schemaJob.submitError"));
      setSchemaRefreshNeedsFull(true);
    }
  };

  useEffect(() => {
    const job = schemaRefreshJobQuery.data;
    if (!job) return;
    const reportKey = `${job.job_id}:${job.status}`;
    if (completedSchemaRefreshJob.current === reportKey) return;
    if (job.status === "done") {
      completedSchemaRefreshJob.current = reportKey;
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      toast.success(t("common.action.schemaRefreshed"));
      void reloadSampleState();
    } else if (job.status === "error") {
      completedSchemaRefreshJob.current = reportKey;
      const needsFull = schemaRefreshRequiresFull(job);
      setSchemaRefreshNeedsFull(needsFull);
      setSchemaRefreshError(schemaRefreshErrorMessage(job));
      toast.error(needsFull ? schemaRefreshRequiredMessage(job.error_code) : t("dataMgmt.schemaJob.error"));
    }
  }, [schemaRefreshJobQuery.data]);

  const trackSchemaRefreshResult = (result: SampleDataMutationData) => {
    if (result.schema_refresh_job_id) {
      completedSchemaRefreshJob.current = "";
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      setSchemaRefreshJobId(result.schema_refresh_job_id);
      return;
    }
    if (result.schema_refresh_required) {
      setSchemaRefreshError(schemaRefreshRequiredMessage(result.schema_refresh_reason_code));
      setSchemaRefreshNeedsFull(true);
    }
  };

  const importSampleData = async () => {
    setLoading("sample-import");
    setMessage("");
    try {
      const result = await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/import", {
        step: sampleStep,
        confirmation: sampleConfirmation.trim(),
        reason: "ui-sample-import",
      });
      setSampleResult(result);
      if (result.executed) await reloadSampleState();
      trackSchemaRefreshResult(result);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      setLoading("");
    }
  };

  const deleteSampleData = async () => {
    setLoading("sample-delete");
    setMessage("");
    try {
      const result = await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/delete", {
        step: "all",
        confirmation: sampleConfirmation.trim(),
        reason: "ui-sample-delete",
      });
      setSampleResult(result);
      if (result.executed) await reloadSampleState();
      trackSchemaRefreshResult(result);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      setLoading("");
    }
  };

  const actionTitle = isDeleteAction ? t("dataTools.sample.delete") : t("dataTools.sample.import");
  const actionDescription = isDeleteAction ? t("dataTools.sample.deleteHint") : t("dataTools.sample.importHint");
  const pageNoticeActionLoading = schemaRefreshNeedsFull
    ? schemaRefreshing || startSchemaRefresh.isPending
    : loading === "load";
  const pageNoticeActionDisabled = schemaRefreshNeedsFull
    ? schemaRefreshing || startSchemaRefresh.isPending
    : loading === "load" || schemaRefreshing;

  return (
    <>
      <PageHeader
        title={t("sampleData.title")}
        subtitle={t("sampleData.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => load(true),
            loading: loading === "load",
          },
        ]}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={
            message
              ? { tone: "danger", message }
              : visibleSchemaRefreshError
                ? { tone: "danger", message: visibleSchemaRefreshError }
                : null
          }
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={pageNoticeActionLoading}
              disabled={pageNoticeActionDisabled}
              onClick={
                schemaRefreshNeedsFull
                  ? () => void refreshSchema()
                  : () => void load()
              }
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>
                {schemaRefreshNeedsFull
                  ? t("common.action.schemaRefresh")
                  : t("dataTools.sample.refresh")}
              </span>
            </Button>
          }
        />

        <DbObjectManagementTabs
          activeView={activeAction}
          tabs={[
            { id: "import", label: t("dataTools.sample.import"), icon: FileSpreadsheet },
            { id: "delete", label: t("dataTools.sample.delete"), icon: Trash2 },
          ] satisfies Array<DbObjectTab<SampleAction>>}
          idPrefix={SAMPLE_DATA_ID}
          ariaLabel={t("dataTools.sample.tabs.label")}
          onViewChange={(view) => {
            setActiveAction(view);
            setSampleResult(null);
          }}
        />

        <DbObjectManagementPanelShell
          id={`sample-data-panel-${activeAction}`}
          labelledBy={`sample-data-tab-${activeAction}`}
          idPrefix={SAMPLE_DATA_ID}
          ariaLabel={t("dataTools.sample.workspace.label")}
          splitId={`sample-data-${activeAction}`}
          preferredWidePane="right"
          processing={
            loading === "load" || schemaRefreshing ? (
              <ProcessingIndicator
                active
                label={
                  schemaRefreshing
                    ? schemaRefreshProcessingLabel(schemaRefreshJob, t("common.processing.schemaRefreshing"))
                    : t("common.processing.refreshing")
                }
                operationKey={schemaRefreshing ? schemaRefreshJobId || "sample-data-schema-refresh" : "sample-data-refresh"}
                placement="workspace"
                className="rounded-md border border-border bg-background px-3 py-2"
                testId="sample-data-workspace-processing"
                activityIcon="none"
              />
            ) : undefined
          }
        >
          <section className="grid min-w-0 content-start gap-4" aria-labelledby="sample-data-action-heading">
            <DbObjectPanelHeader
              headingId="sample-data-action-heading"
              icon={isDeleteAction ? Trash2 : FileSpreadsheet}
              title={actionTitle}
              description={actionDescription}
            />

            {!isDeleteAction && (
              <label className="grid gap-1 text-sm font-medium text-foreground">
                <span>{t("dataTools.sample.step")}</span>
                <select
                  value={sampleStep}
                  onChange={(event) => setSampleStep(event.currentTarget.value as SampleStep)}
                  className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:ring-2 focus:ring-ring/40"
                >
                  {SAMPLE_STEPS.map((step) => (
                    <option key={step} value={step}>
                      {sampleStepLabel(step)}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <ExecutionConfirmationField
              value={sampleConfirmation}
              onChange={setSampleConfirmation}
              confirmed={confirmationMatched}
              placeholder={expectedConfirmation}
              expectedLabel={expectedConfirmation}
              helper={t("dataTools.sample.confirmationHelper", { phrase: expectedConfirmation })}
              tone={isDeleteAction ? "danger" : "neutral"}
              actions={
                <Button
                  type="button"
                  variant={isDeleteAction ? "danger" : "primary"}
                  size="sm"
                  className="w-full sm:w-auto"
                  loading={loading === (isDeleteAction ? "sample-delete" : "sample-import")}
                  disabled={!confirmationMatched}
                  onClick={() => void (isDeleteAction ? deleteSampleData() : importSampleData())}
                >
                  {isDeleteAction ? <Trash2 size={15} aria-hidden="true" /> : <FileSpreadsheet size={15} aria-hidden="true" />}
                  <span>{actionTitle}</span>
                </Button>
              }
            />
          </section>

          <section className="grid min-w-0 content-start gap-4">
            <DbObjectPanelHeader
              icon={Database}
              title={t("dataTools.sample.previewTitle")}
              description={t("dataTools.sample.previewHint")}
            />
            <SampleObjectSummary sampleInfo={sampleInfo} />
            <SampleSqlPreview sql={sampleSqlPreview} />
            {sampleResult && (
              <DbAdminExecutionResult
                result={{
                  executed: sampleResult.executed,
                  runtime: sampleResult.runtime,
                  select_result: null,
                  statements: sampleResult.statements,
                  committed: false,
                  rolled_back: false,
                  warnings: sampleResult.warnings,
                  timing: sampleResult.timing,
                }}
              />
            )}
          </section>
        </DbObjectManagementPanelShell>
      </main>
    </>
  );
}
