import { expect, type Page, test } from "./fixtures/test";

import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  openChatHistory,
  selectSearchAnswerProfile,
} from "./_helpers";

/**
 * RAG 検索・回答の不足の回帰テスト（#304）。
 * 参照 KB が 0 件の検索・回答プロファイルでは検索・チャットしない、
 * チャットは会話の回答の trace_id で保存済みの回答を引き当て、開き直し・削除できる。
 */

const envelope = (data: unknown) => ({ json: { data, error_messages: [], warning_messages: [] } });

const view = (id: string, name: string, knowledgeBaseCount: number) => ({
  id,
  name,
  description: null,
  status: "ACTIVE",
  knowledge_base_count: knowledgeBaseCount,
  created_at: "2026-06-19T00:00:00Z",
  updated_at: "2026-06-19T00:00:00Z",
  archived_at: null,
});

const VIEWS = [view("bv-1", "経理ビュー", 1), view("bv-empty", "準備中ビュー", 0)];
const NO_KB_MESSAGE =
  "この検索・回答プロファイルには参照するナレッジベースがありません。検索・回答プロファイルの設定でナレッジベースを追加してください。";

const summary = (index: number) => ({
  trace_id: `trace-${index}`,
  search_answer_profile_id: "bv-1",
  surface: "search",
  answer_engine: "grounded",
  question: `質問 ${index} 番`,
  rewritten_question: null,
  confidence: null,
  created_at: "2026-09-25T01:00:00Z",
});

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/approved-faq/suggest")) {
      await route.fulfill(envelope({ suggestions: [] }));
      return;
    }
    if (pathname.endsWith("/query-suggestions")) {
      await route.fulfill(envelope({ suggestions: [] }));
      return;
    }
    await route.fulfill(
      envelope({ items: VIEWS, total: VIEWS.length, limit: 50, offset: 0, has_next: false })
    );
  });
  await page.route("**/api/settings/answer-records", (route) =>
    route.fulfill(envelope({ retention_days: 30 }))
  );
  await page.route("**/api/feedback/current**", (route) => route.fulfill(envelope([])));
});

async function mockAnswerHistory(page: Page, total: number) {
  const offsets: number[] = [];
  await page.route("**/api/search/answers**", async (route) => {
    const url = new URL(route.request().url());
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset"));
    offsets.push(offset);
    const items = Array.from(
      { length: Math.max(0, Math.min(limit, total - offset)) },
      (_, index) => summary(offset + index + 1)
    );
    await route.fulfill(
      envelope({ items, total, limit, offset, has_next: offset + items.length < total })
    );
  });
  return offsets;
}

test("対象の検索・回答プロファイルは 1 つを選ぶ欄で、検索・参照 KB の件数・キーボードで選べる（#635）", async ({
  page,
}) => {
  await mockAnswerHistory(page, 0);
  await page.goto("/search");

  const trigger = page.getByRole("button", { name: /検索・回答プロファイル/ });
  await expect(trigger).toContainText("検索・回答プロファイルを検索して選択…");
  await expect(trigger).toHaveAttribute("aria-required", "true");
  // 欄の下に説明文（helper）は出さない（#664）。
  await expect(page.getByText("選んだ検索・回答プロファイルが参照するナレッジベースを検索し")).toHaveCount(0);

  await trigger.click();
  // 欄のラベルは RAG 検索・チャットで同じ「検索・回答プロファイル」（#860）。検索欄の名前は「<ラベル>を検索」。
  const search = page.getByRole("combobox", { name: "検索・回答プロファイルを検索" });
  const listbox = page.getByRole("listbox", { name: /検索・回答プロファイル/ });
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute("placeholder", "検索・回答プロファイルの名前・説明で検索…");
  // 参照 KB が 0 件の検索・回答プロファイルも隠さず、件数を出して後ろに並べる。
  await expect(listbox.getByRole("option")).toHaveText([/経理ビュー.*参照 KB 1 件/, /準備中ビュー.*参照 KB 0 件/]);
  await expect(page.getByRole("checkbox", { name: "参照 KB なしを隠す" })).toHaveCount(0);

  await search.fill("経理");
  await expect(listbox.getByRole("option")).toHaveCount(1);
  await search.press("Enter");
  await expect(listbox).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(trigger).toContainText("経理ビュー");
  // 選択済みの chip は出さない（単一選択）。
  await expect(page.getByLabel("経理ビュー を選択から外す")).toHaveCount(0);

  // 別の検索・回答プロファイルへ置き換える（追加にならない）。Esc で閉じるとボタンへ戻る。
  await trigger.press("ArrowDown");
  await expect(search).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(listbox).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await selectSearchAnswerProfile(page, /準備中ビュー/);
  await expect(trigger).toContainText("準備中ビュー");
  await expect(trigger).not.toContainText("経理ビュー");
  await expectNoPageOverflow(page);
});

test("参照 KB が 0 件の検索・回答プロファイルでは理由を示し、検索を送らない", async ({ page }) => {
  let streamRequests = 0;
  await mockAnswerHistory(page, 0);
  await page.route("**/api/search/stream", async (route) => {
    streamRequests += 1;
    await route.fulfill({ status: 500, json: { data: null, error_messages: [], warning_messages: [] } });
  });

  await page.goto("/search");
  await selectSearchAnswerProfile(page, /準備中ビュー/);
  await expect(page.getByRole("alert").filter({ hasText: NO_KB_MESSAGE })).toBeVisible();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限は？");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  // 送信せず、検索前の案内のまま（回答の生成を始めない）。
  await expect(page.getByText("索引済みドキュメントを自然言語で検索します。").nth(1)).toBeVisible();
  await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0);
  expect(streamRequests).toBe(0);
  await expectNoPageOverflow(page);

  // KB のある検索・回答プロファイルを足せば理由は消える（KB のある検索・回答プロファイルの KB だけを検索する）。
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await expect(page.getByText(NO_KB_MESSAGE)).toHaveCount(0);
});

test("backend が参照 KB のない検索・回答プロファイルを 409 で断ったら、その理由を表示する", async ({ page }) => {
  await mockAnswerHistory(page, 0);
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 409,
      json: { data: null, error_messages: [NO_KB_MESSAGE], warning_messages: [] },
    })
  );

  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限は？");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect(page.getByText(NO_KB_MESSAGE)).toBeVisible();
});

test("RAG 検索画面には回答履歴の一覧を出さない（#444）", async ({ page }) => {
  await mockAnswerHistory(page, 4);

  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await expect(page.getByRole("textbox", { name: "RAG 検索" })).toBeVisible();
  await expect(page.getByText("DocRAG の回答履歴")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "回答履歴のページ" })).toHaveCount(0);
});

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

function chatMessage(role: "USER" | "ASSISTANT", id: string, traceId: string | null) {
  return {
    message_id: id,
    conversation_id: "conv-1",
    role,
    content: role === "USER" ? "経費の上限は？" : "経費の上限は 10 万円です。",
    model: role === "USER" ? null : "m1",
    citations: role === "USER" ? [] : [citationChunk],
    guardrail_warnings: [],
    trace_id: traceId,
    status: "COMPLETE",
    reply_to_message_id: role === "USER" ? null : "u1",
    created_at: "2026-01-01T00:00:00Z",
  };
}

async function mockChat(page: Page, searchAnswerProfileId: string) {
  const messages = [chatMessage("USER", "u1", null), chatMessage("ASSISTANT", "a1", "trace-chat")];
  const conversation = {
    id: "conv-1",
    search_answer_profile_id: searchAnswerProfileId,
    title: "経費の上限は？",
    status: "ACTIVE",
    message_count: messages.length,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:02Z",
  };
  await page.route("**/api/chat/models", (route) => route.fulfill(envelope([])));
  await page.route("**/api/feedback**", (route) => route.fulfill(envelope([])));
  await page.route("**/api/chat/conversations**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/chat/conversations") {
      await route.fulfill(
        envelope({ items: [conversation], total: 1, limit: 50, offset: 0, has_next: false })
      );
      return;
    }
    await route.fulfill(envelope({ ...conversation, messages }));
  });
}

test("チャットは参照 KB が 0 件の検索・回答プロファイルで理由を示し、送信できない", async ({ page }) => {
  await mockChat(page, "bv-empty");
  await page.route("**/api/search/answers**", (route) =>
    route.fulfill(envelope({ items: [], total: 0, limit: 1, offset: 0, has_next: false }))
  );

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "準備中ビュー");
  const banner = page.getByRole("status").filter({ hasText: "参照するナレッジベースがありません" });
  await expect(banner).toBeVisible();
  await expect(banner.getByRole("button", { name: "検索・回答プロファイルの設定を開く" })).toBeVisible();
  const conversations = await openChatHistory(page);
  await conversations.getByRole("list", { name: "会話の履歴" }).getByRole("button").first().click();
  await page.getByRole("textbox", { name: "質問", exact: true }).fill("経費の上限は？");
  await expect(page.getByRole("button", { name: "送信" })).toBeDisabled();
  await expectNoPageOverflow(page);
});

test("チャットは会話の回答の trace_id で保存済みの回答を引き当て、削除できる", async ({ page }) => {
  await mockChat(page, "bv-1");
  const requested: string[][] = [];
  let deleted = false;
  await page.route("**/api/search/answers**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "DELETE") {
      deleted = true;
      await route.fulfill(envelope({ trace_id: "trace-chat" }));
      return;
    }
    if (url.pathname.endsWith("/answers/trace-chat")) {
      await route.fulfill(
        envelope({
          ...summary(1),
          trace_id: "trace-chat",
          surface: "chat",
          answer: "経費の上限は 10 万円です。",
          citations: [citationChunk],
          answer_diagnostics: {},
        })
      );
      return;
    }
    const traceIds = url.searchParams.getAll("trace_id");
    requested.push(traceIds);
    await route.fulfill(
      envelope({
        items:
          !deleted && traceIds.includes("trace-chat")
            ? [{ ...summary(1), trace_id: "trace-chat", surface: "chat" }]
            : [],
        total: 1,
        limit: 1,
        offset: 0,
        has_next: false,
      })
    );
  });

  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理ビュー");
  const conversations = await openChatHistory(page);
  await conversations.getByRole("list", { name: "会話の履歴" }).getByRole("button").first().click();
  await expect(page.getByText("この回答の実行記録", { exact: true })).toBeVisible();
  expect(requested).toContainEqual(["trace-chat"]);

  // 保存された回答の実行記録には引用の一覧を出さず、引用は「根拠（引用）」の 1 か所だけに出す（#1202）。
  await page.getByText("この回答の実行記録", { exact: true }).click();
  const citationSummary = page.locator("summary").filter({ hasText: "根拠（引用） 1 件" });
  await citationSummary.click();
  await expect(page.getByText("経費規程.pdf")).toHaveCount(1);
  // 回答の評価は、根拠を確かめた後に答えられるよう「根拠（引用）」の後ろに出す（#1202）。
  const feedback = page.getByRole("group", { name: "この回答は役に立ちましたか？" });
  await expect(feedback).toBeVisible();
  const citationTop = (await citationSummary.boundingBox())?.y ?? Number.POSITIVE_INFINITY;
  const feedbackTop = (await feedback.boundingBox())?.y ?? Number.NEGATIVE_INFINITY;
  expect(feedbackTop).toBeGreaterThan(citationTop);

  // 保存された回答は、チャットの「この回答の実行記録」から確認を通して削除できる（#147）。
  const answerActions = page.getByRole("group", { name: "保存された回答 の操作" });
  await answerActions.getByRole("button", { name: "その他の操作" }).click();
  await page.getByRole("menuitem", { name: "この回答を削除" }).click();
  await page
    .getByRole("alertdialog", { name: "保存された回答を削除しますか？" })
    .getByRole("button", { name: "削除" })
    .click();
  // 削除の成功は Toast で知らせる（messaging.md §4.2。#285）。
  await expect(page.getByText("保存された回答を削除しました。")).toBeVisible();
  expect(deleted).toBe(true);
  await expect(page.getByText("この回答の実行記録", { exact: true })).toHaveCount(0);
});

// #635: RAG 検索とチャットの「対象の検索・回答プロファイル」は同じ部品・同じ文言・同じ幅（カードの幅いっぱい）。
for (const viewport of [
  { name: "1280", width: 1280, height: 800 },
  { name: "1920", width: 1920, height: 1000 },
  { name: "375", width: 375, height: 812 },
]) {
  test(`RAG 検索とチャットの検索・回答プロファイルの欄は同じ見た目 (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockAnswerHistory(page, 0);
    await mockChat(page, "bv-1");

    const measure = async () => {
      const trigger = page.getByRole("button", { name: /検索・回答プロファイル/ });
      await expect(trigger).toBeVisible();
      await expect(trigger).toContainText("検索・回答プロファイルを検索して選択…");
      await expect(trigger).toHaveAttribute("aria-required", "true");
      await expect(page.getByText("選んだ検索・回答プロファイルが参照するナレッジベースを検索し")).toHaveCount(0);
      return trigger.evaluate((button) => {
        // 欄（ラベル・ボタン）を置いた親の、内側の幅いっぱいに置く。
        const container = button.parentElement!.parentElement!;
        const style = getComputedStyle(container);
        const inner =
          container.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        const rect = button.getBoundingClientRect();
        return { width: rect.width, inner, height: rect.height };
      });
    };

    await page.goto("/search");
    const search = await measure();
    await page.goto("/chat");
    const chat = await measure();
    for (const box of [search, chat]) expect(Math.abs(box.width - box.inner)).toBeLessThanOrEqual(1);
    expect(Math.abs(search.height - chat.height)).toBeLessThanOrEqual(0.5);

    await selectSearchAnswerProfile(page, "経理ビュー");
    await expect(page.getByRole("button", { name: /検索・回答プロファイル/ })).toContainText("経理ビュー");
    await expectNoPageOverflow(page);
  });
}

const ANSWER_MODELS = [
  { model_id: "text-m", display_name: "gpt-oss-120b", kind: "text" },
  { model_id: "vision-m", display_name: "grok-4.3", kind: "vision" },
];

test("回答のモデルは既定のテキストモデルと画像対応モデルだけ。検索は 1 つを選び、チャットは比較できる（#675）", async ({
  page,
  isMobile,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await mockAnswerHistory(page, 0);
  await page.route("**/api/search/models", (route) => route.fulfill(envelope(ANSWER_MODELS)));
  await page.route("**/api/search/stream", async (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 500, json: { data: null, error_messages: [], warning_messages: [] } });
  });

  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
  // 回答を生成しないときはモデルを選ばせない。チャットと同じく入力欄の上のチップの行（#686）。
  const modelRow = page.getByTestId("search-answer-model");
  await expect(modelRow).toHaveCount(0);
  await enableSearchAnswer(page);
  await expect(modelRow.locator("button[aria-pressed]")).toHaveText(["gpt-oss-120b（テキスト）", "grok-4.3（画像対応）"]);
  // 説明は常設せず、ラベルの横の info アイコンから出す（#901）。マウスはポインタを乗せると出し、
  // タッチ端末（ホバーが無い）はタップで出す。
  await expect(modelRow).not.toContainText("根拠の図や画像を読むときだけ画像対応モデルを使います");
  const modelInfo = modelRow.getByRole("button", { name: "回答するモデルの説明" });
  if (isMobile) await modelInfo.tap();
  else await modelInfo.hover();
  await expect(page.getByTestId("search-answer-model-help")).toBeVisible();
  await expect(page.getByTestId("search-answer-model-help")).toContainText(
    "根拠の図や画像を読むときだけ画像対応モデルを使います"
  );
  const vision = modelRow.getByRole("button", { name: "grok-4.3（画像対応）" });
  await vision.click();
  await expect(vision).toHaveAttribute("aria-pressed", "true");
  // 比較はしないので 1 つだけ。テキストを選ぶと画像対応は外れ、もう一度押すと未選択に戻る。
  const text = modelRow.getByRole("button", { name: "gpt-oss-120b（テキスト）" });
  await text.click();
  await expect(vision).toHaveAttribute("aria-pressed", "false");
  await text.click();
  await expect(text).toHaveAttribute("aria-pressed", "false");
  await vision.click();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("図の数値は？");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0]).toMatchObject({ generate_answer: true, model_id: "vision-m" });
  await expectNoPageOverflow(page);

  await mockChat(page, "bv-1");
  await page.route("**/api/chat/models", (route) => route.fulfill(envelope(ANSWER_MODELS)));
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理ビュー");
  await expect(page.getByRole("button", { name: "gpt-oss-120b（テキスト）" })).toBeVisible();
  await expect(page.getByRole("button", { name: "grok-4.3（画像対応）" })).toBeVisible();
  // 説明は常設せず、ラベルの横の info アイコンを押すと出る（#901）。
  const help = page.getByTestId("chat-default-model");
  await expect(help).toBeHidden();
  await page.getByRole("button", { name: "回答するモデルの説明" }).click();
  await expect(help).toBeVisible();
  await expect(help).toHaveText(
    "未選択ならテキストモデルで回答し、根拠の図や画像を読むときだけ画像対応モデルを使います。両方選ぶと回答を並べて比較できます。"
  );
  await page.keyboard.press("Escape");
  await expect(help).toBeHidden();
  await expectNoPageOverflow(page);
});

test("既定のテキストモデルが画像対応モデルも兼ねるとき、回答するモデルに画像対応を出す（#888）", async ({
  page,
}) => {
  // 既定のテキストモデルと既定の画像対応モデルが同じ（例: xai.grok-4.3）なら 1 件で kind は text_vision。
  const shared = [{ model_id: "xai.grok-4.3", display_name: "xai.grok-4.3", kind: "text_vision" }];
  const bodies: Record<string, unknown>[] = [];
  await mockChat(page, "bv-1");
  await page.route("**/api/chat/models", (route) => route.fulfill(envelope(shared)));
  await page.goto("/chat");
  await selectSearchAnswerProfile(page, "経理ビュー");
  const chip = page.getByRole("button", { name: "xai.grok-4.3（テキスト・画像対応）" });
  await expect(chip).toBeVisible();
  await expect(page.getByRole("button", { name: "xai.grok-4.3（テキスト）" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "回答するモデルの説明" })).toHaveAccessibleDescription(
    "このモデルは画像対応モデルも兼ね、根拠の図や画像も読んで回答します。"
  );
  await expectNoPageOverflow(page);

  await mockAnswerHistory(page, 0);
  await page.route("**/api/search/models", (route) => route.fulfill(envelope(shared)));
  await page.route("**/api/search/stream", async (route) => {
    bodies.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({ status: 500, json: { data: null, error_messages: [], warning_messages: [] } });
  });
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await enableSearchAnswer(page);
  const modelRow = page.getByTestId("search-answer-model");
  await expect(modelRow.locator("button[aria-pressed]")).toHaveText(["xai.grok-4.3（テキスト・画像対応）"]);
  // 説明は info アイコンの説明として読み上げに結び付く（閉じている間も読める。#901）。
  await expect(modelRow.getByRole("button", { name: "回答するモデルの説明" })).toHaveAccessibleDescription(
    "このモデルは画像対応モデルも兼ね、根拠の図や画像も読んで回答します。"
  );
  // 選ぶと、そのモデルで回答する（比較はしない）。
  await modelRow.getByRole("button", { name: "xai.grok-4.3（テキスト・画像対応）" }).click();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("図の数値は？");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(bodies[0]).toMatchObject({ generate_answer: true, model_id: "xai.grok-4.3" });
  await expectNoPageOverflow(page);
});
