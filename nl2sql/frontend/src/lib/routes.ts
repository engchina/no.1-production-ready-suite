import { API_KEY_PATHS, SYSTEM_SETTINGS_PATHS, USER_ROLE_PATHS } from "@production-ready/system-settings";

/** NL2SQL コンソールのルート定義。 */
export const APP_ROUTES = {
  login: "/login",
  passwordChange: "/password/change",
  forbidden: "/forbidden",
  home: "/",
  chat: "/chat",
  adminSql: "/admin-sql",
  tableManagement: "/table-management",
  viewManagement: "/view-management",
  dataManagement: "/data-management",
  sampleData: "/sample-data",
  commentManagement: "/comment-management",
  annotationManagement: "/annotation-management",
  domainManagement: "/domain-management",
  query: "/query",
  profiles: "/profiles",
  ontologyBuild: "/ontology-build",
  glossaryRules: "/glossary-rules",
  globalRules: "/global-rules",
  sqlToQuestion: "/sql-to-question",
  directSql: "/direct-sql",
  feedbackManagement: "/feedback-management",
  learning: "/learning",
  questionLearning: "/question-learning",
  questionClassifierModels: "/question-classifier-models",
  history: "/history",
  evaluation: "/evaluation",
  settingsOci: "/settings/oci",
  settingsUploadStorage: "/settings/upload-storage",
  settingsModel: SYSTEM_SETTINGS_PATHS.model,
  settingsDatabase: SYSTEM_SETTINGS_PATHS.database,
  settingsSystemTables: "/settings/system-tables",
  settingsSelectAiCredential: "/settings/select-ai-credential",
  settingsAppearance: SYSTEM_SETTINGS_PATHS.appearance,
  // ユーザー管理・ロール管理は3製品共通（platform の共有パッケージ。#206）。
  securityUsers: USER_ROLE_PATHS.users,
  securityRoles: USER_ROLE_PATHS.roles,
  // 外部の MCP クライアント向けの API キー（RAG / NL2SQL 共通。#1805）。自分のキーはアカウントの
  // メニューから開き（権限を問わない）、全員のキーは「ユーザーとロール」の管理の画面。
  accountApiKeys: API_KEY_PATHS.mine,
  securityApiKeys: API_KEY_PATHS.admin,
  securityPermissions: "/settings/security/permissions",
  securityDeepSec: "/settings/security/deepsec",
  /** 旧「NL2SQL モデル学習」URL。業務プロファイルの Select AI 設定へ転送する。 */
  legacyNl2sqlModelLearning: "/settings/nl2sql-model",
} as const;
