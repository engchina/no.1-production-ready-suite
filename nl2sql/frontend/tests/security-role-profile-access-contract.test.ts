import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const rolesPageSource = readFileSync(
  new URL("../src/features/security/SecurityRolesPage.tsx", import.meta.url),
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
const listDensitySource = readFileSync(
  new URL("../src/lib/list-density.ts", import.meta.url),
  "utf8"
);
const securityManagementSharedSource = readFileSync(
  new URL("../src/features/security/SecurityManagementShared.tsx", import.meta.url),
  "utf8"
);

test("role API and security types carry allowed profile IDs", () => {
  assert.match(securityTypesSource, /allowed_profile_ids: string\[\]/u);
  assert.match(securityApiSource, /allowed_profile_ids\?: string\[\]/u);
  assert.match(securityApiSource, /allowed_profile_ids: role\.allowed_profile_ids/u);
  assert.match(
    securityApiSource,
    /apiGet<ProfileAccessProfile\[\]>\("\/api\/security\/profile-access\/profiles"/u
  );
});

test("role editor fetches profile catalog only for role managers", () => {
  assert.match(rolesPageSource, /const profileRowsRequest = canManage/u);
  assert.match(rolesPageSource, /securityApi\s*\.\s*profileAccessProfiles\(\{ signal \}\)/u);
  assert.match(rolesPageSource, /Promise\.resolve\(\{ rows: \[\] as ProfileAccessProfile\[\], warning: "" \}\)/u);
});

test("role editor keeps core role list when profile catalog fails", () => {
  assert.match(rolesPageSource, /profileAccessLoadWarning/u);
  assert.match(rolesPageSource, /security\.roles\.profileAccessLoadWarning/u);
  assert.match(rolesPageSource, /<Banner severity="warning">\{profileAccessLoadWarning\}<\/Banner>/u);
});

test("role editor saves profile access and supports bulk selection", () => {
  assert.match(rolesPageSource, /allowedProfileIds: role\.allowed_profile_ids/u);
  assert.match(rolesPageSource, /allowed_profile_ids: draft\.allowedProfileIds/u);
  assert.match(rolesPageSource, /toggleProfileAccess\(profile\.id\)/u);
  assert.match(rolesPageSource, /selectProfileAccess\(profileAccessIds\)/u);
  assert.match(rolesPageSource, /clearProfileAccess\(profileAccessIds\)/u);
  assert.match(rolesPageSource, /security-roles-profile-access-selection-actions/u);
  assert.match(rolesPageSource, /security-roles-profile-access-search/u);
});

test("role editor uses the shared responsive height for an accessible profile scroll region", () => {
  assert.match(
    listDensitySource,
    /INFORMATION_LIST_SCROLL_CLASS = "max-h-\[17\.5rem\] overflow-auto md:max-h-\[28rem\]"/u
  );
  assert.match(rolesPageSource, /INFORMATION_LIST_SCROLL_CLASS/u);
  assert.match(rolesPageSource, /id="security-roles-profile-access-label"/u);
  assert.match(rolesPageSource, /role="region"/u);
  assert.match(rolesPageSource, /aria-labelledby="security-roles-profile-access-label"/u);
  assert.match(rolesPageSource, /tabIndex=\{0\}/u);
  assert.match(rolesPageSource, /data-testid="security-roles-profile-access-list"/u);
  assert.match(rolesPageSource, /overflow-x-hidden/u);
  assert.match(rolesPageSource, /INFORMATION_TABLE_FOCUS_CLASS/u);
});

test("role editor handles system admin and empty profile states", () => {
  assert.match(rolesPageSource, /security\.roles\.profileAccessSystemAdmin/u);
  assert.match(rolesPageSource, /security\.roles\.profileAccessEmpty/u);
  assert.match(rolesPageSource, /security\.roles\.profileAccessNoResults/u);
  assert.match(i18nSource, /利用可能な業務プロファイルがありません。管理者に権限付与を依頼してください。/u);
});

test("archived role editor keeps permission and profile options visible but read-only", () => {
  assert.match(
    rolesPageSource,
    /const readOnly = Boolean\(!canManage \|\| editingRole\?\.is_built_in \|\| editingRole\?\.archived\)/u
  );
  assert.match(rolesPageSource, /if \(busy \|\| readOnly\) return/u);
  assert.match(
    rolesPageSource,
    /<fieldset className="grid gap-3" disabled=\{readOnly\}>/u
  );
  assert.match(rolesPageSource, /disabled=\{readOnly \|\| inherited\}/u);
  assert.match(
    rolesPageSource,
    /disabled=\{readOnly\}\s*onChange=\{\(value\) => \{\s*if \(readOnly\) return;\s*setProfileAccessSearch\(value\);/u
  );
  assert.match(rolesPageSource, /disabled=\{readOnly\}\s*onChange=\{\(\) => toggleProfileAccess/u);
  assert.match(securityManagementSharedSource, /disabled\?: boolean/u);
  assert.match(securityManagementSharedSource, /disabled:bg-muted\/20 disabled:text-muted/u);
  assert.ok((rolesPageSource.match(/if \(readOnly\) return;/gu) ?? []).length >= 10);
});
