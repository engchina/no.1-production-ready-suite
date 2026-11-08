import { useMemo } from "react";
import { RolePermissionsPage } from "@production-ready/system-settings";

import type { SecurityRole } from "@/lib/api";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { APP_ROUTES } from "@/lib/routes";
import { PERMISSIONS_API, ragPermissionTargets } from "@/lib/permission-targets";
import { securityApi } from "@/lib/security-api";
import { useAuth } from "./AuthProvider";

/**
 * 権限管理（「セキュリティ設定」のメニュー。#214 / #658）。画面の実体は platform の共通 RolePermissionsPage（#220）。
 * ロールごとの機能権限と、利用できる検索・回答プロファイル・ナレッジベースを設定する。
 */
export function SecurityPermissionsPage() {
  const { hasPermission } = useAuth();
  // 一覧の検索を毎 render 作り直さないよう、targets は mount 中は同じ配列を使う。
  const targets = useMemo(
    () => ragPermissionTargets((kind, query, signal) => securityApi.accessTargets(kind, query, { signal })),
    [],
  );
  return (
    <RolePermissionsPage<SecurityRole>
      api={PERMISSIONS_API}
      canManage={hasPermission(MENU_PERMISSIONS.securityPermissions)}
      // ロール管理を開ける利用者には、同じロールをロール管理で開く導線を出す（#4635）。
      roleManagementPath={hasPermission(MENU_PERMISSIONS.securityRoles) ? APP_ROUTES.securityRoles : undefined}
      targets={targets}
      messages={{
        title: t("nav.securityPermissions"),
        subtitle: t("security.permissions.subtitle"),
        searchPlaceholder: t("security.permissions.searchPlaceholder"),
        formHint: t("security.permissions.formHint"),
        permissionsHint: t("security.permissions.permissionsHint"),
      }}
    />
  );
}
