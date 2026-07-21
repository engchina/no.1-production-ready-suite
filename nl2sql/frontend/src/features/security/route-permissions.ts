import { APP_ROUTES } from "@/lib/routes";

export const ROUTE_PERMISSIONS: Record<string, string> = {
  [APP_ROUTES.adminSql]: "settings.database.sql_execute",
  [APP_ROUTES.tableManagement]: "documents.view",
  [APP_ROUTES.viewManagement]: "documents.view",
  [APP_ROUTES.dataManagement]: "documents.view",
  [APP_ROUTES.sampleData]: "documents.view",
  [APP_ROUTES.commentManagement]: "documents.view",
  [APP_ROUTES.annotationManagement]: "documents.view",
  [APP_ROUTES.query]: "search.view",
  [APP_ROUTES.directSql]: "search.view",
  [APP_ROUTES.sqlAnalysis]: "search.view",
  [APP_ROUTES.sqlToQuestion]: "search.view",
  [APP_ROUTES.history]: "search.view",
  [APP_ROUTES.profiles]: "knowledge_bases.view",
  [APP_ROUTES.ontologyBuild]: "knowledge_bases.view",
  [APP_ROUTES.glossaryRules]: "knowledge_bases.view",
  [APP_ROUTES.globalRules]: "knowledge_bases.view",
  [APP_ROUTES.feedbackManagement]: "evaluation.view",
  [APP_ROUTES.questionClassifierModels]: "evaluation.view",
  [APP_ROUTES.evaluation]: "evaluation.view",
  [APP_ROUTES.nl2sqlSettingsDatabase]: "settings.database.view",
  [APP_ROUTES.settingsOci]: "settings.oci.view",
  [APP_ROUTES.settingsUploadStorage]: "settings.object_storage.view",
  [APP_ROUTES.settingsModel]: "settings.models.view",
  [APP_ROUTES.settingsDatabase]: "settings.database.view",
  [APP_ROUTES.settingsAppearance]: "dashboard.view",
  [APP_ROUTES.securityUsers]: "security.users.view",
  [APP_ROUTES.securityRoles]: "security.roles.view",
  [APP_ROUTES.securityAudit]: "security.audit.view",
  [APP_ROUTES.securityDeepSec]: "security.deepsec.view",
};

const FIRST_ALLOWED_ORDER = [
  APP_ROUTES.query,
  APP_ROUTES.tableManagement,
  APP_ROUTES.profiles,
  APP_ROUTES.evaluation,
  APP_ROUTES.settingsOci,
  APP_ROUTES.securityUsers,
  APP_ROUTES.securityRoles,
  APP_ROUTES.securityAudit,
  APP_ROUTES.securityDeepSec,
];

export function firstAllowedRoute(hasPermission: (permission: string) => boolean): string {
  return (
    FIRST_ALLOWED_ORDER.find((path) => hasPermission(ROUTE_PERMISSIONS[path])) ??
    APP_ROUTES.forbidden
  );
}
