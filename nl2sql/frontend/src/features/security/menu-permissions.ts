export const MENU_PERMISSIONS = {
  query: "menu.query",
  directSql: "menu.direct_sql",
  sqlToQuestion: "menu.sql_to_question",
  history: "menu.history",
  adminSql: "menu.admin_sql",
  tableManagement: "menu.table_management",
  viewManagement: "menu.view_management",
  dataManagement: "menu.data_management",
  commentManagement: "menu.comment_management",
  annotationManagement: "menu.annotation_management",
  glossaryRules: "menu.glossary_rules",
  globalRules: "menu.global_rules",
  sampleData: "menu.sample_data",
  profiles: "menu.profiles",
  ontologyBuild: "menu.ontology_build",
  feedbackManagement: "menu.feedback_management",
  questionClassifierModels: "menu.question_classifier_models",
  evaluation: "menu.evaluation",
  securityUsers: "menu.security_users",
  securityRoles: "menu.security_roles",
  securityDeepSec: "menu.security_deepsec",
  settingsOci: "menu.settings_oci",
  settingsUploadStorage: "menu.settings_upload_storage",
  settingsModel: "menu.settings_model",
  settingsDatabase: "menu.settings_database",
  settingsSystemTables: "menu.settings_system_tables",
  settingsAppearance: "menu.settings_appearance",
} as const;

export type MenuPermission = (typeof MENU_PERMISSIONS)[keyof typeof MENU_PERMISSIONS];

const LEGACY_PERMISSION_ALIASES: Record<string, MenuPermission[]> = {
  "dashboard.view": [MENU_PERMISSIONS.settingsAppearance],
  "documents.view": [
    MENU_PERMISSIONS.tableManagement,
    MENU_PERMISSIONS.viewManagement,
    MENU_PERMISSIONS.dataManagement,
    MENU_PERMISSIONS.commentManagement,
    MENU_PERMISSIONS.annotationManagement,
    MENU_PERMISSIONS.sampleData,
  ],
  "documents.upload": [MENU_PERMISSIONS.dataManagement, MENU_PERMISSIONS.sampleData],
  "documents.preview": [
    MENU_PERMISSIONS.tableManagement,
    MENU_PERMISSIONS.viewManagement,
    MENU_PERMISSIONS.dataManagement,
  ],
  "documents.approve": [MENU_PERMISSIONS.dataManagement],
  "documents.ingest": [MENU_PERMISSIONS.dataManagement],
  "documents.delete": [
    MENU_PERMISSIONS.tableManagement,
    MENU_PERMISSIONS.viewManagement,
    MENU_PERMISSIONS.dataManagement,
  ],
  "knowledge_bases.view": [
    MENU_PERMISSIONS.profiles,
    MENU_PERMISSIONS.ontologyBuild,
    MENU_PERMISSIONS.glossaryRules,
    MENU_PERMISSIONS.globalRules,
  ],
  "knowledge_bases.manage": [
    MENU_PERMISSIONS.profiles,
    MENU_PERMISSIONS.ontologyBuild,
    MENU_PERMISSIONS.glossaryRules,
    MENU_PERMISSIONS.globalRules,
  ],
  "business_views.view": [MENU_PERMISSIONS.profiles],
  "business_views.manage": [MENU_PERMISSIONS.profiles],
  "business_views.use": [MENU_PERMISSIONS.query, MENU_PERMISSIONS.profiles],
  "search.view": [
    MENU_PERMISSIONS.query,
    MENU_PERMISSIONS.directSql,
    MENU_PERMISSIONS.sqlToQuestion,
    MENU_PERMISSIONS.history,
  ],
  "search.execute": [MENU_PERMISSIONS.query, MENU_PERMISSIONS.directSql],
  "search.export": [
    MENU_PERMISSIONS.query,
    MENU_PERMISSIONS.directSql,
    MENU_PERMISSIONS.history,
  ],
  "evaluation.view": [
    MENU_PERMISSIONS.feedbackManagement,
    MENU_PERMISSIONS.questionClassifierModels,
    MENU_PERMISSIONS.evaluation,
  ],
  "evaluation.run": [MENU_PERMISSIONS.evaluation],
  "evaluation.manage": [
    MENU_PERMISSIONS.feedbackManagement,
    MENU_PERMISSIONS.questionClassifierModels,
    MENU_PERMISSIONS.evaluation,
  ],
  "settings.oci.view": [MENU_PERMISSIONS.settingsOci],
  "settings.oci.manage": [MENU_PERMISSIONS.settingsOci],
  "settings.object_storage.view": [MENU_PERMISSIONS.settingsUploadStorage],
  "settings.object_storage.manage": [MENU_PERMISSIONS.settingsUploadStorage],
  "settings.models.view": [MENU_PERMISSIONS.settingsModel],
  "settings.models.manage": [MENU_PERMISSIONS.settingsModel],
  "settings.database.view": [
    MENU_PERMISSIONS.settingsDatabase,
    MENU_PERMISSIONS.settingsSystemTables,
  ],
  "settings.database.manage": [MENU_PERMISSIONS.settingsDatabase],
  "settings.database.sql_execute": [
    MENU_PERMISSIONS.adminSql,
    MENU_PERMISSIONS.settingsSystemTables,
  ],
  "settings.appearance.view": [MENU_PERMISSIONS.settingsAppearance],
  "security.users.view": [MENU_PERMISSIONS.securityUsers],
  "security.users.manage": [MENU_PERMISSIONS.securityUsers],
  "security.roles.view": [MENU_PERMISSIONS.securityRoles],
  "security.roles.manage": [MENU_PERMISSIONS.securityRoles],
  "security.deepsec.view": [MENU_PERMISSIONS.securityDeepSec],
  "security.deepsec.apply": [MENU_PERMISSIONS.securityDeepSec],
  "security.deepsec.verify": [MENU_PERMISSIONS.securityDeepSec],
};

export function normalizeMenuPermissions(permissions: string[]): Set<string> {
  const normalized = new Set<string>();
  const pending = permissions.map((permission) => permission.trim()).filter(Boolean);
  while (pending.length > 0) {
    const permission = pending.pop();
    if (!permission) continue;
    if (permission.startsWith("menu.")) {
      normalized.add(permission);
      continue;
    }
    pending.push(...(LEGACY_PERMISSION_ALIASES[permission] ?? []));
  }
  return normalized;
}
