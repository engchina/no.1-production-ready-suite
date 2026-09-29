import { expect, type Page, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

/**
 * RAG 検索・回答の不足の回帰テスト（#304）。
 * 参照 KB が 0 件の業務ビューでは検索・チャットしない、
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
  "この業務ビューには参照するナレッジベースがありません。業務ビューの設定でナレッジベースを追加してください。";

const summary = (index: number) => ({
  trace_id: `trace-${index}`,
  business_view_id: "bv-1",
  surface: "search",
  answer_engine: "docrag",
  question: `質問 ${index} 番`,
  rewritten_question: null,
  confidence: null,
  created_at: "2026-09-25T01:00:00Z",
});

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/business-views**", async (route) => {
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

async function selectView(page: Page, name: RegExp) {
  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  // 参照 KB なしの業務ビューは既定で隠れるため、表示に切り替えてから選ぶ。
  const hideEmpty = page.getByRole("checkbox", { name: "参照 KB なしを隠す" });
  if (await hideEmpty.isChecked()) await hideEmpty.uncheck();
  await page.getByRole("listbox", { name: /対象の業務ビュー/ }).getByRole("option", { name }).click();
  await page.keyboard.press("Escape");
}

test("業務ビューの選択も、選んでも開いたままで「完了」・Esc で閉じて入力欄に戻る（#316）", async ({
  page,
}) => {
  await mockAnswerHistory(page, 0);
  await page.goto("/search");

  const combobox = page.getByRole("combobox", { name: /対象の業務ビュー/ });
  const listbox = page.getByRole("listbox", { name: /対象の業務ビュー/ });
  await combobox.click();
  await listbox.getByRole("option", { name: /経理ビュー/ }).click();
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option", { name: /経理ビュー/ })).toHaveAttribute(
    "aria-selected",
    "true"
  );

  await page.getByRole("button", { name: "完了" }).click();
  await expect(listbox).toHaveCount(0);
  await expect(combobox).toBeFocused();
  await expect(page.getByLabel("経理ビュー を選択から外す")).toBeVisible();

  // 一覧の中のチェックボックスにフォーカスがあっても Esc で閉じて入力欄へ戻る。
  await combobox.press("ArrowDown");
  const hideEmpty = page.getByRole("checkbox", { name: "参照 KB なしを隠す" });
  await hideEmpty.focus();
  await page.keyboard.press("Escape");
  await expect(listbox).toHaveCount(0);
  await expect(combobox).toBeFocused();
  await expectNoPageOverflow(page);
});

test("参照 KB が 0 件の業務ビューでは理由を示し、検索を送らない", async ({ page }) => {
  let streamRequests = 0;
  await mockAnswerHistory(page, 0);
  await page.route("**/api/search/stream", async (route) => {
    streamRequests += 1;
    await route.fulfill({ status: 500, json: { data: null, error_messages: [], warning_messages: [] } });
  });

  await page.goto("/search");
  await selectView(page, /準備中ビュー/);
  await expect(page.getByRole("alert").filter({ hasText: NO_KB_MESSAGE })).toBeVisible();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限は？");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  // 送信せず、検索前の案内のまま（回答の生成を始めない）。
  await expect(page.getByText("索引済みドキュメントを自然言語で検索します。").nth(1)).toBeVisible();
  await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0);
  expect(streamRequests).toBe(0);
  await expectNoPageOverflow(page);

  // KB のある業務ビューを足せば理由は消える（KB のある業務ビューの KB だけを検索する）。
  await selectView(page, /経理ビュー/);
  await expect(page.getByText(NO_KB_MESSAGE)).toHaveCount(0);
});

test("backend が参照 KB のない業務ビューを 409 で断ったら、その理由を表示する", async ({ page }) => {
  await mockAnswerHistory(page, 0);
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 409,
      json: { data: null, error_messages: [NO_KB_MESSAGE], warning_messages: [] },
    })
  );

  await page.goto("/search");
  await selectView(page, /経理ビュー/);
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限は？");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect(page.getByText(NO_KB_MESSAGE)).toBeVisible();
});

test("RAG 検索画面には DocRAG の回答履歴の一覧を出さない（#444）", async ({ page }) => {
  await mockAnswerHistory(page, 4);

  await page.goto("/search");
  await selectView(page, /経理ビュー/);
  await expect(page.getByRole("textbox", { name: "RAG 検索" })).toBeVisible();
  await expect(page.getByText("DocRAG の回答履歴")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "回答履歴のページ" })).toHaveCount(0);
});

function chatMessage(role: "USER" | "ASSISTANT", id: string, traceId: string | null) {
  return {
    message_id: id,
    conversation_id: "conv-1",
    role,
    content: role === "USER" ? "経費の上限は？" : "経費の上限は 10 万円です。",
    model: role === "USER" ? null : "m1",
    citations: [],
    guardrail_warnings: [],
    trace_id: traceId,
    status: "COMPLETE",
    reply_to_message_id: role === "USER" ? null : "u1",
    created_at: "2026-01-01T00:00:00Z",
  };
}

async function mockChat(page: Page, businessViewId: string) {
  const messages = [chatMessage("USER", "u1", null), chatMessage("ASSISTANT", "a1", "trace-chat")];
  const conversation = {
    id: "conv-1",
    business_view_id: businessViewId,
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

test("チャットは参照 KB が 0 件の業務ビューで理由を示し、送信できない", async ({ page }) => {
  await mockChat(page, "bv-empty");
  await page.route("**/api/search/answers**", (route) =>
    route.fulfill(envelope({ items: [], total: 0, limit: 1, offset: 0, has_next: false }))
  );

  await page.goto("/chat");
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "準備中ビュー" }).click();
  const banner = page.getByRole("status").filter({ hasText: "参照するナレッジベースがありません" });
  await expect(banner).toBeVisible();
  await expect(banner.getByRole("button", { name: "業務ビューの設定を開く" })).toBeVisible();
  await page.getByRole("list", { name: "会話" }).getByRole("button").first().click();
  await page.getByRole("textbox", { name: /メッセージ/ }).fill("経費の上限は？");
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
          citations: [],
          docrag: {},
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
  await page.getByRole("combobox", { name: "業務ビュー" }).click();
  await page.getByRole("option", { name: "経理ビュー" }).click();
  await page.getByRole("list", { name: "会話" }).getByRole("button").first().click();
  await expect(page.getByText("DocRAG の根拠と実行記録")).toBeVisible();
  expect(requested).toContainEqual(["trace-chat"]);

  // 保存された回答は、チャットの「DocRAG の根拠と実行記録」から確認を通して削除できる（#147）。
  await page.getByText("DocRAG の根拠と実行記録").click();
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
  await expect(page.getByText("DocRAG の根拠と実行記録")).toHaveCount(0);
});
