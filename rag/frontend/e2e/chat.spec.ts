import { expect, type Page, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
    chat_enabled: true,
  },
  error_messages: [],
  warning_messages: [],
};

const businessView = {
  id: "bv-1",
  name: "経理アシスタント",
  description: "経費の相談",
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const citationChunk = {
  document_id: "d1",
  chunk_id: "ch1",
  text: "経費の上限は 10 万円です。",
  score: 0.91,
  rerank_score: 0.82,
  file_name: "経費規程.pdf",
  category_name: null,
  metadata: {},
};

const userMessage = {
  message_id: "u1",
  conversation_id: "conv-1",
  role: "USER",
  content: "経費の上限は？",
  model: null,
  citations: [],
  guardrail_warnings: [],
  trace_id: null,
  status: "COMPLETE",
  reply_to_message_id: null,
  created_at: "2026-01-01T00:00:00Z",
};

const assistantMessage = {
  message_id: "a1",
  conversation_id: "conv-1",
  role: "ASSISTANT",
  content: "経費の上限は 10 万円です。",
  model: "m1",
  citations: [citationChunk],
  guardrail_warnings: [],
  trace_id: "t1",
  status: "COMPLETE",
  reply_to_message_id: "u1",
  created_at: "2026-01-01T00:00:01Z",
};

const sseBody = [
  `event: start\ndata: ${JSON.stringify({
    conversation_id: "conv-1",
    user_message: userMessage,
    columns: [{ model_id: "m1", label: "MODEL 1" }],
  })}\n\n`,
  `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "経費の上限は 10 万円です。" })}\n\n`,
  `event: metadata\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1", trace_id: "t1", elapsed_ms: 5, guardrail_warnings: [] })}\n\n`,
  `event: citations\ndata: ${JSON.stringify({ model_id: "m1", citations: [citationChunk] })}\n\n`,
  `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1" })}\n\n`,
  `event: all_done\ndata: ${JSON.stringify({ conversation_id: "conv-1" })}\n\n`,
].join("");

function pageEnvelope<T>(items: T[]) {
  return {
    data: { items, total: items.length, limit: 50, offset: 0, has_next: false },
    error_messages: [],
    warning_messages: [],
  };
}

function conversationDetail(messages: object[]) {
  return {
    id: "conv-1",
    business_view_id: "bv-1",
    title: null,
    status: "ACTIVE",
    message_count: messages.length,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:02Z",
    messages,
  };
}

async function mockChat(page: Page): Promise<void> {
  let sent = false;

  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await page.route("**/api/business-views**", (route) =>
    route.fulfill({ json: pageEnvelope([businessView]) })
  );
  await page.route("**/api/chat/models", (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );

  await page.route("**/api/chat/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/messages/stream")) {
      sent = true;
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: sseBody,
      });
      return;
    }
    if (path === "/api/chat/conversations") {
      if (request.method() === "POST") {
        await route.fulfill({
          json: { data: conversationDetail([]), error_messages: [], warning_messages: [] },
        });
        return;
      }
      const summaries = sent
        ? [{ ...conversationDetail([userMessage, assistantMessage]), messages: undefined }]
        : [];
      await route.fulfill({ json: pageEnvelope(summaries) });
      return;
    }
    // GET /api/chat/conversations/{id}
    const messages = sent ? [userMessage, assistantMessage] : [];
    await route.fulfill({
      json: { data: conversationDetail(messages), error_messages: [], warning_messages: [] },
    });
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`チャットで会話を始めて根拠付き回答を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);

    await page.goto("/chat");
    await expect(page.getByRole("heading", { name: "チャット" })).toBeVisible();

    // 業務ビューを選ぶとチャットを始められる。
    await page.getByRole("combobox", { name: "業務ビュー" }).click();
    await page.getByRole("option", { name: "経理アシスタント" }).click();

    await page.getByRole("button", { name: "新しい会話" }).click();

    const composer = page.getByRole("textbox");
    await composer.fill("経費の上限は？");
    await page.getByRole("button", { name: "送信" }).click();

    // ストリーミング → 永続化で回答と根拠（引用）が表示される。
    await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
    await expect(page.getByText("根拠（引用）").first()).toBeVisible();

    await expectNoPageOverflow(page);
  });
}

test("業務ビュー未選択ではチャットを促す空状態を出す", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page);

  await page.goto("/chat");
  await expect(
    page.getByText("業務ビューを選択するとチャットを始められます。")
  ).toBeVisible();
});
