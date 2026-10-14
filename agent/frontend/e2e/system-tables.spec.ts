import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";
import { openSidebarNav } from "./fixtures/nav";

// 運用設定 > システムテーブル（#751。RAG / NL2SQL と同じ共通のカード）。
// 旧版の DB（検索・回答プロファイルの割り当ての表が残る）は、削除される内容を確認ダイアログで承認してから更新する。

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

test(
  "保存済みのデータを書き換えた後に再起動が要るプロセスは、作成・更新の直下に残す（#1844）",
  { tag: "@responsive" },
  async ({ page, mockApi }) => {
    const dispatcher = "実行の dispatcher（python -m app.features.agent.runtime_dispatcher）";
    mockApi.state.systemTablesRestartRequired = {
      message: "保存済みのデータを書き換える migration を適用しました。次のプロセスは再起動してください。",
      processes: [dispatcher],
    };
    await page.goto("/settings/system-tables");
    const initialize = page.getByRole("button", { name: "作成・更新" });
    await initialize.click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除して更新" }).click();

    const notice = page.getByRole("status").filter({ has: page.getByTestId("system-tables-restart-required") });
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("Agent のシステムテーブルを更新しました。再起動が必要なプロセスがあります");
    await expect(notice.getByRole("listitem")).toHaveText([dispatcher]);
    // 結果は操作の直下の Banner 1 か所だけ（Toast と重ねない。messaging.md §10.1）。
    await expect(page.getByRole("region", { name: "通知" }).getByRole("status")).toHaveCount(0);
    const actionBox = await initialize.boundingBox();
    const noticeBox = await notice.boundingBox();
    expect(actionBox && noticeBox && noticeBox.y >= actionBox.y + actionBox.height).toBeTruthy();
    await expectNoPageOverflow(page);

    // 状態を取り直したら消す（次の操作まで残す。messaging.md §10.4）。
    await page.getByRole("button", { name: "最新情報を取得" }).click();
    await expect(notice).toHaveCount(0);
  },
);

test("サイドナビの運用設定は、システムテーブルから始まる（RAG / NL2SQL と同じ。#658）", async ({ page }) => {
  await page.goto("/settings/system-tables");
  // 375px（mobile-375 の project）ではナビはドロワーなので、開いてから見る（#823）。
  const nav = await openSidebarNav(page);
  const operations = nav.getByRole("link", { name: "システムテーブル" });
  await expect(operations).toBeVisible();
  await expect(operations).toHaveAttribute("aria-current", "page");
});
