import { Route, Routes } from "react-router-dom";

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
  McpServersPage,
  MemoryPage,
  RuntimeSnapshotSettingsPage,
  RuntimeSafetySettingsPage,
  RunsPage,
  SkillsPage,
  ToolPolicySettingsPage,
  ToolsPage,
} from "@/pages/AgentRuntimePages";

export function App() {
  return (
    <div className="flex">
      <AppSidebar />
      <main
        className="h-screen min-w-0 flex-1 overflow-y-auto [contain:layout] focus:outline-none"
        aria-label="メイン領域"
        tabIndex={-1}
      >
        <Routes>
          <Route path={APP_ROUTES.dashboard} element={<DashboardPage />} />
          <Route path={APP_ROUTES.agents} element={<AgentsPage />} />
          <Route path={APP_ROUTES.runs} element={<RunsPage />} />
          <Route path={APP_ROUTES.approvals} element={<ApprovalsPage />} />
          <Route path={APP_ROUTES.audit} element={<AuditPage />} />
          <Route path={APP_ROUTES.tools} element={<ToolsPage />} />
          <Route path={APP_ROUTES.skills} element={<SkillsPage />} />
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
          <Route path={APP_ROUTES.settingsExternalMcp} element={<McpServersPage />} />
          <Route path={APP_ROUTES.settingsToolPolicy} element={<ToolPolicySettingsPage />} />
          <Route path={APP_ROUTES.settingsCommandPolicy} element={<CommandPolicySettingsPage />} />
          <Route path={APP_ROUTES.settingsRuntimeSafety} element={<RuntimeSafetySettingsPage />} />
          <Route path={APP_ROUTES.settingsRuntimeSnapshot} element={<RuntimeSnapshotSettingsPage />} />
        </Routes>
      </main>
    </div>
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
