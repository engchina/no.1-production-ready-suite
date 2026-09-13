import { useResetExecutionConsent, useWorkspaceActivation, useWorkspaceState } from "@/components/WorkspaceState";
import { useEffect, useMemo, useRef, useState } from "react";
import { Database, FileSpreadsheet, RefreshCw, Trash2 } from "lucide-react";

import {
  Button,
  toast,
  StatusBadge,
  PageHeader,
  PageBody,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiGet, apiPost, isAbortError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import { DbAdminExecutionResult, ExecutionConfirmationField } from "../components/DbAdminShared";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import {
  SchemaRefreshHeaderStatus,
  SchemaRefreshProcessing,
} from "../components/SchemaRefreshFeedback";
import { useSchemaRefreshJob } from "../incrementalQueries";
import { useSchemaRefreshCoordinator } from "../SchemaRefreshCoordinator";
import type { SampleDataInfo, SampleDataMutationData, SampleDataset, SchemaRefreshJob } from "../types";

type SampleStep = "tables" | "views" | "data" | "all";
type SampleAction = "import" | "delete";

const SAMPLE_DATA_ID = "sample-data";
const SAMPLE_STEPS: SampleStep[] = ["all", "tables", "views", "data"];
const SAMPLE_DATASETS: SampleDataset[] = ["hr", "sales", "inquiries"];

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

function SampleObjectSummary({ sampleInfo }: { sampleInfo: SampleDataInfo | null }) {
  return (
    <section className="grid gap-2 rounded-md border border-border bg-surface-sunken p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-fg">{t("dataTools.sample.objects")}</p>
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
        <p className="font-semibold text-fg">{t("dataTools.sample.sqlPreview")}</p>
        <p className="mt-1 text-sm text-fg-muted">{t("dataTools.sample.sqlPreviewHint")}</p>
      </div>
      <pre data-surface="code" className="max-h-80 overflow-auto rounded-md border border-border bg-surface p-3 text-sm leading-6 text-fg">
        <code>{sql || "-"}</code>
      </pre>
    </section>
  );
}

export function SampleDataPage() {
  const [dataset, setDataset] = useWorkspaceState<SampleDataset>("sampleData.dataset", "hr");
  const [sampleInfo, setSampleInfo] = useState<SampleDataInfo | null>(null);
  const [sampleLoadFailed, setSampleLoadFailed] = useState(false);
  const [sampleStep, setSampleStep] = useWorkspaceState<SampleStep>("sampleData.step", "all");
  const [activeAction, setActiveAction] = useWorkspaceState<SampleAction>("sampleData.action", "import");
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
  const sharedSchemaRefresh = useSchemaRefreshCoordinator();
  const schemaRefreshJobQuery = useSchemaRefreshJob(schemaRefreshJobId);
  const schemaRefreshing = sharedSchemaRefresh.isRefreshing;
  const visibleSchemaRefreshError = schemaRefreshJobQuery.error
    ? schemaRefreshJobQuery.error instanceof Error
      ? schemaRefreshJobQuery.error.message
      : t("dataMgmt.schemaJob.error")
    : schemaRefreshError || sharedSchemaRefresh.error;

  const sampleInfoUrl = dataset === "hr" ? "/api/nl2sql/sample-data" : `/api/nl2sql/sample-data?dataset=${encodeURIComponent(dataset)}`;
  const sampleReady = Boolean(sampleInfo) && (sampleInfo?.dataset ?? "hr") === dataset && !sampleLoadFailed;
  const expectedConfirmation = sampleInfo?.confirmation ?? "";
  const confirmationMatched = sampleReady && Boolean(expectedConfirmation) && sampleConfirmation.trim() === expectedConfirmation;
  const isDeleteAction = activeAction === "delete";

  const sampleSqlPreview = useMemo(() => {
    if (!sampleInfo) return "";
    if (activeAction === "delete") return joinSql(sampleInfo.sql.delete ?? []);
    const steps = sampleStep === "all" ? ["tables", "views", "data"] : [sampleStep];
    return joinSql(steps.flatMap((step) => sampleInfo.sql[step] ?? []));
  }, [activeAction, sampleInfo, sampleStep]);

  useResetExecutionConsent(() => setSampleConfirmation(""), JSON.stringify([dataset, activeAction, sampleStep, sampleSqlPreview, expectedConfirmation]));

  const load = async (announce = false) => {
    if (loading) return;
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading("load");
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiGet<SampleDataInfo>(sampleInfoUrl, { signal });
        if (!signal.aborted && sequence === loadSequence.current) {
          setSampleInfo(data);
          setSampleLoadFailed(false);
        }
      });
      if (announce && sequence === loadSequence.current) {
        toast.success(t("common.action.refreshed"));
      }
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setSampleLoadFailed(true);
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
  }, [dataset]);

  // 初回取得は上の effect が担い、keep-alive からの復帰時は状態だけ再検証する。
  const visited = useRef(false);
  useWorkspaceActivation(() => {
    if (visited.current) void load();
    visited.current = true;
  });

  const reloadSampleState = async () => {
    const sequence = loadSequence.current;
    await runScopedRequest(async (signal) => {
      const info = await apiGet<SampleDataInfo>(sampleInfoUrl, { signal });
      if (!signal.aborted && sequence === loadSequence.current) setSampleInfo(info);
    });
  };

  const refreshSchema = async () => {
    if (loading || schemaRefreshing) return;
    completedSchemaRefreshJob.current = "";
    try {
      const job = await sharedSchemaRefresh.start();
      setSchemaRefreshJobId(job.job_id);
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
      void reloadSampleState().catch((err: unknown) => {
        setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
      });
    } else if (job.status === "error") {
      completedSchemaRefreshJob.current = reportKey;
      const needsFull = schemaRefreshRequiresFull(job);
      setSchemaRefreshNeedsFull(needsFull);
      setSchemaRefreshError(schemaRefreshErrorMessage(job));
    }
  }, [schemaRefreshJobQuery.data]);

  const trackSchemaRefreshResult = (result: SampleDataMutationData) => {
    if (result.schema_refresh_job_id) {
      completedSchemaRefreshJob.current = "";
      setSchemaRefreshError("");
      setSchemaRefreshNeedsFull(false);
      setSchemaRefreshJobId(result.schema_refresh_job_id);
      sharedSchemaRefresh.track(result.schema_refresh_job_id);
      return;
    }
    if (result.schema_refresh_required) {
      setSchemaRefreshError(schemaRefreshRequiredMessage(result.schema_refresh_reason_code));
      setSchemaRefreshNeedsFull(true);
    }
  };

  const importSampleData = async () => {
    if (loading || !confirmationMatched || !sampleInfo) return;
    setSampleResult(null);
    setLoading("sample-import");
    setMessage("");
    try {
      const result = await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/import", {
        dataset,
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
    if (loading || !confirmationMatched || !sampleInfo) return;
    setSampleResult(null);
    setLoading("sample-delete");
    setMessage("");
    try {
      const result = await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/delete", {
        dataset,
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
  const datasetLabel = t(`dataTools.sample.dataset.${dataset}`);
  const actionDescription = isDeleteAction
    ? t("dataTools.sample.deleteHint", { name: datasetLabel })
    : t("dataTools.sample.importHint", { name: datasetLabel });
  const pageNoticeActionLoading = schemaRefreshNeedsFull
    ? schemaRefreshing
    : loading === "load";
  const pageNoticeActionDisabled = schemaRefreshNeedsFull
    ? schemaRefreshing
    : loading === "load" || schemaRefreshing;

  return (
    <>
      <PageHeader
        title={t("sampleData.title")}
        subtitle={t("sampleData.subtitle")}
        status={<SchemaRefreshHeaderStatus testId="sample-data-schema-refresh-status" />}
        actionsTestId="sample-data-actions"
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => load(true),
            loading: loading === "load",
            disabled: Boolean(loading),
          },
        ]}
      />
      <PageBody className="grid gap-4">
        <PageNotice
          notice={
            message
              ? { tone: "danger", message }
              : visibleSchemaRefreshError
                ? { tone: "danger", message: visibleSchemaRefreshError }
                : sampleInfo?.warnings.length
                  ? { tone: "warning", message: sampleInfo.warnings.join("\n") }
                  : null
          }
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={pageNoticeActionLoading}
              disabled={pageNoticeActionDisabled || Boolean(loading)}
              onClick={
                schemaRefreshNeedsFull
                  ? () => void refreshSchema()
                  : () => void load()
              } icon={RefreshCw}>
              <span>
                {schemaRefreshNeedsFull
                  ? t("common.action.schemaRefresh")
                  : t("dataTools.sample.refresh")}
              </span>
            </Button>
          }
        />

        <section className="grid min-w-0 gap-2" aria-label={t("dataTools.sample.dataset.label")}>
          <label className="grid gap-1 text-sm font-medium text-fg">
            <span>{t("dataTools.sample.dataset.label")}</span>
            <select
              value={dataset}
              disabled={Boolean(loading) || schemaRefreshing}
              aria-describedby="sample-data-dataset-description"
              onChange={(event) => {
                setSampleInfo(null);
                setSampleResult(null);
                setSampleConfirmation("");
                setMessage("");
                setSchemaRefreshError("");
                setSchemaRefreshNeedsFull(false);
                setSchemaRefreshJobId("");
                setDataset(event.currentTarget.value as SampleDataset);
              }}
              className="min-h-11 w-full min-w-0 rounded-md border border-border-control bg-surface px-3 py-2 focus:border-focus-ring focus:ring-2 focus:ring-focus-ring sm:max-w-md"
            >
              {SAMPLE_DATASETS.map((item) => <option key={item} value={item}>{t(`dataTools.sample.dataset.${item}`)}</option>)}
            </select>
          </label>
          <p id="sample-data-dataset-description" className="text-sm text-fg-muted">{t(`dataTools.sample.dataset.${dataset}.description`)}</p>
          <p className="text-sm text-fg-muted">{t(`dataTools.sample.dataset.${dataset}.example`)}</p>
        </section>

        <DbObjectManagementTabs
          activeView={activeAction}
          disabled={Boolean(loading)}
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
            schemaRefreshing ? (
              <SchemaRefreshProcessing testId="sample-data-workspace-processing" />
            ) : undefined
          }
        >
          {loading === "load" ? (
            <DbManagementLoadingSkeleton
              idPrefix="sample-data-workspace-refresh"
              ariaLabel={t("common.processing.refreshing")}
              variant="detail"
              operationKey="sample-data-refresh"
              placement="workspace"
              testId="sample-data-workspace-refresh-skeleton"
              activityIcon="none"
            />
          ) : (
            <section className="grid min-w-0 content-start gap-4" aria-labelledby="sample-data-action-heading">
              <DbObjectPanelHeader
                headingId="sample-data-action-heading"
                icon={isDeleteAction ? Trash2 : FileSpreadsheet}
                title={actionTitle}
                description={actionDescription}
              />

              {!isDeleteAction && (
                <label className="grid gap-1 text-sm font-medium text-fg">
                  <span>{t("dataTools.sample.step")}</span>
                  <select
                    value={sampleStep}
                    disabled={Boolean(loading)}
                    onChange={(event) => setSampleStep(event.currentTarget.value as SampleStep)}
                    className="min-h-11 rounded-md border border-border-control bg-surface px-3 py-2 focus:border-focus-ring focus:ring-2 focus:ring-focus-ring"
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
                disabled={Boolean(loading)}
                onChange={setSampleConfirmation}
                confirmed={confirmationMatched}
                placeholder={expectedConfirmation}
                expectedLabel={expectedConfirmation}
                helper={t("dataTools.sample.confirmationHelper", { phrase: expectedConfirmation })}
                actions={
                  <Button
                    type="button"
                    variant={isDeleteAction ? "danger" : "primary"}
                    size="lg"
                    className="w-full sm:w-auto"
                    loading={loading === (isDeleteAction ? "sample-delete" : "sample-import")}
                    disabled={Boolean(loading) || !confirmationMatched || !sampleInfo}
                    onClick={() => void (isDeleteAction ? deleteSampleData() : importSampleData())}
                  >
                    {isDeleteAction ? <Trash2 size={16} aria-hidden="true" /> : <FileSpreadsheet size={16} aria-hidden="true" />}
                    <span>{actionTitle}</span>
                  </Button>
                }
              />
            </section>
          )}

          {loading !== "load" && (
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
          )}
        </DbObjectManagementPanelShell>
      </PageBody>
    </>
  );
}
