import { useEffect, useMemo, useState } from "react";
import { Database, FileSpreadsheet, RefreshCw, Trash2 } from "lucide-react";

import { Button, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiGet, apiPost } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  DbAdminExecutionResult,
  ExecutionConfirmationField,
} from "../components/DbAdminShared";
import {
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectManagementTabs,
  DbObjectPanelHeader,
  type DbObjectTab,
} from "../components/DbObjectManagementShared";
import type { SampleDataInfo, SampleDataMutationData } from "../types";

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

function SampleStatusBar({
  sampleInfo,
  loading,
  onRefresh,
}: {
  sampleInfo: SampleDataInfo | null;
  loading: string;
  onRefresh: () => void;
}) {
  return (
    <DbObjectManagementStatusBar
      ariaLabel={t("dataTools.sample.status")}
      metrics={[
        {
          label: t("dataTools.sample.metric.objects"),
          value: formatNumber(sampleInfo?.objects.length ?? 0),
          testId: "sample-data-object-count",
          emphasis: true,
        },
        {
          label: t("dataTools.sample.metric.imported"),
          value: formatNumber(sampleInfo?.imported_objects.length ?? 0),
          testId: "sample-data-imported-count",
          emphasis: true,
        },
        {
          label: t("dataTools.sample.metric.runtime"),
          value: sampleInfo?.runtime ?? "deterministic",
          testId: "sample-data-runtime",
        },
      ]}
      actions={
        <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={onRefresh}>
          <RefreshCw size={15} aria-hidden="true" />
          <span>{t("dataTools.sample.refresh")}</span>
        </Button>
      }
    />
  );
}

function SampleObjectSummary({ sampleInfo }: { sampleInfo: SampleDataInfo | null }) {
  return (
    <section className="grid gap-2 rounded-md border border-border bg-background p-3 text-sm">
      <p className="font-semibold text-foreground">{t("dataTools.sample.objects")}</p>
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
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const expectedConfirmation = sampleInfo?.confirmation ?? "SQL_ASSIST_SAMPLE";
  const confirmationMatched = sampleConfirmation.trim() === expectedConfirmation;
  const isDeleteAction = activeAction === "delete";

  const sampleSqlPreview = useMemo(() => {
    if (!sampleInfo) return "";
    if (activeAction === "delete") return joinSql(sampleInfo.sql.delete ?? []);
    const steps = sampleStep === "all" ? ["tables", "views", "data"] : [sampleStep];
    return joinSql(steps.flatMap((step) => sampleInfo.sql[step] ?? []));
  }, [activeAction, sampleInfo, sampleStep]);

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      setSampleInfo(await apiGet<SampleDataInfo>("/api/nl2sql/sample-data"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const reloadSampleState = async () => {
    setSampleInfo(await apiGet<SampleDataInfo>("/api/nl2sql/sample-data"));
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
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      setLoading("");
    }
  };

  const actionTitle = isDeleteAction ? t("dataTools.sample.delete") : t("dataTools.sample.import");
  const actionDescription = isDeleteAction ? t("dataTools.sample.deleteHint") : t("dataTools.sample.importHint");

  return (
    <>
      <PageHeader title={t("sampleData.title")} subtitle={t("sampleData.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={message ? { tone: "danger", message } : null}
          action={
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("dataTools.sample.refresh")}</span>
            </Button>
          }
        />

        <SampleStatusBar sampleInfo={sampleInfo} loading={loading} onRefresh={() => void load()} />

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
