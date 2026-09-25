import { NAV_SECTIONS } from "@/components/layout/nav-config";
import { APP_ROUTES } from "@/lib/routes";

export const ROUTE_PERMISSIONS: Record<string, string> = Object.fromEntries(
  NAV_SECTIONS.flatMap((section) =>
    section.items.map((item) => [item.href, item.permission] as const)
  )
);

const FIRST_ALLOWED_ORDER = NAV_SECTIONS.flatMap((section) =>
  section.items.map((item) => item.href)
);

export function firstAllowedRoute(hasPermission: (permission: string) => boolean): string {
  return (
    FIRST_ALLOWED_ORDER.find((path) => {
      const permission = ROUTE_PERMISSIONS[path];
      return Boolean(permission && hasPermission(permission));
    }) ??
    APP_ROUTES.forbidden
  );
}

/**
 * ログイン後などの既定入口。SQL 生成を利用できない場合は root に戻し、
 * root route が firstAllowedRoute で利用可能な画面へ振り分ける。
 */
export function defaultEntryRoute(hasPermission: (permission: string) => boolean): string {
  return hasPermission(ROUTE_PERMISSIONS[APP_ROUTES.query])
    ? APP_ROUTES.query
    : APP_ROUTES.home;
}
