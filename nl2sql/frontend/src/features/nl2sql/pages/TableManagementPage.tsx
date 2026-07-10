import { useEffect, useState } from "react";
import { Download, RefreshCw, Table2, Trash2, Upload } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  PageHeader,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  DbAdminExecutionResult,
  ObjectDetailPanel,
  ObjectListPanel,
  PageMetric,
  QueryResultsTable,
  StatementRunnerCard,
  WorkSection,
  fileToBase64,
} from "../components/DbAdminShared";
import type {
  DbAdminExecuteData,
  DbAdminImportTabularData,
  DbAdminObjectDetail,
  DbAdminObjectsData,
  SchemaCatalog,
} from "../types";

export function TableManagementPage() {
  const [tables, setTables] = useState<DbAdminObjectsData | null>(null);
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [detail, setDetail] = useState<DbAdminObjectDetail | null>(null);
  const [dropExecute, setDropExecute] = useState(false);
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [dropResult, setDropResult] = useState<DbAdminExecuteData | null>(null);
  const [importTable, setImportTable] = useState("");
  const [importFilename, setImportFilename] = useState("");
  const [importBase64, setImportBase64] = useState("");
  const [importSheet, setImportSheet] = useState("");
  const [importMode, setImportMode] = useState("create");
  const [importExecute, setImportExecute] = useState(false);
  const [importConfirmation, setImportConfirmation] = useState("");
  const [importResult, setImportResult] = useState<DbAdminImportTabularData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const load = async (refreshSchema = false) => {
    setLoading(refreshSchema ? "schema-refresh" : "load");
    setMessage("");
    try {
      const [tableData, catalogData] = await Promise.all([
        apiGet<DbAdminObjectsData>("/api/nl2sql/db-admin/tables"),
        refreshSchema
          ? apiPost<SchemaCatalog>("/api/schema/refresh")
          : apiGet<SchemaCatalog>("/api/schema/catalog"),
      ]);
      setTables(tableData);
      setCatalog(catalogData);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const selectTable = async (name: string) => {
    setLoading(`detail-${name}`);
    setMessage("");
    try {
      setDetail(await apiGet<DbAdminObjectDetail>(`/api/nl2sql/db-admin/tables/${encodeURIComponent(name)}`));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.load"));
    } finally {
      setLoading("");
    }
  };

  const pickImportFile = async (file: File | undefined) => {
    if (!file) return;
    setImportFilename(file.name);
    setImportBase64(await fileToBase64(file));
  };

  const importTabular = async () => {
    if (!importTable.trim() || !importBase64) return;
    setLoading("import-tabular");
    setMessage("");
    try {
      const result = await apiPost<DbAdminImportTabularData>(
        "/api/nl2sql/db-admin/import-tabular",
        {
          table_name: importTable,
          content_base64: importBase64,
          filename: importFilename || "upload.csv",
          sheet_name: importSheet,
          mode: importMode,
          execute: importExecute,
          confirmation: importExecute ? importConfirmation : "",
          reason: "ui-table-management-import-tabular",
        }
      );
      setImportResult(result);
      if (result.executed) {
        await load(true);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.import"));
    } finally {
      setLoading("");
    }
  };

  const dropTable = async () => {
    if (!detail) return;
    setLoading("drop");
    setMessage("");
    try {
      const result = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/drop-table", {
        table_name: detail.name,
        execute: dropExecute,
        confirmation: dropExecute ? dropConfirmation : "",
        reason: "ui-table-management-drop",
      });
      setDropResult(result);
      if (result.executed) {
        setDetail(null);
        await load();
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.drop"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.tableManagement")}
        subtitle={t("tableMgmt.subtitle")}
        actions={
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading === "load"}
              onClick={() => void load()}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("tableMgmt.action.refresh")}</span>
            </Button>
            <Button
              type="button"
              variant="primary"
              size="sm"
              loading={loading === "schema-refresh"}
              onClick={() => void load(true)}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("tableMgmt.action.schemaRefresh")}</span>
            </Button>
          </div>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {message}
          </div>
        )}

        <Card>
          <CardContent className="grid gap-3 p-4 md:grid-cols-3">
            <PageMetric label={t("tableMgmt.metric.tables")} value={formatNumber(tables?.items.length ?? 0)} />
            <PageMetric label={t("tableMgmt.metric.runtime")} value={tables?.runtime ?? "deterministic"} />
            <PageMetric label={t("tableMgmt.metric.schemaRefreshed")} value={formatDateTime(catalog?.refreshed_at)} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="flex items-center gap-2">
                <Table2 size={18} aria-hidden="true" />
                {t("tableMgmt.list.title")}
              </CardTitle>
              <StatusBadge variant="neutral" label={tables?.runtime ?? "deterministic"} />
              <StatusBadge variant="info" label={`${tables?.items.length ?? 0} tables`} />
            </div>
          </CardHeader>
          <CardContent className="grid gap-5 xl:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)]">
            <ObjectListPanel
              items={tables?.items ?? []}
              selectedName={detail?.name}
              onSelect={(item) => void selectTable(item.name)}
              emptyTitle={t("tableMgmt.list.emptyTitle")}
              emptyHint={t("tableMgmt.list.emptyHint")}
            />
            <div className="grid min-w-0 content-start gap-3">
              <ObjectDetailPanel
                detail={detail}
                catalog={catalog}
                actions={
                  detail && (
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() =>
                        window.open(
                          `/api/nl2sql/db-admin/tables/${encodeURIComponent(detail.name)}/export.xlsx`,
                          "_blank",
                          "noopener,noreferrer"
                        )
                      }
                    >
                      <Download size={15} aria-hidden="true" />
                      <span>{t("tableMgmt.export")}</span>
                    </Button>
                  )
                }
              />
            </div>
          </CardContent>
        </Card>

        <WorkSection title={t("tableMgmt.create.title")} description={t("tableMgmt.create.note")}>
          <StatementRunnerCard
            policy="table_ddl"
            target="table"
            title={t("tableMgmt.create.title")}
            placeholder={t("tableMgmt.create.placeholder")}
            onExecuted={() => load()}
          />
        </WorkSection>

        <WorkSection
          title={t("dataTools.dbAdmin.importTitle")}
          description={t("tableMgmt.import.note")}
        >
          <Card>
            <CardContent className="grid gap-4 p-4">
              <div className="grid gap-3 lg:grid-cols-2">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.dbAdmin.tableName")}</span>
                  <input
                    value={importTable}
                    onChange={(event) => setImportTable(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder="IMPORTED_ORDERS"
                  />
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.dbAdmin.sheet")}</span>
                  <input
                    value={importSheet}
                    onChange={(event) => setImportSheet(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder="Sheet1"
                  />
                </label>
              </div>
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem]">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.dbAdmin.file")}</span>
                  <input
                    type="file"
                    accept=".csv,.xlsx,.xls"
                    onChange={(event) => void pickImportFile(event.currentTarget.files?.[0])}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1 file:text-sm file:font-medium file:text-slate-800 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.dbAdmin.mode")}</span>
                  <select
                    value={importMode}
                    onChange={(event) => setImportMode(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  >
                    <option value="create">{t("dataTools.dbAdmin.mode.create")}</option>
                    <option value="replace">{t("dataTools.dbAdmin.mode.replace")}</option>
                    <option value="append">{t("dataTools.dbAdmin.mode.append")}</option>
                    <option value="truncate">{t("dataTools.dbAdmin.mode.truncate")}</option>
                  </select>
                </label>
              </div>
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={importExecute}
                    onChange={(event) => setImportExecute(event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("dataTools.dbAdmin.importExecute")}</span>
                </label>
                <Button
                  type="button"
                  variant={importExecute ? "primary" : "secondary"}
                  size="sm"
                  loading={loading === "import-tabular"}
                  disabled={!importTable.trim() || !importBase64}
                  onClick={() => void importTabular()}
                >
                  <Upload size={15} aria-hidden="true" />
                  <span>
                    {importExecute
                      ? t("dataTools.dbAdmin.import")
                      : t("dataTools.dbAdmin.importDryRun")}
                  </span>
                </Button>
              </div>
              {importExecute && (
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("dataTools.dbAdmin.confirmation")}</span>
                  <input
                    value={importConfirmation}
                    onChange={(event) => setImportConfirmation(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder={importTable || "ADMIN_EXECUTE"}
                  />
                </label>
              )}
              {importResult && (
                <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge
                      variant={importResult.executed ? "success" : "neutral"}
                      label={importResult.table_name}
                    />
                    <StatusBadge variant="info" label={`${importResult.row_count} rows`} />
                    <StatusBadge variant="neutral" label={importResult.mode} />
                  </div>
                  {importResult.warnings.map((warning) => (
                    <p
                      key={warning}
                      className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900"
                    >
                      {warning}
                    </p>
                  ))}
                  <pre className="overflow-auto rounded-md border border-slate-200 bg-white p-3 font-mono text-xs leading-5 text-slate-800">
                    <code>{`${importResult.ddl}\n\n${importResult.insert_sql}`}</code>
                  </pre>
                  {importResult.sample_rows.length > 0 && (
                    <QueryResultsTable
                      results={{
                        columns: Object.keys(importResult.sample_rows[0] ?? {}),
                        rows: importResult.sample_rows,
                        total: importResult.sample_rows.length,
                      }}
                    />
                  )}
                </section>
              )}
            </CardContent>
          </Card>
        </WorkSection>

        <WorkSection title={t("tableMgmt.danger.title")} description={t("tableMgmt.danger.subtitle")} tone="danger">
          {detail ? (
            <div className="grid gap-3 rounded-md border border-red-200 bg-red-50 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="break-all font-mono text-sm font-semibold text-red-950">{detail.name}</p>
                  <p className="mt-1 text-sm text-red-800">{t("tableMgmt.danger.subtitle")}</p>
                </div>
                <Button
                  type="button"
                  variant={dropExecute ? "danger" : "secondary"}
                  size="sm"
                  loading={loading === "drop"}
                  onClick={() => void dropTable()}
                >
                  <Trash2 size={15} aria-hidden="true" />
                  <span>{dropExecute ? t("tableMgmt.drop.run") : t("tableMgmt.drop.dryRun")}</span>
                </Button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-red-200 bg-white p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={dropExecute}
                    onChange={(event) => setDropExecute(event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-red-700 focus:ring-red-500"
                  />
                  <span>{t("tableMgmt.drop.execute")}</span>
                </label>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("tableMgmt.drop.confirmation")}</span>
                  <input
                    value={dropConfirmation}
                    onChange={(event) => setDropConfirmation(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-red-200 bg-white px-3 py-2 focus:border-red-600 focus:ring-2 focus:ring-red-200"
                    placeholder={detail.name}
                  />
                </label>
              </div>
              {dropResult && <DbAdminExecutionResult result={dropResult} />}
            </div>
          ) : (
            <EmptyState title={t("tableMgmt.danger.emptyTitle")} hint={t("tableMgmt.danger.emptyHint")} />
          )}
        </WorkSection>
      </main>
    </>
  );
}
