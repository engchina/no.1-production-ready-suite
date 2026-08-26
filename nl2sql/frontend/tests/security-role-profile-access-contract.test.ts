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

test("role editor handles system admin and empty profile states", () => {
  assert.match(rolesPageSource, /security\.roles\.profileAccessSystemAdmin/u);
  assert.match(rolesPageSource, /security\.roles\.profileAccessEmpty/u);
  assert.match(rolesPageSource, /security\.roles\.profileAccessNoResults/u);
  assert.match(i18nSource, /利用可能な業務プロファイルがありません。管理者に権限付与を依頼してください。/u);
});
