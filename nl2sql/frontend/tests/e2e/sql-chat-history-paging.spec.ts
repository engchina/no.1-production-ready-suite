import { expect, test, type Page } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

/**
 * チャットの会話の履歴のページング（3 製品共通。#1265）。API はカーソルと全件数を返し、画面は前へ戻るカーソルを
 * 積んで、件数「a-b / n 件」と「前へ / N / M ページ / 次へ」を一覧の下に出す。ページは作業状態に残す。
 * desktop と mobile-375 の 2 project で実行する。
 */

const profile = {
  id: "sales",
  name: "売上分析",
  description: "売上の集計",
  archived: false,
  allowed_tables: ["APP.SALES"],
  allowed_views: [],
  allowed_table_count: 1,
  allowed_view_count: 0,
  version: 1,
};
const conversations = Array.from({ length: 14 }, (_, index) => ({
  id: `chat-${index + 1}`,
  title: `会話 ${index + 1}`,
  profile_id: "sales",
  created_at: new Date(Date.UTC(2026, 9, 7, 9, 59 - index)).toISOString(),
}));

async function setup(page: Page) {
  const requests: string[] = [];
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({ json: { data: { items: [profile], total: 1, next_cursor: null } } }),
  );
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) =>
    route.fulfill({ json: { data: profile } }),
  );
  await page.route("**/api/nl2sql/chats**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== "/api/nl2sql/chats") return route.fallback();
    requests.push(url.search);
    const limit = Number(url.searchParams.get("limit"));
    const start = url.searchParams.get("cursor") === "page-2" ? 10 : 0;
    return route.fulfill({
      json: {
        data: {
          items: conversations.slice(start, start + limit),
          next_cursor: start + limit < conversations.length ? "page-2" : null,
          total: conversations.length,
          limit,
        },
      },
    });
  });
  return requests;
}

test("会話の履歴は 10 件ずつ前へ / 次へで送り、再読込でも同じページに戻る", async ({ page }, testInfo) => {
  const requests = await setup(page);
  await page.goto("/chat");
  await page.getByRole("button", { name: "会話の履歴", exact: true }).click();
  const history = page.getByTestId("sql-chat-history");
  const pager = page.getByTestId("sql-chat-history-pagination");
  await expect(history.getByRole("button", { name: /^会話 \d+/ })).toHaveCount(10);
  await expect(pager).toContainText("1-10 / 14 件");
  await expect(pager).toContainText("1 / 2 ページ");
  await expect(pager.getByRole("button", { name: "前へ" })).toBeDisabled();
  expect(requests[0]).toBe("?limit=10");

  await pager.getByRole("button", { name: "次へ" }).click();
  await expect(history.getByRole("button", { name: /^会話 \d+/ })).toHaveCount(4);
  await expect(history.locator('[title="会話 11"]')).toBeVisible();
  await expect(pager).toContainText("11-14 / 14 件");
  await expect(pager).toContainText("2 / 2 ページ");
  await expect(pager.getByRole("button", { name: "次へ" })).toBeDisabled();
  expect(requests.at(-1)).toBe("?limit=10&cursor=page-2");
  // 狭い画面でも一覧とページ送りが横にはみ出さない。
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath("history-page-2.png") });

  // ページは作業状態に残す（再読込で同じページ）。
  await page.reload();
  // lg 以上のインラインの履歴は開閉も作業状態に残る。lg 未満のシートは閉じて戻るので開き直す。
  const toggle = page.getByTestId("sql-chat-history-toggle");
  await expect(toggle).toBeEnabled();
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await expect(page.getByTestId("sql-chat-history-pagination")).toContainText("2 / 2 ページ");

  await page.getByTestId("sql-chat-history-pagination").getByRole("button", { name: "前へ" }).click();
  await expect(page.getByTestId("sql-chat-history-pagination")).toContainText("1 / 2 ページ");
  await expect(page.getByTestId("sql-chat-history").locator('[title="会話 1"]')).toBeVisible();
});
