import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const masterDetailTableSource = readFileSync(
  new URL("../src/components/MasterDetailDataTable.tsx", import.meta.url),
  "utf8"
);
const dbObjectSharedSource = readFileSync(
  new URL("../src/features/nl2sql/components/DbObjectManagementShared.tsx", import.meta.url),
  "utf8"
);
const securityUsersSource = readFileSync(
  new URL("../src/features/security/SecurityUsersPage.tsx", import.meta.url),
  "utf8"
);
const securityRolesSource = readFileSync(
  new URL("../src/features/security/SecurityRolesPage.tsx", import.meta.url),
  "utf8"
);
const profileManagementSource = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8"
);

test("マスタ詳細テーブルは行全体の単一選択契約を持つ", () => {
  assert.match(masterDetailTableSource, /export function MasterDetailDataTable/u);
  assert.match(masterDetailTableSource, /selectedRowKey/u);
  assert.match(masterDetailTableSource, /onRowSelect/u);
  assert.match(masterDetailTableSource, /data-selected/u);
  assert.match(masterDetailTableSource, /aria-current/u);
  assert.match(masterDetailTableSource, /getRowAriaLabel/u);
});

test("行クリックはボタンや menu item などのアクション領域を選択扱いにしない", () => {
  assert.match(masterDetailTableSource, /export function isInteractiveRowTarget/u);
  for (const selector of [
    "button",
    "a",
    "input",
    "select",
    "textarea",
    '[role="button"]',
    '[role="menuitem"]',
    "[data-row-action]",
  ]) {
    assert.match(masterDetailTableSource, new RegExp(selector.replaceAll("[", "\\[").replaceAll("]", "\\]"), "u"));
  }
  assert.match(masterDetailTableSource, /isInteractiveRowTarget\(event\.target\)/u);
});

test("一覧/詳細ページは同じ行選択プリミティブを使う", () => {
  assert.match(securityUsersSource, /MasterDetailDataTable/u);
  assert.match(securityUsersSource, /selectedVisibleKey/u);
  assert.match(securityUsersSource, /selectedRowKey=\{visibleSelectedId\}/u);
  assert.match(securityUsersSource, /selectedUserManualSelection\.current = true/u);
  assert.match(securityUsersSource, /setSelectedId\(user\.user_uuid\)/u);

  assert.match(securityRolesSource, /MasterDetailDataTable/u);
  assert.match(securityRolesSource, /selectedVisibleKey/u);
  assert.match(securityRolesSource, /selectedRowKey=\{visibleSelectedId\}/u);
  assert.match(securityRolesSource, /selectedRoleManualSelection\.current = true/u);
  assert.match(securityRolesSource, /setSelectedId\(role\.role_id\)/u);

  assert.match(dbObjectSharedSource, /isInteractiveRowTarget\(event\.target\)/u);
  assert.match(profileManagementSource, /isInteractiveRowTarget\(event\.target\)/u);
});

test("行メニューには純粋な詳細/編集選択を置かず、実操作だけを残す", () => {
  assert.doesNotMatch(dbObjectSharedSource, /id:\s*"detail"/u);
  assert.match(dbObjectSharedSource, /id:\s*"drop"/u);

  assert.doesNotMatch(profileManagementSource, /profiles\.action\.select"\)\}<\/span>/u);
  assert.match(profileManagementSource, /id:\s*"delete"/u);
  assert.match(profileManagementSource, /RowActionMenu/u);
});
