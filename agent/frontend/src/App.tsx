import { Route, Routes } from "react-router-dom";

import { AppShell } from "@engchina/production-ready-ui";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { PageHeader } from "@/components/PageHeader";
import { DatabaseSettingsClient } from "@/components/settings/DatabaseSettingsClient";
import { ModelSettingsClient } from "@/components/settings/ModelSettingsClient";
import { OciSettingsClient } from "@/components/settings/OciSettingsClient";
import { UploadStorageSettingsClient } from "@/components/settings/UploadStorageSettingsClient";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { DashboardPage } from "@/pages/DashboardPage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import {
  AgentsPage,
  ApprovalsPage,
  AuditPage,
  CommandPolicySettingsPage,
  ExternalSettingsPage,
  MemoryPage,
  RuntimeSnapshotSettingsPage,
  RuntimeSafetySettingsPage,
  RunsPage,
  ToolPolicySettingsPage,
  ToolsPage,
} from "@/pages/AgentRuntimePages";

export function App() {
  return (
    <AppShell sidebar={<AppSidebar />}>
      <Routes>
        <Route path={APP_ROUTES.dashboard} element={<DashboardPage />} />
        <Route path={APP_ROUTES.agents} element={<AgentsPage />} />
        <Route path={APP_ROUTES.runs} element={<RunsPage />} />
        <Route path={APP_ROUTES.approvals} element={<ApprovalsPage />} />
        <Route path={APP_ROUTES.audit} element={<AuditPage />} />
        <Route path={APP_ROUTES.tools} element={<ToolsPage />} />
        <Route path={APP_ROUTES.memory} element={<MemoryPage />} />
        <Route
          path={APP_ROUTES.settingsConnection}
          element={<PlaceholderPage title={t("nav.settingsConnection")} subtitle={t("page.settings.subtitle")} />}
        />
        <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
        <Route path={APP_ROUTES.settingsUploadStorage} element={<SettingsUploadStorageRoute />} />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
        <Route path={APP_ROUTES.settingsExternalRag} element={<ExternalSettingsPage kind="rag" />} />
        <Route path={APP_ROUTES.settingsExternalNl2Sql} element={<ExternalSettingsPage kind="nl2sql" />} />
        <Route path={APP_ROUTES.settingsExternalMcp} element={<ExternalSettingsPage kind="mcp" />} />
        <Route path={APP_ROUTES.settingsToolPolicy} element={<ToolPolicySettingsPage />} />
        <Route path={APP_ROUTES.settingsCommandPolicy} element={<CommandPolicySettingsPage />} />
        <Route path={APP_ROUTES.settingsRuntimeSafety} element={<RuntimeSafetySettingsPage />} />
        <Route path={APP_ROUTES.settingsRuntimeSnapshot} element={<RuntimeSnapshotSettingsPage />} />
      </Routes>
    </AppShell>
  );
}

function SettingsOciRoute() {
  return (
    <div>
      <PageHeader title={t("nav.settingsOci")} subtitle={t("settings.oci.subtitle")} />
      <OciSettingsClient />
    </div>
  );
}

function SettingsUploadStorageRoute() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsUploadStorage")}
        subtitle={t("settings.uploadStorage.subtitle")}
      />
      <UploadStorageSettingsClient />
    </div>
  );
}

function SettingsDatabaseRoute() {
  return (
    <div>
      <PageHeader title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <DatabaseSettingsClient />
    </div>
  );
}
