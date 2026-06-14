/** RAG コンソールの画面ルート定義。 */
export const APP_ROUTES = {
  login: "/login",
  dashboard: "/dashboard",
  upload: "/upload",
  fileList: "/file-list",
  documents: "/documents",
  search: "/search",
  evaluation: "/evaluation",
  settingsOci: "/settings/oci",
  settingsUploadStorage: "/settings/upload-storage",
  settingsModel: "/settings/model",
  settingsDatabase: "/settings/database",
  settingsPrompts: "/settings/prompts",
} as const;
