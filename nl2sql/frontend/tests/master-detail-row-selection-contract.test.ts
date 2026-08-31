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

test("マスタ詳細テーブルは任意のスクロール領域をキーボード操作可能に公開する", () => {
  assert.match(masterDetailTableSource, /scrollAriaLabel/u);
  assert.match(masterDetailTableSource, /role=\{scrollAriaLabel \? "region" : undefined\}/u);
  assert.match(masterDetailTableSource, /tabIndex=\{scrollAriaLabel \? 0 : undefined\}/u);
  assert.match(masterDetailTableSource, /aria-label=\{scrollAriaLabel\}/u);
  assert.match(masterDetailTableSource, /scrollClassName/u);
  assert.match(masterDetailTableSource, /scrollTestId/u);
  assert.match(masterDetailTableSource, /rowClassName/u);
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
  assert.match(securityUsersSource, /INFORMATION_TABLE_SCROLL_CLASS/u);
  assert.match(securityUsersSource, /rowClassName=\{INFORMATION_TABLE_ROW_CLASS\}/u);
  assert.match(securityUsersSource, /scrollTestId="security-users-scroll-region"/u);
  assert.match(securityUsersSource, /scrollAriaLabel/u);

  assert.match(securityRolesSource, /MasterDetailDataTable/u);
  assert.match(securityRolesSource, /selectedVisibleKey/u);
  assert.match(securityRolesSource, /selectedRowKey=\{visibleSelectedId\}/u);
  assert.match(securityRolesSource, /selectedRoleManualSelection\.current = true/u);
  assert.match(securityRolesSource, /setSelectedId\(role\.role_id\)/u);
  assert.match(securityRolesSource, /INFORMATION_TABLE_SCROLL_CLASS/u);
  assert.match(securityRolesSource, /rowClassName=\{INFORMATION_TABLE_ROW_CLASS\}/u);
  assert.match(securityRolesSource, /scrollTestId="security-roles-scroll-region"/u);
  assert.match(securityRolesSource, /scrollAriaLabel/u);

  assert.match(dbObjectSharedSource, /isInteractiveRowTarget\(event\.target\)/u);
  assert.match(profileManagementSource, /isInteractiveRowTarget\(event\.target\)/u);
});

test("行メニューには純粋な詳細/編集選択を置かず、実操作だけを残す", () => {
  assert.doesNotMatch(dbObjectSharedSource, /id:\s*"detail"/u);
  assert.match(dbObjectSharedSource, /id:\s*"drop"/u);

  assert.doesNotMatch(profileManagementSource, /profiles\.action\.select"\)\}<\/span>/u);
  assert.match(profileManagementSource, /id:\s*"delete"/u);
});

test("セキュリティとプロファイルの一覧は操作列を持たず詳細アクションへ集約する", () => {
  assert.doesNotMatch(securityUsersSource, /RowActionMenu/u);
  assert.doesNotMatch(securityUsersSource, /key:\s*"actions"/u);
  assert.match(securityUsersSource, /testId="security-users-detail-actions"/u);

  assert.doesNotMatch(securityRolesSource, /RowActionMenu/u);
  assert.doesNotMatch(securityRolesSource, /key:\s*"actions"/u);
  assert.match(securityRolesSource, /testId="security-roles-detail-actions"/u);

  assert.doesNotMatch(profileManagementSource, /RowActionMenu/u);
  assert.doesNotMatch(profileManagementSource, /profile-management-row-actions/u);
  assert.match(profileManagementSource, /testId="profile-editor-actions"/u);
});
