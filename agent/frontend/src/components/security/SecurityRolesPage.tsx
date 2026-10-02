import { buttonVariants } from "@engchina/production-ready-ui";
import { RoleManagementPage, SYSTEM_ADMIN_ROLE_CODE } from "@engchina/production-ready-system-settings";
import { LockKeyhole } from "lucide-react";
import { Link } from "react-router-dom";

import { AGENT_SPLIT_STORAGE_PREFIX } from "@/components/EntityLayout";
import type { SecurityRole } from "@/lib/api";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { APP_ROUTES } from "@/lib/routes";
import { describeSecurityApiError, securityApi } from "@/lib/security-api";
import { useAuth } from "./AuthProvider";

/**
 * ロール管理。画面の実体は platform の共有パッケージ（#206）。
 * ロールへの権限と対象範囲の付与は Agent の権限管理画面で行うため、詳細に件数と導線だけを出す（#215）。
 */
export function SecurityRolesPage() {
  const { hasPermission } = useAuth();
  const canManagePermissions = hasPermission(MENU_PERMISSIONS.securityPermissions);
  return (
    <RoleManagementPage<SecurityRole>
      api={securityApi}
      canManage={hasPermission(MENU_PERMISSIONS.securityRoles)}
      describeError={describeSecurityApiError}
      splitStoragePrefix={AGENT_SPLIT_STORAGE_PREFIX}
      renderRoleDetailExtra={(role) => (
        <div
          className="flex flex-col gap-3 rounded-md border border-border bg-surface p-3 sm:flex-row sm:items-center sm:justify-between"
          data-testid="security-roles-permission-summary"
        >
          <p className="text-sm text-fg">
            {role.role_code === SYSTEM_ADMIN_ROLE_CODE
              ? t("security.roles.permissionSummarySystemAdmin")
              : t("security.roles.permissionSummary", { count: role.permissions?.length ?? 0 })}
          </p>
          {canManagePermissions && !role.is_built_in && !role.archived ? (
            <Link
              to={`${APP_ROUTES.securityPermissions}?role=${encodeURIComponent(role.role_id)}`}
              className={buttonVariants({ variant: "secondary", size: "sm" })}
              data-testid="security-roles-open-permissions"
            >
              <LockKeyhole size={16} aria-hidden="true" />
              <span>{t("security.roles.openPermissions")}</span>
            </Link>
          ) : null}
        </div>
      )}
    />
  );
}
