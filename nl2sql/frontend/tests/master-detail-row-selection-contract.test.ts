import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dbObjectSharedSource = readFileSync(
  new URL("../src/features/nl2sql/components/DbObjectManagementShared.tsx", import.meta.url),
  "utf8"
);
// ユーザー管理・ロール管理の画面は platform の共有パッケージにある（#206）。
const securityUsersSource = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/UserManagementPage.tsx", import.meta.url),
  "utf8"
);
const securityRolesSource = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/RoleManagementPage.tsx", import.meta.url),
  "utf8"
);
// 一覧の行の選択とページング（今のページの行から選ぶ）は 2 画面で共通の hook（#1891）。
const securitySharedSource = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/shared.tsx", import.meta.url),
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

  // 選択は今のページの行から決める共通の hook（usePagedSelectionList。#1891）。
  assert.match(securitySharedSource, /selectedVisibleKey/u);
  // 利用者が選んだ行は manual として保持し、絞り込み後も選択を残す（render で読むため state で持つ。#181）。
  assert.match(securitySharedSource, /preserveSelected: selection\.manual/u);
  assert.match(securityUsersSource, /usePagedSelectionList/u);
  assert.match(securityUsersSource, /selectedRowKey=\{visibleSelectedId\}/u);
  assert.match(securityUsersSource, /selectUser\(user\.user_uuid\)/u);
  assert.match(securityUsersSource, /visibleRows=\{INFORMATION_TABLE_VISIBLE_ROWS\}/u);
  assert.match(securityUsersSource, /className: INFORMATION_TABLE_ROW_CLASS/u);
  assert.match(securityUsersSource, /scrollTestId="security-users-scroll-region"/u);
  assert.match(securityUsersSource, /scrollAriaLabel/u);

  assert.match(securityRolesSource, /usePagedSelectionList/u);
  assert.match(securityRolesSource, /selectedRowKey=\{visibleSelectedId\}/u);
  assert.match(securityRolesSource, /selectRole\(role\.role_id\)/u);
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
