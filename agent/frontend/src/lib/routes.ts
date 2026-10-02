import { SYSTEM_SETTINGS_PATHS, USER_ROLE_PATHS } from "@engchina/production-ready-system-settings";

/** Agent コンソールのルート定義。 */
export const APP_ROUTES = {
  // 認証（AppShell の外に出す画面。#215）。
  login: "/login",
  passwordChange: "/password/change",
  forbidden: "/forbidden",
  settingsAppearance: SYSTEM_SETTINGS_PATHS.appearance,
  // `/` は画面を持たない入口（ナビの並び順で最初に開ける画面へ移す。ダッシュボードは廃止。#262）。
  home: "/",
  agents: "/agents",
  runtimes: "/runtimes",
  runs: "/runs",
  approvals: "/approvals",
  audit: "/audit",
  tools: "/tools",
  skills: "/skills",
  plugins: "/plugins",
  pluginMarketplaces: "/plugins/marketplaces",
  settingsSystemTables: "/settings/system-tables",
  settingsOci: "/settings/oci",
  settingsUploadStorage: "/settings/upload-storage",
  settingsModel: SYSTEM_SETTINGS_PATHS.model,
  settingsDatabase: SYSTEM_SETTINGS_PATHS.database,
  // MCP 接続（RAG / NL2SQL / 外部 MCP。#757）。
  settingsMcpConnections: "/settings/mcp-connections",
  settingsToolPolicy: "/settings/tool-policy",
  settingsRuntimeSnapshot: "/settings/runtime-snapshot",
  // 共通のユーザー管理・ロール管理（#206）と、Agent 固有の権限管理（#215）。
  securityUsers: USER_ROLE_PATHS.users,
  securityRoles: USER_ROLE_PATHS.roles,
  securityPermissions: "/settings/security/permissions",
} as const;

/** AppShell の外に出す認証の画面（ログイン・パスワード変更・権限なし）。 */
export const AUTH_ROUTE_PATHS: readonly string[] = [APP_ROUTES.login, APP_ROUTES.passwordChange, APP_ROUTES.forbidden];
