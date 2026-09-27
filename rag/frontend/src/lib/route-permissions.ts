import {
  firstAllowedRoute as sharedFirstAllowedRoute,
  routePermissionMap,
  type HasPermission,
} from "@engchina/production-ready-system-settings";

import { NAV_ITEMS, NAV_SECTIONS } from "@/components/layout/nav-config";
import { MENU_PERMISSIONS } from "./permissions";
import { APP_ROUTES } from "./routes";

/**
 * ルートの権限（#214）。ナビ項目の URL は項目の `permission`、ナビに無い詳細画面は開く導線の権限で守る。
 * 判定の実体は platform の共通関数（`routePermissionMap` / `firstAllowedRoute`）。
 */
export const ROUTE_PERMISSIONS: Readonly<Record<string, string>> = routePermissionMap(NAV_ITEMS);

/**
 * 文書の詳細は、文書ワークスペース（アップロード・文書インデックス）と、引用カードから開く画面
 * （RAG 検索・チャット・ナレッジベース）のどれかがあれば開ける（backend の `_DOCUMENT_VIEW` と同じ）。
 */
const DOCUMENT_DETAIL_PERMISSIONS = [
  MENU_PERMISSIONS.upload,
  MENU_PERMISSIONS.fileList,
  MENU_PERMISSIONS.search,
  MENU_PERMISSIONS.chat,
  MENU_PERMISSIONS.knowledgeBases,
];

function isDetailPath(pathname: string, base: string): boolean {
  return pathname.startsWith(`${base}/`) && pathname.length > base.length + 1;
}

/**
 * URL を開くのに必要な権限（どれか 1 つを持てばよい）。undefined は権限を問わない URL
 * （`/` や未知の URL のように、既定の画面へ振り分けるだけのもの）。
 */
export function routeRequiredPermissions(pathname: string): readonly string[] | undefined {
  const exact = ROUTE_PERMISSIONS[pathname];
  if (exact) return [exact];
  if (isDetailPath(pathname, APP_ROUTES.documents)) return DOCUMENT_DETAIL_PERMISSIONS;
  if (isDetailPath(pathname, APP_ROUTES.knowledgeBases)) return [MENU_PERMISSIONS.knowledgeBases];
  return undefined;
}

/** 利用者が URL を開けるか。権限を問わない URL は true。 */
export function canOpenRoute(pathname: string, hasPermission: HasPermission): boolean {
  const required = routeRequiredPermissions(pathname);
  return !required || required.some((permission) => hasPermission(permission));
}

/** ナビの並び順で最初に開ける画面。どれも開けなければ権限なしの画面。 */
export function firstAllowedRoute(hasPermission: HasPermission): string {
  return sharedFirstAllowedRoute(NAV_ITEMS, hasPermission, APP_ROUTES.forbidden);
}

/**
 * ログイン後・`/`・未知の URL の移動先。ダッシュボードを開ければダッシュボード（今までの既定の入口）、
 * 開けなければナビの並び順で最初に開ける画面。
 */
export function defaultEntryRoute(hasPermission: HasPermission): string {
  return hasPermission(MENU_PERMISSIONS.dashboard)
    ? APP_ROUTES.dashboard
    : firstAllowedRoute(hasPermission);
}

/** `/settings` の移動先。共通のシステム設定のうち最初に開ける画面、無ければ既定の入口。 */
export function settingsEntryRoute(hasPermission: HasPermission): string {
  const settings = NAV_SECTIONS.find((section) => section.titleKey === "nav.section.settings");
  const route = sharedFirstAllowedRoute(settings?.items ?? [], hasPermission, "");
  return route || defaultEntryRoute(hasPermission);
}
