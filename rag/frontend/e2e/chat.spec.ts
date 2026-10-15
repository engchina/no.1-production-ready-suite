import { expect, type Locator, type Page, test } from "./fixtures/test";

import {
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  openChatHistory,
  selectSearchAnswerProfile,
} from "./_helpers";

const searchAnswerProfile = {
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
  // 回答エンジンは回答に使った根拠かを記録する（#1208）。
  metadata: { evidence_model_used: true },
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
    search_answer_profile_id: "bv-1",
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
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({ json: pageEnvelope([searchAnswerProfile]) })
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
          json: { data: null, error_messages: ["会話一覧を読み込めませんでした。"], warning_messages: [] },
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
    if (request.method() === "DELETE") {
      created = false;
      sent = false;
      await route.fulfill({ json: { data: null, error_messages: [], warning_messages: [] } });
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

/** 会話の履歴は既定で閉じ、チャットが本文の幅いっぱいに出る（#664）。 */
async function expectChatWorkspaceLayout(page: Page, mode: "desktop" | "mobile") {
  const main = page.getByRole("main");
  const chat = page.getByRole("region", { name: "チャット" });
  await expect(page.getByTestId("chat-history-toggle")).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("chat-history")).toBeHidden();
  const [mainBox, chatBox] = await Promise.all([main.boundingBox(), chat.boundingBox()]);

  if (!mainBox || !chatBox) throw new Error("チャットレイアウトを計測できません。");

  const leftGutter = chatBox.x - mainBox.x;
  const rightGutter = mainBox.x + mainBox.width - (chatBox.x + chatBox.width);
  expect(leftGutter).toBeGreaterThanOrEqual(mode === "desktop" ? 24 : 12);
  expect(rightGutter).toBeGreaterThanOrEqual(0);
  // 履歴の分の幅を取らない（左右の余白の差はスクロールバーの幅まで）。
  expect(Math.abs(leftGutter - rightGutter)).toBeLessThanOrEqual(20);
}

async function openPersistedConversation(page: Page, width: number, messages: object[]) {
  await page.setViewportSize({ width, height: width <= 375 ? 812 : 1000 });
  await mockChat(page, "ready", messages);
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  const history = await openChatHistory(page);
  await history.getByRole("list", { name: "会話の履歴" }).getByRole("button").filter({ hasText: "件・" }).click();
}

/**
 * 回答モデルのカード（見出しの親）の位置と大きさを、同じスクロール位置で一度に測る。
 * カードごとに scrollIntoView してから測ると、測る間にメッセージ一覧のスクロールが動き、
 * 縦並び・横並びの比較が別々のスクロール位置の座標どうしになる（375px で縦並びを誤判定した。#833）。
 */
async function modelCardBoxes(page: Page, models: string[]) {
  const headings = models.map((model) => page.getByRole("heading", { name: model, level: 3 }));
  for (const heading of headings) await expect(heading).toBeAttached();
  await headings[0].scrollIntoViewIfNeeded();
  const cards = await Promise.all(headings.map((heading) => heading.locator("..").elementHandle()));
  return page.evaluate(
    (elements) =>
      elements.map((element) => {
        if (!element) throw new Error("回答カードを計測できません。");
        const { x, y, width, height } = element.getBoundingClientRect();
        return { x, y, width, height };
      }),
    cards
  );
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`チャットで会話を始めて根拠付き回答を表示する (${viewport.name})`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);

    await page.goto("/chat");
    await expect(page.getByRole("heading", { name: "チャット" })).toBeVisible();

    // 検索・回答プロファイルを選ぶとチャットを始められる。
    await selectSearchAnswerProfile(page, "経理アシスタント");

    await page.getByRole("button", { name: "新しい会話" }).click();

    await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
    await expectChatWorkspaceLayout(page, viewport.name as "desktop" | "mobile");

    const composer = page.getByRole("textbox");
    await composer.scrollIntoViewIfNeeded();
    await expect(composer).toBeVisible();
    await expect(page.getByRole("button", { name: "送信" })).toBeVisible();
    await composer.fill("経費の上限は？");
    await page.getByRole("button", { name: "送信" }).click();

    // ストリーミング → 永続化後も根拠は既定で閉じ、キーボードで展開できる。
    await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
    // 会話の名前はチャットの上端に出る。履歴を開くと一覧にもある（#664）。
    await expect(page.getByTestId("chat-conversation-title")).toHaveText("経費の上限は？");
    const sessions = await openChatHistory(page);
    await expect(sessions.getByText("経費の上限は？", { exact: true })).toBeVisible();
    await expect(sessions.getByText(/^2件・\d{2}\/\d{2} \d{2}:\d{2}$/)).toBeVisible();
    if (viewport.name === "mobile") {
      await sessions.getByRole("button", { name: "会話の履歴を閉じる" }).click();
      await expect(sessions).toBeHidden();
    }
    const citationSummary = page
      .locator("summary")
      .filter({ hasText: "AI に渡した 1 件 ／ 回答で引用 1 件" })
      .first();
    const citationDetails = citationSummary.locator("..");
    await expect(citationSummary).toBeVisible();
    await expect(citationDetails).not.toHaveAttribute("open", "");
    await expect(page.getByText("経費規程.pdf")).toBeHidden();

    await citationSummary.focus();
    await page.keyboard.press("Enter");
    await expect(citationDetails).toHaveAttribute("open", "");
    await expect(page.getByText("経費規程.pdf")).toBeVisible();
    // 回答で引用した箇所には「回答で引用」を出す（#1208）。
    await expect(citationDetails.getByText("回答で引用", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /の引用箇所を表示$/ })).toBeVisible();
    await expect(page.getByRole("meter", { name: /取得スコア/ })).toHaveCount(0);
    await expect(page.getByRole("meter", { name: "Rerank スコア: 0.820" })).toBeVisible();

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
    await selectSearchAnswerProfile(page, "経理アシスタント");
    const history = await openChatHistory(page);
    await history.getByRole("button", { name: /^経費の上限は？ 2件/ }).click();

    await expect(page.getByText(/機微情報をマスクしました/)).toBeVisible();
    await expect(page.getByText(/根拠を確認してください/)).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test(
    `AI に渡した件数と、回答で引用・参考の区別を出す (${viewport.name})`,
    { tag: "@responsive" },
    async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      // 回答で引用した箇所の使われ方（該当を確認 / 参考）と、使用していない箇所（#2040）。
      const citations = [
        { ...citationChunk, metadata: { evidence_model_used: true, evidence_answer_use: "confirmed" } },
        {
          ...citationChunk,
          chunk_id: "ch-reference",
          file_name: "旅費規程.pdf",
          metadata: { evidence_model_used: true, evidence_answer_use: "reference" },
        },
        {
          ...citationChunk,
          chunk_id: "ch-unused",
          file_name: "出張手当.pdf",
          metadata: { evidence_model_used: false },
        },
      ];
      await mockChat(page, "ready", [userMessage, { ...assistantMessage, citations }]);

      await page.goto("/chat");
      await selectSearchAnswerProfile(page, "経理アシスタント");
      const history = await openChatHistory(page);
      await history.getByRole("button", { name: /^経費の上限は？ 2件/ }).click();

      // 件数は括弧を使わず区切りで並べ、0 件の項目は出さない。
      const summary = page
        .locator("summary")
        .filter({ hasText: "AI に渡した 3 件 ／ 回答で引用 2 件 ／ うち該当確認済み 1 件 ／ 参考 1 件" })
        .first();
      await expect(summary).toBeVisible();
      await summary.click();
      const cards = summary.locator("..").getByRole("listitem");
      const card = (file: string) => cards.filter({ hasText: file }).first();
      // 該当を確認した箇所は「回答で引用」、参考の箇所は「参考」。色だけでなく文言とアイコンで区別する。
      await expect(card("経費規程.pdf").getByText("回答で引用", { exact: true })).toBeVisible();
      await expect(card("経費規程.pdf").getByText("参考", { exact: true })).toHaveCount(0);
      const reference = card("旅費規程.pdf").locator("[data-status-variant]").filter({ hasText: "参考" });
      await expect(reference).toBeVisible();
      await expect(reference.locator("svg")).toHaveCount(1);
      await expect(card("旅費規程.pdf").getByText("回答で引用", { exact: true })).toHaveCount(0);
      // 回答で引用していない箇所にはバッジを出さない。
      await expect(card("出張手当.pdf").locator("[data-status-variant]")).toHaveCount(0);
      await expectNoPageOverflow(page);
    }
  );
}

test("送信開始後に永続化された質問を重複表示しない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page, "ready", [], { streamBody: sseStart });

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
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
  // 会話名の見出し（最初の質問。#664）は数えず、メッセージだけを数える。
  await expect(
    chat.getByText(userMessage.content, { exact: true }).and(chat.locator(":not(h2)"))
  ).toHaveCount(1);
});

const longJapaneseAnswer =
  "テストデータの根拠によると、申請前に担当者と承認者を確認し、必要な資料をそろえて期限までに提出してください。";

for (const viewport of [
  { name: "desktop", width: 2048 },
  { name: "mobile", width: 375 },
]) {
  test(`長い日本語回答がカード幅を使用する (${viewport.name})`, { tag: "@responsive" }, async ({ page }) => {
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

  const [first, second] = await modelCardBoxes(page, ["xai.grok-4.3", "google.gemini-2.5-pro"]);
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
  await page.getByRole("button", { name: /ナレッジ不足/ }).click();
  await page.getByLabel("修正した回答", { exact: true }).fill("  窓口へ問い合わせてください。  ");
  await page.getByRole("button", { name: "フィードバックを保存" }).click();

  await expect.poll(() => feedbackPayload).toEqual({
    trace_id: "t1",
    search_answer_profile_id: "bv-1",
    target_type: "answer",
    source_surface: "chat",
    document_id: null,
    chunk_id: null,
    message_id: "a1",
    content_snapshot: null,
    rating: "not_helpful",
    reason: "missing_knowledge",
    comment: null,
    corrected_answer: "窓口へ問い合わせてください。",
  });
  await expect(notHelpful).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("保存済み・変更できます")).toBeVisible();
});

test("3モデルはカード幅を維持して次の行へ折り返す", async ({ page }) => {
  await openPersistedConversation(page, 2048, [userMessage, ...comparisonReplies]);

  const [first, second, third] = await modelCardBoxes(page, [
    "xai.grok-4.3",
    "google.gemini-2.5-pro",
    "cohere.command-a",
  ]);
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

    const [first, second] = await modelCardBoxes(page, ["xai.grok-4.3", "google.gemini-2.5-pro"]);
    expect(second.y).toBeGreaterThan(first.y + first.height);
    await expectNoPageOverflow(page);
  });
}

test("検索・回答プロファイル未選択ではチャットを促す空状態を出す", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page);

  await page.goto("/chat");
  await expect(
    page.getByText("検索・回答プロファイルを選択するとチャットを始められます。")
  ).toBeVisible();
  await expectNoPageOverflow(page);
});

test("会話一覧の読み込み中状態をカード内に表示する", { tag: "@responsive" }, async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const releaseConversationList = await mockChat(page, "loading");

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await expectChatWorkspaceLayout(page, "mobile");
  await openChatHistory(page);

  // 読み込み中は文言と経過時間（TimedLoadingState）と行の形の Skeleton を出す（#265）。
  const loading = page.getByTestId("chat-conversations-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toContainText("会話を読み込んでいます");
  await expect(loading.getByRole("timer")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "会話を読み込んでいます" })).toHaveCount(1);
  await expectNoPageOverflow(page);

  releaseConversationList();
});

test("会話一覧の読み込み失敗時に再試行可能なエラーを表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page, "error");

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await openChatHistory(page);

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
    await selectSearchAnswerProfile(page, "経理アシスタント");

    const sessions = await openChatHistory(page);
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

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`会話を確認ダイアログを通して削除できる (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockChat(page, "ready", [userMessage, assistantMessage]);
    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");

    const sessions = await openChatHistory(page);
    const remove = sessions.getByRole("button", { name: "「経費の上限は？」を削除" });
    if (viewport.name === "desktop") await sessions.getByRole("listitem").hover();

    // 取消では消えない。
    await remove.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("この操作は取り消せません");
    await dialog.getByRole("button", { name: "キャンセル" }).click();
    await expect(sessions.getByRole("listitem")).toHaveCount(1);

    if (viewport.name === "desktop") await sessions.getByRole("listitem").hover();
    await remove.click();
    await dialog.getByRole("button", { name: "削除" }).click();
    await expect(page.getByText("会話を削除しました。")).toBeVisible();
    await expect(sessions.getByText("まだ会話がありません。", { exact: false })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("会話名変更の失敗を入力欄直下へ表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page, "ready", [userMessage, assistantMessage], { renameFails: true });
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");

  const sessions = await openChatHistory(page);
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
  await selectSearchAnswerProfile(page, "経理アシスタント");

  const newConversation = page.getByRole("button", { name: "新しい会話", exact: true });
  await newConversation.click();
  const history = await openChatHistory(page);
  await expect(
    history.getByRole("list", { name: "会話の履歴" }).getByText("新しい会話", { exact: true })
  ).toBeVisible();
  await history.getByRole("button", { name: "会話の履歴を閉じる" }).click();
  await expect(history).toBeHidden();
  await newConversation.click();
  await expect(page.getByRole("textbox", { name: "質問", exact: true })).toBeFocused();
  expect(createRequests).toBe(1);
  await expectNoPageOverflow(page);
});

test("長い日本語の会話名でも一覧が横へはみ出さない", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockChat(page, "ready", [userMessage, assistantMessage], {
    initialTitle: "経費精算と国内外出張に関する承認ルールおよび例外申請の確認".repeat(2),
  });
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await openChatHistory(page);
  await expectNoPageOverflow(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(
    `回答に未反映の関連章節の各行から、その章節の原文を開ける (${viewport.name})`,
    { tag: "@responsive" },
    async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const label = "「点検要領.pdf」の「第3章 設備ごとの点検」（p.5〜7）";
      const line = `${label}は、読み込み量の上限に達したため未反映です。`;
      const text = [
        "点検は月に 1 回行います。",
        "",
        "資料により確認できた事項",
        "",
        "・点検は月に 1 回行います。",
        "根拠：点検要領.pdf p.2",
        "",
        "回答に未反映の関連章節",
        "",
        `・${line}`,
      ].join("\n");
      // 読めなかった章節は回答の診断の section_expansion.unread に、本文の行と同じ文で載る（#2040）。
      const answerDiagnostics = {
        section_expansion: {
          unread: [{ document_id: "doc-9", section_id: "s3", label, reason: "token_budget", page: 5, line }],
        },
        execution_steps: [],
        evidence_tree: [],
      };
      const streamBody = [
        sseStart,
        `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text })}\n\n`,
        `event: metadata\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1", trace_id: "t1", elapsed_ms: 5, guardrail_warnings: [], answer_diagnostics: answerDiagnostics })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1" })}\n\n`,
      ].join("");
      await mockChat(page, "ready", [], { streamBody });

      await page.goto("/chat");
      await selectSearchAnswerProfile(page, "経理アシスタント");
      await page.getByRole("button", { name: "新しい会話" }).click();
      await page.getByRole("textbox").fill(userMessage.content);
      await page.getByRole("button", { name: "送信" }).click();

      const answer = page.getByTestId("answer-text");
      await expect(answer.getByRole("heading", { name: "回答に未反映の関連章節" })).toBeVisible();
      await expect(answer.getByText(line)).toBeVisible();
      // 読み上げで何を開くか分かる名前（見える文言「原文を開く」を含む）。キーボードで開ける。
      const open = answer.getByRole("link", { name: `${label}の原文を開く` });
      await expect(open).toHaveText("原文を開く");
      await expect(open).toHaveAttribute("href", "/documents/doc-9?page=5");
      await open.focus();
      const popupPromise = page.waitForEvent("popup");
      await page.keyboard.press("Enter");
      const popup = await popupPromise;
      await expect.poll(() => new URL(popup.url()).pathname + new URL(popup.url()).search).toBe("/documents/doc-9?page=5");
      await popup.close();
      await expectNoPageOverflow(page);
    }
  );
}

// #2096: 回答の処理の経過は、回答の工程ごとの段階（answer_step。工程名・入れ子の深さ・LLM の回数）を 1 か所に
// 既定で開いて出す。名前は「処理の経過」1 つで、回答の本文 → 根拠の一覧 → 処理の経過 → 評価 の順。
for (const theme of ["light", "dark"] as const) {
  test(`保存した回答の処理の経過は工程の一覧を既定で開き、入れ子と計測値を出す (${theme})`, { tag: "@responsive" }, async ({
    page,
  }) => {
    const at = (seconds: number) => new Date(Date.parse("2026-01-01T00:00:01Z") + seconds * 1000).toISOString();
    let seq = 0;
    const step = (stepId: string, kind: string, start: number, end: number, params?: Record<string, string | number>) => ({
      schema_version: 1,
      seq: ++seq,
      target_id: "a1",
      attempt: 0,
      emitted_at: at(end),
      type: "step",
      step_id: stepId,
      kind,
      status: "done",
      started_at: at(start),
      finished_at: at(end),
      ...(params ? { params } : {}),
    });
    const progress = [
      step("s1", "history_rewrite", 0, 0.4),
      step("s2", "answer_step", 0.4, 1.2, { name: "質問の理解", depth: 0, llm_calls: 1 }),
      step("s3", "answer_step", 1.2, 9, { name: "回答生成フロー", depth: 0, llm_calls: 2 }),
      step("s4", "answer_step", 1.3, 1.34, { name: "文書検索", depth: 1, note: "該当なし" }),
      step("s5", "answer_step", 2, 9, { name: "回答文の生成と根拠確認（1回目）", depth: 1, llm_calls: 2 }),
      { schema_version: 1, seq: ++seq, target_id: "a1", attempt: 0, emitted_at: at(9), type: "terminal", status: "done" },
    ];
    await page.emulateMedia({ colorScheme: theme });
    await mockChat(page, "ready", [userMessage, { ...assistantMessage, progress }]);

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");
    const history = await openChatHistory(page);
    await history.getByRole("button", { name: /^経費の上限は？ 2件/ }).click();
    if (await history.isVisible().catch(() => false)) {
      // lg 未満の履歴はシート。会話を選ぶと閉じる。
      await expect(page.getByRole("dialog", { name: "会話の履歴" })).toHaveCount(0);
    }

    const summary = page.getByTestId("chat-answer-progress-summary");
    await expect(summary).toHaveText("処理の経過（5 ステップ・9.0 秒）");
    const process = page.getByRole("region", { name: "処理の経過" });
    await expect(process.locator("details").first()).toHaveAttribute("open", "");
    const nested = page.getByTestId("chat-answer-progress-step-s4");
    await expect(nested).toHaveAttribute("data-level", "1");
    await expect(nested).toContainText("文書の検索");
    await expect(nested).toContainText("該当なし · 0.1 秒未満");
    await expect(page.getByTestId("chat-answer-progress-step-s2")).toContainText("LLM 1 回 · 0.8 秒");
    await expect(page.getByTestId("chat-answer-progress-step-s1")).toContainText("会話を踏まえた質問の書き換え");
    // 状態は英語の生の値ではなくアイコンと読み上げの文言で示す。「実行記録」の表示は無い。
    await expect(process.getByText(/complete/)).toHaveCount(0);
    await expect(page.getByText(/実行記録/)).toHaveCount(0);
    // 回答の本文 → 根拠の一覧 → 処理の経過 → 評価 の順。
    const [answerBox, citationBox, processBox, feedbackBox] = await Promise.all([
      page.getByText(assistantMessage.content).first().boundingBox(),
      page.locator("summary").filter({ hasText: "AI に渡した 1 件" }).boundingBox(),
      summary.boundingBox(),
      page.getByRole("group", { name: "この回答は役に立ちましたか？" }).boundingBox(),
    ]);
    expect(answerBox!.y).toBeLessThan(citationBox!.y);
    expect(citationBox!.y).toBeLessThan(processBox!.y);
    expect(processBox!.y).toBeLessThan(feedbackBox!.y);
    // 字下げは位置でも分かる（入れ子の行の左端が親より右）。
    const [parentLeft, childLeft] = await Promise.all([
      page.getByTestId("chat-answer-progress-step-s3").locator("svg").first().boundingBox(),
      nested.locator("svg").first().boundingBox(),
    ]);
    expect(childLeft!.x).toBeGreaterThan(parentLeft!.x);
    await expectNoPageOverflow(page);
  });
}
test("回答の根拠はまとまりごとに役割・位置・抜粋で出し、件数の合計を AI に渡した件数にそろえる（#2097）", { tag: "@responsive" }, async ({ page }) => {
  // chunk の ID は「文書 ID:chunk set の hash:番号」の長い文字列。既定では画面に出さない。
  const longId = (n: number) => `doc-1:${"a1b2c3d4".repeat(8)}:${n}`;
  const evidenceChunk = (n: number, text: string, metadata: Record<string, unknown>) => ({
    document_id: "doc-1",
    chunk_id: longId(n),
    text,
    score: 0.5,
    rerank_score: 0.8,
    file_name: "経費規程.pdf",
    category_name: null,
    metadata,
  });
  const citations = [
    evidenceChunk(1, "1 回の申請の上限は 10 万円です。", {
      page_start: 2,
      section_title: "第 2 章 上限",
      evidence_role: "retrieved_anchor",
      evidence_model_used: true,
      evidence_answer_use: "confirmed",
    }),
    evidenceChunk(2, "上限を超える場合は事前に申請します。", {
      page_start: 2,
      evidence_role: "neighbor_context",
      evidence_model_used: false,
    }),
    evidenceChunk(3, "申請の画面で金額を入力します。", {
      page_start: 5,
      evidence_role: "operation_context",
      evidence_model_used: false,
    }),
  ];
  const answerDiagnostics = {
    confidence: "high",
    execution_steps: [],
    evidence_tree: [
      {
        parent_id: "doc-1:p1",
        source: "経費規程.pdf",
        page: 2,
        children: [
          { chunk_id: longId(1), role: "retrieved_anchor", is_model_used: true, answer_use: "confirmed" },
          { chunk_id: longId(2), role: "neighbor_context", is_model_used: false },
        ],
      },
      {
        parent_id: "doc-1:p2",
        source: "経費規程.pdf",
        page: 5,
        children: [{ chunk_id: longId(3), role: "operation_context", is_model_used: false }],
      },
    ],
  };
  const streamBody = [
    sseStart,
    `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "経費の上限は 10 万円です。" })}\n\n`,
    `event: metadata\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1", trace_id: "t1", elapsed_ms: 5, guardrail_warnings: [], answer_diagnostics: answerDiagnostics })}\n\n`,
    `event: citations\ndata: ${JSON.stringify({ model_id: "m1", citations })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1" })}\n\n`,
  ].join("");
  await mockChat(page, "ready", [], { streamBody });

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await page.getByRole("button", { name: "新しい会話" }).click();
  await page.getByRole("textbox").fill(userMessage.content);
  await page.getByRole("button", { name: "送信" }).click();

  // 件数の行は箇所の数とまとまりの数を 1 か所で出す（単位を「件」にそろえる）。
  const summary = page
    .locator("summary")
    .filter({ hasText: "AI に渡した 3 件（2 のまとまり） ／ 回答で引用 1 件 ／ うち該当確認済み 1 件" });
  await expect(summary).toBeVisible();
  await summary.click();
  const evidence = page.getByTestId("answer-citations");
  // 実行記録の側には根拠の構成を重ねて出さない。
  await expect(page.getByText(/^根拠の構成/)).toHaveCount(0);

  await evidence.getByRole("tab", { name: /まとまりごと/ }).click();
  const groups = evidence.getByTestId("evidence-group");
  await expect(groups).toHaveCount(2);
  const counts = await evidence.getByTestId("evidence-group-count").allTextContents();
  expect(counts).toEqual(["2 件", "1 件"]);
  // 役割は利用者の言葉（アイコンつき）。英語の値と chunk の ID は既定で出さない。
  await expect(evidence.getByTestId("evidence-role")).toHaveText(["検索で見つかった箇所", "前後の文脈", "そのほかの文脈"]);
  await expect(evidence.getByTestId("evidence-role").first().locator("svg")).toHaveCount(1);
  await expect(evidence).not.toContainText(/neighbor_context|operation_context|retrieved_anchor/);
  await expect(evidence.getByText(longId(1))).toBeHidden();
  const first = evidence.getByTestId("evidence-item").first();
  await expect(first).toContainText("p.2 ・ 第 2 章 上限");
  await expect(first).toContainText("1 回の申請の上限は 10 万円です。");
  await expect(first.getByText("回答で引用", { exact: true })).toBeVisible();
  // ID は「詳細」の中にコピーのボタンつきで置く。
  await first.locator("summary").filter({ hasText: "詳細" }).click();
  await expect(first.getByTestId("evidence-item-chunk-id")).toHaveText(longId(1));
  await expect(first.getByRole("button", { name: "ID をコピー" })).toBeVisible();
  // 箇所を押すと引用箇所のダイアログを開く。
  await first.getByTestId("evidence-item-open").click();
  const preview = page.getByRole("dialog", { name: /経費規程\.pdf/ });
  await expect(preview).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(preview).toBeHidden();
  // 一覧は一定の高さを超えたら中で縦にスクロールする（共有の bounded-scroll-area-lg）。
  await expect(evidence.getByTestId("evidence-groups")).toHaveCSS("overflow-y", "auto");
  await evidence.getByRole("tab", { name: /順に表示/ }).click();
  await expect(evidence.locator("ul.bounded-scroll-area-lg")).toHaveCSS("overflow-y", "auto");
  await expectNoPageOverflow(page);
});

test("回答フローの回答ではチャットにも根拠パネルと会話から補った質問を表示する", { tag: "@responsive" }, async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const answerDiagnostics = {
    confidence: "high",
    needs_human_review: false,
    insufficient_reason: "",
    reasoning_summary: "引用照合済みの説明 1 件、原文のみ提示 0 件、原文と一致せず除外 0 件。",
    external_data_required: true,
    external_data_items: ["申請者の役職"],
    // 回答の対応と業務ガイド（#1252）。
    outcome: "needs_environment_data",
    guide: { guide_id: "g1", title: "経費の上限を確かめる", revision: 3 },
    question_type: ["規則"],
    auto_field_filter: {
      conditions: [{ name: "金額", value_type: "number", op: "gte", value: "100000" }],
      relaxed: false,
    },
    original_question: "それの上限は？",
    rewritten_question: "経費精算の上限額は？",
    generated_queries: [],
    execution_steps: [{ name: "質問の理解", status: "complete", elapsed_seconds: 0.2, llm_calls: 0 }],
    evidence_tree: [],
    models: {
      llm: { model_id: "m1", label: "MODEL 1" },
      vision: { model_id: "vlm-1", label: "VISION 1" },
      embedding: "cohere.embed-v4.0",
      rerank: "cohere.rerank-v4.0-fast",
    },
  };
  const streamBody = [
    sseStart,
    `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "経費の上限は 10 万円です。\n\n資料により確認できた事項\n\n・1 回の申請の上限は 10 万円です。\n根拠：経費規程.pdf p.2" })}\n\n`,
    `event: metadata\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1", trace_id: "t1", elapsed_ms: 5, guardrail_warnings: [], answer_diagnostics: answerDiagnostics })}\n\n`,
    `event: citations\ndata: ${JSON.stringify({
      model_id: "m1",
      citations: [
        {
          document_id: "doc-1",
          chunk_id: "doc-1:c2",
          text: "1 回の申請の上限は 10 万円です。",
          score: 0.5,
          rerank_score: 0.9,
          file_name: "経費規程.pdf",
          category_name: null,
          metadata: { page_start: 2 },
        },
      ],
    })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1" })}\n\n`,
  ].join("");
  await mockChat(page, "ready", [], { streamBody });

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await page.getByRole("button", { name: "新しい会話" }).click();
  await page.getByRole("textbox").fill(userMessage.content);
  await page.getByRole("button", { name: "送信" }).click();

  // 回答の下は 回答の本文 → AI に渡した箇所 → 処理の経過 → 評価 の順（#1417 / #2096）。回答の記録の詳細は
  // 処理の経過の「詳細」に畳む（名前は「処理の経過」1 つ。旧「回答の実行記録」）。
  const process = page.getByRole("region", { name: "処理の経過" });
  await expect(process).toBeVisible();
  await expect(page.getByText(/実行記録/)).toHaveCount(0);
  await page.getByTestId("chat-answer-progress-details").click();
  const panel = process.getByRole("region", { name: "詳細" });
  await expect(panel).toBeVisible();
  const citationSummary = page.locator("summary").filter({ hasText: "AI に渡した 1 件" });
  const feedback = page.getByRole("group", { name: "この回答は役に立ちましたか？" });
  await expect(citationSummary).toBeVisible();
  await expect(feedback).toBeVisible();
  const [citationBox, panelBox, feedbackBox] = await Promise.all([
    citationSummary.boundingBox(),
    process.boundingBox(),
    feedback.boundingBox(),
  ]);
  expect(panelBox?.y ?? Number.NEGATIVE_INFINITY).toBeGreaterThan(citationBox?.y ?? Number.POSITIVE_INFINITY);
  expect(feedbackBox?.y ?? Number.NEGATIVE_INFINITY).toBeGreaterThan(panelBox?.y ?? Number.POSITIVE_INFINITY);
  await expect(panel.getByText("会話の流れから補った質問: 経費精算の上限額は？")).toBeVisible();
  await expect(panel.getByText("信頼度: high")).toBeVisible();
  await expect(panel.getByTestId("answer-outcome-badge")).toHaveText("現場のデータが必要");
  await expect(panel.getByTestId("answer-guide-badge")).toHaveText("業務ガイド: 経費の上限を確かめる（版 3）");
  // 1 列（既定のモデル）でも、どのモデルの回答か・使ったモデルを出す（#649）。
  await expect(page.getByRole("heading", { name: "回答モデル: MODEL 1" })).toBeVisible();
  const models = panel.getByTestId("answer-models");
  await expect(models).toContainText("使用したモデル");
  await expect(models).toContainText("MODEL 1");
  await expect(models).toContainText("VISION 1");
  await expect(models).toContainText("cohere.embed-v4.0");
  await expect(models).toContainText("cohere.rerank-v4.0-fast");
  // rag_poc の回答 viewer と同じ情報（判断理由・外部データの確認・問い合わせ型）と本文の構成（#651）。
  await expect(panel.getByText("問い合わせ型: 規則")).toBeVisible();
  // 質問から読み取った条件（#652）も記録として出す。
  await expect(panel.getByTestId("auto-field-filter")).toContainText("金額 ≥ 100000");
  await expect(panel.getByText(/^判断理由: 引用照合済みの説明 1 件/)).toBeVisible();
  // 資料では決まらない値の案内は「詳細」に畳まず、回答の本文の直下に出す（#2096）。
  const externalData = page.getByRole("status").filter({ hasText: "業務システムで確かめる値" });
  await expect(externalData).toContainText("申請者の役職");
  await expect(panel.getByRole("status").filter({ hasText: "業務システムで確かめる値" })).toHaveCount(0);
  const answerText = page.getByTestId("answer-text");
  await expect(answerText.getByRole("heading", { name: "資料により確認できた事項" })).toBeVisible();
  await expect(answerText.getByRole("listitem")).toContainText("1 回の申請の上限は 10 万円です。");
  await expect(answerText.getByText("根拠：経費規程.pdf p.2")).toBeVisible();
  // 根拠の行から、当たる引用の原文のプレビューを開ける（#657）。Esc で閉じるとフォーカスは根拠の行へ戻る。
  const citationButton = answerText.getByRole("button", { name: "根拠：経費規程.pdf p.2 の原文を開く" });
  await citationButton.focus();
  await page.keyboard.press("Enter");
  const preview = page.getByRole("dialog", { name: /経費規程\.pdf/ });
  await expect(preview).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(preview).toBeHidden();
  await expect(citationButton).toBeFocused();
});

test("IME の変換を確定する Enter では送信しない（#459）", async ({ page }) => {
  await mockChat(page);
  let calls = 0;
  await page.route("**/api/chat/conversations/*/messages/stream", () => {
    calls += 1;
    return new Promise<void>(() => undefined);
  });

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await page.getByRole("button", { name: "新しい会話" }).click();

  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("けいひのじょうげん");
  // 変換中の keydown（isComposing=true）は送信しない。
  await composer.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, bubbles: true });
  await expect(composer).toHaveValue("けいひのじょうげん");
  expect(calls).toBe(0);

  // 変換を確定した後の Enter は送信する。
  await composer.press("Enter");
  await expect.poll(() => calls).toBe(1);
});

test("送信と停止は同じボタンで、生成中の Enter では停止しない（#413）", { tag: "@responsive" }, async ({ page }) => {
  await mockChat(page);
  let calls = 0;
  let aborted = 0;
  page.on("requestfailed", (request) => {
    if (request.url().includes("/messages/stream")) aborted += 1;
  });
  // 応答を返さず、生成中のままにする（mockChat より後に登録したものが優先される）。
  await page.route("**/api/chat/conversations/*/messages/stream", () => {
    calls += 1;
    return new Promise<void>(() => undefined);
  });

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await page.getByRole("button", { name: "新しい会話" }).click();

  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  const button = page.getByTestId("chat-run-stop");
  await expect(button).toHaveAccessibleName("送信");
  // 入力が空の間は送信できない（フォーカスは受ける）。
  await expect(button).toHaveAttribute("aria-disabled", "true");

  await composer.fill("経費の上限は？");
  await expect(button).not.toHaveAttribute("aria-disabled", "true");
  const idleBox = await button.boundingBox();
  await button.click();
  await expect(button).toHaveAccessibleName("停止");
  await expect(button).toBeFocused();
  const runningBox = await button.boundingBox();
  expect(runningBox?.x).toBe(idleBox?.x);
  expect(runningBox?.width).toBe(idleBox?.width);

  // 生成中も次の質問を書ける。Enter では停止も送信もしない。
  await composer.fill("次の質問");
  await composer.press("Enter");
  await page.waitForTimeout(300);
  await expect(button).toHaveAccessibleName("停止");
  await expect(composer).toBeFocused();
  expect(calls).toBe(1);
  expect(aborted).toBe(0);

  // 停止で中断する。フォーカスはボタンに残る。
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(button).toHaveAccessibleName("送信");
  await expect(button).toBeFocused();
  await expect.poll(() => aborted).toBe(1);

  // 停止の後に再び送信できる（書いておいた次の質問）。
  await expect(composer).toHaveValue("次の質問");
  await button.click();
  await expect.poll(() => calls).toBe(2);
  await expect(button).toHaveAccessibleName("停止");
  await expectNoPageOverflow(page);
});

test("会話の履歴は既定で閉じ、開くとチャットの左に並び、開閉の状態が再読込で残る（#664）", { tag: "@responsive" }, async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page, "ready", [userMessage, assistantMessage]);
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");

  await expectChatWorkspaceLayout(page, "desktop");
  const toggle = page.getByTestId("chat-history-toggle");
  await expect(toggle).toHaveAccessibleName("会話の履歴");
  // 新しい会話は履歴を開かなくても押せる。
  await expect(page.getByRole("button", { name: "新しい会話", exact: true })).toBeVisible();

  const history = await openChatHistory(page);
  await expect(page.getByRole("complementary", { name: "会話の履歴" })).toBeVisible();
  const chat = page.getByRole("region", { name: "チャット" });
  const [historyBox, chatBox] = await Promise.all([history.boundingBox(), chat.boundingBox()]);
  if (!historyBox || !chatBox) throw new Error("レイアウトを計測できません。");
  expect(Math.abs(historyBox.y - chatBox.y)).toBeLessThanOrEqual(1);
  expect(chatBox.x).toBeGreaterThan(historyBox.x + historyBox.width);

  // 会話を選んでもインラインのパネルは開いたまま。今の会話の名前は上端に出る。
  await history.getByRole("button", { name: /^経費の上限は？ 2件/ }).click();
  await expect(page.getByTestId("chat-conversation-title")).toHaveText("経費の上限は？");
  await expect(history).toBeVisible();

  // 開閉の状態は作業状態として残る（workspace-state.md）。
  await page.reload();
  await expect(page.getByTestId("chat-history")).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.click();
  await expectChatWorkspaceLayout(page, "desktop");
  await page.reload();
  await expectChatWorkspaceLayout(page, "desktop");
  await expectNoPageOverflow(page);
});

test("375px では会話の履歴をシートで開き、Esc・外側・会話の選択で閉じてフォーカスを開閉ボタンへ戻す（#664）", { tag: "@responsive" }, async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockChat(page, "ready", [userMessage, assistantMessage]);
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await expectChatWorkspaceLayout(page, "mobile");

  const toggle = page.getByTestId("chat-history-toggle");
  const sheet = page.getByRole("dialog", { name: "会話の履歴" });

  // キーボードで開く → 閉じるボタンへフォーカス → Tab は中で回る → Esc で閉じてボタンへ戻る。
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(sheet).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(sheet.getByRole("button", { name: "会話の履歴を閉じる" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(sheet.getByRole("button", { name: /^経費の上限は？ 2件/ })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();

  // シートの外側（scrim）を押すと閉じる。
  await toggle.click();
  await expect(sheet).toBeVisible();
  await expectNoPageOverflow(page);
  await page.getByTestId("chat-history-scrim").click({ position: { x: 360, y: 400 } });
  await expect(sheet).toBeHidden();

  // 会話を選ぶと閉じ、今の会話の名前が上端に出る。
  await toggle.click();
  await sheet.getByRole("button", { name: /^経費の上限は？ 2件/ }).click();
  await expect(sheet).toBeHidden();
  await expect(toggle).toBeFocused();
  await expect(page.getByTestId("chat-conversation-title")).toHaveText("経費の上限は？");
  await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();

  // モーダルのシートの開閉は残さない（再読込で画面を塞がない）。
  await toggle.click();
  await expect(sheet).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("chat-history-toggle")).toHaveAttribute("aria-expanded", "false");
  await expect(sheet).toBeHidden();
});

test("会話を選ばずに送信すると会話を作って回答する（#664）", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await mockChat(page);
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");

  await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
  // 会話を選んでいない間は、上端に会話の名前を出さない。
  await expect(page.getByTestId("chat-conversation-title")).toHaveCount(0);
  const created = page.waitForRequest(
    (request) =>
      request.method() === "POST" && new URL(request.url()).pathname === "/api/chat/conversations"
  );
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await expect(composer).toBeEnabled();
  await composer.fill("経費の上限は？");
  await page.getByRole("button", { name: "送信" }).click();
  await created;

  await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
  await expect(page.getByTestId("chat-conversation-title")).toHaveText("経費の上限は？");
});

const faqSuggestions = [1, 2, 3].map((index) => ({
  id: `faq-${index}`,
  question: `経費の上限について ${index}`,
  matched_question: `経費の上限について ${index}`,
  answer: `承認済みの回答 ${index}`,
  score: 0.8 - index / 100,
  direct: false,
}));

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`近い承認済み FAQ があれば、類似問か「どれでもない」を選んでから回答する（#684） (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);
    const suggestQueries: Record<string, unknown>[] = [];
    await page.route("**/api/search-answer-profiles/*/approved-faq/suggest", async (route) => {
      suggestQueries.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({
        json: { data: { suggestions: faqSuggestions }, error_messages: [], warning_messages: [] },
      });
    });
    const streamBodies: Record<string, unknown>[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/messages/stream")) {
        streamBodies.push(request.postDataJSON() as Record<string, unknown>);
      }
    });

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");
    const composer = page.getByRole("textbox", { name: "質問", exact: true });
    await composer.fill("経費の上限は？");
    await page.getByRole("button", { name: "送信" }).click();

    // 回答を作る前に、最大 3 件の類似問と「どれでもない」を出す。選ぶまで送らない（飛ばす操作は無い）。
    const choice = page.getByTestId("chat-approved-faq-choice");
    await expect(choice).toContainText("経費の上限は？");
    await expect(choice.getByRole("button", { name: "この類似問で回答する" })).toHaveCount(3);
    await expect(
      choice.getByRole("button", { name: "どれでもない（類似問を使わずに回答する）" })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "送信" })).toBeDisabled();
    expect(suggestQueries).toEqual([{ query: "経費の上限は？", purpose: "chat" }]);
    expect(streamBodies).toEqual([]);
    await expectNoPageOverflow(page);

    // 選ぶ前に画面を離れて戻っても（再読込を含む）、同じ質問と候補が残る（作業状態。#702）。
    await page.reload();
    await expect(choice).toContainText("経費の上限は？");
    await expect(choice.getByRole("button", { name: "この類似問で回答する" })).toHaveCount(3);
    expect(suggestQueries).toHaveLength(1);

    // 選んだ類似問の id を質問と一緒に送る。
    await choice.getByRole("listitem").nth(1).getByRole("button").click();
    await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
    await expect(choice).toHaveCount(0);
    expect(streamBodies[0]).toMatchObject({ content: "経費の上限は？", approved_faq_id: "faq-2" });

    // 「どれでもない」は類似問を使わずに送る。
    await composer.fill("交通費は？");
    await page.getByRole("button", { name: "送信" }).click();
    await page
      .getByRole("button", { name: "どれでもない（類似問を使わずに回答する）" })
      .click();
    await expect.poll(() => streamBodies.length).toBe(2);
    expect(streamBodies[1]).toMatchObject({ content: "交通費は？" });
    expect(streamBodies[1]).not.toHaveProperty("approved_faq_id");
  });
}


// #737: 承認済み FAQ から回答したときは、根拠に FAQ の原文（回答した時点）を出典として出す。
for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`承認済み FAQ の回答では、FAQ の質問と承認済みの回答の原文を出典に出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const answerDiagnostics = {
      answer_source: "approved_faq",
      approved_faq_question: "経費精算の上限はいくらですか？",
      approved_faq_answer: "1 回の申請の上限は 10 万円です。\n超える場合は部長の承認が必要です。",
      models: { llm: { model_id: "m1", label: "MODEL 1" }, vision: null, embedding: "", rerank: "" },
    };
    const streamBody = [
      sseStart,
      `event: delta\ndata: ${JSON.stringify({ model_id: "m1", text: "上限は 10 万円です。\n\n（出典: 承認済み FAQ「経費精算の上限はいくらですか？」）" })}\n\n`,
      `event: metadata\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1", trace_id: "t1", elapsed_ms: 5, guardrail_warnings: [], answer_diagnostics: answerDiagnostics })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ model_id: "m1", message_id: "a1" })}\n\n`,
    ].join("");
    await mockChat(page, "ready", [], { streamBody });

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");
    await page.getByRole("button", { name: "新しい会話" }).click();
    await page.getByRole("textbox").fill(userMessage.content);
    await page.getByRole("button", { name: "送信" }).click();

    await page.getByTestId("chat-answer-progress-details").click();
    const panel = page.getByRole("region", { name: "処理の経過" }).getByRole("region", { name: "詳細" });
    await expect(panel.getByText("承認済み FAQ から回答")).toBeVisible();
    const source = panel.getByRole("region", { name: "回答の出典: 承認済み FAQ" });
    await expect(source).toContainText("経費精算の上限はいくらですか？");
    // 原文は言い換えず、改行も保って出す。
    await expect(source.getByText(/1 回の申請の上限は 10 万円です。\s+超える場合は部長の承認が必要です。/)).toBeVisible();
    // 文書を検索していないので、空の「根拠の構成」は出さない。
    await expect(panel.getByText(/^根拠の構成/)).toHaveCount(0);
    await expectNoPageOverflow(page);
  });
}

// #717: 類似問の後に、検索・回答プロファイルのルールの確認の質問を出し、選んだ答えを送る。
const clarificationSuggestion = {
  rule_id: "R01",
  rule_title: "期限の確認",
  clarification: {
    question: "どの規程についてのご質問ですか？",
    multiple: true,
    allow_other: true,
    options: [
      {
        id: "travel",
        label: "出張旅費",
        description: "出張旅費規程",
        search_terms: ["出張"],
        premise: "",
        sections: [
          {
            document_id: "doc-travel",
            document_name: "出張旅費規程.pdf",
            section_id: "sec-6",
            title: "第6条 申請と精算",
            page_start: 2,
            page_end: 3,
          },
        ],
      },
      { id: "expense", label: "経費精算", description: "", search_terms: [], premise: "", sections: [] },
    ],
  },
};

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`回答前の確認に答えてから回答し、確認せずに回答・確認をやめるもできる（#717 / #1839） (${viewport.name})`, { tag: "@responsive" }, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);
    await page.route("**/api/search-answer-profiles/*/approved-faq/suggest", (route) =>
      route.fulfill({ json: { data: { suggestions: [] }, error_messages: [], warning_messages: [] } })
    );
    const clarifyQueries: Record<string, unknown>[] = [];
    await page.route("**/api/search-answer-profiles/*/clarifications/suggest", async (route) => {
      clarifyQueries.push(route.request().postDataJSON() as Record<string, unknown>);
      // 古い保存の allow_other: false でも「その他（自由入力）」を出す（#1839）。
      const suggestion = {
        ...clarificationSuggestion,
        clarification: { ...clarificationSuggestion.clarification, allow_other: false },
      };
      await route.fulfill({
        json: { data: { suggestion }, error_messages: [], warning_messages: [] },
      });
    });
    const streamBodies: Record<string, unknown>[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/messages/stream")) {
        streamBodies.push(request.postDataJSON() as Record<string, unknown>);
      }
    });

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");
    const composer = page.getByRole("textbox", { name: "質問", exact: true });
    await composer.fill("申請の期限は？");
    await page.getByRole("button", { name: "送信" }).click();

    // 3 製品共通の回答前の確認のカード（#1839）。
    const choice = page.getByTestId("chat-clarification-choice");
    await expect(choice.getByRole("heading", { name: "回答の前に確認させてください" })).toBeVisible();
    const prompt = choice.getByRole("heading", { name: "どの規程についてのご質問ですか？" });
    await expect(prompt).toBeVisible();
    // 送信の直後に出るので、確認事項の文へフォーカスを移す。
    await expect(prompt).toBeFocused();
    await expect(choice).toContainText("対象: 「出張旅費規程.pdf」第6条 申請と精算 p.2–3");
    const confirm = choice.getByRole("button", { name: "この内容で回答" });
    await expect(confirm).toBeDisabled();
    await expect(confirm).toHaveAccessibleDescription(
      "選択肢を選ぶか、「その他（自由入力）」に書くと押せます。"
    );
    await expect(choice.getByRole("button")).toHaveText(["この内容で回答", "確認せずに回答", "確認をやめる"]);
    await expect(choice.getByRole("textbox", { name: "その他（自由入力）" })).toBeVisible();
    await expect(page.getByRole("button", { name: "送信" })).toBeDisabled();
    expect(clarifyQueries).toEqual([{ query: "申請の期限は？" }]);
    expect(streamBodies).toEqual([]);
    await expectNoPageOverflow(page);
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
      await page.evaluate((dark) => document.documentElement.classList.toggle("dark", dark), colorScheme === "dark");
      await choice.screenshot({
        path: test.info().outputPath(`chat-confirm-before-answer-${viewport.name}-${colorScheme}.png`),
      });
    }
    await page.emulateMedia({ colorScheme: "light" });
    await page.evaluate(() => document.documentElement.classList.remove("dark"));

    // 選ぶ前に再読込しても残る（作業状態）。
    await page.reload();
    await expect(prompt).toBeVisible();

    await choice.getByRole("checkbox", { name: /出張旅費/ }).check();
    await choice.getByRole("textbox", { name: "その他（自由入力）" }).fill("海外出張");
    await confirm.click();
    await expect.poll(() => streamBodies.length).toBe(1);
    expect(streamBodies[0]).toMatchObject({
      content: "申請の期限は？",
      clarification: { rule_id: "R01", option_ids: ["travel"], other_text: "海外出張" },
    });
    await expect(choice).toHaveCount(0);

    // 「確認せずに回答」は確認を使わずに送り、飛ばしたことだけを会話に残す（前提を示して回答する）。
    await composer.fill("交通費の期限は？");
    await page.getByRole("button", { name: "送信" }).click();
    await expect(choice.getByRole("button", { name: "確認せずに回答" })).toHaveAccessibleDescription(
      /前提を示して回答します。/
    );
    await choice.getByRole("button", { name: "確認せずに回答" }).click();
    await expect.poll(() => streamBodies.length).toBe(2);
    expect(streamBodies[1]).not.toHaveProperty("clarification");
    expect(streamBodies[1]).toMatchObject({ clarification_skipped_rule_id: "R01" });

    // 「確認をやめる」は送らずに、質問を入力欄へ戻す。
    await composer.fill("宿泊費の上限は？");
    await page.getByRole("button", { name: "送信" }).click();
    await expect(composer).toHaveValue("");
    await choice.getByRole("button", { name: "確認をやめる" }).click();
    await expect(choice).toHaveCount(0);
    await expect(composer).toHaveValue("宿泊費の上限は？");
    await expect(composer).toBeFocused();
    expect(streamBodies).toHaveLength(2);
  });

  test(`回答前に確認した内容を、会話の利用者の側に残す（#1839） (${viewport.name})`, { tag: "@responsive" }, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);
    const answered = {
      ...userMessage,
      clarification: {
        outcome: "answered",
        entries: [
          { prompt: "どの規程についてのご質問ですか？", selected: ["出張旅費"], other_text: "海外出張" },
        ],
      },
    };
    await page.route(
      (url) => url.pathname === "/api/chat/conversations/conv-1",
      (route) =>
        route.fulfill({
          json: {
            data: conversationDetail([answered, assistantMessage]),
            error_messages: [],
            warning_messages: [],
          },
        })
    );
    await page.goto("/chat?search_answer_profile_id=bv-1&conversation_id=conv-1");
    const record = page.getByTestId("chat-clarification-record");
    await expect(record.getByRole("group", { name: "回答前に確認した内容" })).toBeVisible();
    await expect(record).toContainText("どの規程についてのご質問ですか？");
    await expect(record).toContainText("出張旅費、その他: 海外出張");
    await expectNoPageOverflow(page);
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
      await page.evaluate((dark) => document.documentElement.classList.toggle("dark", dark), colorScheme === "dark");
      await record.screenshot({
        path: test.info().outputPath(`chat-clarification-record-${viewport.name}-${colorScheme}.png`),
      });
    }
  });
}

test("範囲を絞った回答の下から、範囲を指定せずに同じ質問を送り直せる（#721）", async ({ page }) => {
  await mockChat(page);
  await page.route("**/api/search-answer-profiles/*/approved-faq/suggest", (route) =>
    route.fulfill({ json: { data: { suggestions: [] }, error_messages: [], warning_messages: [] } })
  );
  const clarifyQueries: unknown[] = [];
  await page.route("**/api/search-answer-profiles/*/clarifications/suggest", async (route) => {
    clarifyQueries.push(route.request().postDataJSON());
    await route.fulfill({ json: { data: { suggestion: null }, error_messages: [], warning_messages: [] } });
  });
  // 保存された回答は、確認で絞った範囲を末尾に持つ。
  const scoped = { ...assistantMessage, content: "期限は 1 か月です。（対象: 「出張旅費規程.pdf」の「第6条 申請と精算」）" };
  await page.route(
    (url) => url.pathname === "/api/chat/conversations/conv-1",
    (route) =>
      route.fulfill({
        json: { data: conversationDetail([userMessage, scoped]), error_messages: [], warning_messages: [] },
      })
  );
  const streamBodies: Record<string, unknown>[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/messages/stream")) {
      streamBodies.push(request.postDataJSON() as Record<string, unknown>);
    }
  });

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  await page.getByRole("textbox", { name: "質問", exact: true }).fill("経費の上限は？");
  await page.getByRole("button", { name: "送信" }).click();
  await expect.poll(() => streamBodies.length).toBe(1);

  const askAgain = page.getByRole("button", { name: "範囲を指定せずに質問し直す" });
  await expect(askAgain).toBeVisible();
  const suggestCalls = clarifyQueries.length;
  await askAgain.click();
  await expect.poll(() => streamBodies.length).toBe(2);
  // 類似問・確認の質問を出さずに、同じ質問をそのまま送る。
  expect(streamBodies[1]).toMatchObject({ content: "経費の上限は？" });
  expect(streamBodies[1]).not.toHaveProperty("clarification");
  expect(streamBodies[1]).not.toHaveProperty("approved_faq_id");
  expect(clarifyQueries).toHaveLength(suggestCalls);
});

// #907: 送った質問は、会話の作成・回答（start）を待たずにすぐ会話の欄の末尾へ出す（楽観的な表示）。
// 失敗・停止のときも質問を残し、失敗は「送信できませんでした」と「再送信」を出す。

/** テストの中で応答を止めておき、好きなときに返す。 */
function gate() {
  let open: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { open, opened };
}

/** 回答の作成中の表示だけが回る（動くスピナーは 1 つ。messaging.md §3.7）。 */
async function expectSingleChatSpinner(page: Page) {
  await expect(page.getByTestId("chat-messages").locator("svg.animate-spin:visible")).toHaveCount(1);
  await expect(page.locator("svg.animate-spin:visible")).toHaveCount(1);
}

async function expectChatScrolledToEnd(page: Page) {
  await expect
    .poll(() =>
      page
        .getByTestId("chat-messages")
        .evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop)
    )
    .toBeLessThanOrEqual(2);
}

const chatComposer = (page: Page) => page.getByRole("textbox", { name: "質問", exact: true });

async function screenshotBothThemes(page: Page, target: Locator, path: (theme: string) => string) {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
    }, theme);
    await target.screenshot({ path: path(theme) });
  }
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`送った質問は会話の作成と回答を待たずにすぐ会話の欄へ出る（#907） (${viewport.name})`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);
    const create = gate();
    const stream = gate();
    await page.route("**/api/chat/conversations", async (route) => {
      if (route.request().method() === "POST") await create.opened;
      await route.fallback();
    });
    await page.route("**/api/chat/conversations/*/messages/stream", async (route) => {
      await stream.opened;
      await route.fallback();
    });

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");
    const empty = page.getByText("質問を入力して会話を始めます");
    await expect(empty).toBeVisible();

    const composer = chatComposer(page);
    await composer.fill("経費の上限は？");
    await composer.press("Enter");

    // 会話の作成の応答の前に、質問と回答の作成中の表示が出る。空の状態はすぐ消える。
    const live = page.getByTestId("chat-live-turn");
    await expect(live.locator('[data-status="sending"]')).toHaveText("経費の上限は？");
    await expect(live.getByTestId("chat-answer-progress")).toBeVisible();
    await expect(empty).toHaveCount(0);
    await expect(page.getByRole("log", { name: "会話" })).toContainText("経費の上限は？");
    await expect(composer).toHaveValue("");
    await expect(composer).toBeFocused();
    await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("停止");
    await expectSingleChatSpinner(page);
    await expectChatScrolledToEnd(page);
    await expectNoPageOverflow(page);
    await screenshotBothThemes(page, page.getByRole("region", { name: "チャット" }), (theme) =>
      testInfo.outputPath(`chat-sending-${viewport.name}-${theme}.png`)
    );

    // 会話ができても回答（start）の前は、同じ質問を出し続ける。
    create.open();
    await expect(page.getByTestId("chat-conversation-title")).toBeVisible();
    await expect(live.locator('[data-status="sending"]')).toHaveText("経費の上限は？");

    // 回答が届いたら同じ場所に流し込み、取り直した会話でも質問を二重に出さない。
    stream.open();
    await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
    await expect(page.getByTestId("chat-live-turn")).toHaveCount(0);
    const messages = page.getByTestId("chat-messages");
    await expect(messages.getByText("経費の上限は？", { exact: true })).toHaveCount(1);
    await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("送信");
  });

  test(`続きの会話でも、送った質問は回答を待たずに末尾へ出る（#907） (${viewport.name})`, async ({ page }) => {
    await openPersistedConversation(page, viewport.width, [userMessage, assistantMessage]);
    if (viewport.width < 1024) await page.keyboard.press("Escape");
    await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
    await page.route("**/api/chat/conversations/*/messages/stream", () => new Promise<void>(() => undefined));

    const composer = chatComposer(page);
    await composer.fill("交通費も含まれますか？");
    await composer.press("Enter");

    const live = page.getByTestId("chat-live-turn");
    await expect(live.locator('[data-status="sending"]')).toHaveText("交通費も含まれますか？");
    await expect(live.getByTestId("chat-answer-progress")).toBeVisible();
    // 前の質問と回答の後（末尾）に出る。
    const text = await page.getByTestId("chat-messages").innerText();
    expect(text.indexOf("経費の上限は 10 万円です。")).toBeGreaterThanOrEqual(0);
    expect(text.indexOf("経費の上限は 10 万円です。")).toBeLessThan(text.indexOf("交通費も含まれますか？"));
    await expect(composer).toHaveValue("");
    await expectSingleChatSpinner(page);
    await expectChatScrolledToEnd(page);
    await expectNoPageOverflow(page);
  });

  test(`送信できなかった質問は残し、再送信できる（#907） (${viewport.name})`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);
    let streamCalls = 0;
    await page.route("**/api/chat/conversations/*/messages/stream", async (route) => {
      streamCalls += 1;
      if (streamCalls === 1) {
        await route.fulfill({
          status: 503,
          json: {
            data: null,
            error_messages: ["回答を作成できませんでした。時間をおいて再送信してください。"],
            warning_messages: [],
          },
        });
        return;
      }
      await route.fallback();
    });

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");
    const composer = chatComposer(page);
    await composer.fill("経費の上限は？");
    await composer.press("Enter");

    const live = page.getByTestId("chat-live-turn");
    const failed = live.locator('[data-status="failed"]');
    await expect(failed).toContainText("経費の上限は？");
    await expect(failed).toContainText("送信できませんでした");
    const failure = page.getByTestId("chat-send-failure");
    await expect(failure.getByRole("alert")).toContainText(
      "回答を作成できませんでした。時間をおいて再送信してください。"
    );
    // 入力欄には戻さない（質問は会話の欄に残っている）。処理中の表示は消える。
    await expect(composer).toHaveValue("");
    await expect(live.getByTestId("chat-answer-progress")).toHaveCount(0);
    await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("送信");
    await expectNoPageOverflow(page);
    await screenshotBothThemes(page, live, (theme) =>
      testInfo.outputPath(`chat-failed-${viewport.name}-${theme}.png`)
    );

    await failure.getByRole("button", { name: "再送信" }).click();
    await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
    await expect(page.getByTestId("chat-send-failure")).toHaveCount(0);
    await expect(page.getByTestId("chat-messages").getByText("経費の上限は？", { exact: true })).toHaveCount(1);
    expect(streamCalls).toBe(2);
  });

  test(`停止しても送った質問は会話の欄に残る（#907） (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockChat(page);
    await page.route("**/api/chat/conversations/*/messages/stream", () => new Promise<void>(() => undefined));

    await page.goto("/chat");
    await selectSearchAnswerProfile(page, "経理アシスタント");
    const composer = chatComposer(page);
    await composer.fill("経費の上限は？");
    await composer.press("Enter");
    const live = page.getByTestId("chat-live-turn");
    await expect(live.getByTestId("chat-answer-progress")).toBeVisible();

    const button = page.getByTestId("chat-run-stop");
    await button.click();
    await expect(button).toHaveAccessibleName("送信");
    await expect(live.locator('[data-status="stopped"]')).toHaveText("経費の上限は？");
    await expect(live.getByTestId("chat-stopped")).toHaveText(
      "回答の作成を停止しました。もう一度送ると、新しく回答を作成します。"
    );
    await expect(live.getByTestId("chat-answer-progress")).toHaveCount(0);
    await expect(page.locator("svg.animate-spin:visible")).toHaveCount(0);
    await expect(composer).toHaveValue("");
    await expectNoPageOverflow(page);

    // 止めた質問は新しい会話に持ち越さない。
    await page.getByRole("button", { name: "新しい会話" }).click();
    await expect(page.getByTestId("chat-live-turn")).toHaveCount(0);
    await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
  });
}

// 別の会話・検索・回答プロファイルへ移ったら前の送信を打ち切り、「送信」に戻す（移った先で送れなくしない）。
test("類似問の照会中に別の会話を選んでも、送信中のまま残らず送信できる", async ({ page }) => {
  await mockChat(page, "ready", [userMessage, assistantMessage]);
  // 類似問の照会は中止できないので、応答しないまま別の会話へ移る。
  await page.route("**/api/search-answer-profiles/*/approved-faq/suggest", () => new Promise<void>(() => undefined));

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理アシスタント");
  const composer = chatComposer(page);
  await composer.fill("交通費の上限は？");
  await composer.press("Enter");
  const button = page.getByTestId("chat-run-stop");
  await expect(button).toHaveAccessibleName("停止");

  const history = await openChatHistory(page);
  await history.getByRole("list", { name: "会話の履歴" }).getByRole("button").filter({ hasText: "件・" }).click();
  await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
  await expect(page.getByTestId("chat-live-turn")).toHaveCount(0);
  await expect(button).toHaveAccessibleName("送信");
  await composer.fill("日当は？");
  await expect(button).toBeEnabled();
});

test("生成中に新しい会話を作ると、前の会話の生成を止めて新しい会話で送信できる", async ({ page }) => {
  await openPersistedConversation(page, 1280, [userMessage, assistantMessage]);
  await expect(page.getByText("経費の上限は 10 万円です。").first()).toBeVisible();
  let streamAborted = false;
  page.on("requestfailed", (request) => {
    if (request.url().endsWith("/messages/stream")) streamAborted = true;
  });
  await page.route("**/api/chat/conversations/*/messages/stream", () => new Promise<void>(() => undefined));
  const created = { ...conversationDetail([], null), id: "conv-2" };
  await page.route("**/api/chat/conversations", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({ json: { data: created, error_messages: [], warning_messages: [] } });
  });
  await page.route("**/api/chat/conversations/conv-2", (route) =>
    route.fulfill({ json: { data: created, error_messages: [], warning_messages: [] } })
  );

  const composer = chatComposer(page);
  await composer.fill("交通費の上限は？");
  await composer.press("Enter");
  await expect(page.getByTestId("chat-live-turn").getByTestId("chat-answer-progress")).toBeVisible();

  await page.getByRole("button", { name: "新しい会話" }).click();
  await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
  await expect(page.getByTestId("chat-live-turn")).toHaveCount(0);
  await expect(page.getByTestId("chat-run-stop")).toHaveAccessibleName("送信");
  await expect.poll(() => streamAborted).toBe(true);
});
