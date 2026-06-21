import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Bot,
  Database,
  Download,
  FileSpreadsheet,
  FlaskConical,
  Gauge,
  KeyRound,
  Play,
  RefreshCw,
  Save,
  Wand2,
  type LucideIcon,
} from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { csvImportPayload, defaultCsvImportForm, type CsvImportFormState } from "../csvImportState";
import { engineLabel } from "../labels";
import type {
  AssetRefreshData,
  CsvImportData,
  DiagnosticsData,
  EvaluateData,
  Nl2SqlProfile,
  ProfileUpsertPayload,
  SchemaCatalog,
  SyntheticCasesData,
} from "../types";

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
        title={t("nav.nl2sqlSettingsConnection")}
        subtitle={t("nl2sqlSettings.connection.subtitle")}
        actions={
          <Button type="button" size="sm" variant="secondary" loading={loading} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("settings.action.diagnose")}</span>
          </Button>
        }
      />
      <SettingsShell icon={Gauge} title={t("ops.readiness.title")}>
        <div className="grid gap-3 md:grid-cols-2">
          {(diagnostics?.readiness ?? []).map((item) => (
            <section key={item.area} className="grid gap-3 rounded-md border border-slate-200 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-slate-900">{item.label}</p>
                  <p className="mt-1 text-sm leading-6 text-slate-700">{item.summary}</p>
                </div>
                <StatusBadge variant={item.status === "ok" ? "success" : "warning"} label={item.status} />
              </div>
              {item.next_action && (
                <p className="rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700">
                  <span className="font-medium text-slate-900">{t("ops.readiness.nextAction")}: </span>
                  {item.next_action}
                </p>
              )}
              <p className="text-xs leading-5 text-slate-500">
                {t("ops.readiness.relatedChecks")}: {item.related_checks.join(", ")}
              </p>
            </section>
          ))}
          {(!diagnostics?.readiness || diagnostics.readiness.length === 0) && (
            <div className="grid min-h-32 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500 md:col-span-2">
              {t("ops.readiness.empty")}
            </div>
          )}
        </div>
      </SettingsShell>
      <SettingsShell icon={KeyRound} title={t("nl2sqlSettings.connection.checks")}>
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

function trainingExamplesToText(examples: Array<Record<string, string>>) {
  return examples.map((example) => `${example.question ?? ""} => ${example.sql ?? example.expected_sql ?? ""}`).join("\n");
}

function trainingLines(text: string) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function trainingTextToCases(text: string) {
  return trainingLines(text)
    .map((line) => {
      const [question, ...rest] = line.split("=>");
      return { question: question.trim(), expected_sql: rest.join("=>").trim() };
    })
    .filter((example) => example.question && example.expected_sql);
}

function trainingTextToFewShot(text: string) {
  return trainingTextToCases(text).map((example) => ({
    question: example.question,
    sql: example.expected_sql,
  }));
}

function trainingPayload(profile: Nl2SqlProfile, trainingText: string): ProfileUpsertPayload {
  return {
    name: profile.name,
    description: profile.description,
    allowed_tables: profile.allowed_tables,
    glossary: profile.glossary,
    sql_rules: profile.sql_rules,
    default_row_limit: profile.default_row_limit,
    safety_policy: profile.safety_policy,
    few_shot_examples: trainingTextToFewShot(trainingText),
  };
}

function escapeTrainingCsv(value: string) {
  const normalized = value.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (/[",\n]/.test(normalized)) {
    return `"${normalized.replace(/"/g, '""')}"`;
  }
  return normalized;
}

function downloadTrainingCsv(profileId: string, trainingText: string) {
  const rows = trainingTextToCases(trainingText).map(
    (example) => `${escapeTrainingCsv(example.question)},${escapeTrainingCsv(example.expected_sql)}`
  );
  const blob = new Blob([["QUESTION,SQL", ...rows].join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `nl2sql_training_${profileId || "profile"}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function ModelSettingsPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("default");
  const [trainingText, setTrainingText] = useState("");
  const [syntheticCases, setSyntheticCases] = useState<SyntheticCasesData | null>(null);
  const [evaluation, setEvaluation] = useState<EvaluateData | null>(null);
  const [refreshResults, setRefreshResults] = useState<AssetRefreshData[]>([]);
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === profileId) ?? profiles[0] ?? null,
    [profileId, profiles]
  );

  const trainingCases = useMemo(() => trainingTextToCases(trainingText), [trainingText]);

  useEffect(() => {
    void apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles").then((data) => {
      setProfiles(data);
      const next = data.find((profile) => profile.id === profileId) ?? data[0] ?? null;
      if (next) {
        setProfileId(next.id);
        setTrainingText(trainingExamplesToText(next.few_shot_examples));
      }
    });
  }, []);

  const selectProfile = (nextId: string) => {
    const next = profiles.find((profile) => profile.id === nextId) ?? null;
    setProfileId(nextId);
    setTrainingText(next ? trainingExamplesToText(next.few_shot_examples) : "");
    setSyntheticCases(null);
    setEvaluation(null);
    setMessage("");
  };

  const refresh = async (kind: "select_ai" | "select_ai_agent") => {
    setLoading(kind);
    setMessage("");
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

  const saveTrainingData = async () => {
    if (!selectedProfile) return;
    setLoading("training-save");
    setMessage("");
    try {
      const updated = await apiPatch<Nl2SqlProfile>(
        `/api/nl2sql/profiles/${selectedProfile.id}`,
        trainingPayload(selectedProfile, trainingText)
      );
      setProfiles((current) => current.map((profile) => (profile.id === updated.id ? updated : profile)));
      setTrainingText(trainingExamplesToText(updated.few_shot_examples));
      setMessage(t("nl2sqlSettings.model.training.saved"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("nl2sqlSettings.model.training.errorSave"));
    } finally {
      setLoading("");
    }
  };

  const generateSynthetic = async () => {
    setLoading("training-synthetic");
    setMessage("");
    try {
      const data = await apiPost<SyntheticCasesData>(
        `/api/nl2sql/synthetic-cases?profile_id=${encodeURIComponent(profileId)}&limit=6`
      );
      setSyntheticCases(data);
      if (data.cases.length > 0) {
        const nextText = data.cases.map((item) => `${item.question} => ${item.expected_sql}`).join("\n");
        setTrainingText((current) => [current.trim(), nextText].filter(Boolean).join("\n"));
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("nl2sqlSettings.model.training.errorSynthetic"));
    } finally {
      setLoading("");
    }
  };

  const evaluateTraining = async () => {
    setLoading("training-evaluate");
    setMessage("");
    try {
      setEvaluation(await apiPost<EvaluateData>("/api/nl2sql/evaluate", { cases: trainingCases, engine: "auto" }));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("nl2sqlSettings.model.training.errorEvaluate"));
    } finally {
      setLoading("");
    }
  };

  return (
    <>
      <PageHeader title={t("nav.nl2sqlSettingsModel")} subtitle={t("nl2sqlSettings.model.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        {message && (
          <div className="rounded-md border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900" role="status">
            {message}
          </div>
        )}
        <Card>
          <CardHeader>
            <CardTitle>{t("nl2sqlSettings.model.engines")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <label className="grid max-w-xl gap-1 text-sm font-medium text-slate-800">
              <span>{t("nl2sqlSettings.model.profile")}</span>
              <select
                aria-label={t("nl2sqlSettings.model.profile")}
                value={profileId}
                onChange={(event) => selectProfile(event.currentTarget.value)}
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
                action={t("nl2sqlSettings.model.refreshAgent")}
                loading={loading === "select_ai_agent"}
                onClick={() => void refresh("select_ai_agent")}
              />
              <EngineCard
                title={t("nl2sql.engine.selectAi")}
                description={t("nl2sql.engine.selectAi.desc")}
                action={t("nl2sqlSettings.model.refreshSelectAi")}
                loading={loading === "select_ai"}
                onClick={() => void refresh("select_ai")}
              />
              <EngineCard
                title={t("nl2sql.engine.direct")}
                description={t("nl2sql.engine.direct.desc")}
                action={t("nl2sqlSettings.model.directReady")}
                disabled
              />
            </div>
          </CardContent>
        </Card>
        {refreshResults.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>{t("nl2sqlSettings.model.lastRefresh")}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              {refreshResults.map((result) => (
                <AssetStatusPanel key={result.engine} result={result} />
              ))}
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FlaskConical size={18} aria-hidden="true" />
              {t("nl2sqlSettings.model.training.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.8fr)]">
            <div className="grid gap-4">
              <div className="grid gap-3 md:grid-cols-3">
                <Metric
                  label={t("nl2sqlSettings.model.training.examples")}
                  value={String(trainingCases.length)}
                />
                <Metric
                  label={t("nl2sqlSettings.model.training.tables")}
                  value={String(selectedProfile?.allowed_tables.length ?? 0)}
                />
                <Metric
                  label={t("nl2sqlSettings.model.training.terms")}
                  value={String(Object.keys(selectedProfile?.glossary ?? {}).length)}
                />
              </div>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("nl2sqlSettings.model.training.label")}</span>
                <textarea
                  value={trainingText}
                  onChange={(event) => setTrainingText(event.currentTarget.value)}
                  rows={8}
                  className="min-h-48 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={t("nl2sqlSettings.model.training.placeholder")}
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  loading={loading === "training-save"}
                  disabled={!selectedProfile}
                  onClick={() => void saveTrainingData()}
                >
                  <Save size={15} aria-hidden="true" />
                  <span>{t("nl2sqlSettings.model.training.save")}</span>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  loading={loading === "training-synthetic"}
                  onClick={() => void generateSynthetic()}
                >
                  <Wand2 size={15} aria-hidden="true" />
                  <span>{t("nl2sqlSettings.model.training.synthetic")}</span>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  loading={loading === "training-evaluate"}
                  disabled={trainingCases.length === 0}
                  onClick={() => void evaluateTraining()}
                >
                  <FlaskConical size={15} aria-hidden="true" />
                  <span>{t("nl2sqlSettings.model.training.evaluate")}</span>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={trainingCases.length === 0}
                  onClick={() => downloadTrainingCsv(profileId, trainingText)}
                >
                  <Download size={15} aria-hidden="true" />
                  <span>{t("nl2sqlSettings.model.training.export")}</span>
                </Button>
              </div>
            </div>
            <div className="grid content-start gap-3">
              {evaluation && (
                <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                  <div className="flex flex-wrap gap-2">
                    <StatusBadge
                      variant={evaluation.executable_rate === 1 ? "success" : "warning"}
                      label={t("nl2sqlSettings.model.training.executableRate", {
                        rate: Math.round(evaluation.executable_rate * 100),
                      })}
                    />
                    <StatusBadge
                      variant={evaluation.select_only_rate === 1 ? "success" : "warning"}
                      label={t("nl2sqlSettings.model.training.selectOnlyRate", {
                        rate: Math.round(evaluation.select_only_rate * 100),
                      })}
                    />
                  </div>
                  <Metric label={t("nl2sqlSettings.model.training.totalCases")} value={String(evaluation.total_cases)} />
                  {evaluation.findings.map((finding) => (
                    <p key={finding} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                      {finding}
                    </p>
                  ))}
                </section>
              )}
              {syntheticCases && syntheticCases.cases.length > 0 && (
                <section className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
                  <p className="text-xs font-semibold text-slate-500">{t("nl2sqlSettings.model.training.syntheticResult")}</p>
                  {syntheticCases.cases.map((item) => (
                    <div key={`${item.question}-${item.expected_sql}`} className="rounded-md bg-slate-50 p-3">
                      <p className="font-semibold text-slate-900">{item.question}</p>
                      <code className="mt-2 block break-words text-xs text-slate-700">{item.expected_sql}</code>
                    </div>
                  ))}
                </section>
              )}
            </div>
          </CardContent>
        </Card>
      </main>
    </>
  );
}

export function DatabaseSettingsPage() {
  const [catalog, setCatalog] = useState<SchemaCatalog | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsData | null>(null);
  const [csvForm, setCsvForm] = useState<CsvImportFormState>(() => defaultCsvImportForm());
  const [csvResult, setCsvResult] = useState<CsvImportData | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewError, setOverviewError] = useState("");
  const [csvLoading, setCsvLoading] = useState(false);
  const [csvError, setCsvError] = useState("");

  const loadOverview = async () => {
    setOverviewLoading(true);
    setOverviewError("");
    try {
      const [nextCatalog, nextDiagnostics] = await Promise.all([
        apiGet<SchemaCatalog>("/api/schema/catalog"),
        apiGet<DiagnosticsData>("/api/nl2sql/diagnostics"),
      ]);
      setCatalog(nextCatalog);
      setDiagnostics(nextDiagnostics);
    } catch (err) {
      setOverviewError(err instanceof Error ? err.message : t("nl2sqlSettings.database.overview.error"));
    } finally {
      setOverviewLoading(false);
    }
  };

  useEffect(() => {
    void loadOverview();
  }, []);

  const totalColumns = catalog?.tables.reduce((sum, table) => sum + table.columns.length, 0) ?? 0;
  const canSubmitCsv = csvForm.tableName.trim().length > 0 && csvForm.csvText.trim().length > 0;
  const databaseReadiness = (diagnostics?.readiness ?? []).filter((item) =>
    ["oracle_adb", "persistence", "feedback_embedding", "select_ai", "select_ai_agent"].includes(item.area)
  );

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
      setCsvError(err instanceof Error ? err.message : t("nl2sqlSettings.database.import.error"));
    } finally {
      setCsvLoading(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.nl2sqlSettingsDatabase")}
        subtitle={t("nl2sqlSettings.database.subtitle")}
        actions={
          <Button type="button" size="sm" variant="secondary" loading={overviewLoading} onClick={() => void loadOverview()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("nl2sqlSettings.database.overview.refresh")}</span>
          </Button>
        }
      />
      <main className="grid gap-5 p-4 lg:p-8">
        {overviewError && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
            {overviewError}
          </div>
        )}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database size={18} aria-hidden="true" />
              {t("nl2sqlSettings.database.boundary")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-3">
              <Metric label={t("nl2sqlSettings.database.tables")} value={String(catalog?.tables.length ?? 0)} />
              <Metric label={t("nl2sqlSettings.database.columns")} value={String(totalColumns)} />
              <Metric label={t("nl2sqlSettings.database.safety")} value="SELECT/WITH" />
            </div>
            <section className="mt-5 grid gap-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-slate-900">
                  {t("nl2sqlSettings.database.overview.title")}
                </h3>
                <StatusBadge
                  variant={databaseReadiness.every((item) => item.status === "ok") ? "success" : "warning"}
                  label={`${databaseReadiness.filter((item) => item.status === "ok").length}/${databaseReadiness.length || 0}`}
                />
              </div>
              {databaseReadiness.length > 0 ? (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {databaseReadiness.map((item) => (
                    <div key={item.area} className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="font-semibold text-slate-900">{item.label}</p>
                          <p className="mt-1 text-sm leading-6 text-slate-700">{item.summary}</p>
                        </div>
                        <StatusBadge variant={item.status === "ok" ? "success" : "warning"} label={item.status} />
                      </div>
                      {item.next_action && (
                        <p className="rounded-md bg-white px-3 py-2 text-sm leading-6 text-slate-700">
                          {item.next_action}
                        </p>
                      )}
                      {item.related_checks.length > 0 && (
                        <p className="font-mono text-xs leading-5 text-slate-500">
                          {item.related_checks.join(", ")}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grid min-h-28 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                  {t("nl2sqlSettings.database.overview.empty")}
                </div>
              )}
            </section>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileSpreadsheet size={18} aria-hidden="true" />
              {t("nl2sqlSettings.database.import.title")}
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
                  <span>{t("nl2sqlSettings.database.import.tableName")}</span>
                  <input
                    value={csvForm.tableName}
                    onChange={(event) => updateCsvForm("tableName", event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder={t("nl2sqlSettings.database.import.tableNamePlaceholder")}
                  />
                </label>
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={csvForm.execute}
                    onChange={(event) => updateCsvForm("execute", event.currentTarget.checked)}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500"
                  />
                  <span>{t("nl2sqlSettings.database.import.execute")}</span>
                </label>
                <label className="flex min-h-11 items-start gap-3 rounded-md border border-slate-200 p-3 text-sm text-slate-800">
                  <input
                    type="checkbox"
                    checked={csvForm.replaceExisting}
                    onChange={(event) => updateCsvForm("replaceExisting", event.currentTarget.checked)}
                    disabled={!csvForm.execute}
                    className="mt-1 h-4 w-4 rounded border-slate-300 text-sky-700 focus:ring-sky-500 disabled:opacity-50"
                  />
                  <span>{t("nl2sqlSettings.database.import.replace")}</span>
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
                      ? t("nl2sqlSettings.database.import.actionExecute")
                      : t("nl2sqlSettings.database.import.actionDryRun")}
                  </span>
                </Button>
              </div>
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("nl2sqlSettings.database.import.csv")}</span>
                <textarea
                  value={csvForm.csvText}
                  onChange={(event) => updateCsvForm("csvText", event.currentTarget.value)}
                  rows={10}
                  className="min-h-72 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-sm leading-6 outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder={t("nl2sqlSettings.database.import.csvPlaceholder")}
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

export function AssetStatusPanel({ result }: { result: AssetRefreshData }) {
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

export function CsvImportResult({ result }: { result: CsvImportData }) {
  const sampleColumns = result.columns.map((column) => column.column_name);
  return (
    <section className="grid gap-4 rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-slate-900">{result.table_name}</p>
          <p className="mt-1 text-sm text-slate-600">
            {t("nl2sqlSettings.database.import.resultSummary", {
              rows: result.row_count,
              columns: result.columns.length,
            })}
          </p>
        </div>
        <StatusBadge
          variant={result.executed ? "success" : "info"}
          label={result.executed ? t("nl2sqlSettings.database.import.executed") : t("nl2sqlSettings.database.import.dryRun")}
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
              <th className="px-3 py-2 text-left">{t("nl2sqlSettings.database.import.sourceColumn")}</th>
              <th className="px-3 py-2 text-left">{t("nl2sqlSettings.database.import.oracleColumn")}</th>
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
        <SqlSnippet title={t("nl2sqlSettings.database.import.ddl")} value={result.ddl} />
        <SqlSnippet title={t("nl2sqlSettings.database.import.insertSql")} value={result.insert_sql} />
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
