import { expect, test, type Page } from "@playwright/test";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
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

    await route.fulfill({
      json: { data: null, error_messages: [], warning_messages: [] },
    });
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 720 },
  { name: "mobile", width: 375, height: 667 },
]) {
  test(`settings sidebar icons are semantic on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockApi(page);
    await page.goto("/settings/oci");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    const ociSettings = sidebar.getByRole("link", { name: "OCI 認証設定" });
    const modelSettings = sidebar.getByRole("link", { name: "モデル設定" });

    await expect(ociSettings).toBeVisible();
    await expect(modelSettings).toBeVisible();
    await expect(ociSettings.locator("svg").first()).toHaveClass(/lucide-key-round/);
    await expect(modelSettings.locator("svg").first()).toHaveClass(/lucide-settings/);
  });

  test(`sidebar brand and short labels are stable on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockApi(page);
    await page.goto("/settings/oci");

    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
    const brand = sidebar.locator('[title="Production Ready RAG"]').first();
    const uploadLink = sidebar.getByRole("link", { name: "ドキュメントアップロード" });

    if (viewport.width <= 640) {
      await expect(brand).toHaveAttribute("aria-hidden", "true");
      await expect(brand.getByText("Production Ready", { exact: true })).toBeHidden();
      await expect(page.getByRole("button", { name: "サイドバーを展開" })).toBeVisible();
      await expect(uploadLink).toBeVisible();
      return;
    }

    await expect(brand).toBeVisible();
    await expect(brand.getByText("Production Ready", { exact: true })).toBeVisible();
    await expect(brand.getByText("RAG", { exact: true })).toBeVisible();
    await expect(uploadLink).toHaveAttribute("href", /\/upload$/);
    await expect(uploadLink.getByText("アップロード", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "サイドバーを折りたたむ" }).click();

    await expect(brand).toHaveAttribute("aria-hidden", "true");
    await expect(brand.getByText("Production Ready", { exact: true })).toBeHidden();
    await expect(page.getByRole("button", { name: "サイドバーを展開" })).toBeVisible();
    await expect(uploadLink).toBeVisible();
  });
}
