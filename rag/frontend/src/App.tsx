import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import { ChevronLeft } from "lucide-react";

import { LoginPage } from "@/components/auth/LoginPage";
import { DashboardClient } from "@/components/dashboard/DashboardClient";
import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { EvaluationClient } from "@/components/evaluation/EvaluationClient";
import { FileListClient } from "@/components/file-list/FileListClient";
import { Sidebar } from "@/components/layout/Sidebar";
import { PageHeader } from "@/components/PageHeader";
import { SearchClient } from "@/components/search/SearchClient";
import { ErrorState } from "@/components/StateViews";
import { DatabaseSettingsClient } from "@/components/settings/DatabaseSettingsClient";
import { ModelSettingsClient } from "@/components/settings/ModelSettingsClient";
import { OciSettingsClient } from "@/components/settings/OciSettingsClient";
import { UploadStorageSettingsClient } from "@/components/settings/UploadStorageSettingsClient";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { UploadWorkspace } from "@/components/upload/UploadWorkspace";
import { useAuth } from "@/lib/auth";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";

export function App() {
  const auth = useAuth();

  if (auth.isLoading) {
    return <AuthLoading />;
  }

  if (auth.error) {
    return (
      <main className="grid min-h-dvh place-items-center bg-background p-6">
        <div className="w-full max-w-lg">
          <ErrorState message={t("auth.status.error")} onRetry={() => void auth.refetch()} />
        </div>
      </main>
    );
  }

  return (
    <Routes>
      <Route path={APP_ROUTES.login} element={<LoginRoute />} />
      <Route element={<ProtectedLayout />}>
        <Route path="/" element={<Navigate to={APP_ROUTES.dashboard} replace />} />
        <Route path={APP_ROUTES.dashboard} element={<DashboardClient />} />
        <Route path={APP_ROUTES.upload} element={<UploadWorkspace />} />
        <Route path={APP_ROUTES.fileList} element={<FileListClient />} />
        <Route path={`${APP_ROUTES.documents}/:id`} element={<DocumentDetailRoute />} />
        <Route path={APP_ROUTES.search} element={<SearchClient />} />
        <Route path={APP_ROUTES.evaluation} element={<EvaluationClient />} />
        <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
        <Route
          path={APP_ROUTES.settingsUploadStorage}
          element={<SettingsUploadStorageRoute />}
        />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
        <Route path="/settings" element={<Navigate to={APP_ROUTES.settingsOci} replace />} />
      </Route>
      <Route path="*" element={<Navigate to={APP_ROUTES.dashboard} replace />} />
    </Routes>
  );
}

function ProtectedLayout() {
  const auth = useAuth();
  const location = useLocation();
  if (auth.authRequired && !auth.isAuthenticated) {
    const from = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to={APP_ROUTES.login} state={{ from }} replace />;
  }

  return (
    <div className="flex">
      <Sidebar />
      <main className="h-screen flex-1 overflow-y-auto" aria-label="メイン領域">
        <Outlet />
      </main>
    </div>
  );
}

function LoginRoute() {
  const auth = useAuth();
  const location = useLocation();
  if (!auth.authRequired || auth.isAuthenticated) {
    const redirectTarget = (location.state as { from?: string } | null)?.from;
    return (
      <Navigate
        to={
          redirectTarget && redirectTarget !== APP_ROUTES.login
            ? redirectTarget
            : APP_ROUTES.dashboard
        }
        replace
      />
    );
  }
  return <LoginPage />;
}

function AuthLoading() {
  return (
    <main className="grid min-h-dvh place-items-center bg-background p-6">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-6">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="mt-4 h-11 w-full" />
        <Skeleton className="mt-3 h-11 w-full" />
        <Button className="mt-5 h-11 w-full" disabled>
          {t("auth.status.checking")}
        </Button>
      </div>
    </main>
  );
}

function DocumentDetailRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return <Navigate to={APP_ROUTES.fileList} replace />;

  return (
    <div>
      <div className="border-b border-border bg-card px-8 py-4">
        <Link
          to={APP_ROUTES.fileList}
          className="inline-flex items-center gap-1 text-sm text-muted transition-colors hover:text-foreground"
        >
          <ChevronLeft size={16} aria-hidden />
          {t("workspace.back")}
        </Link>
      </div>
      <div className="p-8">
        <DocumentWorkspace documentId={id} />
      </div>
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
