import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@engchina/production-ready-ui";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { PageHeader } from "@/components/PageHeader";
import { DatabaseSettingsClient } from "@/components/settings/DatabaseSettingsClient";
import { ModelSettingsClient } from "@/components/settings/ModelSettingsClient";
import { OciSettingsClient } from "@/components/settings/OciSettingsClient";
import { UploadStorageSettingsClient } from "@/components/settings/UploadStorageSettingsClient";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { Nl2SqlWorkbench } from "@/features/nl2sql/Nl2SqlWorkbench";
import { DataToolsPage } from "@/features/nl2sql/pages/DataToolsPage";
import { EngineOperationsPage } from "@/features/nl2sql/pages/EngineOperationsPage";
import { EvaluationPage } from "@/features/nl2sql/pages/EvaluationPage";
import { GlossaryRulesPage } from "@/features/nl2sql/pages/GlossaryRulesPage";
import { HistoryPage } from "@/features/nl2sql/pages/HistoryPage";
import { LearningPage } from "@/features/nl2sql/pages/LearningPage";
import { ProfileManagementPage } from "@/features/nl2sql/pages/ProfileManagementPage";
import { SchemaCatalogPage } from "@/features/nl2sql/pages/SchemaCatalogPage";
import {
  ConnectionSettingsPage as Nl2SqlConnectionSettingsPage,
  DatabaseSettingsPage as Nl2SqlDatabaseSettingsPage,
  ModelSettingsPage as Nl2SqlModelSettingsPage,
} from "@/features/nl2sql/pages/SettingsPages";
import { SqlAnalysisPage } from "@/features/nl2sql/pages/SqlAnalysisPage";
import { DashboardPage } from "@/pages/DashboardPage";

export function App() {
  return (
    <AppShell sidebar={<AppSidebar />}>
      <Routes>
        <Route path={APP_ROUTES.dashboard} element={<DashboardPage />} />
        <Route path={APP_ROUTES.schema} element={<SchemaCatalogPage />} />
        <Route path={APP_ROUTES.dataTools} element={<DataToolsPage />} />
        <Route path={APP_ROUTES.query} element={<Nl2SqlWorkbench />} />
        <Route path={APP_ROUTES.profiles} element={<ProfileManagementPage />} />
        <Route path={APP_ROUTES.glossaryRules} element={<GlossaryRulesPage />} />
        <Route path={APP_ROUTES.sqlAnalysis} element={<SqlAnalysisPage />} />
        <Route path={APP_ROUTES.learning} element={<LearningPage />} />
        <Route path={APP_ROUTES.history} element={<HistoryPage />} />
        <Route path={APP_ROUTES.evaluation} element={<EvaluationPage />} />
        <Route path={APP_ROUTES.engineOperations} element={<EngineOperationsPage />} />
        <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
        <Route
          path={APP_ROUTES.settingsUploadStorage}
          element={<SettingsUploadStorageRoute />}
        />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
        <Route
          path={APP_ROUTES.nl2sqlSettingsConnection}
          element={<Nl2SqlConnectionSettingsPage />}
        />
        <Route
          path={APP_ROUTES.nl2sqlSettingsModel}
          element={<Nl2SqlModelSettingsPage />}
        />
        <Route
          path={APP_ROUTES.nl2sqlSettingsDatabase}
          element={<Nl2SqlDatabaseSettingsPage />}
        />
        <Route path="/settings" element={<Navigate to={APP_ROUTES.settingsOci} replace />} />
      </Routes>
    </AppShell>
  );
}

function SettingsOciRoute() {
  return (
    <>
      <PageHeader title={t("nav.settingsOci")} subtitle={t("settings.oci.subtitle")} />
      <OciSettingsClient />
    </>
  );
}

function SettingsUploadStorageRoute() {
  return (
    <>
      <PageHeader
        title={t("nav.settingsUploadStorage")}
        subtitle={t("settings.uploadStorage.subtitle")}
      />
      <UploadStorageSettingsClient />
    </>
  );
}

function SettingsDatabaseRoute() {
  return (
    <>
      <PageHeader title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <DatabaseSettingsClient />
    </>
  );
}
