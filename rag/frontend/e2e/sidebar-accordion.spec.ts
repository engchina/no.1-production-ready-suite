import { expect, test, type Page } from "./fixtures/test";
import { LOCAL_AUTH_ME, openSidebarNav } from "./_helpers";

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: LOCAL_AUTH_ME });
      return;
    }
    await route.fulfill({ json: { data: null, error_messages: [], warning_messages: [] } });
  });
}

test.describe("サイドナビのセクション折りたたみ", () => {
  test("キーボード（Enter / Space）で開閉でき aria-expanded が反映される", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await mockApi(page);
    await page.goto("/settings/appearance");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    const ragItem = sidebar.getByText("RAG 検索", { exact: true });
    await expect(ragItem).toBeVisible();

    // AI 活用セクション見出しへフォーカスして Enter で折りたたむ。
    const toggle = sidebar.getByRole("button", { name: "AI 活用 を折りたたむ" });
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(ragItem).toBeHidden();
    await expect(
      sidebar.getByRole("button", { name: "AI 活用 を展開" })
    ).toHaveAttribute("aria-expanded", "false");

    // Space で再展開。
    await sidebar.getByRole("button", { name: "AI 活用 を展開" }).focus();
    await page.keyboard.press(" ");
    await expect(ragItem).toBeVisible();
  });

  test("折りたたんだセクションの項目はアクセシビリティツリー / タブ順から除外される", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await mockApi(page);
    await page.goto("/settings/appearance");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    const evalLink = sidebar
      .getByRole("link", { name: "品質評価", exact: true })
      .and(sidebar.locator('a[href="/evaluation"]'));
    await expect(evalLink).toHaveCount(1);

    // 折りたたむと visibility:hidden + inert で a11y ツリーから外れ、role として見えなくなる。
    // 品質評価は「改善・運用」セクションにある（#409）。
    await sidebar.getByRole("button", { name: "改善・運用 を折りたたむ" }).click();
    await expect(evalLink).toHaveCount(0);

    // 展開で復帰する。
    await sidebar.getByRole("button", { name: "改善・運用 を展開" }).click();
    await expect(evalLink).toHaveCount(1);
  });

  test("狭幅（375px）のドロワーの中でもセクションを開閉できる（#367）", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await mockApi(page);
    await page.goto("/settings/appearance");

    // md 未満はドロワーの中に展開した幅で描くため、desktop と同じくセクションの開閉ボタンを出す。
    const sidebar = await openSidebarNav(page);
    const ragItem = sidebar.getByText("RAG 検索", { exact: true });
    await expect(ragItem).toBeVisible();
    await sidebar.getByRole("button", { name: "AI 活用 を折りたたむ" }).click();
    await expect(ragItem).toBeHidden();
    await sidebar.getByRole("button", { name: "AI 活用 を展開" }).click();
    await expect(ragItem).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "検索方法" })).toBeVisible();
  });
});
