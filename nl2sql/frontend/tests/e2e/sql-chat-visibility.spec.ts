import { expect, test, type Page } from "./_helpers/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { mockChatRequirement } from "./_helpers/chat-requirement";

/**
 * 「AI 活用」の下の機能のデータの見え方（#1889）。system_admin は全員の会話を見られ、履歴に利用者の
 * 表示名を出す。他の利用者の会話は見るだけ（続きの質問は本人だけ）。一般の利用者の履歴は本人の会話だけで、
 * 利用者を出さない（絞り込みは backend。ここでは画面の出し方を確かめる）。
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

type Conversation = {
  id: string;
  title: string;
  profile_id: string;
  created_at: string;
  owned: boolean;
  actor_display_name: string;
};

async function setup(page: Page, conversations: Conversation[]) {
  await mockDatabaseGateReady(page);
  await mockChatRequirement(page);
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({ json: { data: { items: [profile], total: 1, next_cursor: null } } }),
  );
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) =>
    route.fulfill({ json: { data: profile } }),
  );
  await page.route("**/api/nl2sql/chats**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/nl2sql/chats") {
      return route.fulfill({
        json: {
          data: { items: conversations, next_cursor: null, total: conversations.length, limit: 10 },
        },
      });
    }
    const id = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    const target = conversations.find((item) => item.id === id);
    if (!target) {
      return route.fulfill({ status: 404, json: { error_messages: ["会話が見つかりません。"] } });
    }
    return route.fulfill({ json: { data: { conversation: target, turns: [] } } });
  });
}

async function openHistory(page: Page) {
  await page.goto("/chat");
  const toggle = page.getByTestId("sql-chat-history-toggle");
  await expect(toggle).toBeEnabled();
  await toggle.click();
  return page.getByTestId("sql-chat-history");
}

test.describe("会話のデータの見え方（#1889）", { tag: "@responsive" }, () => {
  test("system_admin は全員の会話を利用者つきで見て、他の利用者の会話は見るだけ", async ({ page }) => {
    await setup(page, [
      {
        id: "chat-alice",
        title: "アリスの売上",
        profile_id: "sales",
        created_at: "2026-10-07T09:59:00Z",
        owned: false,
        actor_display_name: "アリス",
      },
      {
        id: "chat-gone",
        title: "削除済みの人の会話",
        profile_id: "sales",
        created_at: "2026-10-07T09:58:00Z",
        owned: false,
        actor_display_name: "",
      },
    ]);
    const history = await openHistory(page);
    await expect(history.getByText(/・アリス$/)).toBeVisible();
    await expect(history.getByText(/・（削除済みのユーザー）$/)).toBeVisible();

    await history.locator('[title="アリスの売上"]').click();
    await expect(page.getByText(/ほかの利用者の会話です。/)).toBeVisible();
    await page.getByRole("textbox", { name: "質問" }).fill("続きの質問");
    await expect(page.getByTestId("sql-chat-send")).toBeDisabled();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("一般の利用者の履歴は本人の会話だけで、利用者を出さない", async ({ page }) => {
    await setup(page, [
      {
        id: "chat-own",
        title: "自分の売上",
        profile_id: "sales",
        created_at: "2026-10-07T09:59:00Z",
        owned: true,
        actor_display_name: "",
      },
    ]);
    // setup の後に登録した auth/me が優先されるよう、一般の利用者を登録し直す。
    await page.route("**/api/auth/me", (route) =>
      route.fulfill({
        json: {
          data: {
            ...systemAdminMe,
            is_system_admin: false,
            role_codes: ["ANALYST"],
            permissions: ["menu.chat", "nl2sql.query.generate", "nl2sql.sql.execute"],
            allowed_profile_ids: ["sales"],
          },
        },
      }),
    );
    const history = await openHistory(page);
    await expect(history.locator('[title="自分の売上"]')).toBeVisible();
    await expect(history.getByText(/・/)).toHaveCount(0);
    await history.locator('[title="自分の売上"]').click();
    await expect(page.getByText(/ほかの利用者の会話です。/)).toHaveCount(0);
  });
});
