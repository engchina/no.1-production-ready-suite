/** NL2SQL コンソールのルート定義。 */
export const APP_ROUTES = {
  dashboard: "/",
  schema: "/schema",
  query: "/query",
  profiles: "/profiles",
  history: "/history",
  evaluation: "/evaluation",
  settingsConnection: "/settings/connection",
  settingsModel: "/settings/model",
  settingsDatabase: "/settings/database",
} as const;
