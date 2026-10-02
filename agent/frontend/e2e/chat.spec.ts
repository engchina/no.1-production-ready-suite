import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #768: 業務利用者のチャット。会話（スレッド）の続き・出典・使ったツール・承認待ちを確かめる。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

const THREAD_ID = `thread_${"a".repeat(32)}`;

function seedThread(mockApi: MockApi, run: Record<string, unknown>) {
  mockApi.state.runs.push({
    id: "run-chat-seed",
    goal: "契約の更新条件は？",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: THREAD_ID,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    ...run,
  });
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`質問を送ると回答が出て、同じ会話で続けられる (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/chat");

      await expect(page.getByRole("heading", { name: "チャット", level: 1 })).toBeVisible();
      await expect(page.locator("#chat-agent")).toContainText("汎用業務 Agent");
      await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();

      const composer = page.getByRole("textbox", { name: "質問" });
      await composer.fill("今月の売上は？");
      await composer.press("Enter");
      const conversation = page.getByTestId("chat-conversation");
      await expect(conversation.getByText("「今月の売上は？」への回答です。")).toBeVisible();
      await expect(composer).toHaveValue("");
      const first = mockApi.lastRequest("POST", "/api/runs");
      expect(first?.body).toEqual({ goal: "今月の売上は？", agent_id: "default" });

      // 2 回目は同じ会話（thread_id）で送る。
      await composer.fill("先月と比べると？");
      await page.getByTestId("chat-send").click();
      await expect(conversation.getByText("「先月と比べると？」への回答です。")).toBeVisible();
      const second = mockApi.lastRequest("POST", "/api/runs");
      expect((second?.body as { thread_id?: string }).thread_id).toMatch(/^thread_/);

      // 会話の履歴（desktop は左、375px は side sheet）。
      if (viewport.width >= 1024) {
        await expect(page.getByTestId("chat-history").getByText("今月の売上は？")).toBeVisible();
      } else {
        await page.getByRole("button", { name: "会話の履歴" }).click();
        const sheet = page.getByRole("dialog", { name: "会話の履歴" });
        await expect(sheet.getByText("今月の売上は？")).toBeVisible();
        await expect(sheet.getByText("2 往復")).toBeVisible();
        await page.keyboard.press("Escape");
      }
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`chat-${viewport.name}-${theme}.png`), fullPage: true });

      // 新しい会話は thread_id を付けずに送る。
      await page.getByRole("button", { name: "新しい会話" }).first().click();
      await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
    });
  }
}

test("回答の出典と使ったツールを畳んで出し、承認待ちはその場で判断できる", async ({ page, mockApi }) => {
  seedThread(mockApi, {
    status: "waiting_approval",
    steps: [
      {
        id: "step-1",
        run_id: "run-chat-seed",
        kind: "tool",
        status: "completed",
        tool_call: { name: "rag__rag_search", arguments: { query: "契約" } },
      },
      {
        id: "step-2",
        run_id: "run-chat-seed",
        kind: "tool",
        status: "waiting_approval",
        tool_call: { name: "nl2sql__nl2sql_query", arguments: { question: "契約件数" } },
        approval_id: "approval-chat-1",
      },
    ],
    approvals: [
      {
        id: "approval-chat-1",
        run_id: "run-chat-seed",
        step_id: "step-2",
        tool_call: { name: "nl2sql__nl2sql_query", arguments: { question: "契約件数" } },
        status: "pending",
        reason: "承認が必要です。",
        created_at: MOCK_NOW,
      },
    ],
    artifacts: [
      {
        id: "artifact-rag",
        name: "rag__rag_search:step-1",
        kind: "rag_evidence",
        content: {
          answer: "第 5 条",
          citations: [{ file_name: "契約書.pdf", text: "更新は 30 日前までに通知する。" }],
        },
      },
    ],
  });
  await page.goto("/chat");
  await page.getByTestId("chat-history").getByRole("button", { name: /契約の更新条件は？/ }).click();

  const turn = page.getByTestId("chat-turn-run-chat-seed");
  await expect(turn.getByText("ツールの実行に承認が必要です")).toBeVisible();
  await expect(turn.getByText("nl2sql__nl2sql_query を実行します。", { exact: false })).toBeVisible();
  // 承認待ちのあいだは次の質問を送れない。
  await expect(page.getByTestId("chat-composer-hint")).toHaveText(
    "承認待ちのツールがあります。判断が済むと次の質問を送れます。"
  );
  await page.getByRole("textbox", { name: "質問" }).fill("次の質問");
  await expect(page.getByTestId("chat-send")).toBeDisabled();

  await turn.getByText("出典（1）").click();
  await expect(turn.getByText("1. 契約書.pdf")).toBeVisible();
  await turn.getByText("使ったツール（2）").click();
  await expect(turn.getByText("rag__rag_search")).toBeVisible();

  await turn.getByRole("button", { name: "承認して実行" }).click();
  await expect(page.getByText("承認しました")).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/approvals/approval-chat-1/decision")?.body).toEqual({
    approved: true,
  });
});

test("実行に失敗した回答は理由を出す", async ({ page, mockApi }) => {
  seedThread(mockApi, {
    status: "failed",
    events: [
      {
        id: "event-failed",
        run_id: "run-chat-seed",
        type: "runtime.failed",
        message: "モデルの呼び出しに失敗しました（BadRequestError）。",
        payload: {},
        created_at: MOCK_NOW,
      },
    ],
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/chat");
  await page.getByRole("button", { name: "会話の履歴" }).click();
  await page.getByRole("dialog", { name: "会話の履歴" }).getByText("契約の更新条件は？").click();

  const turn = page.getByTestId("chat-turn-run-chat-seed");
  await expect(turn.getByText("回答できませんでした")).toBeVisible();
  await expect(turn.getByText("モデルの呼び出しに失敗しました（BadRequestError）。")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("公開していない業務 Agent はチャットで選べない（#792）", async ({ page, mockApi }) => {
  const published = mockApi.state.agents[0] as Record<string, unknown>;
  mockApi.state.agents.push({
    ...published,
    id: "draft-792",
    name: "下書きの業務 Agent",
    versions: [],
    published_version: null,
    unpublished_changes: true,
  });
  await page.goto("/chat");
  await page.locator("#chat-agent").click();
  await expect(page.getByRole("option", { name: new RegExp(String(published.name)) })).toBeVisible();
  await expect(page.getByRole("option", { name: /下書きの業務 Agent/ })).toHaveCount(0);
  await page.keyboard.press("Escape");

  // 公開した業務 Agent が 1 つも無いときは、公開を管理者に依頼するよう案内する。
  mockApi.state.agents.forEach((agent) => {
    (agent as Record<string, unknown>).published_version = null;
  });
  await page.reload();
  await expect(page.getByText("業務 Agent の公開と権限の付与を管理者に依頼してください。", { exact: false })).toBeVisible();
});
