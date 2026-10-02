import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";
import { openSidebarNav } from "./fixtures/nav";

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
  test(`サイドナビはセキュリティ設定 → ユーザーとロール → 運用設定 → システム設定の順に並べる (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/settings/appearance");
    // 375px ではナビがドロワー（#367）。開いてから並びを読む。
    const sidebar = await openSidebarNav(page);
    // 認証の確認後にサイドナビを描く。最後のセクションが出るまで待ってから並びを読む。
    await expect(sidebar.locator("#nav-section-nav-section-settings")).toHaveCount(1);

    // 並びは NL2SQL / RAG と同じ「… → 改善・運用 → セキュリティ設定 → ユーザーとロール → 運用設定 → システム設定」
    // （#87 / #215 / #658 / #776）。
    const sectionIds = await sidebar
      .locator('[id^="nav-section-nav-section-"]')
      .evaluateAll((elements) => elements.map((element) => element.id));
    expect(sectionIds).toEqual([
      "nav-section-nav-section-controlPlane",
      "nav-section-nav-section-improve",
      "nav-section-nav-section-security",
      "nav-section-nav-section-userRoles",
      "nav-section-nav-section-operations",
      "nav-section-nav-section-settings",
    ]);

    // セキュリティ設定は権限管理、運用設定はシステムテーブル（先頭。#751）と Agent 固有の3項目
    // （MCP 接続・API キー・バックアップ。#757 / #762 / #778）、
    // ユーザーとロール・システム設定は3製品共通。
    // 改善・運用は品質評価（#776。アイコンは RAG / NL2SQL と同じ FlaskConical）。
    await expect(sidebar.locator('#nav-section-nav-section-improve a[href="/evaluation"]')).toHaveCount(1);
    const security = sidebar.locator("#nav-section-nav-section-security");
    const operations = sidebar.locator("#nav-section-nav-section-operations");
    const userRoles = sidebar.locator("#nav-section-nav-section-userRoles");
    const settings = sidebar.locator("#nav-section-nav-section-settings");
    await expect(security.getByRole("link")).toHaveCount(1);
    await expect(security.locator('a[href="/settings/security/permissions"]')).toHaveCount(1);
    await expect(operations.getByRole("link")).toHaveCount(4);
    // 運用設定の先頭はシステムテーブル（RAG / NL2SQL と同じ）。
    await expect(operations.getByRole("link").first()).toHaveAttribute("href", "/settings/system-tables");
    await expect(userRoles.getByRole("link")).toHaveCount(2);
    await expect(settings.getByRole("link")).toHaveCount(5);
    for (const href of [
      "/settings/system-tables",
      "/settings/mcp-connections",
      "/settings/api-keys",
      "/settings/runtime-snapshot",
    ]) {
      await expect(operations.locator(`a[href="${href}"]`)).toHaveCount(1);
    }
    for (const href of ["/settings/security/users", "/settings/security/roles"]) {
      await expect(userRoles.locator(`a[href="${href}"]`)).toHaveCount(1);
    }
    await expect(sidebar.getByText("セキュリティ設定", { exact: true })).toBeVisible();
    // 同じアイコンを 2 つの項目に使わない（機能ごとに違うアイコン。#658）。
    const iconSignatures = await sidebar.locator("nav a svg").evaluateAll((icons) =>
      icons.map((icon) => icon.innerHTML.replace(/\s+/g, " ").trim())
    );
    expect(new Set(iconSignatures).size).toBe(iconSignatures.length);
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
