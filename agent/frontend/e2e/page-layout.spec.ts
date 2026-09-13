import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";

import { WIDE_PAGE_ROUTES } from "../src/lib/page-layout";
import { APP_ROUTES } from "../src/lib/routes";

const CONTENT_MAX_WIDTH = 1440;

/** PageHeader の計測枠と、その直後の PageBody の位置・幅を返す。 */
async function measureLayout(page: Page) {
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  return page.evaluate(() => {
    const header = document.querySelector("main header");
    const headerMeasure = header?.firstElementChild;
    const body = header?.nextElementSibling;
    const main = document.querySelector("main");
    if (!headerMeasure || !body || !main) {
      throw new Error("PageHeader / PageBody が見つかりません");
    }
    const h = headerMeasure.getBoundingClientRect();
    const b = body.getBoundingClientRect();
    return {
      headerLeft: Math.round(h.left),
      headerWidth: Math.round(h.width),
      bodyLeft: Math.round(b.left),
      bodyWidth: Math.round(b.width),
      mainWidth: Math.round(main.getBoundingClientRect().width),
    };
  });
}

test.describe("画面幅（PageHeader / PageBody の wide）", () => {
  test("2,560px で作業画面だけ本文が広がり、全画面でタイトルと本文の左端がそろう", async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 2560, height: 1200 });
    const wideRoutes: readonly string[] = WIDE_PAGE_ROUTES;

    for (const route of Object.values(APP_ROUTES)) {
      await page.goto(route);
      const layout = await measureLayout(page);
      const label = `${route} ${JSON.stringify(layout)}`;

      expect(layout.headerLeft, label).toBe(layout.bodyLeft);
      expect(layout.headerWidth, label).toBe(layout.bodyWidth);
      if (wideRoutes.includes(route)) {
        expect(layout.bodyWidth, label).toBe(layout.mainWidth);
        expect(layout.bodyWidth, label).toBeGreaterThan(CONTENT_MAX_WIDTH);
      } else {
        expect(layout.bodyWidth, label).toBe(CONTENT_MAX_WIDTH);
      }
    }
  });

  test("1920px でも作業画面のタイトルと本文の左端がそろう", async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    for (const route of WIDE_PAGE_ROUTES) {
      await page.goto(route);
      const layout = await measureLayout(page);
      const label = `${route} ${JSON.stringify(layout)}`;
      expect(layout.headerLeft, label).toBe(layout.bodyLeft);
      expect(layout.bodyWidth, label).toBe(layout.mainWidth);
      expect(layout.bodyWidth, label).toBeGreaterThan(CONTENT_MAX_WIDTH);
    }
  });
});

test.describe("操作前の案内", () => {
  test("MCP 未設定の案内は警告にせず、取得ボタンが使えない理由として関連付ける", async ({ page }) => {
    await page.route("**/api/settings/external-mcp-servers", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: { servers: [], default_server_id: "default" }, error_messages: [], warning_messages: [] }),
      })
    );
    await page.goto("/settings/external-mcp");

    const hint = page.getByText("Base URL を設定すると MCP tool 一覧を取得できます。");
    await expect(hint).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "Base URL を設定すると" })).toHaveCount(0);

    const refresh = page.getByRole("button", { name: "取得" });
    await expect(refresh).toBeDisabled();
    await expect(refresh).toHaveAccessibleDescription("Base URL を設定すると MCP tool 一覧を取得できます。");
  });

  test("置換ボタンが使えない理由を常時表示し、入力後は関連付けを外す", async ({ page }) => {
    await page.goto("/settings/runtime-snapshot");

    const hint = "置換するには REPLACE と入力してください";
    const replace = page.getByRole("button", { name: "置換" });
    await expect(page.getByText(hint)).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(replace).toBeDisabled();
    await expect(replace).toHaveAccessibleDescription(hint);
    await expect(page.getByLabel("確認入力")).toHaveAccessibleDescription(hint);

    await page.getByLabel("確認入力").fill("REPLACE");
    await expect(replace).toBeEnabled();
    await expect(replace).toHaveAccessibleDescription("");
  });
});
