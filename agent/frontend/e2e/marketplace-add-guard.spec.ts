import type { Page } from "@playwright/test";

import { expect, test, type MockApi } from "./fixtures/mock-api";

// #928: マーケットプレイスの ID の重複・形の検証と、導入済みのプラグインの表示。

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

function seedMarketplace(mockApi: MockApi, pluginCount = 0) {
  mockApi.state.marketplaces.push({
    id: "fixture_market",
    name: "Fixture Market",
    url: "http://marketplace.example.test/marketplace",
    plugin_count: pluginCount,
    last_error: null,
    refresh_status: pluginCount ? "ready" : "not_fetched",
    revision: null,
  });
}

test("同じ ID のマーケットプレイスは追加せず、既存を変えない", async ({ page, mockApi }) => {
  seedMarketplace(mockApi);
  await page.goto("/plugins/marketplaces?id=new");
  await expect(page.getByText("英数字・_・-・. で入力します（追加後は変更できません）。")).toBeVisible();
  await page.locator("#mkt-id").fill("fixture_market");
  await page.locator("#mkt-name").fill("別の配布元");
  await page.locator("#mkt-url").fill("http://other.example.test/marketplace");
  await page.getByRole("button", { name: "作成", exact: true }).click();

  await expect(page.getByTestId("marketplace-add-error")).toContainText("同じ ID のマーケットプレイスがあります。");
  await expect(page).toHaveURL(/\?id=new$/);
  expect(mockApi.state.marketplaces).toHaveLength(1);
  expect(mockApi.state.marketplaces[0]).toMatchObject({ name: "Fixture Market" });
  await expectNoHorizontalOverflow(page);
});

test("ID に / や空白を入れると欄の下にエラーを出して送らない", async ({ page, mockApi }) => {
  await page.goto("/plugins/marketplaces?id=new");
  await page.locator("#mkt-id").fill("team/market 1");
  await page.getByRole("button", { name: "作成", exact: true }).click();

  const idField = page.locator("#mkt-id");
  await expect(idField).toBeFocused();
  await expect(idField).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByText("ID は英数字で始め、英数字・_・-・. の 100 文字以内にしてください。")).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/plugins/marketplaces")).toBeUndefined();
});

test("導入済みのプラグインは「導入済み」を出し、インストールの操作を出さない", async ({ page, mockApi }) => {
  seedMarketplace(mockApi, 1);
  await page.goto("/plugins/marketplaces?id=fixture_market");
  const row = page.getByRole("row", { name: /Fixture Plugin/ });
  await expect(row).toBeVisible();
  await expect(row.getByText("導入済み", { exact: true })).toHaveCount(0);

  await page.getByTestId("marketplace-plugin-row-actions-fixture_plugin").click();
  await page.getByRole("menuitem", { name: "インストール" }).click();
  await expect(page.getByText("プラグインをインストールしました")).toBeVisible();

  await expect(row.getByText("導入済み", { exact: true })).toBeVisible();
  await expect(page.getByTestId("marketplace-plugin-row-actions-fixture_plugin")).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
});
