import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Bot, Database, FileSpreadsheet, KeyRound, Play, RefreshCw, type LucideIcon } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { csvImportPayload, defaultCsvImportForm, type CsvImportFormState } from "../csvImportState";
import { engineLabel } from "../labels";
import type { AssetRefreshData, CsvImportData, DiagnosticsData, Nl2SqlProfile, SchemaCatalog } from "../types";

export function ConnectionSettingsPage() {
  const [diagnostics, setDiagnostics] = useState<DiagnosticsData | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setDiagnostics(await apiGet<DiagnosticsData>("/api/nl2sql/diagnostics"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    <>
      <PageHeader
        title={t("nav.settingsConnection")}
        subtitle={t("settings.connection.subtitle")}
        actions={
          <Button type="button" size="sm" variant="secondary" loading={loading} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("settings.action.diagnose")}</span>
          </Button>
        }
      />
      <SettingsShell icon={KeyRound} title={t("settings.connection.checks")}>
        <div className="grid gap-3">
          {diagnostics?.checks.map((check) => (
            <div key={check.name} className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-slate-200 p-3">
              <div>
                <p className="font-mono text-xs text-slate-500">{check.name}</p>
                <p className="mt-1 text-sm text-slate-800">{check.message}</p>
              </div>
              <StatusBadge variant={check.status === "ok" ? "success" : "warning"} label={check.status} />
            </div>
          ))}
        </div>
      </SettingsShell>
    </>
  );
}

export function ModelSettingsPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("default");
  const [refreshResults, setRefreshResults] = useState<AssetRefreshData[]>([]);
  const [loading, setLoading] = useState("");

  useEffect(() => {
    void apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles").then((data) => {
      setProfiles(data);
      if (data.length > 0) setProfileId(data[0].id);
    });
  }, []);

  const refresh = async (kind: "select_ai" | "select_ai_agent") => {
    setLoading(kind);
    try {
      const path =
        kind === "select_ai"
          ? "/api/nl2sql/select-ai/profiles/refresh"
          : "/api/nl2sql/select-ai-agent/assets/refresh";
      const result = await apiPost<AssetRefreshData>(
        `${path}?profile_id=${encodeURIComponent(profileId)}`
      );
      setRefreshResults((current) => [
        result,
        ...current.filter((item) => item.engine !== result.engine),
      ]);
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.model.engines")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <label className="grid max-w-xl gap-1 text-sm font-medium text-slate-800">
              <span>{t("settings.model.profile")}</span>
              <select
                value={profileId}
                onChange={(event) => setProfileId(event.currentTarget.value)}
                className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
              >
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid gap-3 md:grid-cols-3">
              <EngineCard
                title={t("nl2sql.engine.agent")}
                description={t("nl2sql.engine.agent.desc")}
                action={t("settings.model.refreshAgent")}
                loading={loading === "select_ai_agent"}
                onClick={() => void refresh("select_ai_agent")}
              />
              <EngineCard
                title={t("nl2sql.engine.selectAi")}
                description={t("nl2sql.engine.selectAi.desc")}
                action={t("settings.model.refreshSelectAi")}
                loading={loading === "select_ai"}
                onClick={() => void refresh("select_ai")}
              />
              <EngineCard
                title={t("nl2sql.engine.direct")}
                description={t("nl2sql.engine.direct.desc")}
                action={t("settings.model.directReady")}
                disabled
              />
            </div>
          </CardContent>
        </Card>
        {refreshResults.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>{t("settings.model.lastRefresh")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              {refreshResults.map((result) => (
                <AssetStatusPanel key={result.engine} result={result} />
              ))}
            </CardContent>
          </Card>
        )}
      </main>
    </>
  );
}

export function DatabaseSettingsPage() {
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [csvForm, setCsvForm] = useState<CsvImportFormState>(() => defaultCsvImportForm());
  const [csvResult, setCsvResult] = useState<CsvImportData | null>(null);
  const [csvLoading, setCsvLoading] = useState(false);
  const [csvError, setCsvError] = useState("");

  useEffect(() => {
    void apiGet<SchemaCatalog>("/api/schema/catalog").then(setCatalog);
  }, []);

  const totalColumns = catalog?.tables.reduce((sum, table) => sum + table.columns.length, 0) ?? 0;
  const canSubmitCsv = csvForm.tableName.trim().length > 0 && csvForm.csvText.trim().length > 0;

  const updateCsvForm = <K extends keyof CsvImportFormState>(key: K, value: CsvImportFormState[K]) => {
    setCsvForm((current) => ({ ...current, [key]: value }));
  };

  const importCsv = async () => {
    if (!canSubmitCsv) return;
    setCsvLoading(true);
    setCsvError("");
    try {
      const result = await apiPost<CsvImportData>("/api/schema/import-csv", csvImportPayload(csvForm));
      setCsvResult(result);
      if (result.executed) {
        setCatalog(await apiGet<SchemaCatalog>("/api/schema/catalog"));
      }
    } catch (err) {
      setCsvError(err instanceof Error ? err.message : t("settings.database.import.error"));
    } finally {
      setCsvLoading(false);
    }
  };

  return (
    <>
      <PageHeader title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database size={18} aria-hidden="true" />
              {t("settings.database.boundary")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-3">
              <Metric label={t("settings.database.tables")} value={String(catalog?.tables.length ?? 0)} />
              <Metric label={t("settings.database.columns")} value={String(totalColumns)} />
              <Metric label={t("settings.database.safety")} value="SELECT/WITH" />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileSpreadsheet size={18} aria-hidden="true" />
              {t("settings.database.import.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5">
            {csvError && (
              <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
                {csvError}
              </div>
            )}
            <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
              <div className="grid gap-4 content-start">
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("settings.database.import.tableName")}</span>
                  <input
                    value={csvForm.tableName}
                    onChange={(event) => updateCsvForm("tableName", event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder={t("settings.database.import.tableNamePlaceholder")}
                  />
                </label>
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={csvForm.execute}
                    onChange={(event) => updateCsvForm("execute", event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("settings.database.import.execute")}</span>
                </label>
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={csvForm.replaceExisting}
                    onChange={(event) => updateCsvForm("replaceExisting", event.currentTarget.checked)}
                    disabled={!csvForm.execute}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500 disabled:opacity-50"
                  />
                  <span>{t("settings.database.import.replace")}</span>
                </label>
                <Button
                  type="button"
                  variant={csvForm.execute && csvForm.replaceExisting ? "danger" : "primary"}
                  size="md"
                  loading={csvLoading}
                  disabled={!canSubmitCsv || csvLoading}
                  onClick={() => void importCsv()}
                >
                  {csvForm.execute ? <Play size={16} aria-hidden="true" /> : <FileSpreadsheet size={16} aria-hidden="true" />}
                  <span>
                    {csvForm.execute
                      ? t("settings.database.import.actionExecute")
                      : t("settings.database.import.actionDryRun")}
                  </span>
                </Button>
              </div>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("settings.database.import.csv")}</span>
                <textarea
                  value={csvForm.csvText}
                  onChange={(event) => updateCsvForm("csvText", event.currentTarget.value)}
                  rows={10}
                  className="min-h-72 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={t("settings.database.import.csvPlaceholder")}
                />
              </label>
            </div>

            {csvResult && <CsvImportResult result={csvResult} />}
          </CardContent>
        </Card>
      </main>
    </>
  );
}

function SettingsShell({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: ReactNode;
}) {
  return (
    <main className="p-4 lg:p-8">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Icon size={18} aria-hidden="true" />
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>
    </main>
  );
}

function EngineCard({
  title,
  description,
  action,
  loading,
  disabled,
  onClick,
}: {
  title: string;
  description: string;
  action: string;
  loading?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <div className="grid gap-3 rounded-md border border-slate-200 p-4">
      <div>
        <p className="font-semibold text-slate-900">{title}</p>
        <p className="mt-1 text-sm text-slate-600">{description}</p>
      </div>
      <Button type="button" variant="secondary" size="sm" loading={loading} disabled={disabled} onClick={onClick}>
        {!disabled && <RefreshCw size={15} aria-hidden="true" />}
        {action}
      </Button>
    </div>
  );
}

function AssetStatusPanel({ result }: { result: AssetRefreshData }) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-2">
          <Bot size={17} className="mt-0.5 text-sky-700" aria-hidden="true" />
          <div>
            <p className="font-semibold text-slate-900">{engineLabel(result.engine)}</p>
            <p className="mt-1 text-xs text-slate-500">
              {result.refreshed_at ? new Date(result.refreshed_at).toLocaleString("ja-JP") : "-"}
            </p>
          </div>
        </div>
        <StatusBadge variant={result.refreshed ? "success" : "warning"} label={result.status} />
      </div>
      <dl className="grid gap-2 text-sm">
        {Object.entries(result.asset_names).map(([name, value]) => (
          <div key={name} className="grid gap-1 rounded-md bg-slate-50 p-2">
            <dt className="text-xs font-medium uppercase text-slate-500">{name}</dt>
            <dd className="break-all font-mono text-xs text-slate-800">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function CsvImportResult({ result }: { result: CsvImportData }) {
  const sampleColumns = result.columns.map((column) => column.column_name);
  return (
    <section className="grid gap-4 rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900">{result.table_name}</p>
          <p className="mt-1 text-sm text-slate-600">
            {t("settings.database.import.resultSummary", {
              rows: result.row_count,
              columns: result.columns.length,
            })}
          </p>
        </div>
        <StatusBadge
          variant={result.executed ? "success" : "info"}
          label={result.executed ? t("settings.database.import.executed") : t("settings.database.import.dryRun")}
        />
      </div>

      {result.warnings.length > 0 && (
        <div className="grid gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          {result.warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </div>
      )}

      <div className="overflow-auto rounded-md border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-white">
            <tr>
              <th className="px-3 py-2 text-left">{t("settings.database.import.sourceColumn")}</th>
              <th className="px-3 py-2 text-left">{t("settings.database.import.oracleColumn")}</th>
              <th className="px-3 py-2 text-left">{t("schema.col.type")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {result.columns.map((column) => (
              <tr key={column.column_name}>
                <td className="px-3 py-2">{column.source_name}</td>
                <td className="px-3 py-2 font-mono text-xs">{column.column_name}</td>
                <td className="px-3 py-2">{column.data_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <SqlSnippet title={t("settings.database.import.ddl")} value={result.ddl} />
        <SqlSnippet title={t("settings.database.import.insertSql")} value={result.insert_sql} />
      </div>

      {result.sample_rows.length > 0 && (
        <div className="overflow-auto rounded-md border border-slate-200 bg-white">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-white">
              <tr>
                {sampleColumns.map((column) => (
                  <th key={column} className="px-3 py-2 text-left font-mono text-xs">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {result.sample_rows.map((row, index) => (
                <tr key={index}>
                  {sampleColumns.map((column) => (
                    <td key={column} className="px-3 py-2">
                      {row[column] ?? "-"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SqlSnippet({ title, value }: { title: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-slate-200 bg-white p-3">
      <p className="text-xs font-medium text-slate-500">{title}</p>
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-slate-800">
        <code>{value}</code>
      </pre>
    </div>
  );
}
