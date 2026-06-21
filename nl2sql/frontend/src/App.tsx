import { Route, Routes } from "react-router-dom";

import { AppShell } from "@engchina/production-ready-ui";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { APP_ROUTES } from "@/lib/routes";
import { Nl2SqlWorkbench } from "@/features/nl2sql/Nl2SqlWorkbench";
import { EvaluationPage } from "@/features/nl2sql/pages/EvaluationPage";
import { HistoryPage } from "@/features/nl2sql/pages/HistoryPage";
import { ProfileManagementPage } from "@/features/nl2sql/pages/ProfileManagementPage";
import { SchemaCatalogPage } from "@/features/nl2sql/pages/SchemaCatalogPage";
import {
  ConnectionSettingsPage,
  DatabaseSettingsPage,
  ModelSettingsPage,
} from "@/features/nl2sql/pages/SettingsPages";
import { DashboardPage } from "@/pages/DashboardPage";

export function App() {
  return (
    <AppShell sidebar={<AppSidebar />}>
      <Routes>
        <Route path={APP_ROUTES.dashboard} element={<DashboardPage />} />
        <Route path={APP_ROUTES.schema} element={<SchemaCatalogPage />} />
        <Route path={APP_ROUTES.query} element={<Nl2SqlWorkbench />} />
        <Route path={APP_ROUTES.profiles} element={<ProfileManagementPage />} />
        <Route path={APP_ROUTES.history} element={<HistoryPage />} />
        <Route path={APP_ROUTES.evaluation} element={<EvaluationPage />} />
        <Route path={APP_ROUTES.settingsConnection} element={<ConnectionSettingsPage />} />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsPage />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<DatabaseSettingsPage />} />
      </Routes>
    </AppShell>
  );
}
