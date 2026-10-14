import { expect, type Page, test } from "./fixtures/test";

import { mockLocalAuth, openChatHistory, selectSearchAnswerProfile } from "./_helpers";

/**
 * 「AI 活用」のチャットの会話の見え方（#1889・#1928）。system_admin は全員の会話を見られ、他の利用者の
 * 会話には履歴に利用者の表示名を添え、開くと本人と同じに続きの質問を送れる。持ち主以外が送った質問には
 * 送った人を添える。持ち主での絞り込み（一般の利用者は自分の会話だけ・他人は 404）と、送った人の記録は
 * backend のテスト（tests/test_chat_visibility.py）で確かめる。
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

const message = (overrides: Record<string, unknown>) => ({
  conversation_id: "conv-alice",
  model: null,
  citations: [],
  guardrail_warnings: [],
  trace_id: null,
  status: "COMPLETE",
  reply_to_message_id: null,
  created_at: "2026-01-01T00:00:00Z",
  sent_by_owner: true,
  sender_display_name: "",
  ...overrides,
});

// アリスの会話: アリスの質問と、system_admin（ローカル利用者）が送った続きの質問。
const aliceMessages = [
  message({ message_id: "u1", role: "USER", content: "経費の上限は？" }),
  message({ message_id: "a1", role: "ASSISTANT", content: "10 万円です。", reply_to_message_id: "u1", trace_id: "t1" }),
  message({
    message_id: "u2",
    role: "USER",
    content: "交通費は？",
    sent_by_owner: false,
    sender_display_name: "ローカル利用者",
  }),
  message({ message_id: "a2", role: "ASSISTANT", content: "実費です。", reply_to_message_id: "u2", trace_id: "t2" }),
];

const followUp = message({
  message_id: "u3",
  role: "USER",
  content: "宿泊費は？",
  sent_by_owner: false,
  sender_display_name: "ローカル利用者",
});

const sseBody = [
  `event: start\ndata: ${JSON.stringify({
    conversation_id: "conv-alice",
    user_message: followUp,
    columns: [{ model_id: "m1", label: "MODEL 1" }],
  })}\n\n`,
  `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "1 泊 1 万円までです。" })}\n\n`,
  `event: metadata\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a3", trace_id: "t3", elapsed_ms: 5, guardrail_warnings: [] })}\n\n`,
  `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a3" })}\n\n`,
  `event: all_done\ndata: ${JSON.stringify({ conversation_id: "conv-alice" })}\n\n`,
].join("");

async function setup(page: Page) {
  const state = { sent: [] as string[] };
  await mockLocalAuth(page);
  await page.route("**/api/feedback/current**", (route) => route.fulfill({ json: envelope([]) }));
  await page.route("**/api/chat/models", (route) => route.fulfill({ json: envelope([]) }));
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: envelope({ items: [searchAnswerProfile], total: 1, limit: 50, offset: 0, has_next: false }),
    })
  );
  await page.route("**/api/chat/conversations**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/messages/stream")) {
      state.sent.push(path);
      await route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: sseBody });
      return;
    }
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
    const answered = [
      followUp,
      message({ message_id: "a3", role: "ASSISTANT", content: "1 泊 1 万円までです。", reply_to_message_id: "u3", trace_id: "t3" }),
    ];
    const messages =
      id === "conv-alice" ? [...aliceMessages, ...(state.sent.length > 0 ? answered : [])] : [];
    await route.fulfill({ json: envelope({ ...conversation, messages }) });
  });
  return state;
}

test.describe("チャットの会話の見え方（#1889・#1928）", { tag: "@responsive" }, () => {
  test("system_admin は他の利用者の会話を利用者つきで見て、続きの質問を送れる", async ({ page }) => {
    const state = await setup(page);
    await page.goto("/chat");
    await selectSearchAnswerProfile(page, /経理アシスタント/);
    const history = await openChatHistory(page);
    // 自分の会話には利用者を添えず、他の利用者の会話にだけ添える（引けない利用者は削除済み）。
    await expect(history.getByText(/・アリス$/)).toBeVisible();
    await expect(history.getByText(/・（削除済みのユーザー）$/)).toBeVisible();
    await expect(history.getByText(/・ローカル利用者$/)).toHaveCount(0);

    await history.locator('[title="アリスの質問"]').click();
    // 見るだけの案内は出さない。持ち主以外が送った質問にだけ送った人を添える。
    await expect(page.getByText(/ほかの利用者の会話です。/)).toHaveCount(0);
    await expect(page.getByText("交通費は？")).toBeVisible();
    const senders = page.locator("[data-chat-sender]");
    await expect(senders).toHaveCount(1);
    await expect(senders).toHaveText("ローカル利用者 が送信");

    const input = page.getByRole("textbox", { name: "質問", exact: true });
    await expect(input).toBeEditable();
    await input.fill("宿泊費は？");
    await input.press("Enter");
    await expect(page.getByText("1 泊 1 万円までです。")).toBeVisible();
    expect(state.sent).toEqual(["/api/chat/conversations/conv-alice/messages/stream"]);
    await expect(senders).toHaveCount(2);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
