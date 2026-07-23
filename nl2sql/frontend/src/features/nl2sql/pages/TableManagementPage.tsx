import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, Code2, RefreshCw, Table2, Upload } from "lucide-react";

import {
  Button,
  PageHeader,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiFetch, apiGet, apiPost, isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  ExecutionConfirmationField,
  FileInputControl,
  QueryResultsTable,
  StatementRunnerCard,
  downloadBlob,
  fileToBase64,
} from "../components/DbAdminShared";
import {
  DbObjectDetailPanel,
  DbObjectGrid,
  DbObjectManagementPanelShell,
  DbObjectPanelHeader,
  DbObjectStatusBar,
  DbObjectStepIndicator,
  DropDbObjectDialog,
  dbObjectSortValue,
  type DbObjectDetailTab,
  type DbObjectFilter,
  type DbObjectSortKey,
  type DbObjectSortState,
} from "../components/DbObjectManagementShared";
import type {
  DbAdminExecuteData,
  DbAdminImportTabularData,
  DbAdminObjectDetail,
  DbAdminObjectPage,
  DbAdminObjectsData,
  SchemaRefreshJob,
} from "../types";
import { waitForSchemaRefreshJob } from "../incrementalQueries";
import { filterUserVisibleDbAdminObjectPage } from "../objectVisibility";
import { useDbObjectDetailRequest } from "../useDbObjectDetailRequest";

type ActiveView = "list" | "create" | "import";
type ImportStep = "file" | "execute";

const importFieldClass = "grid min-w-0 gap-1 text-sm font-medium leading-5 text-foreground";
const importControlClass =
  "h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40";

function ImportWizard({
  table,
  sheet,
  filename,
  fileReady,
  mode,
  result,
  step,
  confirmation,
  loading,
  onTableChange,
  onSheetChange,
  onModeChange,
  onFilePick,
  onFileClear,
  onExecute,
  onConfirmationChange,
}: {
  table: string;
  sheet: string;
  filename: string;
  fileReady: boolean;
  mode: string;
  result: DbAdminImportTabularData | null;
  step: ImportStep;
  confirmation: string;
  loading: boolean;
  onTableChange: (value: string) => void;
  onSheetChange: (value: string) => void;
  onModeChange: (value: string) => void;
  onFilePick: (file: File | undefined) => void;
  onFileClear: () => void;
  onExecute: () => void;
  onConfirmationChange: (value: string) => void;
}) {
  const steps: Array<{ id: ImportStep; label: string }> = [
    { id: "file", label: t("tableMgmt.importWizard.stepFile") },
    { id: "execute", label: t("tableMgmt.importWizard.stepExecute") },
  ];
  const activeIndex = steps.findIndex((item) => item.id === step);
  const isConfirmed = confirmation.trim() === "ADMIN_EXECUTE";
  const canExecute = Boolean(table.trim()) && fileReady && isConfirmed;

  return (
    <div className="grid gap-4">
      <DbObjectPanelHeader
        icon={Upload}
        title={t("dataTools.dbAdmin.importTitle")}
        description={t("tableMgmt.import.note")}
      />

      <DbObjectStepIndicator
        steps={steps.map((item) => item.label)}
        activeIndex={activeIndex}
        ariaLabel={t("tableMgmt.importWizard.steps")}
        dataTestId="table-import-steps"
      />

      <div className="grid gap-3 lg:grid-cols-2">
          <label className={importFieldClass}>
            <span>{t("dataTools.dbAdmin.tableName")}</span>
            <input
              value={table}
              onChange={(event) => onTableChange(event.currentTarget.value)}
              className={`${importControlClass} py-2`}
              placeholder="IMPORTED_ORDERS"
            />
          </label>
          <label className={importFieldClass}>
            <span>{t("dataTools.dbAdmin.sheet")}</span>
            <input
              value={sheet}
              onChange={(event) => onSheetChange(event.currentTarget.value)}
              className={`${importControlClass} py-2`}
              placeholder="Sheet1"
            />
          </label>
        </div>

        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem]">
          <FileInputControl
            label={t("dataTools.dbAdmin.file")}
            accept=".csv,.xlsx,.xls"
            filename={filename}
            selectedText={filename ? t("tableMgmt.importWizard.selectedFile", { filename }) : ""}
            emptyText={t("tableMgmt.importWizard.noFile")}
            pickText={t("tableMgmt.importWizard.filePick")}
            replaceText={t("tableMgmt.importWizard.fileReplace")}
            clearAriaLabel={t("tableMgmt.importWizard.clearFile")}
            icon="spreadsheet"
            className="min-w-0"
            dataTestId="table-import-file-field"
            onPick={onFilePick}
            onClear={onFileClear}
          />
          <label className={importFieldClass} data-testid="table-import-mode-field">
            <span>{t("dataTools.dbAdmin.mode")}</span>
            <select
              value={mode}
              onChange={(event) => onModeChange(event.currentTarget.value)}
              className={`${importControlClass} py-2`}
            >
              <option value="create">{t("dataTools.dbAdmin.mode.create")}</option>
              <option value="append">{t("dataTools.dbAdmin.mode.append")}</option>
              <option value="truncate">{t("dataTools.dbAdmin.mode.truncate")}</option>
              <option value="replace">{t("dataTools.dbAdmin.mode.replace")}</option>
            </select>
          </label>
        </div>

        {result && (
          <section className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm" aria-label={t("tableMgmt.importWizard.result")}>
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant={result.executed ? "success" : "neutral"} label={result.executed ? "executed" : "not executed"} />
              <StatusBadge variant="info" label={result.table_name} />
              <StatusBadge variant="info" label={t("tableMgmt.importWizard.rows", { count: result.row_count })} />
              <StatusBadge variant="neutral" label={result.mode} />
            </div>
            {result.warnings.map((warning) => (
              <p key={warning} className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-warning">
                {warning}
              </p>
            ))}
            <pre className="overflow-auto rounded-md border border-border bg-card p-3 font-mono text-sm leading-6 text-foreground">
              <code>{`${result.ddl}\n\n${result.insert_sql}`}</code>
            </pre>
            {result.sample_rows.length > 0 && (
              <QueryResultsTable
                results={{
                  columns: Object.keys(result.sample_rows[0] ?? {}),
                  rows: result.sample_rows,
                  total: result.sample_rows.length,
                }}
              />
            )}
          </section>
        )}

      <fieldset className="grid gap-3 rounded-md border border-border bg-card p-3">
        <legend className="px-1 text-sm font-semibold text-foreground">{t("tableMgmt.importWizard.executeTitle")}</legend>
        <ExecutionConfirmationField
          value={confirmation}
          onChange={onConfirmationChange}
          confirmed={isConfirmed}
          placeholder="ADMIN_EXECUTE"
          expectedLabel="ADMIN_EXECUTE"
          helper={t("tableMgmt.importWizard.executeHint")}
          actions={
            <Button
              type="button"
              variant="danger"
              size="sm"
              className="w-full sm:w-auto"
              loading={loading}
              disabled={!canExecute}
              onClick={onExecute}
            >
              <Upload size={15} aria-hidden="true" />
              <span>{t("dataTools.dbAdmin.import")}</span>
            </Button>
          }
        />
      </fieldset>
    </div>
  );
}

export function TableManagementPage() {
  const [tables, setTables] = useState<DbAdminObjectsData | null>(null);
  const [detailTab, setDetailTab] = useState<DbObjectDetailTab>("columns");
  const [exactCountDone, setExactCountDone] = useState(false);
  const [activeView, setActiveView] = useState<ActiveView>("list");
  const [tableSearch, setTableSearch] = useState("");
  const [tableFilter, setTableFilter] = useState<DbObjectFilter>("all");
  const [tableSort, setTableSort] = useState<DbObjectSortState>({ key: "name", direction: "asc" });
  const [dropTargetName, setDropTargetName] = useState("");
  const [dropConfirmation, setDropConfirmation] = useState("");
  const [importTable, setImportTable] = useState("");
  const [importFilename, setImportFilename] = useState("");
  const [importBase64, setImportBase64] = useState("");
  const [importSheet, setImportSheet] = useState("");
  const [importMode, setImportMode] = useState("create");
  const [importStep, setImportStep] = useState<ImportStep>("file");
  const [importConfirmation, setImportConfirmation] = useState("");
  const [importResult, setImportResult] = useState<DbAdminImportTabularData | null>(null);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();
  const detailRequest = useDbObjectDetailRequest({
    collectionPath: "/api/nl2sql/db-admin/tables",
    loadErrorMessage: t("tableMgmt.error.detail"),
    timeoutErrorMessage: t("dbAdmin.detail.timeout", { seconds: 15 }),
  });
  const {
    selectedName: selectedTableName,
    detail,
    setDetail,
  } = detailRequest;

  const fetchDetail = async (name: string) => {
    setDetailTab("columns");
    setExactCountDone(false);
    await detailRequest.load(name);
  };

  // DDL は重い GET_DDL を伴うため列タブでは取得せず、DDL タブ初回表示時に後追いで取得する。
  const handleDetailTabChange = (nextTab: DbObjectDetailTab) => {
    setDetailTab(nextTab);
    if (nextTab !== "ddl" || !detail || detail.ddl) return;
    const name = detail.name;
    void (async () => {
      try {
        const full = await apiGet<DbAdminObjectDetail>(
          `/api/nl2sql/db-admin/tables/${encodeURIComponent(name)}?include_ddl=1`,
        );
        setDetail((current) =>
          current && current.name === name ? { ...current, ddl: full.ddl } : current,
        );
      } catch {
        // DDL 取得失敗時は既存の "-" 表示のまま
      }
    })();
  };

  // 行数バッジは既定で num_rows 統計(一覧と統一・高速)。正確な件数は明示操作で COUNT(*)。
  const handleExactCount = async (name: string) => {
    setLoading("count");
    try {
      const full = await apiGet<DbAdminObjectDetail>(
        `/api/nl2sql/db-admin/tables/${encodeURIComponent(name)}?include_ddl=0&exact_count=1`,
      );
      setDetail((current) =>
        current && current.name === name ? { ...current, row_count: full.row_count } : current,
      );
      setExactCountDone(true);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.exactCount"));
    } finally {
      setLoading("");
    }
  };

  const load = async (refreshSchema = false) => {
    const sequence = loadSequence.current + 1;
    const detailVersionAtStart = detailRequest.requestVersion();
    loadSequence.current = sequence;
    setLoading(refreshSchema ? "schema-refresh" : "load");
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        // 列サンプル値は詳細 API が返すため catalog 全取得はしない。schema-refresh 時のみ
        // サーバ側 catalog を再構築してから一覧(refreshed_at を含む)を取り直す。
        if (refreshSchema) {
          const job = await apiPost<SchemaRefreshJob>("/api/schema/refresh-jobs", undefined, {
            signal,
            timeoutMs: API_TIMEOUT_MS.jobControl,
          });
          if (job.job_id) await waitForSchemaRefreshJob(job.job_id, signal);
        }
        const page = filterUserVisibleDbAdminObjectPage(
          await apiGet<DbAdminObjectPage>(
            "/api/nl2sql/db-admin/objects?limit=100&type=table&row_state=all",
            { signal, timeoutMs: API_TIMEOUT_MS.interactiveList }
          )
        );
        const tableData: DbAdminObjectsData = {
          runtime: page.runtime,
          items: page.items,
          refreshed_at: page.refreshed_at,
          warnings: page.warnings,
        };
        if (signal.aborted || sequence !== loadSequence.current) return;
        setTables(tableData);
        const nextSelected =
          tableData.items.find((item) => item.name === selectedTableName)?.name ||
          tableData.items[0]?.name ||
          "";
        if (detailRequest.requestVersion() === detailVersionAtStart) {
          if (nextSelected) {
            void fetchDetail(nextSelected);
          } else {
            detailRequest.clear();
          }
        }
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.load"));
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

  const reloadAfterMutation = async (result: { schema_refresh_job_id?: string }) => {
    if (result.schema_refresh_job_id) {
      await waitForSchemaRefreshJob(result.schema_refresh_job_id);
    }
    await load();
  };

  const filteredTables = useMemo(() => {
    const q = tableSearch.trim().toLowerCase();
    return (tables?.items ?? [])
      .filter((item) => {
        if (tableFilter === "with_rows" && !(item.row_count != null && item.row_count > 0)) return false;
        if (tableFilter === "empty_rows" && item.row_count !== 0) return false;
        if (!q) return true;
        return (
          item.name.toLowerCase().includes(q) ||
          item.comment.toLowerCase().includes(q) ||
          item.owner.toLowerCase().includes(q)
        );
      })
      .sort((left, right) => {
        const a = dbObjectSortValue(left, tableSort.key);
        const b = dbObjectSortValue(right, tableSort.key);
        const result = a < b ? -1 : a > b ? 1 : 0;
        return tableSort.direction === "asc" ? result : -result;
      });
  }, [tables, tableSearch, tableFilter, tableSort]);

  const toggleSort = (key: DbObjectSortKey) => {
    setTableSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const pickImportFile = async (file: File | undefined) => {
    if (!file) return;
    setImportFilename(file.name);
    setImportBase64("");
    setImportBase64(await fileToBase64(file));
    setImportResult(null);
    setImportStep("file");
  };

  const clearImportFile = () => {
    setImportFilename("");
    setImportBase64("");
    setImportResult(null);
    setImportStep("file");
  };

  const importTabular = async () => {
    if (!importTable.trim() || !importBase64) return;
    setLoading("import-tabular");
    setMessage("");
    try {
      const result = await apiPost<DbAdminImportTabularData>("/api/nl2sql/db-admin/import-tabular", {
        table_name: importTable,
        content_base64: importBase64,
        filename: importFilename || "upload.csv",
        sheet_name: importSheet,
        mode: importMode,
        confirmation: importConfirmation,
        reason: "ui-table-management-import-tabular",
      });
      setImportResult(result);
      setImportStep("execute");
      if (result.executed) {
        await reloadAfterMutation(result);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("dataTools.error.import"));
    } finally {
      setLoading("");
    }
  };

  const downloadColumnsXlsx = async (name: string) => {
    setLoading("table-export");
    setMessage("");
    try {
      const response = await apiFetch(`/api/nl2sql/db-admin/tables/${encodeURIComponent(name)}/export.xlsx`, {
        headers: {
          Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
      });
      if (!response.ok) {
        throw new Error(t("tableMgmt.error.export"));
      }
      downloadBlob(`${name.toLowerCase()}_columns.xlsx`, await response.blob());
      toast.success(t("common.action.downloaded"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.export"));
    } finally {
      setLoading("");
    }
  };

  const openDropDialog = (name: string) => {
    setDropTargetName(name);
    setDropConfirmation("");
  };

  const dropTable = async () => {
    if (!dropTargetName) return;
    setLoading("drop");
    setMessage("");
    try {
      const result = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin/drop-table", {
        table_name: dropTargetName,
        confirmation: dropConfirmation,
        reason: "ui-table-management-drop",
      });
      if (result.executed) {
        const dropped = dropTargetName;
        setDropTargetName("");
        setDropConfirmation("");
        toast.success(t("tableMgmt.drop.success", { name: dropped }));
        await reloadAfterMutation(result);
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("tableMgmt.error.drop"));
    } finally {
      setLoading("");
    }
  };

  const taskContent =
    activeView === "create" ? (
      <StatementRunnerCard
        policy="table_ddl"
        title={t("tableMgmt.create.title")}
        description={t("tableMgmt.create.note")}
        placeholder={t("tableMgmt.create.placeholder")}
        progress={({ hasSql }) => (
          <DbObjectStepIndicator
            steps={[t("tableMgmt.create.stepSql"), t("tableMgmt.create.stepExecute")]}
            activeIndex={hasSql ? 1 : 0}
            ariaLabel={t("tableMgmt.create.steps")}
            dataTestId="table-create-steps"
          />
        )}
        confirmationTitle={t("tableMgmt.create.executeTitle")}
        executeOnly
        framed={false}
        onExecuted={reloadAfterMutation}
      />
    ) : activeView === "import" ? (
      <ImportWizard
        table={importTable}
        sheet={importSheet}
        filename={importFilename}
        fileReady={Boolean(importBase64)}
        mode={importMode}
        result={importResult}
        step={importStep}
        confirmation={importConfirmation}
        loading={loading === "import-tabular"}
        onTableChange={(value) => {
          setImportTable(value);
          setImportResult(null);
          setImportStep("file");
        }}
        onSheetChange={(value) => {
          setImportSheet(value);
          setImportResult(null);
          setImportStep("file");
        }}
        onModeChange={(value) => {
          setImportMode(value);
          setImportResult(null);
          setImportStep("file");
        }}
        onFilePick={(file) => void pickImportFile(file)}
        onFileClear={clearImportFile}
        onExecute={() => void importTabular()}
        onConfirmationChange={(value) => {
          setImportConfirmation(value);
          if (value.trim()) setImportStep("execute");
        }}
      />
    ) : null;

  return (
    <>
      <PageHeader
        title={t("nav.tableManagement")}
        subtitle={t("tableMgmt.subtitle")}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <PageNotice
          notice={
            message
              ? { tone: "danger", message: `${message} ${t("tableMgmt.error.retryHint")}` }
              : null
          }
          action={
            <Button type="button" variant="secondary" size="sm" onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("tableMgmt.action.refresh")}</span>
            </Button>
          }
        />

        <DbObjectStatusBar
          count={tables?.items.length ?? 0}
          runtime={tables?.runtime ?? "deterministic"}
          refreshedAt={tables?.refreshed_at ?? ""}
          loading={loading}
          labels={{
            ariaLabel: t("tableMgmt.toolbar.status"),
            count: t("tableMgmt.metric.tables"),
            runtime: t("tableMgmt.metric.runtime"),
            refreshedAt: t("tableMgmt.metric.schemaRefreshed"),
            refresh: t("tableMgmt.action.refresh"),
            schemaRefresh: t("tableMgmt.action.schemaRefresh"),
          }}
          onRefresh={() => void load()}
          onSchemaRefresh={() => void load(true)}
        />

        {activeView === "list" ? (
          <>
            <div
              className="flex flex-wrap items-center justify-end gap-2"
              data-testid="table-management-actions"
              aria-label={t("tableMgmt.tabs.label")}
            >
              <Button type="button" variant="secondary" size="sm" onClick={() => setActiveView("create")}>
                <Code2 size={15} aria-hidden="true" />
                <span>{t("tableMgmt.create.title")}</span>
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={() => setActiveView("import")}>
                <Upload size={15} aria-hidden="true" />
                <span>{t("dataTools.dbAdmin.importTitle")}</span>
              </Button>
            </div>
            <DbObjectManagementPanelShell
              id="table-management-panel-list"
              role="region"
              idPrefix="table-management"
              ariaLabel={t("tableMgmt.workspace.label")}
              splitId="table-management-list"
              preferredWidePane="right"
            >
            <DbObjectGrid
              idPrefix="table-management"
              headingId="table-grid-heading"
              icon={Table2}
              items={filteredTables}
              selectedName={selectedTableName}
              loading={loading === "load" && !tables}
              search={tableSearch}
              filter={tableFilter}
              sort={tableSort}
              labels={{
                title: t("tableMgmt.list.title"),
                hint: t("tableMgmt.grid.hint"),
                count: t("tableMgmt.grid.count", { count: filteredTables.length }),
                loading: t("tableMgmt.list.loading"),
                emptyTitle: t("tableMgmt.list.emptyTitle"),
                emptyHint: t("tableMgmt.list.emptyHint"),
                noResultsTitle: t("tableMgmt.list.noResultsTitle"),
                noResultsHint: t("tableMgmt.list.noResultsHint"),
                filter: t("tableMgmt.toolbar.filter"),
                filterAll: t("tableMgmt.toolbar.filterAll"),
                filterWithRows: t("tableMgmt.toolbar.filterWithRows"),
                filterEmptyRows: t("tableMgmt.toolbar.filterEmptyRows"),
                objectName: t("tableMgmt.grid.tableName"),
                rows: t("tableMgmt.grid.rows"),
                owner: t("tableMgmt.grid.owner"),
                actions: t("tableMgmt.grid.actions"),
                detail: t("tableMgmt.grid.detail"),
                drop: t("tableMgmt.grid.drop"),
                showObject: (name) => t("tableMgmt.grid.showTable", { name }),
              }}
              onSearchChange={setTableSearch}
              onFilterChange={setTableFilter}
              onSortChange={toggleSort}
              onSelect={(name) => void fetchDetail(name)}
              onDrop={openDropDialog}
            />
            <DbObjectDetailPanel
              idPrefix="table-management"
              headingId="table-detail-heading"
              detail={detail}
              loading={detailRequest.loading || (loading === "load" && !tables)}
              error={detailRequest.error}
              exporting={loading === "table-export"}
              countingRows={loading === "count"}
              tab={detailTab}
              labels={{
                loading: t("tableMgmt.detail.loading"),
                tabsLabel: t("tableMgmt.detailTabs.label"),
                columns: t("tableMgmt.detailTabs.columns"),
                ddl: t("tableMgmt.detailTabs.ddl"),
                export: t("tableMgmt.export"),
                exportAria: t("tableMgmt.exportColumns"),
                exactCount: t("tableMgmt.exactCount"),
                exactCountAria: t("tableMgmt.exactCountAria"),
                drop: t("tableMgmt.grid.drop"),
              }}
              onTabChange={handleDetailTabChange}
              onRetry={() => void fetchDetail(selectedTableName)}
              onExport={(name) => void downloadColumnsXlsx(name)}
              onExactCount={exactCountDone ? undefined : (name) => void handleExactCount(name)}
              onDrop={openDropDialog}
            />
            </DbObjectManagementPanelShell>
          </>
        ) : (
          <>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={() => setActiveView("list")}>
                <ArrowLeft size={15} aria-hidden="true" />
                <span>{t("tableMgmt.action.backToList")}</span>
              </Button>
            </div>
            <DbObjectManagementPanelShell
              id={`table-management-panel-${activeView}`}
              role="region"
              idPrefix="table-management"
              ariaLabel={t("tableMgmt.toolbar.taskPanel")}
            >
              {taskContent}
            </DbObjectManagementPanelShell>
          </>
        )}
      </main>

      {dropTargetName && (
        <DropDbObjectDialog
          objectName={dropTargetName}
          confirmation={dropConfirmation}
          loading={loading === "drop"}
          labels={{
            title: t("tableMgmt.dropDialog.title"),
            subtitle: t("tableMgmt.dropDialog.subtitle"),
            close: t("tableMgmt.dropDialog.close"),
            target: t("tableMgmt.dropDialog.target"),
            executeTitle: t("tableMgmt.dropDialog.executeTitle"),
            executeHint: t("tableMgmt.dropDialog.executeHint"),
            cancel: t("tableMgmt.dropDialog.cancel"),
            run: t("tableMgmt.drop.run"),
          }}
          onConfirmationChange={setDropConfirmation}
          onExecute={() => void dropTable()}
          onClose={() => setDropTargetName("")}
        />
      )}
    </>
  );
}
