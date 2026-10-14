import { expect, type Page, test } from "./fixtures/test";

import { mockLocalAuth, openChatHistory, selectSearchAnswerProfile } from "./_helpers";

/**
 * 「AI 活用」のチャットの会話の見え方（#1889）。system_admin は全員の会話を見られ、他の利用者の会話には
 * 履歴に利用者の表示名を添え、開くと見るだけ（続きの質問は本人だけ）。持ち主での絞り込み（一般の利用者は
 * 自分の会話だけ・他人は 404）は backend のテスト（tests/test_chat_visibility.py）で確かめる。
 */

const searchAnswerProfile = {
  id: "bv-1",
  name: "経理アシスタント",
  description: "経費の相談",
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

const conversations = [
  {
    id: "conv-own",
    search_answer_profile_id: "bv-1",
    title: "自分の質問",
    status: "ACTIVE",
    message_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:03Z",
    owned: true,
    actor_display_name: "ローカル利用者",
  },
  {
    id: "conv-alice",
    search_answer_profile_id: "bv-1",
    title: "アリスの質問",
    status: "ACTIVE",
    message_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:02Z",
    owned: false,
    actor_display_name: "アリス",
  },
  {
    id: "conv-gone",
    search_answer_profile_id: "bv-1",
    title: "削除済みの人の質問",
    status: "ACTIVE",
    message_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:01Z",
    owned: false,
    actor_display_name: "",
  },
];

async function setup(page: Page) {
  await mockLocalAuth(page);
  await page.route("**/api/chat/models", (route) => route.fulfill({ json: envelope([]) }));
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: envelope({ items: [searchAnswerProfile], total: 1, limit: 50, offset: 0, has_next: false }),
    })
  );
  await page.route("**/api/chat/conversations**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/chat/conversations") {
      await route.fulfill({
        json: envelope({
          items: conversations,
          total: conversations.length,
          limit: 10,
          offset: 0,
          has_next: false,
        }),
      });
      return;
    }
    const id = decodeURIComponent(path.split("/").at(-1) ?? "");
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) {
      await route.fulfill({
        status: 404,
        json: { data: null, error_messages: ["会話が見つかりません。"], warning_messages: [] },
      });
      return;
    }
    await route.fulfill({ json: envelope({ ...conversation, messages: [] }) });
  });
}

test.describe("チャットの会話の見え方（#1889）", { tag: "@responsive" }, () => {
  test("system_admin は他の利用者の会話を利用者つきで見て、続きの質問は送れない", async ({ page }) => {
    await setup(page);
    await page.goto("/chat");
    await selectSearchAnswerProfile(page, /経理アシスタント/);
    const history = await openChatHistory(page);
    // 自分の会話には利用者を添えず、他の利用者の会話にだけ添える（引けない利用者は削除済み）。
    await expect(history.getByText(/・アリス$/)).toBeVisible();
    await expect(history.getByText(/・（削除済みのユーザー）$/)).toBeVisible();
    await expect(history.getByText(/・ローカル利用者$/)).toHaveCount(0);

    await history.locator('[title="アリスの質問"]').click();
    await expect(page.getByText(/ほかの利用者の会話です。/)).toBeVisible();
    await page.getByRole("textbox", { name: "質問", exact: true }).fill("続きの質問");
    await expect(page.getByTestId("chat-run-stop")).toBeDisabled();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
