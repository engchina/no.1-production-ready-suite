/** Agent コンソールのルート定義。 */
export const APP_ROUTES = {
  dashboard: "/",
  agents: "/agents",
  runs: "/runs",
  tools: "/tools",
  history: "/history",
  settingsConnection: "/settings/connection",
  settingsModel: "/settings/model",
  settingsDatabase: "/settings/database",
} as const;
