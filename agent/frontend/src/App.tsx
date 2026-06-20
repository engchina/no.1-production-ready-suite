import { Route, Routes } from "react-router-dom";

import { AppShell } from "@engchina/production-ready-ui";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { DashboardPage } from "@/pages/DashboardPage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";

export function App() {
  return (
    <AppShell sidebar={<AppSidebar />}>
      <Routes>
        <Route path={APP_ROUTES.dashboard} element={<DashboardPage />} />
        <Route path={APP_ROUTES.agents} element={<PlaceholderPage title={t("nav.agents")} />} />
        <Route
          path={APP_ROUTES.runs}
          element={<PlaceholderPage title={t("nav.runs")} subtitle={t("page.runs.subtitle")} />}
        />
        <Route path={APP_ROUTES.tools} element={<PlaceholderPage title={t("nav.tools")} />} />
        <Route path={APP_ROUTES.history} element={<PlaceholderPage title={t("nav.history")} />} />
        <Route
          path={APP_ROUTES.settingsConnection}
          element={<PlaceholderPage title={t("nav.settingsConnection")} subtitle={t("page.settings.subtitle")} />}
        />
        <Route path={APP_ROUTES.settingsModel} element={<PlaceholderPage title={t("nav.settingsModel")} />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<PlaceholderPage title={t("nav.settingsDatabase")} />} />
      </Routes>
    </AppShell>
  );
}
