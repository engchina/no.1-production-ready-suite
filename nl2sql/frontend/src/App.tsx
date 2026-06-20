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
        <Route path={APP_ROUTES.schema} element={<PlaceholderPage title={t("nav.schema")} />} />
        <Route
          path={APP_ROUTES.query}
          element={<PlaceholderPage title={t("nav.query")} subtitle={t("page.query.subtitle")} />}
        />
        <Route path={APP_ROUTES.history} element={<PlaceholderPage title={t("nav.history")} />} />
        <Route path={APP_ROUTES.evaluation} element={<PlaceholderPage title={t("nav.evaluation")} />} />
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
