import { expect, test, type Page } from "@playwright/test";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: authStatus });
      return;
    }
    await route.fulfill({ json: { data: null, error_messages: [], warning_messages: [] } });
  });
}

test.describe("サイドナビのセクション折りたたみ", () => {
  test("キーボード（Enter / Space）で開閉でき aria-expanded が反映される", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await mockApi(page);
    await page.goto("/dashboard");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    const ragItem = sidebar.getByText("RAG 検索", { exact: true });
    await expect(ragItem).toBeVisible();

    // 業務ビューセクション見出しへフォーカスして Enter で折りたたむ。
    const toggle = sidebar.getByRole("button", { name: "業務ビュー を折りたたむ" });
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(ragItem).toBeHidden();
    await expect(
      sidebar.getByRole("button", { name: "業務ビュー を展開" })
    ).toHaveAttribute("aria-expanded", "false");

    // Space で再展開。
    await sidebar.getByRole("button", { name: "業務ビュー を展開" }).focus();
    await page.keyboard.press(" ");
    await expect(ragItem).toBeVisible();
  });

  test("折りたたんだセクションの項目はアクセシビリティツリー / タブ順から除外される", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await mockApi(page);
    await page.goto("/dashboard");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    const evalLink = sidebar
      .getByRole("link", { name: "品質評価", exact: true })
      .and(sidebar.locator('a[href="/evaluation"]'));
    await expect(evalLink).toHaveCount(1);

    // 折りたたむと visibility:hidden + inert で a11y ツリーから外れ、role として見えなくなる。
    await sidebar.getByRole("button", { name: "業務ビュー を折りたたむ" }).click();
    await expect(evalLink).toHaveCount(0);

    // 展開で復帰する。
    await sidebar.getByRole("button", { name: "業務ビュー を展開" }).click();
    await expect(evalLink).toHaveCount(1);
  });

  test("狭幅（375px・icon-only）ではアコーディオン無効で全項目を表示する", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await mockApi(page);
    await page.goto("/dashboard");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    // icon-only 幅ではセクション開閉ボタンは出さず、全リンクを表示する。
    await expect(sidebar.getByRole("button", { name: "業務ビュー を折りたたむ" })).toHaveCount(0);
    await expect(sidebar.getByRole("link", { name: "検索方法" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "高度な検索" })).toBeVisible();
  });
});
