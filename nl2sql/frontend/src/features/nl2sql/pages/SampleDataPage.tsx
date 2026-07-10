import { useEffect, useMemo, useState } from "react";
import { Database, FileSpreadsheet, RefreshCw, Trash2 } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { DbAdminExecutionResult } from "../components/DbAdminShared";
import type { SampleDataInfo, SampleDataMutationData } from "../types";

type SampleStep = "tables" | "views" | "data" | "all";

export function SampleDataPage() {
  const [sampleInfo, setSampleInfo] = useState<SampleDataInfo | null>(null);
  const [sampleStep, setSampleStep] = useState<SampleStep>("all");
  const [sampleExecute, setSampleExecute] = useState(false);
  const [sampleConfirmation, setSampleConfirmation] = useState("");
  const [sampleResult, setSampleResult] = useState<SampleDataMutationData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const sampleSqlPreview = useMemo(() => {
    if (!sampleInfo) return "";
    const steps = sampleStep === "all" ? ["tables", "views", "data"] : [sampleStep];
    return steps.flatMap((step) => sampleInfo.sql[step] ?? []).join(";\n\n");
  }, [sampleInfo, sampleStep]);

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
      setSampleResult(
        await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/import", {
          step: sampleStep,
          execute: sampleExecute,
          confirmation: sampleExecute ? sampleConfirmation : "",
          reason: "ui-sample-import",
        })
      );
      if (sampleExecute) await reloadSampleState();
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
      setSampleResult(
        await apiPost<SampleDataMutationData>("/api/nl2sql/sample-data/delete", {
          step: "all",
          execute: sampleExecute,
          confirmation: sampleExecute ? sampleConfirmation : "",
          reason: "ui-sample-delete",
        })
      );
      if (sampleExecute) await reloadSampleState();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.sample"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader
        title={t("sampleData.title")}
        subtitle={t("sampleData.subtitle")}
        actions={
          <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("dataTools.action.refresh")}</span>
          </Button>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database size={18} aria-hidden="true" />
              {t("dataTools.sample.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-[minmax(16rem,0.45fr)_minmax(0,1fr)]">
            <section className="grid content-start gap-3">
              <p className="text-sm leading-6 text-slate-600">{t("dataTools.sample.subtitle")}</p>
              <div className="flex flex-wrap gap-2">
                <StatusBadge variant="neutral" label={sampleInfo?.runtime ?? "deterministic"} />
                <StatusBadge
                  variant={(sampleInfo?.imported_objects.length ?? 0) > 0 ? "success" : "neutral"}
                  label={`${t("dataTools.sample.imported")} ${sampleInfo?.imported_objects.length ?? 0}`}
                />
              </div>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataTools.sample.step")}</span>
                <select
                  value={sampleStep}
                  onChange={(event) => setSampleStep(event.currentTarget.value as SampleStep)}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                >
                  {["all", "tables", "views", "data"].map((step) => (
                    <option key={step} value={step}>
                      {step}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("dataTools.sample.confirmation")}</span>
                <input
                  value={sampleConfirmation}
                  onChange={(event) => setSampleConfirmation(event.currentTarget.value)}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={sampleInfo?.confirmation ?? "SQL_ASSIST_SAMPLE"}
                />
              </label>
              <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                <input
                  type="checkbox"
                  checked={sampleExecute}
                  onChange={(event) => setSampleExecute(event.currentTarget.checked)}
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                />
                <span>{t("dataTools.sample.execute")}</span>
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant={sampleExecute ? "danger" : "secondary"}
                  size="sm"
                  loading={loading === "sample-import"}
                  onClick={() => void importSampleData()}
                >
                  <FileSpreadsheet size={15} aria-hidden="true" />
                  <span>{sampleExecute ? t("dataTools.sample.import") : t("dataTools.sample.importDryRun")}</span>
                </Button>
                <Button
                  type="button"
                  variant={sampleExecute ? "danger" : "secondary"}
                  size="sm"
                  loading={loading === "sample-delete"}
                  onClick={() => void deleteSampleData()}
                >
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{sampleExecute ? t("dataTools.sample.delete") : t("dataTools.sample.deleteDryRun")}</span>
                </Button>
              </div>
            </section>
            <section className="grid min-w-0 gap-3">
              <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                <p className="font-semibold text-slate-900">{t("dataTools.sample.objects")}</p>
                <div className="flex flex-wrap gap-2">
                  {(sampleInfo?.objects ?? []).map((objectName) => (
                    <StatusBadge
                      key={objectName}
                      variant={sampleInfo?.imported_objects.includes(objectName) ? "success" : "neutral"}
                      label={objectName}
                    />
                  ))}
                </div>
              </div>
              <div className="grid gap-2">
                <p className="font-semibold text-slate-900">{t("dataTools.sample.sqlPreview")}</p>
                <pre className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
                  <code>{sampleSqlPreview || "-"}</code>
                </pre>
              </div>
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
          </CardContent>
        </Card>
      </main>
    </>
  );
}
