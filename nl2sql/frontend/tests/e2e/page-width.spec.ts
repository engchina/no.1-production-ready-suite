import { expect, type Page, test } from "@playwright/test";

import { APP_ROUTES } from "../../src/lib/routes";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

// NL2SQL は全画面が画面幅いっぱい（#566）。タイトルと本文の左端がそろう。
// 本文の幅が変わるのは main が 1440px + ガターより広いときだけなので、広い画面で計測する。
// ログイン・パスワード変更・権限なし（AppShell 外）と、別ルートへ転送するだけの旧 URL は対象外。
const NON_PAGE_ROUTES = new Set<string>([
  APP_ROUTES.login,
  APP_ROUTES.passwordChange,
  APP_ROUTES.forbidden,
  APP_ROUTES.home,
  APP_ROUTES.learning,
  APP_ROUTES.questionLearning,
  APP_ROUTES.legacyNl2sqlModelLearning,
]);
const PAGE_ROUTES = Object.values(APP_ROUTES).filter((route) => !NON_PAGE_ROUTES.has(route));

async function measure(page: Page) {
  return page.evaluate(() => {
    const header = document.querySelector("main header")!;
    const content = (element: Element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return {
        left: Math.round(rect.left + parseFloat(style.paddingLeft)),
        right: Math.round(rect.right - parseFloat(style.paddingRight)),
        width: Math.round(rect.width),
      };
    };
    // PageBody は常に `[&>*]:min-w-0` を持つ（計測コンテナの目印）。
    const bodies = Array.from(document.querySelectorAll("main *")).filter(
      (element) => !header.contains(element) && element.classList.contains("[&>*]:min-w-0")
    );
    return { header: content(header.firstElementChild!), bodies: bodies.map(content) };
  });
}

async function openPage(page: Page, route: string) {
  await page.goto(route);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
}

test.describe("本文幅", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/**", (route) =>
      route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ data: null, error_messages: ["e2e unmocked"], warning_messages: [] }) })
    );
    await mockDatabaseGateReady(page);
  });

  for (const width of [1920, 2560]) {
    test(`${width}px で全画面が全幅になり、タイトルと本文の左端・右端がそろう`, async ({ page, isMobile }) => {
      test.skip(isMobile, "広い画面の計測");
      test.setTimeout(180_000);
      await page.setViewportSize({ width, height: 1000 });
      for (const route of PAGE_ROUTES) {
        await openPage(page, route);
        const metrics = await measure(page);
        expect(metrics.bodies.length, `${route} に PageBody がある`).toBeGreaterThan(0);
        expect(metrics.header.width, `${route} のヘッダーは全幅`).toBeGreaterThan(1440);
        for (const body of metrics.bodies) {
          expect(body.left, `${route} のタイトルと本文の左端`).toBe(metrics.header.left);
          expect(body.right, `${route} のタイトルと本文の右端`).toBe(metrics.header.right);
          expect(body.width, `${route} は全幅`).toBeGreaterThan(1440);
        }
      }
    });
  }

  test("375px でも全画面のタイトルと本文の左端がそろう", async ({ page, isMobile }) => {
    test.skip(!isMobile, "モバイル幅の計測");
    test.setTimeout(180_000);
    for (const route of PAGE_ROUTES) {
      await openPage(page, route);
      const metrics = await measure(page);
      expect(metrics.bodies.length, `${route} に PageBody がある`).toBeGreaterThan(0);
      for (const body of metrics.bodies) expect(body.left, `${route} のタイトルと本文の左端`).toBe(metrics.header.left);
    }
  });
});

test("AI要件確認の未入力案内は補助テキストで、無効なボタンに関連付ける", async ({ page }) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ data: null, error_messages: ["e2e unmocked"], warning_messages: [] }) })
  );
  await mockDatabaseGateReady(page);
  await page.goto(APP_ROUTES.query);
  const hint = page.locator("#nl2sql-guided-query-required");
  await expect(hint).toBeVisible();
  await expect(hint).not.toHaveAttribute("role", "alert");
  await expect(page.getByRole("alert")).toHaveCount(0);
  const button = page.getByRole("button", { name: "AI要件確認" });
  await expect(button).toBeDisabled();
  await expect(button).toHaveAccessibleDescription("AI要件確認を始めるにはクエリを入力してください。");
  const colors = await hint.evaluate((node) => {
    const probe = document.createElement("span");
    probe.style.color = "var(--color-fg-muted)";
    document.body.append(probe);
    const muted = getComputedStyle(probe).color;
    probe.remove();
    return { hint: getComputedStyle(node).color, muted };
  });
  expect(colors.hint).toBe(colors.muted);
});
