import { expect, test, type Page } from "@playwright/test";
import { LOCAL_AUTH_ME } from "./_helpers";

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

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 667 },
]) {
  test(`RAG navigation does not expose SQL or Select AI product routes on ${viewport.name}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockApi(page);
    await page.goto("/settings/appearance");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    await expect(sidebar).toContainText("ナレッジ構築");
    await expect(sidebar).toContainText("検索・回答設定");
    await expect(sidebar.getByRole("link", { name: "RAG 検索" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: "業務ビュー (Business View)" })).toBeVisible();
    await expect(sidebar.getByRole("link", { name: /NL2SQL|SQL コンソール|Select AI/ })).toHaveCount(0);
  });
}
