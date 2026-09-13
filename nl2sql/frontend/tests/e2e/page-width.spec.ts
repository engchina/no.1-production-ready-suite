import { expect, test } from "@playwright/test";

import { WIDE_PAGE_ROUTES } from "../../src/lib/page-layout";
import { APP_ROUTES } from "../../src/lib/routes";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

// 作業画面は画面幅いっぱい、読む・入力する画面は 1440px。どちらもタイトルと本文の左端がそろう。
// 本文の幅が変わるのは main が 1440px + ガターより広いときだけなので、広い画面で計測する。
const NARROW_ROUTES = [APP_ROUTES.profiles, APP_ROUTES.glossaryRules, APP_ROUTES.settingsModel, APP_ROUTES.securityUsers];

test.describe("本文幅", () => {
  test.skip(({ isMobile }) => isMobile, "広い画面の計測");

  for (const width of [1920, 2560]) {
    test(`${width}px で作業画面だけ全幅になり、タイトルと本文の左端がそろう`, async ({ page }) => {
      test.setTimeout(120_000);
      await page.route("**/api/**", (route) =>
        route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ data: null, error_messages: ["e2e unmocked"], warning_messages: [] }) })
      );
      await mockDatabaseGateReady(page);
      await page.setViewportSize({ width, height: 1000 });
      for (const route of [...WIDE_PAGE_ROUTES, ...NARROW_ROUTES]) {
        await page.goto(route);
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        const metrics = await page.evaluate(() => {
          const header = document.querySelector("main header")!;
          const content = (element: Element) => {
            const rect = element.getBoundingClientRect();
            return { left: Math.round(rect.left + parseFloat(getComputedStyle(element).paddingLeft)), width: Math.round(rect.width) };
          };
          const headerInner = content(header.firstElementChild!);
          // PageBody は常に `[&>*]:min-w-0` を持つ（計測コンテナの目印）。
          const body = Array.from(document.querySelectorAll("main *")).find(
            (element) => !header.contains(element) && element.classList.contains("[&>*]:min-w-0")
          );
          return { headerInner, body: body ? content(body) : null };
        });
        expect(metrics.body, `${route} に PageBody がある`).not.toBeNull();
        expect(metrics.body!.left, `${route} のタイトルと本文の左端`).toBe(metrics.headerInner.left);
        if (WIDE_PAGE_ROUTES.has(route)) {
          expect(metrics.body!.width, `${route} は全幅`).toBeGreaterThan(1440);
        } else {
          expect(metrics.body!.width, `${route} は 1440px`).toBe(1440);
        }
      }
    });
  }
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
