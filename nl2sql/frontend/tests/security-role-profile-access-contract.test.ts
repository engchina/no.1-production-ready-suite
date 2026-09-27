import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// 業務プロファイル利用権限は NL2SQL の権限管理画面が扱う。ロールの基本情報は共通のロール管理（#206）。
const permissionsPageSource = readFileSync(
  new URL("../src/features/security/SecurityPermissionsPage.tsx", import.meta.url),
  "utf8"
);
const rolesPageSource = readFileSync(
  new URL(
    "../../../platform/packages/system-settings/src/users-roles/RoleManagementPage.tsx",
    import.meta.url
  ),
  "utf8"
);
const sharedRoleTypesSource = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/types.ts", import.meta.url),
  "utf8"
);
const sharedRoleMessagesSource = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/messages.ts", import.meta.url),
  "utf8"
);
const securityManagementSharedSource = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/shared.tsx", import.meta.url),
  "utf8"
);
const securityApiSource = readFileSync(
  new URL("../src/features/security/api.ts", import.meta.url),
  "utf8"
);
const securityTypesSource = readFileSync(
  new URL("../src/features/security/types.ts", import.meta.url),
  "utf8"
);
const i18nSource = readFileSync(new URL("../src/lib/i18n.ts", import.meta.url), "utf8");

test("role API and security types carry allowed profile IDs", () => {
  assert.match(securityTypesSource, /allowed_profile_ids: string\[\]/u);
  assert.match(securityApiSource, /"permissions" \| "allowed_profile_ids"/u);
  assert.match(securityApiSource, /allowed_profile_ids: role\.allowed_profile_ids/u);
  assert.match(
    securityApiSource,
    /apiGet<ProfileAccessProfile\[\]>\("\/api\/security\/profile-access\/profiles"/u
  );
});

test("permissions are saved through the permission endpoint, role basics through PATCH", () => {
  assert.match(
    securityApiSource,
    /updateRolePermissions:[\s\S]*apiPut<SecurityRole>\(`\/api\/security\/roles\/\$\{role\.role_id\}\/permissions`/u
  );
  const updateRole = securityApiSource.slice(
    securityApiSource.indexOf("updateRole: (role: SecurityRole)"),
    securityApiSource.indexOf("updateRolePermissions:")
  );
  assert.doesNotMatch(updateRole, /permissions|allowed_profile_ids/u);
  assert.doesNotMatch(rolesPageSource, /allowed_profile_ids|profileAccess|togglePermission/u);
});

test("permission editor keeps the role list when the profile catalog fails", () => {
  assert.match(permissionsPageSource, /securityApi\s*\.\s*profileAccessProfiles\(\{ signal \}\)/u);
  assert.match(permissionsPageSource, /profileAccessLoadWarning/u);
  assert.match(permissionsPageSource, /security\.roles\.profileAccessLoadWarning/u);
  assert.match(
    permissionsPageSource,
    /<Banner severity="warning">\{profileAccessLoadWarning\}<\/Banner>/u
  );
});

test("permission editor saves profile access and supports bulk selection", () => {
  assert.match(permissionsPageSource, /allowedProfileIds: role\.allowed_profile_ids/u);
  assert.match(
    permissionsPageSource,
    /allowed_profile_ids: draftGrantsAllProfileAccess \? \[\] : draft\.allowedProfileIds/u
  );
  assert.match(permissionsPageSource, /toggleProfileAccess\(profile\.id\)/u);
  assert.match(permissionsPageSource, /selectProfileAccess\(profileAccessIds\)/u);
  assert.match(permissionsPageSource, /clearProfileAccess\(profileAccessIds\)/u);
  assert.match(permissionsPageSource, /security-roles-profile-access-selection-actions/u);
  assert.match(permissionsPageSource, /security-roles-profile-access-search/u);
});

test("permission editor uses the shared responsive height for an accessible profile scroll region", () => {
  assert.match(
    securityManagementSharedSource,
    /SECURITY_LIST_SCROLL_CLASS = "max-h-\[17\.5rem\] overflow-auto md:max-h-\[28rem\]"/u
  );
  assert.match(permissionsPageSource, /SECURITY_LIST_SCROLL_CLASS/u);
  assert.match(permissionsPageSource, /id="security-roles-profile-access-label"/u);
  assert.match(permissionsPageSource, /role="region"/u);
  assert.match(permissionsPageSource, /aria-labelledby="security-roles-profile-access-label"/u);
  assert.match(permissionsPageSource, /tabIndex=\{0\}/u);
  assert.match(permissionsPageSource, /data-testid="security-roles-profile-access-list"/u);
  assert.match(permissionsPageSource, /overflow-x-hidden/u);
  assert.match(permissionsPageSource, /SECURITY_LIST_FOCUS_CLASS/u);
});

test("permission editor handles system admin and empty profile states", () => {
  assert.match(permissionsPageSource, /const PROFILE_MANAGE_PERMISSION = "nl2sql\.profiles\.manage"/u);
  assert.match(permissionsPageSource, /roleGrantsAllProfileAccess/u);
  assert.match(permissionsPageSource, /draftGrantsAllProfileAccess/u);
  assert.match(permissionsPageSource, /profileAccessReadOnly/u);
  assert.match(permissionsPageSource, /security\.roles\.profileAccessSystemAdmin/u);
  assert.match(permissionsPageSource, /security\.roles\.profileAccessManagedAll/u);
  assert.match(permissionsPageSource, /security\.roles\.profileAccessEmpty/u);
  assert.match(permissionsPageSource, /security\.roles\.profileAccessNoResults/u);
  assert.match(i18nSource, /利用可能な業務プロファイルがありません。管理者に権限付与を依頼してください。/u);
});

test("role management blocks reserved SYSTEM_ADMIN role creation before submit", () => {
  assert.match(sharedRoleTypesSource, /export const SYSTEM_ADMIN_ROLE_CODE = "SYSTEM_ADMIN"/u);
  assert.match(rolesPageSource, /normalizedRoleCode === SYSTEM_ADMIN_ROLE_CODE/u);
  assert.match(rolesPageSource, /setFieldErrors\(nextErrors\)/u);
  assert.match(rolesPageSource, /SECURITY_ROLE_CODE_RESERVED/u);
  assert.match(sharedRoleMessagesSource, /"security\.roles\.codeReserved"/u);
});

test("built-in and archived roles cannot be edited from the permission editor", () => {
  assert.match(permissionsPageSource, /return !role\.is_built_in && !role\.archived;/u);
  assert.match(
    permissionsPageSource,
    /const readOnly = Boolean\(!canManage \|\| \(editingRole && !permissionsEditable\(editingRole\)\)\)/u
  );
  assert.match(permissionsPageSource, /canManage && permissionsEditable\(role\)/u);
  assert.match(permissionsPageSource, /<fieldset className="grid gap-3" disabled=\{inputReadOnly\}>/u);
  assert.match(permissionsPageSource, /disabled=\{inputReadOnly \|\| inherited\}/u);
  assert.match(
    permissionsPageSource,
    /disabled=\{profileAccessReadOnly\}\s*onChange=\{\(value\) => \{\s*if \(profileAccessReadOnly\) return;\s*setProfileAccessSearch\(value\);/u
  );
  assert.match(
    permissionsPageSource,
    /disabled=\{profileAccessReadOnly\}\s*onChange=\{\(\) => toggleProfileAccess/u
  );
  assert.match(securityManagementSharedSource, /disabled\?: boolean/u);
  assert.match(securityManagementSharedSource, /disabled:bg-surface-hover disabled:text-fg-disabled/u);
  assert.ok((permissionsPageSource.match(/if \(inputReadOnly\) return;/gu) ?? []).length >= 3);
  assert.ok((permissionsPageSource.match(/if \(profileAccessReadOnly\) return;/gu) ?? []).length >= 4);
});
