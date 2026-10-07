import { expect, test, type Page } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

/**
 * NL2SQL のジョブの失敗の文（#1072）。1 文目は利用者の言葉（何が起きたか・次の操作）にし、
 * 例外・Oracle のエラーの元の文（error_detail）とエラーコードは開いた「詳細」に分けて出す
 * （UX 契約 messaging.md §10.3）。
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
const now = "2026-10-02T22:00:00Z";
const failureMessage =
  "SQL の生成に失敗しました。時間をおいてもう一度実行してください。繰り返し失敗するときは、「詳細」の内容を管理者に伝えてください。";
const failureDetail =
  "Select AI の生成に失敗しました: ORA-04027: self-deadlock during automatic validation for object DBMS_CLOUD_AI";

async function mockChatWithFailedTurn(page: Page) {
  await mockDatabaseGateReady(page);
  const conversation = { id: "chat-1", title: "カテゴリ別売上", profile_id: "sales", created_at: now };
  const turn = {
    job_id: "chat-1",
    question: "カテゴリ別売上",
    status: "error",
    generation_only: true,
    created_at: now,
    steps: [],
    result: null,
    error_message: failureMessage,
    error_code: "ORA-04027",
    error_detail: failureDetail,
  };
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({ json: { data: { items: [profile], total: 1, next_cursor: null } } }),
  );
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) =>
    route.fulfill({ json: { data: profile } }),
  );
  await page.route("**/api/nl2sql/chats**", (route) =>
    route.fulfill({
      json: {
        data:
          new URL(route.request().url()).pathname === "/api/nl2sql/chats"
            ? { items: [conversation], next_cursor: null, total: 1, limit: 10 }
            : { conversation, turns: [turn] },
      },
    }),
  );
}

for (const width of [1280, 375]) {
  test(`チャットのジョブの失敗は、利用者向けの文と開いた「詳細」の元の文に分けて出す (${width}px)`, async ({
    page,
  }) => {
    await mockChatWithFailedTurn(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/chat");
    await page.getByRole("button", { name: "会話の履歴", exact: true }).click();
    await page.getByTestId("sql-chat-history").getByText("カテゴリ別売上", { exact: true }).click();
    if (width >= 1024) await page.getByRole("button", { name: "会話の履歴", exact: true }).click();

    const failure = page.getByTestId("nl2sql-job-failure");
    await expect(failure).toBeVisible();
    // 1 文目（本文）には例外・ORA の文を出さない。
    const summary = failure.locator("p").first();
    await expect(summary).toHaveText(failureMessage);
    await expect(summary).not.toContainText("ORA-");
    // 「詳細」は失敗なので開いて出し、エラーコードと元のメッセージを並べる。
    const details = failure.locator("details");
    await expect(details).toHaveAttribute("open", "");
    await expect(details).toContainText("エラーコード");
    await expect(details).toContainText("ORA-04027");
    await expect(details).toContainText("元のメッセージ");
    await expect(details).toContainText(failureDetail);
    // 長い元の文でも横にはみ出さない。
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
