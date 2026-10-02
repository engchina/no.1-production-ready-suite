import type { ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { AppShell, PageBody, PageHeader } from "@engchina/production-ready-ui";
import { RequireAuth, useForbiddenRedirect } from "@engchina/production-ready-system-settings";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { ChatPage } from "@/pages/ChatPage";
import { ForbiddenPage, LoginPage, PasswordChangePage } from "@/components/security/AuthPages";
import { agentIdentityKey, useAuth } from "@/components/security/AuthProvider";
import { CapabilityGate } from "@/components/security/CapabilityGate";
import { SecurityPermissionsPage } from "@/components/security/SecurityPermissionsPage";
import { SecurityRolesPage } from "@/components/security/SecurityRolesPage";
import { SecurityUsersPage } from "@/components/security/SecurityUsersPage";
import { DatabaseSettingsClient } from "@/components/settings/DatabaseSettingsClient";
import { SystemTablesCard } from "@/components/settings/SystemTablesCard";
import { AppearanceSettings } from "@/components/settings/AppearanceSettings";
import { ModelSettingsClient } from "@/components/settings/ModelSettingsClient";
import { OciSettingsClient } from "@/components/settings/OciSettingsClient";
import { UploadStorageSettingsClient } from "@/components/settings/UploadStorageSettingsClient";
import { DatabaseGate } from "@/components/system/DatabaseGate";
import { useCapabilities, type AgentCapabilities } from "@/lib/permissions";
import { canOpenRoute, defaultEntryRoute, firstAllowedRoute } from "@/lib/route-permissions";
import { APP_ROUTES } from "@/lib/routes";
import { AutomationsPage } from "@/pages/AutomationsPage";
import { EvaluationPage } from "@/pages/EvaluationPage";
import { ApiKeysPage } from "@/pages/ApiKeysPage";
import { t, type I18nKey } from "@/lib/i18n";
import {
  AgentsPage,
  ApprovalsPage,
  AuditPage,
  McpConnectionsPage,
  PluginMarketplacesPage,
  PluginsPage,
  RuntimeSnapshotSettingsPage,
  RuntimesPage,
  RunsPage,
  SkillsPage,
  ToolPolicySettingsPage,
  ToolsPage,
} from "@/pages/AgentRuntimePages";

/** 認証画面とルートの保護が使う URL（共通の RequireAuth へ渡す。#215）。 */
const AUTH_ROUTES = {
  login: APP_ROUTES.login,
  passwordChange: APP_ROUTES.passwordChange,
  forbidden: APP_ROUTES.forbidden,
};

export function App() {
  // API の 403 は共通のイベントで受け、request ID を載せて権限なしの画面へ移す（#215）。
  useForbiddenRedirect(APP_ROUTES.forbidden);

  return (
    <Routes>
      <Route path={APP_ROUTES.login} element={<LoginPage />} />
      <Route path={APP_ROUTES.passwordChange} element={<PasswordChangePage />} />
      <Route path={APP_ROUTES.forbidden} element={<ForbiddenPage />} />
      <Route element={<ProtectedLayout />}>
        {/* `/` は画面を持たず、ナビの並び順で最初に開ける画面へ移す（ダッシュボードは廃止。#262）。 */}
        <Route path={APP_ROUTES.home} element={<HomeRedirect />} />
        <Route
          path={APP_ROUTES.agents}
          element={
            <Capability need="viewRuns" titleKey="nav.agents">
              <AgentsPage />
            </Capability>
          }
        />
        <Route
          path={APP_ROUTES.runtimes}
          element={
            <Capability need="viewRuns" titleKey="nav.runtimes">
              <RuntimesPage />
            </Capability>
          }
        />
        <Route
          path={APP_ROUTES.chat}
          element={
            <Capability need="operateRuns" titleKey="nav.chat">
              <ChatPage />
            </Capability>
          }
        />
        <Route
          path={APP_ROUTES.runs}
          element={
            <Capability need="viewRuns" titleKey="nav.runs">
              <RunsPage />
            </Capability>
          }
        />
        <Route
          path={APP_ROUTES.approvals}
          element={
            <Capability need="viewRuns" titleKey="nav.approvals">
              <ApprovalsPage />
            </Capability>
          }
        />
        <Route
          path={APP_ROUTES.audit}
          element={
            <Capability need="viewAudit" titleKey="nav.audit">
              <AuditPage />
            </Capability>
          }
        />
        <Route path={APP_ROUTES.tools} element={<ToolsPage />} />
        <Route
          path={APP_ROUTES.skills}
          element={
            <Capability need="viewRuns" titleKey="nav.skills">
              <SkillsPage />
            </Capability>
          }
        />
        <Route
          path={APP_ROUTES.pluginMarketplaces}
          element={
            <Capability need="viewRuns" titleKey="nav.pluginMarketplaces">
              <PluginMarketplacesPage />
            </Capability>
          }
        />
        <Route
          path={APP_ROUTES.plugins}
          element={
            <Capability need="viewRuns" titleKey="nav.plugins">
              <PluginsPage />
            </Capability>
          }
        />
        <Route path={APP_ROUTES.automations} element={<AutomationsPage />} />
        <Route path={APP_ROUTES.evaluation} element={<EvaluationPage />} />
        <Route path={APP_ROUTES.settingsSystemTables} element={<SettingsSystemTablesRoute />} />
        <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
        <Route path={APP_ROUTES.settingsUploadStorage} element={<SettingsUploadStorageRoute />} />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
        <Route path={APP_ROUTES.settingsMcpConnections} element={<McpConnectionsPage />} />
        <Route path={APP_ROUTES.settingsApiKeys} element={<ApiKeysPage />} />
        <Route path={APP_ROUTES.settingsToolPolicy} element={<ToolPolicySettingsPage />} />
        <Route
          path={APP_ROUTES.settingsRuntimeSnapshot}
          element={
            <Capability need="admin" titleKey="nav.settingsRuntimeSnapshot">
              <RuntimeSnapshotSettingsPage />
            </Capability>
          }
        />
        <Route path={APP_ROUTES.settingsAppearance} element={<AppearanceSettings />} />
        <Route path={APP_ROUTES.securityUsers} element={<SecurityUsersPage />} />
        <Route path={APP_ROUTES.securityRoles} element={<SecurityRolesPage />} />
        <Route path={APP_ROUTES.securityPermissions} element={<SecurityPermissionsPage />} />
        <Route path="*" element={<EntryRedirect />} />
      </Route>
    </Routes>
  );
}

/** `/` は、ナビの並び順で最初に開ける画面へ（NL2SQL と同じ。#262）。 */
function HomeRedirect() {
  const { hasPermission } = useAuth();
  return <Navigate to={firstAllowedRoute(hasPermission)} replace />;
}

/** 未知の URL は既定入口（Run を開ければ Run、開けなければ `/` 経由で最初に開ける画面。#215 / #262）。 */
function EntryRedirect() {
  const { hasPermission } = useAuth();
  return <Navigate to={defaultEntryRoute(hasPermission)} replace />;
}

/**
 * 認証が必要な画面の入口。確認中・未認証・強制パスワード変更の振り分けは共通の RequireAuth（#220）、
 * URL ごとの権限（ナビに出さない画面を含む）はここで判定する（#215）。
 */
function ProtectedLayout() {
  return (
    <RequireAuth routes={AUTH_ROUTES}>
      <RoutePermissionGuard>
        <AuthorizedLayout />
      </RoutePermissionGuard>
    </RequireAuth>
  );
}

function RoutePermissionGuard({ children }: { children: ReactNode }) {
  const { hasPermission } = useAuth();
  const { pathname } = useLocation();
  if (!canOpenRoute(pathname, hasPermission)) {
    return <Navigate to={APP_ROUTES.forbidden} replace />;
  }
  return <>{children}</>;
}

function AuthorizedLayout() {
  const { user } = useAuth();
  // 利用者や権限・対象範囲が変わったら、画面の state ごと作り直す（cache は AuthProvider が破棄する）。
  return (
    <AppShell
      key={user ? agentIdentityKey(user) : ""}
      sidebar={<AppSidebar />}
      mainClassName="[contain:layout]"
      skipLinkLabel={t("common.skipToMain")}
    >
      {/* DB が使えるまで本文だけを案内に替える（サイドナビは残し、システム設定の 5 画面は開ける。#325）。 */}
      <DatabaseGate>
        <Outlet />
      </DatabaseGate>
    </AppShell>
  );
}

const CAPABILITY_REQUIREMENTS: Record<keyof AgentCapabilities, I18nKey> = {
  viewRuns: "capability.viewRuns",
  operateRuns: "capability.operateRuns",
  decideApprovals: "capability.decideApprovals",
  viewAudit: "capability.viewAudit",
  admin: "capability.admin",
};

/** 画面の実データの閲覧に必要な capability が無ければ、データを取らずに権限不足を説明する（#215）。 */
function Capability({
  need,
  titleKey,
  children,
}: {
  need: keyof AgentCapabilities;
  titleKey: I18nKey;
  children: ReactNode;
}) {
  const capabilities = useCapabilities();
  return (
    <CapabilityGate allowed={capabilities[need]} title={t(titleKey)} requirement={t(CAPABILITY_REQUIREMENTS[need])}>
      {children}
    </CapabilityGate>
  );
}

function SettingsOciRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsOci")} subtitle={t("settings.oci.subtitle")} />
      <OciSettingsClient />
    </div>
  );
}

function SettingsUploadStorageRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsUploadStorage")}
        subtitle={t("settings.uploadStorage.subtitle")}
      />
      <UploadStorageSettingsClient />
    </div>
  );
}

/** システムテーブル（運用設定の先頭。構成は RAG / NL2SQL と同じ。#658 / #751）。 */
function SettingsSystemTablesRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsSystemTables")} subtitle={t("settings.systemTables.subtitle")} />
      <PageBody wide>
        <SystemTablesCard />
      </PageBody>
    </div>
  );
}

function SettingsDatabaseRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <DatabaseSettingsClient />
    </div>
  );
}
