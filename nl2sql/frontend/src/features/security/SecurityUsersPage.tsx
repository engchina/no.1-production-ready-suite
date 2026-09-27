import { UserManagementPage } from "@engchina/production-ready-system-settings";

import { FIXED_SPLIT_STORAGE_PREFIX } from "@/lib/ui-store";
import { useAuth } from "./AuthProvider";
import { securityApi } from "./api";
import { describeSecurityApiError } from "./describe-error";
import { MENU_PERMISSIONS } from "./menu-permissions";

/** ユーザー管理。画面の実体は platform の共有パッケージ（#206）。 */
export function SecurityUsersPage() {
  const { hasPermission, user } = useAuth();
  return (
    <UserManagementPage
      api={securityApi}
      canManage={hasPermission(MENU_PERMISSIONS.securityUsers)}
      currentUserUuid={user?.user_uuid ?? null}
      describeError={describeSecurityApiError}
      splitStoragePrefix={FIXED_SPLIT_STORAGE_PREFIX}
    />
  );
}
