import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const usersPage = readFileSync(
  new URL("../../../platform/packages/system-settings/src/users-roles/UserManagementPage.tsx", import.meta.url),
  "utf8"
);

test("ユーザー作成後は値を保持して作ったユーザーのエディタへ移る", () => {
  assert.doesNotMatch(usersPage, /create-result/u);
  assert.match(usersPage, /type UserView = "list" \| "create" \| "edit" \| "missing"/u);
  assert.match(
    usersPage,
    /if \(operationBusy \|\| !canSubmitUserForm\) return/u
  );
  assert.match(usersPage, /showUserWithPassword\(created\.user, created\.temporary_password, "replace"\)/u);
  assert.doesNotMatch(usersPage, /OneTimePasswordResult|security-users-create-password/u);
});

test("作成・リセット結果は「サインインの状態」の同じ読み取り専用欄とコピー操作へ統合する", () => {
  // 作成の入力は password の欄、発行した値はエディタの「サインインの状態」の読み取り専用の欄（#4635）。
  assert.match(usersPage, /label=\{t\("security\.users\.tempPassword"\)\}\s+type="password"/u);
  assert.match(usersPage, /label=\{t\("security\.users\.oneTimePassword\.valueLabel"\)\}[\s\S]*?readOnly/u);
  assert.match(usersPage, /data-testid="security-user-temporary-password"/u);
  // コピーは共有の CopyButton（#2099）。
  assert.match(usersPage, /testId="security-user-temporary-password-copy"/u);
  assert.match(usersPage, /<CopyButton\s+text=\{draft\.temporaryPassword\}/u);
  assert.match(usersPage, /temporaryPassword: result\.temporary_password/u);
  assert.doesNotMatch(usersPage, /oneTimePassword\.createdTitle|oneTimePassword\.resetTitle/u);
});

test("無効ユーザー・管理の権限が無い利用者は保存とパスワードリセットを表示・実行しない", () => {
  assert.match(usersPage, /const disabledUser = view === "edit" && editingUser\?\.status !== "ACTIVE"/u);
  assert.match(usersPage, /const userFormReadOnly = !canManage \|\| disabledUser/u);
  assert.match(usersPage, /const canSubmitUserForm = !userFormReadOnly/u);
  assert.match(
    usersPage,
    /if \(operationBusy \|\| !canSubmitUserForm\) return/u
  );
  assert.match(
    usersPage,
    /if \(user\.status !== "ACTIVE" \|\| operationBusy\) return/u
  );
  assert.match(
    usersPage,
    /visible: user\.status === "ACTIVE" && !user\.is_bootstrap_admin/u
  );
  // 保存・作成はフォームの末尾の FormActionBar で、保存できないときは出さない（#1786）。
  assert.match(usersPage, /\{canSubmitUserForm \? \([\s\S]*?<FormActionBar[\s\S]*?id: view === "edit" \? "save" : "create"/u);
});

test("無効ユーザーの編集内容は選択状態を表示したまま読み取り専用になる", () => {
  assert.match(usersPage, /if \(userFormReadOnly\) return;\s*setDraft/u);
  assert.match(usersPage, /disabled=\{inputReadOnly\}/u);
  assert.match(
    usersPage,
    /<fieldset className="grid gap-2 border-t border-border pt-5" disabled=\{inputReadOnly\}>/u
  );
  assert.match(
    usersPage,
    /const disabled = userFormReadOnly;/u
  );
});

test("一覧の行のパスワードリセットも、そのユーザーのエディタへ結果を集約する", () => {
  assert.match(usersPage, /showUserWithPassword\(result\.user, result\.temporary_password, "open"\)/u);
  assert.match(usersPage, /security-user-temporary-password-error/u);
});
