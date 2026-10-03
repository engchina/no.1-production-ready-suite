import { expect, test, type Page } from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { openSidebarNav, closeSidebarNav } from "./_helpers/sidebar-nav";

const profile = {
  id: "sales",
  name: "売上分析",
  description: "売上の集計",
  archived: false,
  allowed_tables: ["APP.SALES"],
  allowed_views: [],
  version: 1,
};
const now = "2026-10-02T22:00:00Z";
interface Turn {
  job_id: string;
  question: string;
  status: string;
  result: unknown;
  error_message?: string;
  error_code?: string;
  created_at: string;
  steps: unknown[];
}
async function setup(page: Page) {
  await mockDatabaseGateReady(page);
  const state = {
    turns: [] as Turn[],
    requests: [] as Record<string, unknown>[],
    failSend: false,
    pending: false,
  };
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({
      json: { data: { items: [profile], total: 1, next_cursor: null } },
    }),
  );
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) =>
    route.fulfill({ json: { data: profile } }),
  );
  await page.route("**/api/nl2sql/chats**", (route) => {
    const conversation = {
      id: "chat-1",
      title: state.turns[0]?.question,
      profile_id: "sales",
      created_at: now,
    };
    return route.fulfill({
      json: {
        data:
          new URL(route.request().url()).pathname === "/api/nl2sql/chats"
            ? {
                items: state.turns.length ? [conversation] : [],
                next_cursor: null,
              }
            : { conversation, turns: state.turns },
      },
    });
  });
  await page.route("**/api/nl2sql/jobs", (route) => {
    const body = route.request().postDataJSON();
    state.requests.push(body);
    if (state.failSend)
      return route.fulfill({
        status: 503,
        json: { error: "モデルへ接続できません。" },
      });
    const id = `chat-${state.turns.length + 1}`;
    const result = {
      generated_sql: `SELECT CATEGORY, SUM(AMOUNT) FROM APP.SALES GROUP BY CATEGORY${state.turns.length ? " ORDER BY SUM(AMOUNT) DESC" : ""}`,
      original_question: body.question,
      explanation: "カテゴリごとの売上合計です。",
      safety: { is_safe: true },
    };
    const turn: Turn = {
      job_id: id,
      question: body.question,
      status: state.pending ? "running" : "done",
      created_at: now,
      steps: [],
      result: state.pending ? null : result,
    };
    state.turns.push(turn);
    return route.fulfill({
      json: {
        data: { job_id: id, status: turn.status, created_at: now, steps: [] },
      },
    });
  });
  await page.route("**/api/nl2sql/jobs/*/cancel", (route) => {
    Object.assign(state.turns.at(-1)!, {
      status: "error",
      error_code: "JOB_CANCELLED",
      error_message: "停止しました。",
    });
    return route.fulfill({ json: { data: state.turns.at(-1) } });
  });
  return state;
}
for (const width of [1280, 375]) {
  test(`SQL を実行せず多輪生成し、履歴とリロードで復元する (${width}px)`, async ({
    page,
  }, testInfo) => {
    const state = await setup(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/chat");
    await expect(
      page.getByRole("heading", { name: "チャット", level: 1 }),
    ).toBeVisible();
    const history = page.getByRole("button", {
      name: "会話の履歴",
      exact: true,
    });
    await expect(history).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("sql-chat-history")).not.toBeVisible();
    const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
    await composer.fill("カテゴリ別売上");
    await composer.press("Enter");
    await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
    await composer.fill("多い順にして");
    await page.getByTestId("sql-chat-send").click();
    await expect(page.getByTestId("sql-chat-turn")).toHaveCount(2);
    await expect(page.locator("pre").last()).toContainText(
      "ORDER BY SUM(AMOUNT) DESC",
    );
    expect(state.requests[0]).toMatchObject({
      generation_only: true,
      profile_id: "sales",
      previous_job_id: null,
    });
    expect(state.requests[1]).toMatchObject({
      generation_only: true,
      previous_job_id: "chat-1",
      question: "多い順にして",
    });
    await page.reload();
    await expect(page.getByTestId("sql-chat-turn")).toHaveCount(2);
    await history.click();
    await expect(
      page
        .getByTestId("sql-chat-history")
        .getByText("カテゴリ別売上", { exact: true }),
    ).toBeVisible();
    if (width < 1024) {
      await page.keyboard.press("Escape");
      await expect(history).toBeFocused();
    } else await history.click();
    await page.getByRole("button", { name: "新しい会話", exact: true }).click();
    await expect(
      page.getByText("どのような SQL を生成しますか？"),
    ).toBeVisible();
    await history.click();
    await page
      .getByTestId("sql-chat-history")
      .getByText("カテゴリ別売上", { exact: true })
      .click();
    if (width >= 1024) await history.click();
    await expect(page.getByTestId("sql-chat-turn")).toHaveCount(2);
    if (width < 1024)
      await expect(
        page.getByRole("dialog", { name: "会話の履歴" }),
      ).toHaveCount(0);
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(0);
    if (width >= 1024)
      expect(
        await page
          .locator("#pr-main")
          .evaluate((el) => el.scrollHeight - el.clientHeight),
      ).toBeLessThanOrEqual(1);
    await composer.focus();
    await expect(composer).toBeInViewport();
    await page.screenshot({
      path: testInfo.outputPath(`chat-${width}.png`),
      fullPage: true,
    });
  });
}
test("生成中は停止でき、失敗時も草稿と入力欄を保持する", async ({ page }) => {
  const state = await setup(page);
  state.pending = true;
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
  await composer.fill("カテゴリ別売上");
  await composer.press("Enter");
  const send = page.getByTestId("sql-chat-send");
  await expect(send).toHaveAccessibleName("停止");
  await composer.fill("次の条件");
  await composer.press("Enter");
  expect(state.requests).toHaveLength(1);
  await send.click();
  await expect(page.getByText("SQL の生成を停止しました")).toBeVisible();
  await expect(send).toHaveAccessibleName("送信");
  await expect(composer).toHaveValue("次の条件");
  state.failSend = true;
  await send.click();
  await expect(page.getByText("モデルへ接続できません。")).toBeVisible();
  await expect(composer).toHaveValue("次の条件");
});
test("AI 活用の先頭のチャットは、生成だけの権限でも利用できる", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: {
        data: {
          ...systemAdminMe,
          is_system_admin: false,
          role_codes: ["CHAT"],
          permissions: ["menu.chat"],
          allowed_profile_ids: ["sales"],
        },
      },
    }),
  );
  await page.goto("/chat");
  const sidebar = await openSidebarNav(page);
  await expect(
    sidebar.getByRole("link", { name: "チャット", exact: true }),
  ).toBeVisible();
  await expect(
    sidebar.getByRole("link", { name: "SQL 生成", exact: true }),
  ).toHaveCount(0);
  await closeSidebarNav(page);
  await page
    .getByRole("textbox", { name: "クエリ", exact: true })
    .fill("カテゴリ別売上");
  await page.getByTestId("sql-chat-send").click();
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
});

test("安全検査でブロックした SQL は未実行のまま表示する", async ({ page }) => {
  const state = await setup(page);
  state.turns.push({
    job_id: "chat-1",
    question: "許可対象外の表を参照",
    status: "error",
    created_at: now,
    steps: [],
    result: {
      generated_sql: "SELECT * FROM PRIVATE.TABLES",
      safety: { is_safe: false },
    },
    error_message: "許可された対象の範囲外です。",
    error_code: "SQL_BLOCKED",
  });
  await page.goto("/chat");
  await page.getByRole("button", { name: "会話の履歴", exact: true }).click();
  await page
    .getByTestId("sql-chat-history")
    .getByText("許可対象外の表を参照", { exact: true })
    .click();
  if ((page.viewportSize()?.width ?? 0) >= 1024)
    await page.getByRole("button", { name: "会話の履歴", exact: true }).click();
  await expect(
    page.getByText("安全検査でブロック", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("許可された対象の範囲外です。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("安全検査済み・未実行", { exact: true }),
  ).toHaveCount(0);
});

test("利用できるプロファイルがないと送信を禁止する", async ({ page }) => {
  const state = await setup(page);
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({
      json: { data: { items: [], total: 0, next_cursor: null } },
    }),
  );
  await page.goto("/chat");
  await expect(
    page.getByText("利用できる業務プロファイルがありません", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "クエリ", exact: true })
    .fill("カテゴリ別売上");
  await expect(page.getByTestId("sql-chat-send")).toHaveAttribute(
    "aria-disabled",
    "true",
  );
  await page.getByRole("textbox", { name: "クエリ", exact: true }).press("Enter");
  expect(state.requests).toHaveLength(0);
});
