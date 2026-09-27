import { useMemo } from "react";
import { RolePermissionsPage } from "@engchina/production-ready-system-settings";

import { AGENT_SPLIT_STORAGE_PREFIX } from "@/components/EntityLayout";
import type { SecurityRole } from "@/lib/api";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { PERMISSIONS_API, accessTargetsLoader, agentPermissionTargets } from "@/lib/permission-targets";
import { securityApi } from "@/lib/security-api";
import { useAuth } from "./AuthProvider";

/**
 * 権限管理（「Agent セキュリティ」のメニュー。#215）。画面の実体は platform の共通 RolePermissionsPage（#220）。
 * ロールごとの機能権限と、利用できるエージェント・業務ビューを設定する。
 */
export function SecurityPermissionsPage() {
  const { hasPermission } = useAuth();
  // 一覧の検索を毎 render 作り直さないよう、targets は mount 中は同じ配列を使う。
  const targets = useMemo(
    () => agentPermissionTargets(accessTargetsLoader((signal) => securityApi.accessTargets({ signal }))),
    [],
  );
  return (
    <RolePermissionsPage<SecurityRole>
      api={PERMISSIONS_API}
      canManage={hasPermission(MENU_PERMISSIONS.securityPermissions)}
      targets={targets}
      splitStoragePrefix={AGENT_SPLIT_STORAGE_PREFIX}
      messages={{
        title: t("nav.securityPermissions"),
        subtitle: t("security.permissions.subtitle"),
        listHint: t("security.permissions.listHint"),
        searchPlaceholder: t("security.permissions.searchPlaceholder"),
        formHint: t("security.permissions.formHint"),
        permissionsHint: t("security.permissions.permissionsHint"),
      }}
    />
  );
}
