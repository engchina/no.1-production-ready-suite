/** RAG コンソールの画面ルート定義。 */
export const APP_ROUTES = {
  login: "/login",
  dashboard: "/dashboard",
  upload: "/upload",
  fileList: "/file-list",
  knowledgeBases: "/knowledge-bases",
  documents: "/documents",
  search: "/search",
  evaluation: "/evaluation",
  settingsOci: "/settings/oci",
  settingsUploadStorage: "/settings/upload-storage",
  settingsParserAdapters: "/settings/parser-adapters",
  settingsModel: "/settings/model",
  settingsDatabase: "/settings/database",
  settingsPrompts: "/settings/prompts",
} as const;
