import assert from "node:assert/strict";
import test from "node:test";

import {
  MENU_PERMISSIONS,
  currentUserHasPermission,
} from "../src/features/security/menu-permissions.ts";
import type { CurrentUser } from "../src/features/security/types.ts";

function currentUser(overrides: Partial<CurrentUser>): CurrentUser {
  return {
    user_uuid: "user-1",
    login_user_id: "operations.admin",
    display_name: "運用管理者",
    status: "ACTIVE",
    force_password_change: false,
    role_codes: [],
    is_system_admin: false,
    permissions: [],
    data_entitlements: [],
    debug_mode: false,
    password_change_allowed: true,
    ...overrides,
  };
}

test("SYSTEM_ADMIN 能力は login_user_id ではなく is_system_admin で判定する", () => {
  const user = currentUser({
    login_user_id: "operations.admin",
    role_codes: ["OPERATIONS_ADMIN"],
    is_system_admin: true,
  });

  assert.equal(currentUserHasPermission(user, MENU_PERMISSIONS.securityUsers), true);
  assert.equal(currentUserHasPermission(user, MENU_PERMISSIONS.adminSql), true);
});

test("通常ユーザーは付与された権限だけ利用できる", () => {
  const user = currentUser({
    login_user_id: "sales.user",
    role_codes: ["QUERY_USER"],
    permissions: [MENU_PERMISSIONS.query],
  });

  assert.equal(currentUserHasPermission(user, MENU_PERMISSIONS.query), true);
  assert.equal(currentUserHasPermission(user, MENU_PERMISSIONS.securityUsers), false);
});
