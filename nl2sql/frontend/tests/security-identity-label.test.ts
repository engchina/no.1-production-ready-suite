import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  identityInlineLabel,
  identitySecondaryName,
} from "../src/features/security/identity-label.ts";

const source = (path: string) =>
  readFileSync(new URL(`../src/features/security/${path}`, import.meta.url), "utf8");

test("表示名は ID と異なるときだけ補助表示し、空・同一なら重複行を出さない", () => {
  assert.equal(identitySecondaryName("data_user", "データユーザー"), "データユーザー");
  assert.equal(identitySecondaryName("data_user", "  データユーザー "), "データユーザー");
  assert.equal(identitySecondaryName("data_user", "data_user"), "");
  assert.equal(identitySecondaryName("data_user", " data_user "), "");
  assert.equal(identitySecondaryName("data_user", ""), "");
  assert.equal(identitySecondaryName("data_user", null), "");
  assert.equal(identitySecondaryName("data_user"), "");
});

test("併記するときは ID を先に書く", () => {
  assert.equal(identityInlineLabel("DATA_ADMIN", "データ管理者"), "DATA_ADMIN（データ管理者）");
  assert.equal(identityInlineLabel("DATA_ADMIN", "DATA_ADMIN"), "DATA_ADMIN");
});

test("ユーザー / ロール / DeepSec の名前と ID の組は ID 先の共通表示を使い、ID で並べる", () => {
  const users = source("SecurityUsersPage.tsx");
  const roles = source("SecurityRolesPage.tsx");
  const deepsec = source("SecurityDeepSecPage.tsx");

  assert.match(users, /<SecurityIdentityLines id=\{user\.login_user_id\} name=\{user\.display_name\} \/>/u);
  assert.match(users, /<SecurityIdentityLines id=\{role\.role_code\} name=\{role\.display_name\} \/>/u);
  assert.match(users, /compareText\(left\.login_user_id, right\.login_user_id, sort\.direction\)/u);
  assert.doesNotMatch(users, /compareText\(left\.display_name/u);

  assert.match(roles, /<SecurityIdentityLines id=\{role\.role_code\} name=\{role\.display_name\} \/>/u);
  assert.match(roles, /compareText\(left\.role_code, right\.role_code, sort\.direction\)/u);
  assert.doesNotMatch(roles, /compareText\(left\.display_name/u);

  assert.match(deepsec, /<SecurityIdentityLines id=\{role\.role_code\} name=\{role\.display_name\}/u);
  assert.match(deepsec, /left\.role_code\.localeCompare\(right\.role_code, "ja"\)/u);
});
