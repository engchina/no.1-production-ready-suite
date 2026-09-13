import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

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

test("一覧/詳細ページは共有 DataTable の行選択（selectedRowKey / onRowClick）と表示行数を使う", () => {
  // 行全体の単一選択・行内操作の除外・スクロール領域の公開は共有 DataTable が持つ（#530）。
  for (const source of [securityUsersSource, securityRolesSource, dbObjectSharedSource, profileManagementSource]) {
    assert.match(source, /<DataTable/u);
    assert.doesNotMatch(source, /MasterDetailDataTable|isInteractiveRowTarget|<table/u);
    assert.match(source, /selectedRowKey=/u);
    assert.match(source, /onRowClick=/u);
  }

  assert.match(securityUsersSource, /selectedVisibleKey/u);
  assert.match(securityUsersSource, /selectedRowKey=\{visibleSelectedId\}/u);
  assert.match(securityUsersSource, /selectedUserManualSelection\.current = true/u);
  assert.match(securityUsersSource, /setSelectedId\(user\.user_uuid\)/u);
  assert.match(securityUsersSource, /visibleRows=\{INFORMATION_TABLE_VISIBLE_ROWS\}/u);
  assert.match(securityUsersSource, /className: INFORMATION_TABLE_ROW_CLASS/u);
  assert.match(securityUsersSource, /scrollTestId="security-users-scroll-region"/u);
  assert.match(securityUsersSource, /scrollAriaLabel/u);

  assert.match(securityRolesSource, /selectedVisibleKey/u);
  assert.match(securityRolesSource, /selectedRowKey=\{visibleSelectedId\}/u);
  assert.match(securityRolesSource, /selectedRoleManualSelection\.current = true/u);
  assert.match(securityRolesSource, /setSelectedId\(role\.role_id\)/u);
  assert.match(securityRolesSource, /visibleRows=\{INFORMATION_TABLE_VISIBLE_ROWS\}/u);
  assert.match(securityRolesSource, /className: INFORMATION_TABLE_ROW_CLASS/u);
  assert.match(securityRolesSource, /scrollTestId="security-roles-scroll-region"/u);
  assert.match(securityRolesSource, /scrollAriaLabel/u);
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
