import { expect, test, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { findClippedPlaceholders, measureHorizontalOverflow } from "../../../../platform/packages/ui/e2e/layout-checks";

// 共通のユーザー・ロール・権限管理の一覧 → 全幅のエディタ（A 型。page-archetypes.md §1 A。#4635）。
// 一覧は全幅の表（詳細の列・空の選択の状態を持たない）。行か名前のリンクで全幅のエディタを開き、
// 開いている対象は URL（?user= / ?role=、新規作成は =new）が持つ。「一覧へ戻る」は行のあったページへ戻り、
// その行の名前へフォーカスを戻す。

const ROLE = {
  role_id: "role-viewer",
  role_code: "LIST_VIEWER",
  display_name: "一覧閲覧",
  description: "合成のロール",
  is_built_in: false,
  archived: false,
  version: 1,
  permissions: ["menu.query"],
  data_entitlements: [],
  allowed_profile_ids: [],
};

// 1 ページ（既定 25 件）を超える件数。3 件目は無効（読み取り専用のエディタになる）。
const USERS = Array.from({ length: 30 }, (_, index) => {
  const sequence = String(index + 1).padStart(2, "0");
  return {
    user_uuid: `list-user-${sequence}`,
    login_user_id: `list.user.${sequence}`,
    display_name: `一覧ユーザー ${sequence}`,
    status: index === 2 ? "DISABLED" : "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 1,
    role_ids: [ROLE.role_id],
    is_bootstrap_admin: false,
  };
});

async function fulfill(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data, error_messages: [], warning_messages: [] }),
  });
}

async function mockSecurity(page: Page) {
  await mockDatabaseGateReady(page);
  await page.route("**/api/security/roles?include_archived=*", (route) => fulfill(route, [ROLE]));
  await page.route("**/api/security/users", (route) => fulfill(route, USERS));
  await page.route("**/api/security/permissions", (route) =>
    fulfill(route, [
      { code: "menu.query", group: "AI 活用", label: "SQL 生成", description: "合成の権限", implies: [] },
    ])
  );
}

/** 一覧の行の名前のリンク（`RowTitleButton` の `<a href="?user=…">`）。 */
function userLink(page: Page, sequence: string) {
  return page.getByRole("link", { name: `一覧ユーザー ${sequence} を表示` });
}

test.describe("ユーザー・ロール・権限管理の一覧 → 全幅のエディタ（A 型。#4635）", () => {
  test("一覧は全幅の表で詳細の列を持たず、名前のリンクから全幅のエディタを開き、再読込でも同じエディタを出す", { tag: "@responsive" }, async ({ page }) => {
    await mockSecurity(page);
    await page.goto("/settings/security/users");

    const list = page.getByTestId("security-users-list");
    const grid = page.getByTestId("security-users-grid");
    await expect(userLink(page, "01")).toBeVisible();
    // 一覧 + 詳細（B 型）の列・空の選択の状態・戻るは持たず、先頭の行を自動で開かない。
    await expect(page.locator('[data-testid^="list-detail-"]')).toHaveCount(0);
    await expect(page.getByText("ユーザーを選択してください")).toHaveCount(0);
    await expect(page).not.toHaveURL(/user=/u);
    await expect(page.getByTestId("security-users-back")).toHaveCount(0);
    await expect(page.getByTestId("security-users-editor")).toHaveCount(0);
    await expect(grid.locator('tbody tr[data-selected="true"]')).toHaveCount(0);
    // 表（のスクロール領域）は一覧の区画の全幅。
    const [listBox, gridBox] = await Promise.all([
      list.boundingBox(),
      page.getByTestId("security-users-scroll-region").boundingBox(),
    ]);
    expect(Math.abs(listBox!.width - gridBox!.width)).toBeLessThanOrEqual(1);
    // 行の名前は表示名、2 行目はログイン ID（ID の書体）。名前は ?user= のリンク（新しいタブで開ける）。
    await expect(userLink(page, "03")).toHaveAttribute("href", "?user=list-user-03");
    await expect(userLink(page, "01").locator("code")).toHaveText("list.user.01");
    await expect(userLink(page, "01").locator("code")).toHaveClass(/font-mono/u);
    // 1 ページ目は 25 件で、ページ送りがある（前へ / 次へで対象を移る詳細の pager は無い）。
    await expect(grid.locator("tbody tr")).toHaveCount(25);
    await expect(page.getByTestId("security-users-pagination")).toBeVisible();
    await expect(page.getByTestId("security-users-detail-pager-next")).toHaveCount(0);
    expect(await findClippedPlaceholders(page)).toEqual([]);
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);

    await userLink(page, "03").click();
    await expect(page).toHaveURL(/\?user=list-user-03$/u);
    // エディタの見出し（h1）は対象の表示名で、状態のバッジを横に出す。一覧は出さない。
    await expect(page.getByRole("heading", { level: 1, name: "一覧ユーザー 03", exact: true })).toBeVisible();
    await expect(page.getByTestId("security-users-editor")).toBeVisible();
    await expect(page.getByTestId("security-users-sign-in")).toBeVisible();
    await expect(grid).toHaveCount(0);
    await expect(page.getByTestId("security-users-back")).toHaveText("一覧へ戻る");
    // 無効なユーザーは同じエディタを読み取り専用で出す（入力は disabled、確定の行は出さない）。
    await expect(page.getByRole("textbox", { name: "表示名" })).toBeDisabled();
    await expect(page.getByTestId("security-users-submit")).toHaveCount(0);
    // エディタは本文の全幅（狭い一覧の列と並べない）。
    const [mainBox, editorBox] = await Promise.all([
      page.locator("#pr-main").boundingBox(),
      page.getByTestId("security-users-editor").boundingBox(),
    ]);
    expect(editorBox!.width).toBeGreaterThan(mainBox!.width * 0.75);
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);

    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "一覧ユーザー 03", exact: true })).toBeVisible();
    await expect(page.getByTestId("security-users-editor")).toBeVisible();
  });

  test("行の名前以外の領域を押してもエディタを開き、行のメニューは開かずに操作だけを出す", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "md 未満は名前の列だけを出すため、名前以外の列は desktop で確かめる");
    await mockSecurity(page);
    await page.goto("/settings/security/users");
    const row = page.getByTestId("security-users-row-list-user-05");
    // 行のメニューは実際の操作だけ（「編集」は無い）。メニューを押しても行は開かない。
    await page.getByTestId("security-users-row-actions-list-user-05-trigger").click();
    await expect(page.getByRole("menuitem", { name: "無効化" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "編集" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page).not.toHaveURL(/user=/u);

    // 割り当てロールの列（名前のリンクの外）を押す。
    await row.locator("td").first().click();
    await expect(page).toHaveURL(/\?user=list-user-05$/u);
    await expect(page.getByRole("heading", { level: 1, name: "一覧ユーザー 05", exact: true })).toBeVisible();
    await expect(page.getByTestId("security-users-object-actions")).toBeVisible();
    // 有効なユーザーは編集できる。
    await expect(page.getByRole("textbox", { name: "表示名" })).toBeEnabled();
    await expect(page.getByTestId("security-users-submit")).toHaveText("保存");

    // ブラウザの「戻る」でも一覧へ戻る（一覧から入るときは履歴に積む）。
    await page.goBack();
    await expect(page.getByTestId("security-users-grid")).toBeVisible();
    await expect(page).not.toHaveURL(/user=/u);
  });

  test("「一覧へ戻る」は行のあったページへ戻り、その行の名前へフォーカスを戻す", { tag: "@responsive" }, async ({ page }) => {
    await mockSecurity(page);
    // URL から 2 ページ目の行のエディタを直接開く。
    await page.goto("/settings/security/users?user=list-user-27");
    await expect(page.getByRole("heading", { level: 1, name: "一覧ユーザー 27", exact: true })).toBeVisible();
    await page.getByTestId("security-users-back").click();
    await expect(page).not.toHaveURL(/user=/u);
    await expect(page.getByTestId("security-users-grid")).toBeVisible();
    await expect(userLink(page, "27")).toBeVisible();
    await expect(userLink(page, "27")).toBeFocused();
    await expect(userLink(page, "01")).toHaveCount(0);

    // 1 ページ目へ移って開き直し、戻ると 1 ページ目のその行へフォーカスを戻す。
    await page.getByTestId("security-users-pagination").getByRole("button", { name: /前のページ|前へ/u }).click();
    await expect(userLink(page, "04")).toBeVisible();
    await userLink(page, "04").click();
    await expect(page).toHaveURL(/\?user=list-user-04$/u);
    await page.getByTestId("security-users-back").click();
    await expect(userLink(page, "04")).toBeFocused();
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  });

  test("URL の対象が見つからなければ「見つかりません」と一覧へ戻る導線を出し、ほかの対象へ置き換えない", { tag: "@responsive" }, async ({ page }) => {
    await mockSecurity(page);
    await page.goto("/settings/security/users?user=missing-user");
    const notFound = page.getByTestId("security-users-detail-not-found");
    await expect(notFound).toContainText("ユーザーが見つかりません");
    await expect(page).toHaveURL(/\?user=missing-user$/u);
    await expect(page.getByTestId("security-users-editor")).toHaveCount(0);
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    await notFound.getByRole("button", { name: "一覧へ戻る" }).click();
    await expect(page).not.toHaveURL(/user=/u);
    await expect(page.getByTestId("security-users-grid")).toBeVisible();

    await page.goto("/settings/security/roles?role=missing-role");
    await expect(page.getByTestId("security-roles-detail-not-found")).toContainText("ロールが見つかりません");
    await page.getByTestId("security-roles-back").click();
    await expect(page.getByTestId("security-roles-grid")).toBeVisible();
  });

  test("md 未満は名前の列だけを出し、ほかの列の値は名前の下の 1 行に畳む", { tag: "@responsive" }, async ({ page }, testInfo) => {
    const narrow = testInfo.project.name === "mobile-375";
    await mockSecurity(page);

    await page.goto("/settings/security/users");
    const usersGrid = page.getByTestId("security-users-grid");
    await expect(userLink(page, "01")).toBeVisible();
    const userSummary = page.getByTestId("security-users-row-summary").first();
    const rolesHeader = usersGrid.getByRole("columnheader", { name: "割り当てロール" });
    const statusHeader = usersGrid.getByRole("columnheader", { name: "状態" });
    if (narrow) {
      await expect(userSummary).toBeVisible();
      await expect(userSummary).toHaveText("一覧閲覧 · 有効");
      await expect(rolesHeader).toBeHidden();
      await expect(statusHeader).toBeHidden();
    } else {
      await expect(userSummary).toBeHidden();
      await expect(rolesHeader).toBeVisible();
      await expect(statusHeader).toBeVisible();
    }
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);

    await page.goto("/settings/security/roles");
    const rolesGrid = page.getByTestId("security-roles-grid");
    await expect(rolesGrid.getByRole("link", { name: "一覧閲覧 を表示" })).toBeVisible();
    const roleSummary = page.getByTestId("security-roles-row-summary").first();
    if (narrow) {
      await expect(roleSummary).toBeVisible();
      await expect(roleSummary).toHaveText("LIST_VIEWER · カスタム");
      await expect(rolesGrid.getByRole("columnheader", { name: "ロールコード" })).toBeHidden();
      await expect(rolesGrid.getByRole("columnheader", { name: "種類" })).toBeHidden();
    } else {
      await expect(roleSummary).toBeHidden();
      await expect(rolesGrid.getByRole("columnheader", { name: "ロールコード" })).toBeVisible();
      await expect(rolesGrid.getByRole("columnheader", { name: "種類" })).toBeVisible();
    }
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);

    await page.goto("/settings/security/permissions");
    const permissionsGrid = page.getByTestId("security-permissions-grid");
    await expect(permissionsGrid.getByRole("link", { name: "一覧閲覧 を表示" })).toBeVisible();
    const permissionSummary = page.getByTestId("security-permissions-row-summary").first();
    if (narrow) {
      await expect(permissionSummary).toBeVisible();
      await expect(permissionSummary).toHaveText("1 / 1 件 · 使用可能な業務プロファイル: 0 件");
      await expect(permissionsGrid.getByRole("columnheader", { name: "機能権限" })).toBeHidden();
    } else {
      await expect(permissionSummary).toBeHidden();
      await expect(permissionsGrid.getByRole("columnheader", { name: "機能権限" })).toBeVisible();
    }
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  });

  test("1920px でもエディタはタイトルと本文の左端がそろい、横にあふれない（ダーク）", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "1920px は desktop で確かめる");
    await page.setViewportSize({ width: 1920, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark" });
    await mockSecurity(page);
    await page.goto("/settings/security/roles?role=role-viewer");
    const title = page.getByRole("heading", { level: 1, name: "一覧閲覧" });
    await expect(title).toBeVisible();
    const [titleBox, editorBox] = await Promise.all([
      title.boundingBox(),
      page.getByTestId("security-roles-editor").boundingBox(),
    ]);
    expect(Math.abs(editorBox!.x - titleBox!.x)).toBeLessThanOrEqual(1);
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  });

  test("ロール管理のエディタから権限管理を開くと、同じロールの全幅のエディタになり、ロール管理へ戻れる", { tag: "@responsive" }, async ({ page }) => {
    await mockSecurity(page);
    await page.goto("/settings/security/roles?role=role-viewer");
    await page.getByTestId("security-roles-open-permissions").click();
    await expect(page).toHaveURL(/\/settings\/security\/permissions\?role=role-viewer$/u);
    await expect(page.getByTestId("security-permissions-editor")).toBeVisible();
    await expect(page.getByRole("heading", { name: "一覧閲覧", level: 1 })).toBeVisible();
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    await page.getByTestId("security-permissions-open-role").click();
    await expect(page).toHaveURL(/\/settings\/security\/roles\?role=role-viewer$/u);
    await expect(page.getByTestId("security-roles-editor")).toBeVisible();
  });
});


test("管理画面の読込表示は対象を短く示して一覧の幅に収まる", { tag: "@responsive" }, async ({ page }) => {
  await mockSecurity(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/security/**", async route => { await pending; await route.fallback(); });
  try {
    for (const [kind, label] of [["users", "ユーザーを読込中"], ["roles", "ロールを読込中"], ["permissions", "権限を読込中"]]) {
      await page.goto(`/settings/security/${kind}`);
      const loading = page.getByTestId(`security-${kind}-loading`);
      await expect(loading.locator("[data-processing-label]")).toHaveText(label);
      await expect(loading.locator("[data-processing-label]")).toBeVisible();
      await expect(loading).toContainText("経過時間");
      const size = await loading.locator("[data-processing-label]").evaluate(node => ({height: node.getBoundingClientRect().height, line: parseFloat(getComputedStyle(node).lineHeight)}));
      expect(size.height).toBeLessThanOrEqual(size.line * 2 + 1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    }
  } finally { release(); }
});
