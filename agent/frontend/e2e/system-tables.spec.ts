import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";

// 運用設定 > システムテーブル（#751。RAG / NL2SQL と同じ共通のカード）。
// 旧版の DB（業務ビューの割り当ての表が残る）は、削除される内容を確認ダイアログで承認してから更新する。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
] as const;

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

for (const viewport of VIEWPORTS) {
  test.describe(`システムテーブル (${viewport.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("運用設定の先頭にあり、データを消す更新は確認ダイアログで承認してから実行する", async ({ page, mockApi }) => {
      await page.goto("/settings/system-tables");
      await expect(page.getByRole("heading", { name: "システムテーブル管理", level: 1 })).toBeVisible();
      await expect(page.getByText("一部不足").first()).toBeVisible();
      await expect(page.getByText("データを削除する更新があります")).toBeVisible();
      await expect(page.getByText("AGENT_ROLE_BUSINESS_VIEWS").first()).toBeVisible();
      await expectNoPageOverflow(page);

      await page.getByRole("button", { name: "作成・更新" }).click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog.getByText("データを削除する更新を実行しますか？")).toBeVisible();
      await dialog.getByRole("button", { name: "削除して更新" }).click();

      await expect(page.getByText("初期化済み").first()).toBeVisible();
      const request = mockApi.lastRequest("POST", "/api/settings/database/system-tables/initialize");
      expect(request?.body).toMatchObject({ recreate: false, allow_destructive: true });
      await expectNoPageOverflow(page);
    });
  });
}

test("サイドナビの運用設定は、システムテーブルから始まる（RAG / NL2SQL と同じ。#658）", async ({ page }) => {
  await page.goto("/settings/system-tables");
  const nav = page.getByRole("navigation").first();
  const operations = nav.getByRole("link", { name: "システムテーブル" });
  await expect(operations).toBeVisible();
  await expect(operations).toHaveAttribute("aria-current", "page");
});
