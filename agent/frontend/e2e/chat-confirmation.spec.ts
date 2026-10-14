import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #1839: 回答前の確認。Agent 自身の確認（ask_user_confirmation）とツールの結果の確認（NL2SQL・RAG）を、3 製品共通の
// 確認のカード（ClarificationCard）で出す。答えで続きが動き、スキップは前提を示して回答し、やめると質問を入力欄に戻す。
// 答えた内容は会話の利用者の側に「回答前に確認した内容」で残す。desktop と mobile-375 の 2 project で実行する。

const THREAD_ID = `thread_${"e".repeat(32)}`;
const GOAL = "売上を教えて";

const AGENT_CONFIRMATION = {
  id: "confirmation_agent_1",
  source: "agent",
  call_id: "call-ask",
  items: [
    {
      id: "item_1",
      prompt: "どの期間の売上を見ますか？",
      reason: "期間が質問から決められません。",
      selection: "single",
      options: [
        { id: "option_1", label: "今月", description: "" },
        { id: "option_2", label: "今年度", description: "4 月から今日まで" },
      ],
    },
  ],
  round: 1,
  max_rounds: 2,
  can_skip: true,
  status: "pending",
  answers: [],
  created_at: MOCK_NOW,
};

const RAG_CONFIRMATION = {
  id: "confirmation_rag_1",
  source: "rag",
  items: [
    {
      id: "screen",
      prompt: "どの画面の操作ですか？",
      reason: "「画面」によって回答が変わります。",
      selection: "single",
      options: [
        { id: "option_1", label: "受注入力" },
        { id: "option_2", label: "請求" },
      ],
    },
  ],
  round: 2,
  max_rounds: 2,
  can_skip: true,
  status: "pending",
  answers: [],
  created_at: MOCK_NOW,
};

const NL2SQL_CONFIRMATION = {
  ...RAG_CONFIRMATION,
  id: "confirmation_nl2sql_1",
  source: "nl2sql",
  round: 1,
  can_skip: false,
  items: [
    {
      id: "item-period",
      prompt: "SALES のどの日付の列で期間を絞りますか？",
      reason: "受注と出荷のどちらの日付か決められません。",
      selection: "single",
      options: [
        { id: "c-order", label: "SALES.ORDER_DATE（受注日）" },
        { id: "c-ship", label: "SALES.SHIP_DATE（出荷日）" },
      ],
    },
  ],
};

function seedRun(mockApi: MockApi, id: string, extra: Record<string, unknown>) {
  mockApi.state.runs.push({
    id,
    goal: GOAL,
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    confirmations: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: THREAD_ID,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    // 確認の定数は spec の間で共有するので、mock が状態を書き換えても次の test に残らないよう複製する。
    ...structuredClone(extra),
  });
}

function answer(id: string, text: string) {
  return [{ id: `${id}-answer`, kind: "answer", name: "回答", created_at: MOCK_NOW, content: { text } }];
}

async function openSeedThread(page: Page, title = GOAL) {
  await page.goto("/chat");
  const viewport = page.viewportSize();
  await page.getByTestId("chat-history-toggle").click();
  if (viewport && viewport.width >= 1024) {
    await page.getByTestId("chat-history").getByRole("button", { name: new RegExp(`^${title}`) }).click();
    return;
  }
  await page.getByRole("dialog", { name: "会話の履歴" }).getByText(title).click();
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test.describe("回答前の確認のカード", { tag: "@responsive" }, () => {
  test("Agent 自身の確認をカードで出し、選んで「この内容で回答」で続きが動き、答えを利用者の側に残す", async ({
    page,
    mockApi,
  }, testInfo) => {
    seedRun(mockApi, "run-ask-1", { status: "waiting_confirmation", confirmations: [AGENT_CONFIRMATION] });
    await openSeedThread(page);

    const turn = page.getByTestId("chat-turn-run-ask-1");
    const card = turn.getByTestId("chat-confirmation");
    await expect(card.getByRole("heading", { name: "回答の前に確認させてください" })).toBeVisible();
    await expect(card.getByTestId("chat-confirmation-meta")).toHaveText("確認 1 回目（上限 2 回）");
    await expect(card.getByRole("heading", { name: "どの期間の売上を見ますか？" })).toBeVisible();
    await expect(card.getByText("期間が質問から決められません。")).toBeVisible();
    // 「その他（自由入力）」は常に出る。答えるまで確定は押せず、理由を出す。
    await expect(card.getByLabel("その他（自由入力）")).toBeVisible();
    const confirm = card.getByRole("button", { name: "この内容で回答" });
    await expect(confirm).toBeDisabled();
    await expect(card.getByTestId("chat-confirmation-blocked")).toHaveText(
      "選択肢を選ぶか、「その他（自由入力）」に書くと押せます。"
    );
    // 確認待ちの間は次の質問を送らず、停止はできる。
    await expect(page.getByTestId("chat-send")).toHaveAccessibleName("停止");
    // 状態は色だけで示さない（処理の段階の文で示す）。
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("agent-confirmation-light.png"), fullPage: true });

    // キーボードで選ぶ（ラジオは矢印キーで移る）。
    const option = card.getByRole("radio", { name: /今年度/ });
    await option.focus();
    await page.keyboard.press("Space");
    await expect(option).toBeChecked();
    await expect(confirm).toBeEnabled();
    await confirm.click();

    await expect
      .poll(() => mockApi.lastRequest("POST", "/api/runs/run-ask-1/confirmations/confirmation_agent_1")?.body)
      .toEqual({ action: "answer", answers: [{ item_id: "item_1", option_ids: ["option_2"], other_text: "" }] });
    await expect(turn.getByTestId("chat-confirmation")).toHaveCount(0);
    const record = turn.getByTestId("chat-confirmation-record-confirmation_agent_1");
    await expect(record.getByText("回答前に確認した内容")).toBeVisible();
    await expect(record.getByText("今年度")).toBeVisible();
    await expect(turn.getByText("確認の答えで回答しました。")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("agent-confirmation-answered.png"), fullPage: true });
  });

  test("ダークテーマでも確認のカードと答えの記録を出す", async ({ page, mockApi }, testInfo) => {
    await page.addInitScript(() => {
      window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: "dark" }, version: 0 }));
    });
    seedRun(mockApi, "run-dark-0", {
      goal: "前の質問",
      created_at: "2026-09-01T00:00:00Z",
      artifacts: answer("run-dark-0", "前の回答"),
      confirmations: [{ ...AGENT_CONFIRMATION, id: "confirmation_dark_0", status: "answered", answers: [{ item_id: "item_1", option_ids: ["option_1"], other_text: "" }] }],
    });
    seedRun(mockApi, "run-dark-1", { status: "waiting_confirmation", confirmations: [AGENT_CONFIRMATION] });
    await openSeedThread(page, "前の質問");
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    await expect(page.getByTestId("chat-confirmation-record-confirmation_dark_0").getByText("今月")).toBeVisible();
    await expect(page.getByTestId("chat-turn-run-dark-1").getByTestId("chat-confirmation")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("agent-confirmation-dark.png"), fullPage: true });
  });

  test("「確認せずに回答」は前提を示して回答し、「確認せずに回答しました」を残す", async ({ page, mockApi }) => {
    seedRun(mockApi, "run-ask-2", { status: "waiting_confirmation", confirmations: [AGENT_CONFIRMATION] });
    await openSeedThread(page);
    const turn = page.getByTestId("chat-turn-run-ask-2");
    await expect(turn.getByText("確認せずに回答: 前提を示して回答します。")).toBeVisible();
    await turn.getByRole("button", { name: "確認せずに回答" }).click();
    await expect
      .poll(() => mockApi.lastRequest("POST", "/api/runs/run-ask-2/confirmations/confirmation_agent_1")?.body)
      .toEqual({ action: "skip" });
    await expect(turn.getByTestId("chat-confirmation-record-confirmation_agent_1-skipped")).toHaveText(
      "確認せずに回答しました（前提を示して回答）"
    );
  });

  test("「確認をやめる」は質問を入力欄に戻し、会話には何も残さない", async ({ page, mockApi }) => {
    seedRun(mockApi, "run-before", { goal: "前の質問", artifacts: answer("run-before", "前の回答") , created_at: "2026-09-01T00:00:00Z" });
    seedRun(mockApi, "run-ask-3", { status: "waiting_confirmation", confirmations: [AGENT_CONFIRMATION] });
    await openSeedThread(page, "前の質問");
    const turn = page.getByTestId("chat-turn-run-ask-3");
    await expect(turn.getByText("確認をやめる: 質問は入力欄に戻ります。")).toBeVisible();
    await turn.getByRole("button", { name: "確認をやめる" }).click();
    const composer = page.getByRole("textbox", { name: "質問" });
    await expect(composer).toHaveValue(GOAL);
    await expect(composer).toBeFocused();
    await expect(page.getByTestId("chat-turn-run-ask-3")).toHaveCount(0);
    await expect(page.getByTestId("chat-turn-run-before")).toBeVisible();
  });

  test("RAG の確認はカードで出し、その他（自由入力）の答えで次の回答を作り、質問の代わりに答えを残す", async ({
    page,
    mockApi,
  }) => {
    seedRun(mockApi, "run-rag-1", {
      artifacts: answer("run-rag-1", "どの画面の操作か確かめさせてください。"),
      confirmations: [RAG_CONFIRMATION],
    });
    await openSeedThread(page);
    const turn = page.getByTestId("chat-turn-run-rag-1");
    const card = turn.getByTestId("chat-confirmation");
    await expect(card.getByTestId("chat-confirmation-source")).toHaveText("資料（RAG）の確認事項です。");
    // 上限の回では最後の確認であることを添える。
    await expect(card.getByTestId("chat-confirmation-last-round")).toBeVisible();
    // 取り消しの操作はカードの中の操作（「資料の検索は不要」）。カードの外の取り消しの行は重ねない。
    await expect(card.getByRole("button", { name: "資料の検索は不要" })).toBeVisible();
    await expect(turn.getByRole("group", { name: "確認の取り消し" })).toHaveCount(0);

    await card.getByLabel("その他（自由入力）").fill("在庫照会の画面");
    await card.getByRole("button", { name: "この内容で回答" }).click();
    await expect
      .poll(() => mockApi.lastRequest("POST", "/api/runs/run-rag-1/confirmations/confirmation_rag_1")?.body)
      .toEqual({ action: "answer", answers: [{ item_id: "screen", option_ids: [], other_text: "在庫照会の画面" }] });
    const next = page.getByTestId("chat-turn-run-chat-2");
    const record = next.getByTestId("chat-confirmation-record-run-chat-2");
    await expect(record.getByText("どの画面の操作ですか？")).toBeVisible();
    await expect(record.getByText("その他: 在庫照会の画面")).toBeVisible();
    await expect(next.getByText("確認の答えで回答しました。")).toBeVisible();
    // 答えた確認のカードは閉じる。
    await expect(page.getByTestId("chat-confirmation")).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  });

  test("NL2SQL の確認は「確認せずに回答」を出さず、取り消しの操作をカードに置く", async ({ page, mockApi }) => {
    seedRun(mockApi, "run-sql-1", {
      artifacts: answer("run-sql-1", "SQL を作る前に確認します。"),
      confirmations: [NL2SQL_CONFIRMATION],
    });
    await openSeedThread(page);
    const card = page.getByTestId("chat-turn-run-sql-1").getByTestId("chat-confirmation");
    await expect(card.getByTestId("chat-confirmation-source")).toHaveText("データベース（NL2SQL）の確認事項です。");
    await expect(card.getByRole("radio", { name: "SALES.ORDER_DATE（受注日）" })).toBeVisible();
    await expect(card.getByRole("button", { name: "確認せずに回答" })).toHaveCount(0);
    await expect(card.getByRole("button", { name: "データベースの検索は不要" })).toBeVisible();
    await expect(card.getByRole("button", { name: "確認をやめる" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
});
