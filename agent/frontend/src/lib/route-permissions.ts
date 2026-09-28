import {
  firstAllowedRoute as sharedFirstAllowedRoute,
  routePermissionMap,
  type HasPermission,
} from "@engchina/production-ready-system-settings";

import { NAV_ITEMS } from "@/components/layout/nav-config";
import { CAPABILITY_PERMISSIONS, MENU_PERMISSIONS } from "./permissions";
import { APP_ROUTES } from "./routes";

/**
 * ルートの権限（#215）。ナビ項目の URL は項目の `permission`、ナビに出さない画面は
 * backend の manifest と同じ権限で守る（どれか 1 つを持てばよい）。
 * 判定の実体は platform の共通関数（`routePermissionMap` / `firstAllowedRoute`）。
 */
export const ROUTE_PERMISSIONS: Readonly<Record<string, string>> = routePermissionMap(NAV_ITEMS);

/** ナビに出さない画面（URL を直接開く管理者向けの画面と、プラグインの一覧）。 */
const HIDDEN_ROUTE_PERMISSIONS: Readonly<Record<string, readonly string[]>> = {
  // プラグインはマーケットプレイスの画面から開く（同じメニュー権限）。
  [APP_ROUTES.plugins]: [MENU_PERMISSIONS.pluginMarketplaces],
  // legacy Memory（読取専用の export）は監査の閲覧と管理者だけ（backend の `/memory/search` と同じ）。
  [APP_ROUTES.memory]: [CAPABILITY_PERMISSIONS.auditView, CAPABILITY_PERMISSIONS.admin],
  // ツール一覧・ツール権限・Command Policy・Runtime Safety はナビに出さない管理者だけの画面。
  [APP_ROUTES.tools]: [CAPABILITY_PERMISSIONS.admin],
  [APP_ROUTES.settingsToolPolicy]: [CAPABILITY_PERMISSIONS.admin],
  [APP_ROUTES.settingsCommandPolicy]: [CAPABILITY_PERMISSIONS.admin],
  [APP_ROUTES.settingsRuntimeSafety]: [CAPABILITY_PERMISSIONS.admin],
};

/**
 * URL を開くのに必要な権限（どれか 1 つを持てばよい）。undefined は権限を問わない URL
 * （未知の URL のように、既定の画面へ振り分けるだけのもの）。
 */
export function routeRequiredPermissions(pathname: string): readonly string[] | undefined {
  const exact = ROUTE_PERMISSIONS[pathname];
  if (exact) return [exact];
  return HIDDEN_ROUTE_PERMISSIONS[pathname];
}

/** 利用者が URL を開けるか。権限を問わない URL は true。 */
export function canOpenRoute(pathname: string, hasPermission: HasPermission): boolean {
  const required = routeRequiredPermissions(pathname);
  return !required || required.some((permission) => hasPermission(permission));
}

/**
 * `/` の移動先。ナビの並び順で最初に開ける画面。どれも開けなければ権限なしの画面（NL2SQL と同じ。#262）。
 */
export function firstAllowedRoute(hasPermission: HasPermission): string {
  return sharedFirstAllowedRoute(NAV_ITEMS, hasPermission, APP_ROUTES.forbidden);
}

/**
 * ログイン後・未知の URL・権限なしの画面から戻るときの既定入口。主画面の Run を開ければ Run、
 * 開けなければ `/` に戻し、`/` のルートが `firstAllowedRoute` で開ける画面へ振り分ける（NL2SQL と同じ形。#262）。
 */
export function defaultEntryRoute(hasPermission: HasPermission): string {
  return hasPermission(ROUTE_PERMISSIONS[APP_ROUTES.runs]) ? APP_ROUTES.runs : APP_ROUTES.home;
}
