import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// 業務プロファイル利用権限は NL2SQL の権限管理が扱う。画面の実体は platform の共通 RolePermissionsPage で、
// NL2SQL は業務プロファイルを targets の 1 つとして渡す（#220）。ロールの基本情報は共通のロール管理（#206）。
const permissionsPageSource = readFileSync(
  new URL(
    "../../../platform/packages/system-settings/src/permissions/RolePermissionsPage.tsx",
    import.meta.url
  ),
  "utf8"
);
const nl2sqlPermissionsSource = readFileSync(
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
  assert.match(nl2sqlPermissionsSource, /securityApi\.profileAccessProfiles\(\{ signal \}\)/u);
  assert.match(nl2sqlPermissionsSource, /loadWarning: t\("security\.roles\.profileAccessLoadWarning"\)/u);
  assert.match(permissionsPageSource, /\.catch\(\(cause: unknown\) => \{\s*if \(isAbortError\(cause\)\) throw cause;/u);
  assert.match(permissionsPageSource, /warning: formatMessage\(target\.messages\.loadWarning, \{ message \}\)/u);
  assert.match(
    permissionsPageSource,
    /<Banner key=\{target\.key\} severity="warning">\s*\{targetLoadWarnings\[target\.key\]\}\s*<\/Banner>/u
  );
});

test("permission editor saves profile access and supports bulk selection", () => {
  assert.match(nl2sqlPermissionsSource, /const PROFILE_ACCESS_KEY = "profile-access"/u);
  assert.match(nl2sqlPermissionsSource, /selectedIds: \(role\) => role\.allowed_profile_ids \?\? \[\]/u);
  assert.match(nl2sqlPermissionsSource, /allowed_profile_ids: draft\.targets\[PROFILE_ACCESS_KEY\] \?\? \[\]/u);
  assert.match(
    permissionsPageSource,
    /draftGrantsAll\(target\) \? \[\] : \(draft\.targets\[target\.key\] \?\? \[\]\)/u
  );
  assert.match(permissionsPageSource, /const idPrefix = `security-roles-\$\{target\.key\}`/u);
  assert.match(permissionsPageSource, /onChange=\{\(\) => toggle\(item\.id\)\}/u);
  assert.match(permissionsPageSource, /onSelectAll=\{selectVisible\}/u);
  assert.match(permissionsPageSource, /onClearAll=\{clearVisible\}/u);
  assert.match(permissionsPageSource, /dataTestId=\{`\$\{idPrefix\}-selection-actions`\}/u);
  assert.match(permissionsPageSource, /testId=\{`\$\{idPrefix\}-search`\}/u);
});

test("permission editor uses the shared responsive height for an accessible profile scroll region", () => {
  assert.match(
    securityManagementSharedSource,
    /SECURITY_LIST_SCROLL_CLASS = "max-h-\[17\.5rem\] overflow-auto md:max-h-\[28rem\]"/u
  );
  assert.match(permissionsPageSource, /SECURITY_LIST_SCROLL_CLASS/u);
  assert.match(permissionsPageSource, /id=\{`\$\{idPrefix\}-label`\}/u);
  assert.match(permissionsPageSource, /role="region"/u);
  assert.match(permissionsPageSource, /aria-labelledby=\{`\$\{idPrefix\}-label`\}/u);
  assert.match(permissionsPageSource, /tabIndex=\{0\}/u);
  assert.match(permissionsPageSource, /data-testid=\{`\$\{idPrefix\}-list`\}/u);
  assert.match(permissionsPageSource, /overflow-x-hidden/u);
  assert.match(permissionsPageSource, /SECURITY_LIST_FOCUS_CLASS/u);
});

test("permission editor handles system admin and empty profile states", () => {
  assert.match(nl2sqlPermissionsSource, /const PROFILE_MANAGE_PERMISSION = "nl2sql\.profiles\.manage"/u);
  assert.match(
    nl2sqlPermissionsSource,
    /grantsAll: \(effectivePermissions\) => effectivePermissions\.has\(PROFILE_MANAGE_PERMISSION\)/u
  );
  assert.match(nl2sqlPermissionsSource, /security\.roles\.profileAccessSystemAdmin/u);
  assert.match(nl2sqlPermissionsSource, /security\.roles\.profileAccessManagedAll/u);
  assert.match(nl2sqlPermissionsSource, /security\.roles\.profileAccessEmpty/u);
  assert.match(nl2sqlPermissionsSource, /security\.roles\.profileAccessNoResults/u);
  assert.match(permissionsPageSource, /roleCode === SYSTEM_ADMIN_ROLE_CODE \|\| Boolean\(target\.grantsAll\?\.\(effectiveCodes\)\)/u);
  assert.match(permissionsPageSource, /const targetReadOnly = inputReadOnly \|\| grantsAll;/u);
  assert.match(permissionsPageSource, /systemAdmin \? tm\.grantsAllSystemAdmin : tm\.grantsAllByPermission/u);
  assert.match(permissionsPageSource, /\{tm\.empty\}/u);
  assert.match(permissionsPageSource, /\{tm\.noResults\}/u);
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
  assert.match(permissionsPageSource, /<fieldset className="grid gap-3" disabled=\{targetReadOnly\}>/u);
  assert.match(permissionsPageSource, /disabled=\{inputReadOnly \|\| inherited\}/u);
  assert.match(
    permissionsPageSource,
    /disabled=\{targetReadOnly\}\s*onChange=\{\(value\) => \{\s*if \(targetReadOnly\) return;\s*onSearchChange\(value\);/u
  );
  assert.match(permissionsPageSource, /disabled=\{targetReadOnly\}\s*onChange=\{\(\) => toggle/u);
  assert.match(securityManagementSharedSource, /disabled\?: boolean/u);
  // 検索欄は共有の TextField（無効の見た目は TextField が持つ。#384）に disabled を渡す。
  assert.match(securityManagementSharedSource, /export function SecuritySearchField[\s\S]*?<TextField[\s\S]*?disabled=\{disabled\}/u);
  assert.ok((permissionsPageSource.match(/if \(inputReadOnly\) return;/gu) ?? []).length >= 3);
  assert.ok((permissionsPageSource.match(/if \(targetReadOnly\) return;/gu) ?? []).length >= 4);
});
