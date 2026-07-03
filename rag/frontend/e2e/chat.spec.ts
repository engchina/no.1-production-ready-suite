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

const comparisonReplies = [
  {
    ...assistantMessage,
    message_id: "a-model-1",
    model: "xai.grok-4.3",
    content: "経費の上限は 10 万円です。申請前に承認者を確認してください。",
    citations: [{ ...citationChunk, chunk_id: "ch-model-1" }],
  },
  {
    ...assistantMessage,
    message_id: "a-model-2",
    model: "google.gemini-2.5-pro",
    content: "規程上の上限額は 10 万円です。例外申請には追加承認が必要です。",
    citations: [{ ...citationChunk, chunk_id: "ch-model-2" }],
  },
  {
    ...assistantMessage,
    message_id: "a-model-3",
    model: "cohere.command-a",
    content: "通常の経費上限は 10 万円で、超過する場合は事前申請が必要です。",
    citations: [{ ...citationChunk, chunk_id: "ch-model-3" }],
  },
];

const sseStart = `event: start\ndata: ${JSON.stringify({
  conversation_id: "conv-1",
  user_message: userMessage,
  columns: [{ model_id: "m1", label: "MODEL 1" }],
})}\n\n`;

const sseBody = [
  sseStart,
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

function conversationDetail(messages: object[], title: string | null = null) {
  return {
    id: "conv-1",
    business_view_id: "bv-1",
    title,
    status: "ACTIVE",
    message_count: messages.length,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:02Z",
    messages,
  };
}

type ConversationListMode = "ready" | "loading" | "error";

async function mockChat(
  page: Page,
  conversationListMode: ConversationListMode = "ready",
  initialMessages: object[] = [],
  options: { initialTitle?: string | null; renameFails?: boolean; streamBody?: string } = {}
): Promise<() => void> {
  let sent = false;
  let created = initialMessages.length > 0;
  let conversationTitle =
    options.initialTitle ?? (initialMessages.length > 0 ? userMessage.content : null);
  let releaseConversationList: () => void = () => undefined;
  const conversationListReady =
    conversationListMode === "loading"
      ? new Promise<void>((resolve) => {
          releaseConversationList = resolve;
        })
      : Promise.resolve();

  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await page.route("**/api/business-views**", (route) =>
    route.fulfill({ json: pageEnvelope([businessView]) })
  );
  await page.route("**/api/chat/models", (route) =>
    route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } })
  );
  let feedbackItems: Record<string, unknown>[] = [];
  await page.route("**/api/feedback**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/feedback/current") {
      await route.fulfill({
        json: { data: feedbackItems, error_messages: [], warning_messages: [] },
      });
      return;
    }
    if (request.method() === "POST") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      feedbackItems = [
        {
          feedback_id: "feedback-1",
          created_at: "2026-07-01T00:00:00Z",
          ...payload,
        },
      ];
      await route.fulfill({
        json: {
          data: { feedback_id: "feedback-1", ...payload },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fallback();
  });

  await page.route("**/api/chat/conversations**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/messages/stream")) {
      sent = true;
      created = true;
      conversationTitle = userMessage.content;
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: options.streamBody ?? sseBody,
      });
      return;
    }
    if (path === "/api/chat/conversations") {
      if (request.method() === "POST") {
        created = true;
        await route.fulfill({
          json: {
            data: conversationDetail([], conversationTitle),
            error_messages: [],
            warning_messages: [],
          },
        });
        return;
      }
      if (conversationListMode === "error") {
        await route.fulfill({
          status: 500,
          json: { data: null, error_messages: ["test error"], warning_messages: [] },
        });
        return;
      }
      await conversationListReady;
      const messages = sent ? [userMessage, assistantMessage] : initialMessages;
      const summaries = created
        ? [{ ...conversationDetail(messages, conversationTitle), messages: undefined }]
        : [];
      await route.fulfill({ json: pageEnvelope(summaries) });
      return;
    }
    if (request.method() === "PATCH") {
      if (options.renameFails) {
        await route.fulfill({
          status: 500,
          json: {
            data: null,
            error_messages: [
              "会話名を変更できませんでした。入力内容を確認して再試行してください。",
            ],
            warning_messages: [],
          },
        });
        return;
      }
      conversationTitle = (request.postDataJSON() as { title: string }).title;
      await route.fulfill({
        json: {
          data: { ...conversationDetail([], conversationTitle), messages: undefined },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    // GET /api/chat/conversations/{id}
    const messages = sent ? [userMessage, assistantMessage] : initialMessages;
    await route.fulfill({
      json: {
        data: conversationDetail(messages, conversationTitle),
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  return releaseConversationList;
}

async function expectChatWorkspaceLayout(page: Page, mode: "desktop" | "mobile") {
  const main = page.getByRole("main", { name: "メイン領域" });
  const sessions = page.getByRole("complementary", { name: "会話" });
  const chat = page.getByRole("region", { name: "チャット" });
  const [mainBox, sessionsBox, chatBox] = await Promise.all([
    main.boundingBox(),
    sessions.boundingBox(),
    chat.boundingBox(),
  ]);

  if (!mainBox || !sessionsBox || !chatBox) throw new Error("チャットレイアウトを計測できません。");

  expect(sessionsBox.x - mainBox.x).toBeGreaterThanOrEqual(mode === "desktop" ? 24 : 12);
  expect(chatBox.x + chatBox.width).toBeLessThanOrEqual(mainBox.x + mainBox.width + 1);
  if (mode === "desktop") {
    expect(Math.abs(sessionsBox.y - chatBox.y)).toBeLessThanOrEqual(1);
    expect(chatBox.x).toBeGreaterThan(sessionsBox.x + sessionsBox.width);
  } else {
    expect(chatBox.y).toBeGreaterThan(sessionsBox.y + sessionsBox.height);
  }
}

async function openPersistedConversation(page: Page, width: number, messages: object[]) {
  await page.setViewportSize({ width, height: width <= 375 ? 812 : 1000 });
  await mockChat(page, "ready", messages);
  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理アシスタント" }).click();
  await page
    .getByRole("list", { name: "会話" })
    .getByRole("button")
    .filter({ hasText: "件・" })
    .click();
}

async function modelCardBox(page: Page, model: string) {
  const heading = page.getByRole("heading", { name: model, level: 3 });
  await heading.scrollIntoViewIfNeeded();
  const box = await heading.locator("..").boundingBox();
  if (!box) throw new Error(`${model} の回答カードを計測できません。`);
  return box;
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

    await expect(page.getByText("最初のメッセージを送信して会話を始めましょう。")).toBeVisible();
    await expectChatWorkspaceLayout(page, viewport.name as "desktop" | "mobile");

    const composer = page.getByRole("textbox");
    await composer.scrollIntoViewIfNeeded();
    await expect(composer).toBeVisible();
    await expect(page.getByRole("button", { name: "送信" })).toBeVisible();
    await composer.fill("経費の上限は？");
    await page.getByRole("button", { name: "送信" }).click();

    // ストリーミング → 永続化後も根拠は既定で閉じ、キーボードで展開できる。
    await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
    const sessions = page.getByRole("complementary", { name: "会話" });
    await expect(sessions.getByText("経費の上限は？", { exact: true })).toBeVisible();
    await expect(sessions.getByText(/^2件・\d{2}\/\d{2} \d{2}:\d{2}$/)).toBeVisible();
    const citationSummary = page
      .locator("summary")
      .filter({ hasText: "根拠（引用） 1 件" })
      .first();
    const citationDetails = citationSummary.locator("..");
    await expect(citationSummary).toBeVisible();
    await expect(citationDetails).not.toHaveAttribute("open", "");
    await expect(page.getByText("経費規程.pdf")).toBeHidden();

    await citationSummary.focus();
    await page.keyboard.press("Enter");
    await expect(citationDetails).toHaveAttribute("open", "");
    await expect(page.getByText("経費規程.pdf")).toBeVisible();
    await expect(page.getByRole("button", { name: "プレビュー" })).toBeVisible();

    await expectNoPageOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`チャットは安全チェック警告を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page, "ready", [
      { ...userMessage, guardrail_warnings: ["機微情報をマスクしました。"] },
      { ...assistantMessage, guardrail_warnings: ["根拠を確認してください。"] },
    ]);

    await page.goto("/chat");
    await page.getByRole("combobox", { name: "業務ビュー" }).click();
    await page.getByRole("option", { name: "経理アシスタント" }).click();
    await page.getByRole("button", { name: /^経費の上限は？ 2件/ }).click();

    await expect(page.getByText(/機微情報をマスクしました/)).toBeVisible();
    await expect(page.getByText(/根拠を確認してください/)).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("送信開始後に永続化された質問を重複表示しない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page, "ready", [], { streamBody: sseStart });

  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理アシスタント" }).click();
  await page.getByRole("button", { name: "新しい会話" }).click();

  const detailRefreshed = page.waitForResponse((response) => {
    const request = response.request();
    return (
      request.method() === "GET" &&
      new URL(response.url()).pathname === "/api/chat/conversations/conv-1"
    );
  });
  await page.getByRole("textbox").fill(userMessage.content);
  await page.getByRole("button", { name: "送信" }).click();
  await detailRefreshed;

  const chat = page.getByRole("region", { name: "チャット" });
  await expect(chat.getByText(userMessage.content, { exact: true })).toHaveCount(1);
});

const longJapaneseAnswer =
  "テストデータの根拠によると、申請前に担当者と承認者を確認し、必要な資料をそろえて期限までに提出してください。";

for (const viewport of [
  { name: "desktop", width: 2048 },
  { name: "mobile", width: 375 },
]) {
  test(`長い日本語回答がカード幅を使用する (${viewport.name})`, async ({ page }) => {
    await openPersistedConversation(page, viewport.width, [
      userMessage,
      { ...assistantMessage, content: longJapaneseAnswer },
    ]);

    const answer = page.getByText(longJapaneseAnswer, { exact: true });
    const [answerBox, cardBox] = await Promise.all([
      answer.boundingBox(),
      answer.locator("..").boundingBox(),
    ]);
    if (!answerBox || !cardBox) throw new Error("回答幅を計測できません。");
    expect(answerBox.width).toBeGreaterThan(cardBox.width - 32);
    if (viewport.name === "desktop") {
      const lineHeight = await answer.evaluate((element) =>
        Number.parseFloat(getComputedStyle(element).lineHeight)
      );
      expect(answerBox.height).toBeLessThan(lineHeight * 1.5);
    }
    await expectNoPageOverflow(page);
  });
}

test("2モデルは広い画面で空き列なく横並びになる", async ({ page }) => {
  await openPersistedConversation(page, 2048, [userMessage, ...comparisonReplies.slice(0, 2)]);

  const first = await modelCardBox(page, "xai.grok-4.3");
  const second = await modelCardBox(page, "google.gemini-2.5-pro");
  expect(Math.abs(first.y - second.y)).toBeLessThanOrEqual(1);
  expect(first.width).toBeGreaterThanOrEqual(560);
  expect(second.x).toBeGreaterThan(first.x + first.width);
  await expect(page.locator("details[open]")).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("チャット回答の低評価理由を保存し、選択状態を維持する", async ({ page }) => {
  let feedbackPayload: Record<string, unknown> | null = null;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/feedback") {
      feedbackPayload = request.postDataJSON() as Record<string, unknown>;
    }
  });
  await openPersistedConversation(page, 1280, [userMessage, assistantMessage]);

  const notHelpful = page.getByRole("button", { name: "この回答は役に立たなかった" });
  await notHelpful.click();
  await page.getByRole("button", { name: "内容が正しくない" }).click();
  await page.getByRole("button", { name: "フィードバックを保存" }).click();

  await expect.poll(() => feedbackPayload).toEqual({
    trace_id: "t1",
    business_view_id: "bv-1",
    target_type: "answer",
    source_surface: "chat",
    document_id: null,
    chunk_id: null,
    message_id: "a1",
    content_snapshot: null,
    rating: "not_helpful",
    reason: "incorrect",
    comment: null,
  });
  await expect(notHelpful).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("保存済み・変更できます")).toBeVisible();
});

test("3モデルはカード幅を維持して次の行へ折り返す", async ({ page }) => {
  await openPersistedConversation(page, 2048, [userMessage, ...comparisonReplies]);

  const first = await modelCardBox(page, "xai.grok-4.3");
  const second = await modelCardBox(page, "google.gemini-2.5-pro");
  const third = await modelCardBox(page, "cohere.command-a");
  expect(Math.abs(first.y - second.y)).toBeLessThanOrEqual(1);
  expect(third.y).toBeGreaterThan(first.y + first.height);
  expect(Math.min(first.width, second.width, third.width)).toBeGreaterThanOrEqual(560);
  expect(third.width).toBeGreaterThan(first.width * 1.8);
  await expectNoPageOverflow(page);
});

for (const viewport of [
  { name: "desktop", width: 1440 },
  { name: "mobile", width: 375 },
]) {
  test(`複数モデルは狭い領域で縦並びになる (${viewport.name})`, async ({ page }) => {
    await openPersistedConversation(page, viewport.width, [
      userMessage,
      ...comparisonReplies.slice(0, 2),
    ]);

    const first = await modelCardBox(page, "xai.grok-4.3");
    const second = await modelCardBox(page, "google.gemini-2.5-pro");
    expect(second.y).toBeGreaterThan(first.y + first.height);
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
  await expectNoPageOverflow(page);
});

test("会話一覧の読み込み中状態をカード内に表示する", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const releaseConversationList = await mockChat(page, "loading");

  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理アシスタント" }).click();

  await expect(page.getByRole("status", { name: "会話" })).toBeVisible();
  await expectChatWorkspaceLayout(page, "mobile");
  await expectNoPageOverflow(page);

  releaseConversationList();
});

test("会話一覧の読み込み失敗時に再試行可能なエラーを表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page, "error");

  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理アシスタント" }).click();

  const error = page.getByRole("alert").filter({ hasText: "会話一覧を読み込めませんでした。" });
  await expect(error).toBeVisible({ timeout: 10_000 });
  await expect(error.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoPageOverflow(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`会話名をキーボードで変更・取消できる (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockChat(page, "ready", [userMessage, assistantMessage]);
    await page.goto("/chat");
    await page.getByRole("combobox", { name: "業務ビュー" }).click();
    await page.getByRole("option", { name: "経理アシスタント" }).click();

    const sessions = page.getByRole("complementary", { name: "会話" });
    const rename = sessions.getByRole("button", { name: "「経費の上限は？」の名前を変更" });
    if (viewport.name === "desktop") await sessions.getByRole("listitem").hover();
    await rename.click();

    const input = sessions.getByRole("textbox", { name: "会話名" });
    await input.fill("経費精算ルール");
    await input.press("Enter");
    await expect(sessions.getByText("経費精算ルール", { exact: true })).toBeVisible();

    await sessions
      .getByRole("button", { name: "「経費精算ルール」の名前を変更" })
      .click();
    await input.fill("保存しない名前");
    await input.press("Escape");
    await expect(sessions.getByText("経費精算ルール", { exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("会話名変更の失敗を入力欄直下へ表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page, "ready", [userMessage, assistantMessage], { renameFails: true });
  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理アシスタント" }).click();

  const sessions = page.getByRole("complementary", { name: "会話" });
  await sessions.getByRole("listitem").hover();
  await sessions.getByRole("button", { name: "「経費の上限は？」の名前を変更" }).click();
  const input = sessions.getByRole("textbox", { name: "会話名" });
  await input.fill("変更後");
  await input.press("Enter");
  await expect(
    sessions.getByRole("alert").filter({ hasText: "会話名を変更できませんでした。" })
  ).toBeVisible();
  await expect(input).toBeFocused();
});

test("未送信の会話があれば新しい会話を増やさず再利用する", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  let createRequests = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/chat/conversations"
    ) {
      createRequests += 1;
    }
  });
  await mockChat(page);
  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理アシスタント" }).click();

  const newConversation = page.getByRole("button", { name: "新しい会話", exact: true });
  await newConversation.click();
  await expect(
    page
      .getByRole("list", { name: "会話" })
      .getByText("新しい会話", { exact: true })
  ).toBeVisible();
  await newConversation.click();
  await expect(page.getByRole("textbox", { name: "メッセージを入力…（Enter で送信 / Shift+Enter で改行）" })).toBeFocused();
  expect(createRequests).toBe(1);
  await expectNoPageOverflow(page);
});

test("長い日本語の会話名でも一覧が横へはみ出さない", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockChat(page, "ready", [userMessage, assistantMessage], {
    initialTitle: "経費精算と国内外出張に関する承認ルールおよび例外申請の確認".repeat(2),
  });
  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理アシスタント" }).click();
  await expectNoPageOverflow(page);
});
