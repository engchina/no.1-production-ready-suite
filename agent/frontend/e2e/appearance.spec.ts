import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";

// 外観（テーマ切替）は platform の共有パッケージの画面を使う（#95）。

async function isDark(page: Page) {
  return page.evaluate(() => document.documentElement.classList.contains("dark"));
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`外観でライト / ダーク / 自動を切り替え、再読込後も保持する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/settings/appearance");

    await expect(page.getByRole("heading", { name: "外観", level: 1 })).toBeVisible();
    const toggle = page.getByTestId("appearance-theme-toggle");
    await expect(toggle.getByRole("button", { name: "ライト" })).toHaveAttribute("aria-pressed", "true");
    expect(await isDark(page)).toBe(false);

    await toggle.getByRole("button", { name: "ダーク" }).click();
    await expect(toggle.getByRole("button", { name: "ダーク" })).toHaveAttribute("aria-pressed", "true");
    await expect.poll(() => isDark(page)).toBe(true);

    await page.reload();
    await expect(page.getByTestId("appearance-theme-toggle").getByRole("button", { name: "ダーク" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect.poll(() => isDark(page)).toBe(true);

    // 自動は OS 設定に追従する
    await page.getByTestId("appearance-theme-toggle").getByRole("button", { name: "自動（OS 設定）" }).click();
    await expect.poll(() => isDark(page)).toBe(false);
    await page.emulateMedia({ colorScheme: "dark" });
    await expect.poll(() => isDark(page)).toBe(true);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("サイドナビのシステム設定に外観がある", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/settings/appearance");
  await expect(page.getByRole("link", { name: "外観" })).toHaveAttribute("href", "/settings/appearance");
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`サイドナビは運用設定のあとに共通のシステム設定を並べる (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/settings/appearance");
    const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });

    // 「… → 運用設定 → システム設定」の順（#87）。
    const sectionIds = await sidebar
      .locator('[id^="nav-section-nav-section-"]')
      .evaluateAll((elements) => elements.map((element) => element.id));
    expect(sectionIds.slice(-2)).toEqual([
      "nav-section-nav-section-operations",
      "nav-section-nav-section-settings",
    ]);

    // 運用設定は Agent 固有の5項目、システム設定は3製品共通の5項目。
    const operations = sidebar.locator("#nav-section-nav-section-operations");
    const settings = sidebar.locator("#nav-section-nav-section-settings");
    await expect(operations.getByRole("link")).toHaveCount(5);
    await expect(settings.getByRole("link")).toHaveCount(5);
    for (const href of [
      "/settings/connection",
      "/settings/external-rag",
      "/settings/external-nl2sql",
      "/settings/external-mcp",
      "/settings/runtime-snapshot",
    ]) {
      await expect(operations.locator(`a[href="${href}"]`)).toHaveCount(1);
    }
    for (const href of [
      "/settings/oci",
      "/settings/upload-storage",
      "/settings/model",
      "/settings/database",
      "/settings/appearance",
    ]) {
      await expect(settings.locator(`a[href="${href}"]`)).toHaveCount(1);
    }
  });
}
