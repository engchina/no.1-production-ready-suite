import { expect, test, type Route } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { openSidebarNav } from "./_helpers/sidebar-nav";

async function fulfill(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data, error_messages: [], warning_messages: [] }),
  });
}

test("本文の描画の例外はサイドナビを残して本文だけをエラーの表示に替え、別の画面へ移れる（#4800）", async ({ page }) => {
  await mockDatabaseGateReady(page);
  // 実行計画の応答が段（steps）を持たないと、DeepSec の画面の描画が例外になる（壊れた応答の再現）。
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, { version: "V001" }));
  await page.route("**/api/security/deepsec/status", (route) => fulfill(route, { configured: false }));
  await page.route("**/api/security/deepsec/data-entitlements", (route) => fulfill(route, []));
  await page.route("**/api/security/deepsec/target-objects?*", (route) =>
    fulfill(route, { runtime: "oracle", owner: "", items: [], total: null, counts_included: false })
  );

  await page.goto("/settings/security/deepsec");

  const fallback = page.locator("[data-render-error-boundary]");
  await expect(fallback.getByRole("alert")).toContainText("この項目を表示できません");
  await expect(fallback.getByRole("button", { name: "再試行" })).toBeVisible();
  // React Router の既定のエラー画面（英語の stack trace）に置き換わらず、本文の外のナビが残る。
  await expect(page.getByText("Unexpected Application Error")).toHaveCount(0);
  const sidebar = await openSidebarNav(page);

  // ナビで別の画面へ移ると（再読み込みなし）、エラーの状態を解いてその画面を出す。
  await sidebar.getByRole("link", { name: "権限管理" }).click();
  await expect(page).toHaveURL(/\/settings\/security\/permissions$/);
  await expect(page.locator("[data-render-error-boundary]")).toHaveCount(0);
  await expect(page.locator("main h1")).toBeVisible();
});
