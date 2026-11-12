import type { Locator } from "@playwright/test";

import { findFieldLoadFailureViolations } from "../../../platform/packages/ui/e2e/field-load-failure";
import { expect, test } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";

// #906: 通信断（ブラウザの `TypeError: Failed to fetch`）のとき、英語の文をそのまま出さず、
// 利用者向けの日本語の文（何が起きたか + 次の操作）を出し、元の文は「詳細」にだけ出す。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

/** TanStack Query の既定の再試行（3 回・計 7 秒）を待つ。 */
const AFTER_RETRIES = { timeout: 20_000 };

/** 要約・次の操作は日本語で、英語の元の文は開いた「詳細」の中にだけある。 */
async function expectNetworkFailure(failure: Locator, request: string) {
  await expect(failure).toContainText("サーバーに接続できませんでした。");
  await expect(failure).toContainText("ネットワークの接続とサーバーの起動状態を確かめてから");
  const details = failure.locator("details");
  await expect(details).toHaveAttribute("open", "");
  await expect(details).toContainText(request);
  await expect(details).toContainText("TypeError");
  const outside = await failure.evaluate((element) => {
    const clone = element.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("details").forEach((node) => node.remove());
    return clone.textContent ?? "";
  });
  expect(outside).not.toMatch(/Failed to fetch|TypeError|NetworkError/u);
}

for (const viewport of VIEWPORTS) {
  test(`業務 Agent の一覧の取得が通信断で失敗したら、日本語の文と再試行と「詳細」を出す (${viewport.name})`, async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.route(
      (url) => url.pathname === "/api/agents",
      (route) => (route.request().method() === "GET" ? route.abort("failed") : route.fallback())
    );
    await page.goto("/agents");

    const failure = page.getByRole("alert").filter({ hasText: "サーバーに接続できませんでした。" }).first();
    await expect(failure).toBeVisible(AFTER_RETRIES);
    await expectNetworkFailure(failure, "GET /api/agents");
    await expect(failure.getByRole("button", { name: "再試行" })).toBeVisible();
    await expectNoPageHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`agents-network-${viewport.name}.png`), fullPage: true });
  });

  test(`テンプレートの取得が通信断で失敗したら、選ぶ欄の位置に欄ごとの文と再試行を出し、日本語の文と技術的な情報は「詳細」に入れる (${viewport.name})`, async ({ page }, testInfo) => {
    test.setTimeout(60_000);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    let fail = true;
    await page.route("**/api/agent-templates", (route) => (fail ? route.abort("failed") : route.fallback()));
    await page.goto("/agents?id=new");

    await expect(page.getByRole("heading", { name: "テンプレートから始める" })).toBeVisible();
    // 共有の部品（欄の直下の失敗。#4817）: 見出しは何を読めなかったか、原因と技術的な情報は畳んだ「詳細」。
    const failure = page.getByTestId("agent-templates-load-failed");
    await expect(failure.getByRole("alert")).toHaveText("テンプレートを読み込めませんでした。", AFTER_RETRIES);
    expect(await findFieldLoadFailureViolations(page, { expectCount: 1 })).toEqual([]);
    await expect(failure.getByText("サーバーに接続できませんでした。")).toBeHidden();
    await failure.getByRole("button", { name: "詳細" }).click();
    await expect(failure).toContainText("サーバーに接続できませんでした。");
    await expect(failure).toContainText("ネットワークの接続とサーバーの起動状態を確かめてから");
    await expect(failure).toContainText("GET /api/agent-templates");
    await expect(failure).toContainText("TypeError");
    await expectNoPageHorizontalOverflow(page);
    await failure.screenshot({ path: testInfo.outputPath(`agent-templates-network-${viewport.name}.png`) });

    fail = false;
    await failure.getByRole("button", { name: "再試行" }).click();
    await expect(failure).toHaveCount(0);
    await expect(page.getByTestId("agent-templates")).toBeVisible();
  });
}
