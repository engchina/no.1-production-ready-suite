import { useResetExecutionConsent, useWorkspaceActivation, useWorkspaceState } from "@/components/WorkspaceState";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useValuesChanged } from "@/lib/render-sync";
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
import { DbObjectName } from "../components/DbObjectName";
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
            icon={false}
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
      <ul className="grid gap-1 sm:grid-cols-2" data-testid="sample-data-object-list">
        {(sampleInfo?.objects ?? []).map((objectName) => {
          const ref = sampleInfo?.object_refs?.find((item) => item.name === objectName);
          const conflict = Boolean(sampleInfo?.conflicting_objects?.includes(objectName));
          const imported = !conflict && Boolean(sampleInfo?.imported_objects.includes(objectName));
          // 状態は色だけに頼らず、ラベルとアイコン付きの StatusBadge で示す。
          return (
            <li
              key={objectName}
              className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface px-2 py-1"
              data-testid="sample-data-object"
            >
              <DbObjectName
                object={{ name: objectName, owner: ref?.owner ?? sampleInfo?.owner, qualified_name: ref?.qualified_name }}
                size="xs"
                className="min-w-0"
              />
              <StatusBadge
                variant={conflict ? "warning" : imported ? "success" : "neutral"}
                label={t(
                  conflict
                    ? "dataTools.sample.objectStatus.conflict"
                    : imported
                      ? "dataTools.sample.objectStatus.imported"
                      : "dataTools.sample.objectStatus.notImported"
                )}
              />
            </li>
          );
        })}
      </ul>
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
  // dataset の切り替えで始めた読み込みの回数。取得は effect で行う。
  const [datasetLoadRequest, setDatasetLoadRequest] = useState(0);
  // 報告済みの schema refresh の終端（`<job_id>:<status>`）。
  const [reportedSchemaRefresh, setReportedSchemaRefresh] = useState("");
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

  // sample 情報を取り直す。loading / message は呼び出し側で先に設定しておく。
  // state の更新は応答の callback の中だけで行う（effect からも呼ぶため）。
  const fetchSampleInfo = (announce: boolean) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    return runScopedRequest(async (signal) => {
      const data = await apiGet<SampleDataInfo>(sampleInfoUrl, { signal });
      if (!signal.aborted && sequence === loadSequence.current) {
        setSampleInfo(data);
        setSampleLoadFailed(false);
      }
    })
      .then(() => {
        if (announce && sequence === loadSequence.current) {
          toast.success(t("common.action.refreshed"));
        }
      })
      .catch((err: unknown) => {
        if (isAbortError(err)) {
          return;
        }
        setSampleLoadFailed(true);
        setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
      })
      .finally(() => {
        if (sequence === loadSequence.current) setLoading("");
      });
  };

  const load = async (announce = false) => {
    if (loading) return;
    setLoading("load");
    setMessage("");
    await fetchSampleInfo(announce);
  };

  // dataset が変わったレンダーで読み込み中の表示にし（effect で setState しない）、取得は effect で行う。
  // 別の処理の実行中は読み込まない（load() と同じ）。
  const datasetChanged = useValuesChanged([dataset]);
  if (datasetChanged && !loading) {
    setLoading("load");
    setMessage("");
    setDatasetLoadRequest((request) => request + 1);
  }
  // 取得は読み込みの要求が増えたときだけ行う。取得の関数は毎レンダー作り直すので、最新のものを ref から呼ぶ。
  const fetchSampleInfoRef = useRef(fetchSampleInfo);
  useLayoutEffect(() => { fetchSampleInfoRef.current = fetchSampleInfo; });
  useEffect(() => {
    if (datasetLoadRequest > 0) void fetchSampleInfoRef.current(false);
  }, [datasetLoadRequest]);
  // abortAll は固定の関数なので、dataset が変わったときだけ前の読み込みを捨てる条件は変わらない。
  useEffect(() => {
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, [dataset, abortAll]);

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
    setReportedSchemaRefresh("");
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

  // job の終端を初めて見たレンダーで、error 表示を直す（effect で setState しない）。
  // sample 情報の再取得は、終端を報告した後の effect で行う。
  const schemaRefreshJob = schemaRefreshJobQuery.data;
  const schemaRefreshJobChanged = useValuesChanged([schemaRefreshJob]);
  if (
    schemaRefreshJobChanged &&
    schemaRefreshJob &&
    (schemaRefreshJob.status === "done" || schemaRefreshJob.status === "error")
  ) {
    const reportKey = `${schemaRefreshJob.job_id}:${schemaRefreshJob.status}`;
    if (reportedSchemaRefresh !== reportKey) {
      setReportedSchemaRefresh(reportKey);
      if (schemaRefreshJob.status === "done") {
        setSchemaRefreshError("");
        setSchemaRefreshNeedsFull(false);
      } else {
        setSchemaRefreshNeedsFull(schemaRefreshRequiresFull(schemaRefreshJob));
        setSchemaRefreshError(schemaRefreshErrorMessage(schemaRefreshJob));
      }
    }
  }
  // 再取得は終端を報告したときだけ行う。sampleInfoUrl（dataset）が変わっても再取得しないよう、最新の関数を ref から呼ぶ。
  const reloadSampleStateRef = useRef(reloadSampleState);
  useLayoutEffect(() => { reloadSampleStateRef.current = reloadSampleState; });
  useEffect(() => {
    if (!reportedSchemaRefresh.endsWith(":done")) return;
    void reloadSampleStateRef.current().catch((err: unknown) => {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    });
  }, [reportedSchemaRefresh]);

  const trackSchemaRefreshResult = (result: SampleDataMutationData) => {
    if (result.schema_refresh_job_id) {
      setReportedSchemaRefresh("");
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
      <PageHeader wide
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
      <PageBody wide className="grid gap-4">
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

        {/* 種類の選択（1）と、その説明・クエリ例（2）を同じ行に置き、選択欄だけを左に残さない。 */}
        <section className="grid min-w-0 gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] lg:items-end lg:gap-x-6" aria-label={t("dataTools.sample.dataset.label")}>
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
              className="min-h-11 w-full min-w-0 rounded-md border border-border-control bg-surface px-3 py-2 focus:border-focus-ring focus:ring-2 focus:ring-focus-ring"
            >
              {SAMPLE_DATASETS.map((item) => <option key={item} value={item}>{t(`dataTools.sample.dataset.${item}`)}</option>)}
            </select>
          </label>
          <div className="grid min-w-0 gap-1 lg:min-h-11 lg:content-center">
            <p id="sample-data-dataset-description" className="text-sm text-fg-muted">{t(`dataTools.sample.dataset.${dataset}.description`)}</p>
            <p className="text-sm text-fg-muted">{t(`dataTools.sample.dataset.${dataset}.example`)}</p>
          </div>
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
                    icon={isDeleteAction ? Trash2 : FileSpreadsheet}
                    loading={loading === (isDeleteAction ? "sample-delete" : "sample-import")}
                    disabled={Boolean(loading) || !confirmationMatched || !sampleInfo}
                    onClick={() => void (isDeleteAction ? deleteSampleData() : importSampleData())}
                  >
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
