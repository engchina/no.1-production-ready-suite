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
// 一覧の行の選択（URL）とページングは 2 画面で共通の hook（#1891 / #2282）。
const securitySharedSource = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/shared.tsx", import.meta.url),
  "utf8"
);
const profileManagementSource = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8"
);

test("一覧/詳細ページは共有 DataTable の行のクリックと表示行数を使う", () => {
  // 行全体のクリック・行内操作の除外・スクロール領域の公開は共有 DataTable が持つ（#530）。
  for (const source of [securityUsersSource, securityRolesSource, dbObjectSharedSource, profileManagementSource]) {
    assert.match(source, /<DataTable/u);
    assert.doesNotMatch(source, /MasterDetailDataTable|isInteractiveRowTarget|<table/u);
    assert.match(source, /onRowClick=/u);
  }
  for (const source of [dbObjectSharedSource, profileManagementSource]) {
    assert.match(source, /selectedRowKey=/u);
  }

  // ユーザー・ロール管理は全幅の一覧 → 全幅のエディタ（A 型。#4635）。開いている対象は URL の検索パラメータ
  // （共有の useUrlSelection）が持ち、表は visibleRows の高さで中をスクロールする。
  assert.match(securitySharedSource, /useUrlSelection\(/u);
  assert.doesNotMatch(securitySharedSource, /ListDetailLayout|selectedVisibleKey|FixedSplitPane/u);
  for (const [source, param, grid] of [
    [securityUsersSource, "user", "users"],
    [securityRolesSource, "role", "roles"],
  ] as const) {
    assert.match(source, /usePagedSelectionList/u);
    assert.match(source, new RegExp(`useSecurityEditorSelection\\("${param}"\\)`, "u"));
    assert.match(source, /useReturnFocusToRow\(view === "list", lastOpenedId\)/u);
    assert.doesNotMatch(source, /selectedRowKey=/u);
    assert.match(source, /visibleRows=\{INFORMATION_TABLE_VISIBLE_ROWS\}/u);
    assert.match(source, /className: INFORMATION_TABLE_ROW_CLASS/u);
    assert.match(source, new RegExp(`scrollTestId="security-${grid}-scroll-region"`, "u"));
    assert.match(source, /scrollAriaLabel/u);
  }
});

test("行メニューには純粋な詳細/編集選択を置かず、実操作だけを残す", () => {
  assert.doesNotMatch(dbObjectSharedSource, /id:\s*"detail"/u);
  assert.match(dbObjectSharedSource, /id:\s*"drop"/u);

  assert.doesNotMatch(profileManagementSource, /profiles\.action\.select"\)\}<\/span>/u);
  assert.match(profileManagementSource, /id:\s*"delete"/u);
});

test("プロファイルの一覧は操作列を持たず、セキュリティの一覧の行のメニューはエディタと同じ操作の定義を使う", () => {
  // ユーザー・ロール管理（A 型。#4635）: 行のメニューとエディタの ObjectActionBar に同じ EntityAction を渡す
  // （page-archetypes.md §0-6）。開くのは行と名前のリンクで、メニューに「編集」「表示」を入れない（§0-7）。
  assert.match(securityUsersSource, /<RowActionMenu\s+actions=\{accountActions\(user\)\}/u);
  assert.match(securityUsersSource, /<ObjectActionBar\s+actions=\{accountActions\(editingUser\)\}/u);
  assert.match(securityRolesSource, /<RowActionMenu\s+actions=\{stateActions\(role\)\}/u);
  assert.match(securityRolesSource, /<ObjectActionBar\s+actions=\{stateActions\(editingRole\)\}/u);
  for (const source of [securityUsersSource, securityRolesSource]) {
    assert.doesNotMatch(source, /id:\s*"edit"/u);
  }

  assert.doesNotMatch(profileManagementSource, /RowActionMenu/u);
  assert.doesNotMatch(profileManagementSource, /profile-management-row-actions/u);
  assert.match(profileManagementSource, /testId="profile-editor-actions"/u);
});
