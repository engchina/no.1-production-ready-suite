import { NAV_SECTIONS } from "@/components/layout/nav-config";
import { APP_ROUTES } from "@/lib/routes";

export const ROUTE_PERMISSIONS: Record<string, string> = Object.fromEntries(
  NAV_SECTIONS.flatMap((section) =>
    section.items.map((item) => [item.href, item.permission] as const)
  )
);

const FIRST_ALLOWED_ORDER = [
  APP_ROUTES.query,
  APP_ROUTES.directSql,
  APP_ROUTES.sqlToQuestion,
  APP_ROUTES.history,
  APP_ROUTES.tableManagement,
  APP_ROUTES.viewManagement,
  APP_ROUTES.dataManagement,
  APP_ROUTES.profiles,
  APP_ROUTES.ontologyBuild,
  APP_ROUTES.evaluation,
  APP_ROUTES.settingsOci,
  APP_ROUTES.settingsAppearance,
  APP_ROUTES.securityUsers,
  APP_ROUTES.securityRoles,
  APP_ROUTES.securityDeepSec,
];

export function firstAllowedRoute(hasPermission: (permission: string) => boolean): string {
  return (
    FIRST_ALLOWED_ORDER.find((path) => hasPermission(ROUTE_PERMISSIONS[path])) ??
    APP_ROUTES.forbidden
  );
}
