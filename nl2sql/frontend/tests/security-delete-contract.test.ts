import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(
  new URL("../src/features/security/api.ts", import.meta.url),
  "utf8"
);
const usersPageSource = readFileSync(
  new URL("../src/features/security/SecurityUsersPage.tsx", import.meta.url),
  "utf8"
);
const rolesPageSource = readFileSync(
  new URL("../src/features/security/SecurityRolesPage.tsx", import.meta.url),
  "utf8"
);

test("ユーザーとロールの削除 API は現在 version を strong If-Match で送る", () => {
  assert.match(
    apiSource,
    /deleteUser:[\s\S]*apiDelete<SecurityUserDeleteResult>[\s\S]*"If-Match": `"\$\{user\.version\}"`/u
  );
  assert.match(
    apiSource,
    /deleteRole:[\s\S]*apiDelete<SecurityRoleDeleteResult>[\s\S]*"If-Match": `"\$\{role\.version\}"`/u
  );
});

test("削除は状態条件付き EntityAction と不可逆確認を一覧・詳細・フォームで共有する", () => {
  assert.match(
    usersPageSource,
    /user\.status === "DISABLED"[\s\S]*!user\.is_bootstrap_admin/u
  );
  assert.match(rolesPageSource, /!role\.is_built_in && role\.archived/u);
  for (const source of [usersPageSource, rolesPageSource]) {
    assert.match(source, /id: "delete"[\s\S]*tone: "danger"/u);
    assert.match(source, /dismissOnOverlay: false/u);
    assert.match(source, /entityActionToFormAction/u);
  }
});
