import { API_KEY_PATHS, SYSTEM_SETTINGS_PATHS, USER_ROLE_PATHS } from "@production-ready/system-settings";

/** RAG コンソールの画面ルート定義。 */
export const APP_ROUTES = {
  settingsAppearance: SYSTEM_SETTINGS_PATHS.appearance,
  // 認証画面（NL2SQL と同じ URL。#214）
  login: "/login",
  passwordChange: "/password/change",
  forbidden: "/forbidden",
  home: "/",
  upload: "/upload",
  fileList: "/file-list",
  knowledgeBases: "/knowledge-bases",
  searchAnswerProfiles: "/search-answer-profiles",
  documents: "/documents",
  chat: "/chat",
  search: "/search",
  evaluation: "/evaluation",
  feedback: "/feedback",
  settingsPipeline: "/settings/pipeline",
  settingsOci: "/settings/oci",
  settingsUploadStorage: "/settings/upload-storage",
  settingsPreprocess: "/settings/preprocess",
  settingsParserAdapters: "/settings/parser-adapters",
  settingsChunking: "/settings/chunking",
  settingsRetrieval: "/settings/retrieval",
  settingsGuardrail: "/settings/guardrail",
  settingsVectorIndex: "/settings/vector-index",
  settingsEvaluation: "/settings/evaluation",
  settingsGraph: "/settings/graph",
  settingsModel: SYSTEM_SETTINGS_PATHS.model,
  settingsDatabase: SYSTEM_SETTINGS_PATHS.database,
  settingsSystemTables: "/settings/system-tables",
  settingsHuggingface: "/settings/huggingface",
  settingsServices: "/settings/services",
  settingsPrompts: "/settings/prompts",
  // ユーザー管理・ロール管理は3製品共通の画面（#206）。権限管理は RAG 固有（#214）。
  securityUsers: USER_ROLE_PATHS.users,
  securityRoles: USER_ROLE_PATHS.roles,
  // 外部の MCP クライアント向けの API キー（RAG / NL2SQL 共通。#1805）。自分のキーはアカウントの
  // メニューから開き（権限を問わない）、全員のキーは「ユーザーとロール」の管理の画面。
  accountApiKeys: API_KEY_PATHS.mine,
  securityApiKeys: API_KEY_PATHS.admin,
  securityPermissions: "/settings/security/permissions",
} as const;
