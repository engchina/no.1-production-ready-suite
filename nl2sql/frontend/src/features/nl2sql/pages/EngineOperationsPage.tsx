import { type ChangeEvent, useEffect, useState } from "react";
import { Bot, Code2, Database, Download, FileJson, Gauge, ListChecks, RefreshCw, Save, ShieldCheck, Trash2, Upload } from "lucide-react";

import { Button, Card, CardContent, CardHeader, CardTitle, PageHeader, StatusBadge } from "@engchina/production-ready-ui";

import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { engineLabel } from "../labels";
import { formatElapsed } from "../useOperationTimer";
import type {
  AgentConversationsData,
  AgentConversationCreateData,
  AgentPrivilegeCheckData,
  AgentTeamRunData,
  AssetCleanupData,
  AssetRefreshData,
  DiagnosticConfigGuide,
  DiagnosticConfigVar,
  DiagnosticReadiness,
  DiagnosticSmokeCheck,
  DiagnosticsData,
  Nl2SqlEngine,
  Nl2SqlProfile,
  SelectAiAgentAssetsData,
  SelectAiDbProfileDetailData,
  SelectAiDbProfileMutationData,
  SelectAiDbProfilesData,
  SelectAiProfilesExportData,
} from "../types";
import { AssetStatusPanel } from "./SettingsPages";

export function EngineOperationsPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [diagnostics, setDiagnostics] = useState<DiagnosticsData | null>(null);
  const [refreshResults, setRefreshResults] = useState<AssetRefreshData[]>([]);
  const [cleanupResults, setCleanupResults] = useState<AssetCleanupData[]>([]);
  const [cleanupConfirmation, setCleanupConfirmation] = useState("");
  const [dbProfiles, setDbProfiles] = useState<SelectAiDbProfilesData | null>(null);
  const [dbProfileDropConfirmation, setDbProfileDropConfirmation] = useState("");
  const [dbProfileDropResults, setDbProfileDropResults] = useState<AssetCleanupData[]>([]);
  const [profileJson, setProfileJson] = useState("");
  const [profileConfirmation, setProfileConfirmation] = useState("");
  const [profileMutation, setProfileMutation] = useState<SelectAiDbProfileMutationData | null>(null);
  const [profileExport, setProfileExport] = useState<SelectAiProfilesExportData | null>(null);
  const [agentAssets, setAgentAssets] = useState<SelectAiAgentAssetsData | null>(null);
  const [agentPrompt, setAgentPrompt] = useState("登録済みの表から主要な列を一覧したい");
  const [agentToolName, setAgentToolName] = useState("");
  const [agentConversationId, setAgentConversationId] = useState("");
  const [agentRun, setAgentRun] = useState<AgentTeamRunData | null>(null);
  const [agentConversations, setAgentConversations] = useState<AgentConversationsData | null>(null);
  const [agentCreatedConversation, setAgentCreatedConversation] = useState<AgentConversationCreateData | null>(null);
  const [agentPrivilegeCheck, setAgentPrivilegeCheck] = useState<AgentPrivilegeCheckData | null>(null);
  const [reportInput, setReportInput] = useState("");
  const [parsedReport, setParsedReport] = useState<ManualIntegrationReport | null>(null);
  const [reportMessage, setReportMessage] = useState("");
  const [loading, setLoading] = useState("");
  const [message, setMessage] = useState("");

  const load = async () => {
    setLoading("load");
    setMessage("");
    try {
      const [profileData, diagnosticsData, dbProfileData, agentAssetData] = await Promise.all([
        apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"),
        apiGet<DiagnosticsData>("/api/nl2sql/diagnostics"),
        apiGet<SelectAiDbProfilesData>("/api/nl2sql/select-ai/db-profiles"),
        apiGet<SelectAiAgentAssetsData>("/api/nl2sql/select-ai-agent/assets"),
      ]);
      setProfiles(profileData);
      setDiagnostics(diagnosticsData);
      setDbProfiles(dbProfileData);
      setAgentAssets(agentAssetData);
      setProfileId((current) => current || profileData[0]?.id || "default");
      setAgentToolName((current) => current || agentAssetData.items[0]?.tool_name || "");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.load"));
    } finally {
      setLoading("");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const refresh = async (kind: "select_ai" | "select_ai_agent") => {
    setLoading(kind);
    setMessage("");
    try {
      const path =
        kind === "select_ai"
          ? "/api/nl2sql/select-ai/profiles/refresh"
          : "/api/nl2sql/select-ai-agent/assets/refresh";
      const result = await apiPost<AssetRefreshData>(`${path}?profile_id=${encodeURIComponent(profileId || "default")}`);
      setRefreshResults((current) => [result, ...current.filter((item) => item.engine !== result.engine)]);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.refresh"));
    } finally {
      setLoading("");
    }
  };

  const cleanupAssets = async () => {
    setLoading("cleanup");
    setMessage("");
    try {
      const result = await apiPost<AssetCleanupData[]>("/api/nl2sql/select-ai/assets/cleanup", {
        profile_id: profileId || null,
        engines: ["select_ai_agent", "select_ai"],
        confirmation: cleanupConfirmation,
        reason: "ui-engine-cleanup",
      });
      setCleanupResults(result);
      setDbProfiles(await apiGet<SelectAiDbProfilesData>("/api/nl2sql/select-ai/db-profiles"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.cleanup"));
    } finally {
      setLoading("");
    }
  };

  const dropDbProfile = async (profileName: string) => {
    setLoading(`db-profile-drop-${profileName}`);
    setMessage("");
    try {
      const result = await apiPost<AssetCleanupData>(
        `/api/nl2sql/select-ai/db-profiles/${encodeURIComponent(profileName)}/drop`,
        {
          confirmation: dbProfileDropConfirmation,
          reason: "ui-db-profile-drop",
        }
      );
      setDbProfileDropResults((current) => [
        result,
        ...current.filter((item) => item.profile_name !== result.profile_name),
      ]);
      setDbProfiles(await apiGet<SelectAiDbProfilesData>("/api/nl2sql/select-ai/db-profiles"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.dbProfileDrop"));
    } finally {
      setLoading("");
    }
  };

  const loadDbProfileDetail = async (profileName: string) => {
    setLoading(`db-profile-detail-${profileName}`);
    setMessage("");
    try {
      const detail = await apiGet<SelectAiDbProfileDetailData>(
        `/api/nl2sql/select-ai/db-profiles/${encodeURIComponent(profileName)}`
      );
      setProfileJson(JSON.stringify(detail.profile, null, 2));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.dbProfileDetail"));
    } finally {
      setLoading("");
    }
  };

  const saveDbProfileJson = async () => {
    setLoading("db-profile-save");
    setMessage("");
    try {
      const parsed = JSON.parse(profileJson) as {
        name?: string;
        attributes?: Record<string, unknown>;
        description?: string;
        category?: string;
      };
      const result = await apiPost<SelectAiDbProfileMutationData>("/api/nl2sql/select-ai/db-profiles", {
        profile_name: parsed.name || "",
        attributes: parsed.attributes || parsed,
        description: parsed.description || "",
        category: parsed.category || "",
        confirmation: profileConfirmation,
        reason: "ui-profile-json-save",
      });
      setProfileMutation(result);
      setDbProfiles(await apiGet<SelectAiDbProfilesData>("/api/nl2sql/select-ai/db-profiles"));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.dbProfileSave"));
    } finally {
      setLoading("");
    }
  };

  const exportDbProfilesJson = async () => {
    setLoading("db-profile-export");
    setMessage("");
    try {
      const exported = await apiGet<SelectAiProfilesExportData>("/api/nl2sql/select-ai/profiles/export.json");
      setProfileExport(exported);
      setProfileJson(JSON.stringify(exported.profiles[0] ?? { name: "", attributes: {} }, null, 2));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.dbProfileExport"));
    } finally {
      setLoading("");
    }
  };

  const runAgentTeam = async () => {
    if (!agentPrompt.trim()) return;
    setLoading("agent-run");
    setMessage("");
    try {
      setAgentRun(
        await apiPost<AgentTeamRunData>("/api/nl2sql/select-ai-agent/run-team", {
          prompt: agentPrompt,
          profile_id: profileId || null,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.agentRun"));
    } finally {
      setLoading("");
    }
  };

  const runAgentTool = async () => {
    if (!agentPrompt.trim() || !agentToolName.trim()) return;
    setLoading("agent-tool-run");
    setMessage("");
    try {
      setAgentRun(
        await apiPost<AgentTeamRunData>("/api/nl2sql/select-ai-agent/run-tool", {
          prompt: agentPrompt,
          tool_name: agentToolName,
          conversation_id: agentConversationId,
        })
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.agentRun"));
    } finally {
      setLoading("");
    }
  };

  const createAgentConversation = async () => {
    setLoading("agent-conversation-create");
    setMessage("");
    try {
      const created = await apiPost<AgentConversationCreateData>("/api/nl2sql/select-ai-agent/conversations/create", {
        profile_id: profileId || null,
      });
      setAgentCreatedConversation(created);
      setAgentConversationId(created.conversation_id);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.agentConversations"));
    } finally {
      setLoading("");
    }
  };

  const loadAgentConversations = async () => {
    setLoading("agent-conversations");
    setMessage("");
    try {
      setAgentConversations(
        await apiGet<AgentConversationsData>("/api/nl2sql/select-ai-agent/conversations?limit=10")
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.agentConversations"));
    } finally {
      setLoading("");
    }
  };

  const checkAgentPrivileges = async () => {
    setLoading("agent-privileges");
    setMessage("");
    try {
      setAgentPrivilegeCheck(
        await apiGet<AgentPrivilegeCheckData>("/api/nl2sql/select-ai-agent/privileges/check")
      );
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t("engineOps.error.agentPrivileges"));
    } finally {
      setLoading("");
    }
  };

  const parseReport = (rawValue = reportInput) => {
    setReportMessage("");
    try {
      const parsed = normalizeManualIntegrationReport(JSON.parse(rawValue));
      if (!parsed) {
        setParsedReport(null);
        setReportMessage(t("engineOps.report.error.invalid"));
        return;
      }
      setParsedReport(parsed);
      setReportInput(JSON.stringify(parsed, null, 2));
    } catch {
      setParsedReport(null);
      setReportMessage(t("engineOps.report.error.parse"));
    }
  };

  const importReportFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) {
      return;
    }
    const text = await file.text();
    setReportInput(text);
    parseReport(text);
  };

  return (
    <>
      <PageHeader
        title={t("nav.engineOperations")}
        subtitle={t("engineOps.subtitle")}
        actions={
          <Button type="button" variant="secondary" size="sm" loading={loading === "load"} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            <span>{t("engineOps.action.refresh")}</span>
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
              <Bot size={18} aria-hidden="true" />
              {t("engineOps.assets.title")}
            </CardTitle>
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
              <EngineActionCard
                engine="select_ai_agent"
                description={t("nl2sql.engine.agent.desc")}
                action={t("settings.model.refreshAgent")}
                loading={loading === "select_ai_agent"}
                onClick={() => void refresh("select_ai_agent")}
              />
              <EngineActionCard
                engine="select_ai"
                description={t("nl2sql.engine.selectAi.desc")}
                action={t("settings.model.refreshSelectAi")}
                loading={loading === "select_ai"}
                onClick={() => void refresh("select_ai")}
              />
              <EngineActionCard
                engine="enterprise_ai_direct"
                description={t("nl2sql.engine.direct.desc")}
                action={t("settings.model.directReady")}
                disabled
              />
            </div>

            <section className="grid gap-3 border-t border-slate-200 pt-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-slate-900">{t("engineOps.cleanup.title")}</p>
                  <p className="mt-1 text-sm text-slate-600">{t("engineOps.cleanup.subtitle")}</p>
                </div>
                <Button
                  type="button"
                  variant="danger"
                  size="sm"
                  loading={loading === "cleanup"}
                  onClick={() => void cleanupAssets()}
                >
                  <Database size={15} aria-hidden="true" />
                  <span>{t("engineOps.cleanup.execute")}</span>
                </Button>
              </div>
              <label className="grid max-w-sm gap-1 text-sm font-medium text-slate-800">
                <span>{t("engineOps.confirmation")}</span>
                <input
                  value={cleanupConfirmation}
                  onChange={(event) => setCleanupConfirmation(event.currentTarget.value)}
                  className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  placeholder="ADMIN_EXECUTE"
                />
                <span className="text-xs font-normal text-slate-500">{t("engineOps.cleanup.confirm")}</span>
              </label>
              <div className="grid gap-2 md:grid-cols-2">
                {cleanupResults.map((item) => (
                  <section key={`${item.engine}-${item.profile_name}-${item.team_name}`} className="rounded-md border border-slate-200 p-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="font-semibold text-slate-900">{engineLabel(item.engine)}</p>
                      <StatusBadge variant={item.executed ? "success" : item.status === "error" ? "danger" : "neutral"} label={item.status} />
                    </div>
                    <p className="mt-2 break-all font-mono text-xs text-slate-600">
                      {Object.values(item.asset_names).join(" / ") || item.profile_name}
                    </p>
                    {item.warning && (
                      <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
                        {item.warning}
                      </p>
                    )}
                  </section>
                ))}
              </div>
            </section>

            <section className="border-t border-slate-200 pt-4">
              <FixedSplitPane
                splitId="engine-operations-db-agent"
                preferredWidePane="right"
                left={
                  <div className="grid content-start gap-3">
                <p className="font-semibold text-slate-900">{t("engineOps.dbProfiles.title")}</p>
                <div className="flex flex-wrap gap-2">
                  <StatusBadge variant="neutral" label={dbProfiles?.runtime ?? "deterministic"} />
                  <StatusBadge variant="neutral" label={`${dbProfiles?.profiles.length ?? 0} profiles`} />
                </div>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("engineOps.confirmation")}</span>
                  <input
                    value={dbProfileDropConfirmation}
                    onChange={(event) => setDbProfileDropConfirmation(event.currentTarget.value)}
                    className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    placeholder="PROFILE_NAME / ADMIN_EXECUTE"
                  />
                  <span className="text-xs font-normal text-slate-500">{t("engineOps.dbProfiles.dropConfirm")}</span>
                </label>
                <div className="grid gap-2">
                  {(dbProfiles?.profiles ?? []).slice(0, 6).map((profile) => (
                    <div key={profile.name} className="rounded-md border border-slate-200 p-3 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="break-all font-mono text-xs font-semibold text-slate-900">{profile.name}</p>
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge variant="neutral" label={profile.status} />
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            loading={loading === `db-profile-detail-${profile.name}`}
                            onClick={() => void loadDbProfileDetail(profile.name)}
                          >
                            <FileJson size={15} aria-hidden="true" />
                            <span>{t("engineOps.dbProfiles.detail")}</span>
                          </Button>
                          <Button
                            type="button"
                            variant="danger"
                            size="sm"
                            loading={loading === `db-profile-drop-${profile.name}`}
                            onClick={() => void dropDbProfile(profile.name)}
                          >
                            <Trash2 size={15} aria-hidden="true" />
                            <span>{t("engineOps.dbProfiles.dropExecute")}</span>
                          </Button>
                        </div>
                      </div>
                    </div>
                  ))}
                  {(dbProfiles?.profiles.length ?? 0) === 0 && (
                    <div className="grid min-h-20 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-3 text-sm text-slate-500">
                      {t("engineOps.dbProfiles.empty")}
                    </div>
                  )}
                </div>
                {dbProfileDropResults.length > 0 && (
                  <div className="grid gap-2">
                    <p className="text-sm font-semibold text-slate-900">{t("engineOps.dbProfiles.dropResult")}</p>
                    {dbProfileDropResults.map((item) => (
                      <section
                        key={`${item.profile_name}-${item.cleaned_at}-${item.status}`}
                        className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="break-all font-mono text-xs font-semibold text-slate-900">
                            {item.profile_name}
                          </p>
                          <StatusBadge
                            variant={item.executed ? "success" : item.status === "error" ? "danger" : "neutral"}
                            label={item.status}
                          />
                        </div>
                        {item.warning && (
                          <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-950">
                            {item.warning}
                          </p>
                        )}
                        <p className="mt-2 text-xs leading-5 text-slate-600">
                          runtime: {String(item.engine_meta.runtime ?? "deterministic")}
                        </p>
                      </section>
                    ))}
                  </div>
                )}
                {dbProfiles?.warnings?.length ? (
                  <div className="grid gap-2">
                    {dbProfiles.warnings.map((warning) => (
                      <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
                        {warning}
                      </p>
                    ))}
                  </div>
                ) : null}
                <section className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-slate-900">{t("engineOps.dbProfiles.jsonEditor")}</p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        loading={loading === "db-profile-export"}
                        onClick={() => void exportDbProfilesJson()}
                      >
                        <Download size={15} aria-hidden="true" />
                        <span>{t("engineOps.dbProfiles.exportJson")}</span>
                      </Button>
                      <Button
                        type="button"
                        variant="danger"
                        size="sm"
                        loading={loading === "db-profile-save"}
                        disabled={!profileJson.trim()}
                        onClick={() => void saveDbProfileJson()}
                      >
                        <Save size={15} aria-hidden="true" />
                        <span>{t("engineOps.dbProfiles.saveExecute")}</span>
                      </Button>
                    </div>
                  </div>
                  <textarea
                    value={profileJson}
                    onChange={(event) => setProfileJson(event.currentTarget.value)}
                    rows={10}
                    className="min-h-60 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                  <div className="grid gap-3">
                    <label className="grid gap-1 text-sm font-medium text-slate-800">
                      <span>{t("engineOps.confirmation")}</span>
                      <input
                        value={profileConfirmation}
                        onChange={(event) => setProfileConfirmation(event.currentTarget.value)}
                        className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                        placeholder="PROFILE_NAME / ADMIN_EXECUTE"
                      />
                      <span className="text-xs font-normal text-slate-500">{t("engineOps.dbProfiles.saveExecuteConfirm")}</span>
                    </label>
                  </div>
                  {profileExport && (
                    <StatusBadge variant="neutral" label={`${profileExport.profiles.length} exported`} />
                  )}
                  {profileMutation && (
                    <div className="grid gap-2 rounded-md border border-slate-200 bg-white p-3 text-sm">
                      <div className="flex flex-wrap gap-2">
                        <StatusBadge variant={profileMutation.executed ? "success" : "neutral"} label={profileMutation.status} />
                        <StatusBadge variant="neutral" label={profileMutation.runtime} />
                      </div>
                      {profileMutation.ddl.map((line) => (
                        <code key={line} className="block break-words rounded-md bg-slate-50 p-2 text-xs text-slate-700">
                          {line}
                        </code>
                      ))}
                      {profileMutation.warnings.map((warning) => (
                        <p key={warning} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-950">
                          {warning}
                        </p>
                      ))}
                    </div>
                  )}
                </section>
                  </div>
                }
                right={
                  <div className="grid content-start gap-3">
                <p className="font-semibold text-slate-900">{t("engineOps.agentRun.title")}</p>
                <div className="flex flex-wrap gap-2">
                  <StatusBadge variant="neutral" label={agentAssets?.runtime ?? "deterministic"} />
                  <StatusBadge variant="neutral" label={`${agentAssets?.items.length ?? 0} assets`} />
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("engineOps.agentRun.tool")}</span>
                    <input
                      value={agentToolName}
                      onChange={(event) => setAgentToolName(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    />
                  </label>
                  <label className="grid gap-1 text-sm font-medium text-slate-800">
                    <span>{t("engineOps.agentRun.conversationId")}</span>
                    <input
                      value={agentConversationId}
                      onChange={(event) => setAgentConversationId(event.currentTarget.value)}
                      className="min-h-11 rounded-md border border-slate-300 bg-white px-3 py-2 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                    />
                  </label>
                </div>
                <label className="grid gap-1 text-sm font-medium text-slate-800">
                  <span>{t("engineOps.agentRun.prompt")}</span>
                  <textarea
                    value={agentPrompt}
                    onChange={(event) => setAgentPrompt(event.currentTarget.value)}
                    rows={3}
                    className="min-h-24 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    loading={loading === "agent-run"}
                    disabled={!agentPrompt.trim()}
                    onClick={() => void runAgentTeam()}
                  >
                    <Bot size={15} aria-hidden="true" />
                    <span>{t("engineOps.agentRun.action")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "agent-tool-run"}
                    disabled={!agentPrompt.trim() || !agentToolName.trim()}
                    onClick={() => void runAgentTool()}
                  >
                    <Code2 size={15} aria-hidden="true" />
                    <span>{t("engineOps.agentRun.toolAction")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "agent-conversation-create"}
                    onClick={() => void createAgentConversation()}
                  >
                    <FileJson size={15} aria-hidden="true" />
                    <span>{t("engineOps.agentRun.createConversation")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "agent-conversations"}
                    onClick={() => void loadAgentConversations()}
                  >
                    <ListChecks size={15} aria-hidden="true" />
                    <span>{t("engineOps.agentRun.conversations")}</span>
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    loading={loading === "agent-privileges"}
                    onClick={() => void checkAgentPrivileges()}
                  >
                    <ShieldCheck size={15} aria-hidden="true" />
                    <span>{t("engineOps.agentRun.privileges")}</span>
                  </Button>
                </div>
                {agentCreatedConversation && (
                  <div className="rounded-md border border-sky-200 bg-sky-50 p-3 text-sm text-slate-800">
                    {agentCreatedConversation.conversation_id}
                  </div>
                )}
                {agentRun && (
                  <div className="grid gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge variant="neutral" label={agentRun.runtime} />
                      <StatusBadge variant="info" label={agentRun.team_name} />
                    </div>
                    <pre className="max-h-32 overflow-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs leading-5 text-slate-50">
                      <code>{agentRun.generated_sql || "-"}</code>
                    </pre>
                  </div>
                )}
                {agentConversations && (
                  <div className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
                    <StatusBadge variant="neutral" label={agentConversations.runtime} />
                    {agentConversations.items.slice(0, 3).map((item) => (
                      <p key={`${item.conversation_id}-${item.created_at}`} className="break-words text-slate-700">
                        {item.prompt}
                      </p>
                    ))}
                    {agentConversations.items.length === 0 && (
                      <p className="text-slate-500">{t("engineOps.agentRun.noConversations")}</p>
                    )}
                  </div>
                )}
                {agentPrivilegeCheck && (
                  <div className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge
                        variant={agentPrivilegeCheck.status === "ok" ? "success" : "warning"}
                        label={agentPrivilegeCheck.status}
                      />
                      <StatusBadge variant="neutral" label={agentPrivilegeCheck.runtime} />
                    </div>
                    {agentPrivilegeCheck.checks.map((check) => (
                      <div key={check.name} className="rounded-md bg-slate-50 p-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="font-mono text-xs text-slate-500">{check.name}</p>
                          <StatusBadge
                            variant={check.status === "ok" ? "success" : check.status === "error" ? "danger" : "warning"}
                            label={check.status}
                          />
                        </div>
                        <p className="mt-1 text-slate-700">{check.message}</p>
                      </div>
                    ))}
                  </div>
                )}
                  </div>
                }
              />
            </section>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Gauge size={18} aria-hidden="true" />
              {t("ops.readiness.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
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
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ListChecks size={18} aria-hidden="true" />
              {t("engineOps.smoke.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 xl:grid-cols-2">
            {(diagnostics?.smoke_checks ?? []).map((item) => (
              <SmokeCheckCard key={item.id} item={item} />
            ))}
            {(!diagnostics?.smoke_checks || diagnostics.smoke_checks.length === 0) && (
              <div className="grid min-h-32 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500 xl:col-span-2">
                {t("engineOps.smoke.empty")}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ListChecks size={18} aria-hidden="true" />
              {t("engineOps.requiredSmoke.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 lg:grid-cols-2">
            <RequiredSmokeCommand
              label={t("engineOps.requiredSmoke.diagnosticsOnly")}
              command="uv run python scripts/nl2sql_manual_integration.py --diagnostics-only --json-report reports/nl2sql-diagnostics.json"
              readiness={findReadiness(diagnostics, "oracle_adb")}
              requiredEnvVars={["ORACLE_USER", "ORACLE_DSN", "NL2SQL_RUNTIME_MODE"]}
            />
            <RequiredSmokeCommand
              label={t("engineOps.requiredSmoke.releaseGate")}
              command='uv run python scripts/nl2sql_manual_integration.py --release-gate --engines select_ai_agent,select_ai --allowed-table YOUR_TABLE --question "YOUR_QUESTION" --timeout 15 --diagnostics-timeout 8 --synthetic-limit 2 --json-report reports/nl2sql-release-gate.json'
              readiness={releaseGateReadiness(diagnostics)}
              requiredEnvVars={[
                "ORACLE_USER",
                "ORACLE_DSN",
                "NL2SQL_RUNTIME_MODE",
                "NL2SQL_PERSISTENCE_MODE",
                "NL2SQL_SELECT_AI_CREDENTIAL_NAME",
                "NL2SQL_SELECT_AI_MODEL",
              ]}
            />
            <RequiredSmokeCommand
              label={t("engineOps.requiredSmoke.legacyAbsorption")}
              command='uv run python scripts/nl2sql_manual_integration.py --check-legacy-absorption --require-oracle --require-oracle-persistence --require-feedback-embedding --require-classifier-oracle-state --engines select_ai_agent,select_ai --allowed-table YOUR_TABLE --question "YOUR_QUESTION" --json-report reports/nl2sql-legacy-absorption.json'
              readiness={releaseGateReadiness(diagnostics)}
              requiredEnvVars={[
                "ORACLE_USER",
                "ORACLE_DSN",
                "NL2SQL_RUNTIME_MODE",
                "NL2SQL_PERSISTENCE_MODE",
                "OCI_REGION",
                "OCI_COMPARTMENT_ID",
                "OCI_GENAI_ENDPOINT",
                "OCI_GENAI_EMBED_MODEL_ID",
              ]}
            />
            <RequiredSmokeCommand
              label={t("engineOps.requiredSmoke.enterpriseAi")}
              command="uv run python scripts/nl2sql_manual_integration.py --require-enterprise-ai --engines enterprise_ai_direct --execute"
              readiness={findReadiness(diagnostics, "enterprise_ai_direct")}
              requiredEnvVars={[
                "OCI_ENTERPRISE_AI_ENDPOINT",
                "OCI_ENTERPRISE_AI_API_KEY",
                "OCI_ENTERPRISE_AI_LLM_MODEL",
              ]}
            />
            <RequiredSmokeCommand
              label={t("engineOps.requiredSmoke.feedback")}
              command="uv run python scripts/nl2sql_manual_integration.py --require-oracle --require-feedback-embedding --seed-demo-learning --execute-feedback-index --engines enterprise_ai_direct"
              readiness={findReadiness(diagnostics, "feedback_embedding")}
              requiredEnvVars={[
                "NL2SQL_FEEDBACK_EMBEDDING_ENABLED",
                "OCI_REGION",
                "OCI_COMPARTMENT_ID",
                "OCI_GENAI_ENDPOINT",
                "OCI_GENAI_EMBED_MODEL_ID",
              ]}
            />
            <RequiredSmokeCommand
              label={t("engineOps.requiredSmoke.persistence")}
              command="uv run python scripts/nl2sql_manual_integration.py --require-oracle --require-oracle-persistence --engines select_ai"
              readiness={findReadiness(diagnostics, "persistence")}
              requiredEnvVars={["NL2SQL_PERSISTENCE_MODE", "NL2SQL_ORACLE_STATE_TABLE", "ORACLE_USER", "ORACLE_DSN"]}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck size={18} aria-hidden="true" />
              {t("engineOps.configGuide.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 xl:grid-cols-3">
            {(diagnostics?.config_guides ?? []).map((guide) => (
              <ConfigGuideCard key={guide.id} guide={guide} />
            ))}
            {(!diagnostics?.config_guides || diagnostics.config_guides.length === 0) && (
              <div className="grid min-h-32 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500 xl:col-span-3">
                {t("engineOps.configGuide.empty")}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileJson size={18} aria-hidden="true" />
              {t("engineOps.report.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
            <section className="grid gap-3">
              <label className="grid gap-1 text-sm font-medium text-slate-800">
                <span>{t("engineOps.report.input")}</span>
                <textarea
                  value={reportInput}
                  onChange={(event) => setReportInput(event.currentTarget.value)}
                  className="min-h-44 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs leading-5 text-slate-800 focus:border-sky-600 focus:ring-2 focus:ring-sky-200"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" size="sm" onClick={() => parseReport()}>
                  <FileJson size={15} aria-hidden="true" />
                  <span>{t("engineOps.report.parse")}</span>
                </Button>
                <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 focus-within:ring-2 focus-within:ring-sky-200">
                  <Upload size={15} aria-hidden="true" />
                  <span>{t("engineOps.report.import")}</span>
                  <input className="sr-only" type="file" accept="application/json,.json" onChange={importReportFile} />
                </label>
              </div>
              {reportMessage && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-950">
                  {reportMessage}
                </p>
              )}
            </section>
            <ManualReportSummary report={parsedReport} />
          </CardContent>
        </Card>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck size={18} aria-hidden="true" />
                {t("settings.connection.checks")}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              {diagnostics?.checks.map((check) => (
                <div
                  key={check.name}
                  className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-slate-200 p-3"
                >
                  <div>
                    <p className="font-mono text-xs text-slate-500">{check.name}</p>
                    <p className="mt-1 text-sm text-slate-800">{check.message}</p>
                  </div>
                  <StatusBadge variant={check.status === "ok" ? "success" : "warning"} label={check.status} />
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database size={18} aria-hidden="true" />
                {t("settings.model.lastRefresh")}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3">
              {refreshResults.length > 0 ? (
                refreshResults.map((result) => <AssetStatusPanel key={result.engine} result={result} />)
              ) : (
                <div className="grid min-h-40 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
                  {t("engineOps.assets.empty")}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </main>
    </>
  );
}

interface ManualIntegrationReportStep {
  name: string;
  ok: boolean;
  message: string;
}

interface ManualIntegrationReport {
  schema_version: string;
  generated_at: string;
  started_at: string;
  finished_at: string;
  elapsed_ms: number;
  release_gate: boolean;
  ok: boolean;
  exit_code: number;
  profile_id: string;
  engines: string[];
  allowed_tables: string[];
  summary: {
    total: number;
    passed: number;
    failed: number;
  };
  steps: ManualIntegrationReportStep[];
}

function ManualReportSummary({ report }: { report: ManualIntegrationReport | null }) {
  if (!report) {
    return (
      <section className="grid min-h-56 place-items-center rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500">
        {t("engineOps.report.empty")}
      </section>
    );
  }

  return (
    <section className="grid gap-3 rounded-md border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">{report.schema_version}</p>
          <p className="mt-1 text-sm leading-6 text-slate-700">
            {t("engineOps.report.generatedAt")}: {report.generated_at || "-"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge variant={report.ok ? "success" : "warning"} label={report.ok ? "ok" : "ng"} />
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => downloadTextFile(manualReportFileName(report), buildManualReportMarkdown(report))}
          >
            <Download size={15} aria-hidden="true" />
            <span>{t("engineOps.report.download")}</span>
          </Button>
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        <ReportMetric label={t("engineOps.report.total")} value={report.summary.total} />
        <ReportMetric label={t("engineOps.report.passed")} value={report.summary.passed} />
        <ReportMetric label={t("engineOps.report.failed")} value={report.summary.failed} />
        <ReportMetric label={t("engineOps.report.exitCode")} value={report.exit_code} />
        <ReportMetric label={t("engineOps.report.elapsed")} value={formatElapsed(report.elapsed_ms)} />
      </div>
      <div className="grid gap-2 text-sm leading-6 text-slate-700">
        <p>
          <span className="font-medium text-slate-900">{t("engineOps.report.profile")}: </span>
          {report.profile_id || "-"}
        </p>
        <p>
          <span className="font-medium text-slate-900">{t("engineOps.report.engines")}: </span>
          {report.engines.join(", ") || "-"}
        </p>
        <p>
          <span className="font-medium text-slate-900">{t("engineOps.report.tables")}: </span>
          {report.allowed_tables.join(", ") || "-"}
        </p>
      </div>
      <div className="grid gap-2">
        <p className="text-xs font-medium text-slate-500">{t("engineOps.report.steps")}</p>
        <div className="grid max-h-80 gap-2 overflow-y-auto pr-1">
          {report.steps.map((step) => (
            <div key={step.name} className="grid gap-2 rounded-md border border-slate-200 p-3 text-sm">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="font-mono text-xs font-semibold text-slate-900">{step.name}</p>
                <StatusBadge variant={step.ok ? "success" : "warning"} label={step.ok ? "ok" : "ng"} />
              </div>
              <p className="break-words text-sm leading-6 text-slate-700">{step.message || "-"}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ReportMetric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 font-mono text-lg font-semibold text-slate-900">{value}</p>
    </div>
  );
}

function normalizeManualIntegrationReport(value: unknown): ManualIntegrationReport | null {
  if (!isRecord(value)) {
    return null;
  }
  const steps = normalizeReportSteps(value.steps);
  if (steps.length === 0) {
    return null;
  }
  const summary = isRecord(value.summary) ? value.summary : {};
  const failed = numberValue(summary.failed, steps.filter((step) => !step.ok).length);
  const passed = numberValue(summary.passed, steps.filter((step) => step.ok).length);
  const total = numberValue(summary.total, steps.length);
  return {
    schema_version: stringValue(value.schema_version, "nl2sql_manual_integration_report_v1"),
    generated_at: stringValue(value.generated_at, ""),
    started_at: stringValue(value.started_at, ""),
    finished_at: stringValue(value.finished_at, stringValue(value.generated_at, "")),
    elapsed_ms: numberValue(value.elapsed_ms, 0),
    release_gate: Boolean(value.release_gate),
    ok: Boolean(value.ok),
    exit_code: numberValue(value.exit_code, failed > 0 ? 1 : 0),
    profile_id: stringValue(value.profile_id, "default"),
    engines: stringList(value.engines),
    allowed_tables: stringList(value.allowed_tables),
    summary: { total, passed, failed },
    steps,
  };
}

function normalizeReportSteps(value: unknown): ManualIntegrationReportStep[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!isRecord(item)) {
      return [];
    }
    const name = stringValue(item.name, "");
    if (!name) {
      return [];
    }
    return [
      {
        name,
        ok: Boolean(item.ok),
        message: stringValue(item.message, ""),
      },
    ];
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function manualReportFileName(report: ManualIntegrationReport): string {
  const timestamp = report.generated_at.replace(/\D/g, "").slice(0, 14) || "undated";
  return `nl2sql_manual_report_${timestamp}.md`;
}

function buildManualReportMarkdown(report: ManualIntegrationReport): string {
  const lines = [
    "# NL2SQL Manual Integration Report",
    "",
    `- Schema: ${report.schema_version}`,
    `- Generated at: ${report.generated_at || "-"}`,
    `- Started at: ${report.started_at || "-"}`,
    `- Finished at: ${report.finished_at || "-"}`,
    `- Elapsed: ${formatElapsed(report.elapsed_ms)}`,
    `- Result: ${report.ok ? "ok" : "ng"}`,
    `- Exit code: ${report.exit_code}`,
    `- Profile: ${report.profile_id || "-"}`,
    `- Engines: ${report.engines.join(", ") || "-"}`,
    `- Allowed tables: ${report.allowed_tables.join(", ") || "-"}`,
    `- Steps: ${report.summary.passed}/${report.summary.total} passed, ${report.summary.failed} failed`,
    "",
    "## Steps",
    "",
  ];
  for (const step of report.steps) {
    lines.push(`### ${step.ok ? "[ok]" : "[ng]"} ${step.name}`, "", step.message || "-", "");
  }
  return `${lines.join("\n").trim()}\n`;
}

function downloadTextFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function ConfigGuideCard({ guide }: { guide: DiagnosticConfigGuide }) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">{guide.label}</p>
          <p className="mt-1 text-sm leading-6 text-slate-700">{guide.summary}</p>
        </div>
        <StatusBadge variant={guide.status === "ok" ? "success" : "warning"} label={guide.status} />
      </div>
      {guide.next_action && (
        <p className="rounded-md bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-700">
          <span className="font-medium text-slate-900">{t("ops.readiness.nextAction")}: </span>
          {guide.next_action}
        </p>
      )}
      <ConfigVarList label={t("engineOps.configGuide.required")} items={guide.required_env_vars} />
      {guide.optional_env_vars.length > 0 && (
        <ConfigVarList label={t("engineOps.configGuide.optional")} items={guide.optional_env_vars} />
      )}
      {guide.env_template && <CompactCode label={t("engineOps.configGuide.envTemplate")} value={guide.env_template} />}
      {guide.smoke_command && <CompactCode label={t("engineOps.configGuide.smokeCommand")} value={guide.smoke_command} />}
    </section>
  );
}

function ConfigVarList({ label, items }: { label: string; items: DiagnosticConfigVar[] }) {
  return (
    <div className="grid gap-2">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <div className="flex flex-wrap gap-2">
        {items.map((item) => (
          <code
            key={`${label}-${item.name}`}
            className={`max-w-full break-all rounded border px-2 py-1 text-xs font-medium leading-5 ${
              item.status === "ok"
                ? "border-emerald-200 bg-emerald-50 text-emerald-950"
                : item.required
                  ? "border-amber-200 bg-amber-50 text-amber-950"
                  : "border-slate-200 bg-slate-50 text-slate-700"
            }`}
            title={item.note || item.status}
          >
            {item.name}
          </code>
        ))}
      </div>
    </div>
  );
}

function SmokeCheckCard({ item }: { item: DiagnosticSmokeCheck }) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-semibold text-slate-900">{item.label}</p>
          <p className="mt-1 text-xs font-medium text-slate-500">{item.category}</p>
        </div>
        <StatusBadge variant={item.status === "ok" ? "success" : "warning"} label={item.status} />
      </div>
      {(item.endpoint || item.command) && (
        <div className="grid gap-2">
          {item.endpoint && (
            <CompactCode label={t("engineOps.smoke.endpoint")} value={`${item.method} ${item.endpoint}`} />
          )}
          {item.request_hint && (
            <CompactCode label={t("engineOps.smoke.request")} value={item.request_hint} />
          )}
          {item.command && <CompactCode label={t("engineOps.smoke.command")} value={item.command} />}
        </div>
      )}
      <p className="rounded-md bg-slate-50 px-3 py-2 leading-6 text-slate-700">
        <span className="font-medium text-slate-900">{t("engineOps.smoke.expected")}: </span>
        {item.expected}
      </p>
      {item.next_action && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 leading-6 text-amber-950">
          <span className="font-medium">{t("ops.readiness.nextAction")}: </span>
          {item.next_action}
        </p>
      )}
      <p className="text-xs leading-5 text-slate-500">
        {t("engineOps.smoke.related")}: {item.related_readiness.join(", ") || "-"}
      </p>
    </section>
  );
}

function findReadiness(diagnostics: DiagnosticsData | null, area: string) {
  return (diagnostics?.readiness ?? []).find((item) => item.area === area) ?? null;
}

const releaseGateReadinessAreas = [
  { area: "oracle_adb", label: "Oracle / ADB" },
  { area: "persistence", label: "NL2SQL persistence" },
  { area: "select_ai", label: "Oracle Select AI" },
  { area: "select_ai_agent", label: "Oracle Select AI Agent" },
];

function releaseGateReadiness(diagnostics: DiagnosticsData | null): DiagnosticReadiness | null {
  const readiness = diagnostics?.readiness ?? [];
  if (readiness.length === 0) {
    return null;
  }

  const required = releaseGateReadinessAreas.map((requiredArea) => ({
    ...requiredArea,
    readiness: readiness.find((item) => item.area === requiredArea.area),
  }));
  const notReady = required.filter((item) => item.readiness?.status !== "ok");
  const missingLabels = notReady.map((item) => item.readiness?.label || item.label);
  const nextActions = notReady
    .map((item) => {
      if (item.readiness?.next_action) {
        return `${item.readiness.label}: ${item.readiness.next_action}`;
      }
      if (item.readiness?.summary) {
        return `${item.readiness.label}: ${item.readiness.summary}`;
      }
      return `${item.label}: ${t("engineOps.requiredSmoke.releaseGateMissingReadiness")}`;
    })
    .join(" / ");

  return {
    area: "release_gate",
    label: t("engineOps.requiredSmoke.releaseGate"),
    status: notReady.length === 0 ? "ok" : "warning",
    summary:
      notReady.length === 0
        ? t("engineOps.requiredSmoke.releaseGateReady")
        : t("engineOps.requiredSmoke.releaseGateNeeds", { areas: missingLabels.join(", ") }),
    next_action: nextActions,
    related_checks: releaseGateReadinessAreas.map((item) => item.area),
  };
}

function RequiredSmokeCommand({
  label,
  command,
  readiness,
  requiredEnvVars = [],
}: {
  label: string;
  command: string;
  readiness: DiagnosticReadiness | null;
  requiredEnvVars?: string[];
}) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">{label}</p>
          {readiness && <p className="mt-1 text-sm leading-6 text-slate-700">{readiness.summary}</p>}
        </div>
        {readiness && (
          <StatusBadge variant={readiness.status === "ok" ? "success" : "warning"} label={readiness.status} />
        )}
      </div>
      {readiness?.next_action && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-950">
          <span className="font-medium">{t("ops.readiness.nextAction")}: </span>
          {readiness.next_action}
        </p>
      )}
      <CompactCode label={t("engineOps.smoke.command")} value={command} />
      {requiredEnvVars.length > 0 && (
        <div className="grid gap-2">
          <p className="text-xs font-medium text-slate-500">{t("engineOps.requiredSmoke.envVars")}</p>
          <div className="flex flex-wrap gap-2">
            {requiredEnvVars.map((envVar) => (
              <code
                key={`${label}-${envVar}`}
                className="max-w-full break-all rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-medium leading-5 text-slate-700"
              >
                {envVar}
              </code>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function CompactCode({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <code className="block overflow-x-auto rounded-md border border-slate-200 bg-slate-950 px-3 py-2 text-xs leading-5 text-slate-50">
        {value}
      </code>
    </div>
  );
}

function EngineActionCard({
  engine,
  description,
  action,
  loading,
  disabled,
  onClick,
}: {
  engine: Nl2SqlEngine;
  description: string;
  action: string;
  loading?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <section className="grid gap-3 rounded-md border border-slate-200 p-4">
      <div>
        <p className="font-semibold text-slate-900">{engineLabel(engine)}</p>
        <p className="mt-1 text-sm text-slate-600">{description}</p>
      </div>
      <Button type="button" variant="secondary" size="sm" loading={loading} disabled={disabled} onClick={onClick}>
        {!disabled && <RefreshCw size={15} aria-hidden="true" />}
        <span>{action}</span>
      </Button>
    </section>
  );
}
