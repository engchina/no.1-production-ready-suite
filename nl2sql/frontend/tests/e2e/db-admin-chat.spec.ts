// DB 管理チャット（#2264）。質問から Oracle の SQL を作り、生成した SQL を必ず見せる。SELECT は「実行」で、
// それ以外は実行確認語を入力したときだけ実行する。画面の骨格・入力欄・送信中の往復・SQL の表示・結果の表・
// 処理の経過は「チャット」と同じ共通の部品で描き、会話は保存しない（履歴のパネルを持たない）。
// LLM・Oracle は page.route で決定論の応答に置き換える（表・列の名前は合成のもの）。
// 各 test は playwright.config の desktop（1280px）と mobile-375 の両方で動く。
import { expect, test, type Page, type Request } from "./_helpers/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { openSidebarNav } from "./_helpers/sidebar-nav";

const timing = { created_at: "2026-10-18T00:00:00Z", elapsed_ms: 120 };

function plan(sql: string, overrides: Record<string, unknown> = {}) {
  const select = /^\s*(select|with)\b/i.test(sql);
  return {
    sql,
    statements: [
      {
        index: 1,
        statement_type: select ? "SELECT" : sql.trim().split(/\s+/)[0]!.toUpperCase(),
        sql,
        select_only: select,
        implicit_commit: false,
        blocked_reason: "",
      },
    ],
    read_only: select,
    requires_confirmation: !select,
    confirmation_target: select ? "" : "ADMIN_EXECUTE",
    implicit_commit: false,
    blocked: false,
    blocked_reasons: [],
    ...overrides,
  };
}

function generated(question: string, planData: ReturnType<typeof plan>, explanation = "説明の文です。") {
  return {
    data: {
      question,
      explanation,
      plan: planData,
      context_tables: [],
      model: "test-text-model",
      warnings: [],
      timing,
    },
  };
}

const selectResult = {
  executed: true,
  runtime: "oracle",
  execution_context: "admin_control_plane",
  select_result: {
    columns: ["BANNER_FULL"],
    rows: [{ BANNER_FULL: "Oracle AI Database Release 1.0" }],
    total: 1,
    returned_count: 1,
    has_more: false,
    truncated: false,
    execution_context: "admin_control_plane",
  },
  statements: [
    { index: 1, statement_type: "SELECT", status: "executed", sql: "SELECT 1", row_count: 1, message: "1 rows", elapsed_ms: 5 },
  ],
  committed: false,
  rolled_back: false,
  warnings: [],
  timing,
};

function mutationResult(sql: string, ok = true) {
  return {
    executed: ok,
    runtime: "oracle",
    execution_context: "admin_control_plane",
    statements: [
      ok
        ? { index: 1, statement_type: "DELETE", status: "success", sql, row_count: 3, message: "RowsAffected=3", elapsed_ms: 5 }
        : {
            index: 1,
            statement_type: "DELETE",
            status: "error",
            sql,
            error_message: "ORA-00942: table or view does not exist",
            error_code: "ORA-00942",
            message: "",
            elapsed_ms: 5,
          },
    ],
    committed: ok,
    rolled_back: !ok,
    warnings: [],
    timing,
  };
}

interface ChatMock {
  generateBodies: Record<string, unknown>[];
  executeBodies: Record<string, unknown>[];
  analyzeBodies: Record<string, unknown>[];
}

async function setup(
  page: Page,
  {
    responses,
    execute,
    analyze,
  }: {
    responses: ReturnType<typeof generated>[];
    execute?: (body: Record<string, unknown>) => unknown;
    analyze?: unknown;
  },
): Promise<ChatMock> {
  await mockDatabaseGateReady(page);
  const mock: ChatMock = { generateBodies: [], executeBodies: [], analyzeBodies: [] };
  const body = (request: Request) => JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
  await page.route("**/api/nl2sql/db-admin-chat/generate", (route) => {
    mock.generateBodies.push(body(route.request()));
    const next = responses.shift();
    return next
      ? route.fulfill({ json: next })
      : route.fulfill({ status: 502, json: { detail: "SQL の生成に失敗しました。" } });
  });
  await page.route("**/api/nl2sql/db-admin-chat/execute", (route) => {
    const request = body(route.request());
    mock.executeBodies.push(request);
    return route.fulfill({ json: { data: execute ? execute(request) : selectResult } });
  });
  await page.route("**/api/nl2sql/db-admin-chat/analyze-error", (route) => {
    mock.analyzeBodies.push(body(route.request()));
    return route.fulfill({ json: { data: analyze } });
  });
  return mock;
}

async function ask(page: Page, question: string) {
  const composer = page.locator("#db-admin-chat-composer");
  await composer.fill(question);
  // Enter で送信（Shift+Enter は改行）。チャットと同じ共通の入力欄。
  await composer.press("Enter");
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test("DB 管理チャットは「データ準備」の「管理 SQL を実行」の上にあり、会話の履歴のパネルを持たない", { tag: "@responsive" }, async ({ page }) => {
  await setup(page, { responses: [] });
  await page.goto("/db-admin-chat");
  await expect(page.getByRole("heading", { level: 1, name: "DB 管理チャット" })).toBeVisible();
  await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
  // 会話は保存しないので、履歴の開閉ボタンとパネルを出さない（共通の ChatLayout の history を渡さない）。
  await expect(page.getByTestId("db-admin-chat-history-toggle")).toHaveCount(0);
  await expect(page.getByTestId("db-admin-chat-history")).toHaveCount(0);
  await expect(page.getByTestId("db-admin-chat-panel")).toBeVisible();

  const sidebar = await openSidebarNav(page);
  const expand = sidebar.getByRole("button", { name: "データ準備 を展開" });
  if (await expand.isVisible()) await expand.click();
  const chatLink = sidebar.getByRole("link", { name: "DB 管理チャット" });
  const adminSqlLink = sidebar.getByRole("link", { name: "管理 SQL を実行" });
  await expect(chatLink).toBeVisible();
  const chatBox = await chatLink.boundingBox();
  const adminBox = await adminSqlLink.boundingBox();
  expect(chatBox && adminBox && chatBox.y < adminBox.y).toBeTruthy();
  await expectNoHorizontalOverflow(page);
});

test("SELECT は「実行」を押すと実行し、結果を表で出す。続きの質問は前の質問と SQL を文脈に送る", { tag: "@responsive" }, async ({ page }) => {
  const mock = await setup(page, {
    responses: [
      generated("データベースの版", plan("SELECT BANNER_FULL FROM V$VERSION")),
      generated("表領域の使用状況", plan("SELECT TABLESPACE_NAME, USED_PERCENT FROM DBA_TABLESPACE_USAGE_METRICS")),
    ],
  });
  await page.goto("/db-admin-chat");
  await ask(page, "データベースの版");

  const turn = page.getByTestId("db-admin-chat-turn").first();
  await expect(turn.getByTestId("db-admin-chat-sql")).toHaveText("SELECT BANNER_FULL FROM V$VERSION");
  await expect(turn.getByText("読み取り専用・未実行")).toBeVisible();
  // 生成しただけでは実行しない。
  expect(mock.executeBodies).toHaveLength(0);
  // SELECT は確認語の欄を出さない。
  await expect(turn.getByRole("textbox", { name: "実行確認語" })).toHaveCount(0);
  await turn.getByTestId("db-admin-chat-execute").click();
  await expect(turn.getByTestId("db-admin-chat-result")).toBeVisible();
  await expect(turn.getByText("Oracle AI Database Release 1.0")).toBeVisible();
  await expect(turn.getByText("読み取り専用・実行済み")).toBeVisible();
  expect(mock.executeBodies[0]).toMatchObject({ sql: "SELECT BANNER_FULL FROM V$VERSION", confirmation: "" });

  await ask(page, "表領域の使用状況");
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(2);
  const history = mock.generateBodies[1]!.history as Record<string, string>[];
  expect(history[0]).toMatchObject({ question: "データベースの版", sql: "SELECT BANNER_FULL FROM V$VERSION" });
  expect(history[0]!.outcome).toContain("成功");

  // 「新しい会話」で画面の中の会話を消す（保存しない）。
  await page.getByRole("button", { name: "新しい会話" }).click();
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(0);
  await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("SELECT 以外は実行確認語を入れたときだけ実行し、失敗は AI の分析で直した SQL を確認からやり直す", { tag: "@responsive" }, async ({ page }) => {
  const sql = "DELETE FROM APP.TEMP_ITEMS WHERE QTY = 0";
  const corrected = "DELETE FROM APP.TEMP_ITEMS_V2 WHERE QTY = 0";
  let calls = 0;
  const mock = await setup(page, {
    responses: [generated("在庫が 0 の行を消す", plan(sql, { implicit_commit: false }))],
    execute: (request) => {
      calls += 1;
      return mutationResult(String(request.sql), calls > 1);
    },
    analyze: {
      analysis: "1) エラーの原因: 表がありません 2) 直し方: 表の名前を直す 3) 結論: 直した SQL を実行してください",
      corrected_plan: plan(corrected),
      source: "oci_enterprise_ai",
      model: "test-text-model",
      warnings: [],
    },
  });
  await page.goto("/db-admin-chat");
  await ask(page, "在庫が 0 の行を消す");
  const turn = page.getByTestId("db-admin-chat-turn").first();
  await expect(turn.getByText("確認語が必要・未実行")).toBeVisible();
  await expect(turn.getByText("この SQL はデータベースを変更します。", { exact: false })).toBeVisible();
  const confirmation = turn.getByRole("textbox", { name: "実行確認語" });
  const execute = turn.getByTestId("db-admin-chat-execute");
  await expect(execute).toBeDisabled();
  await confirmation.fill("admin_execute");
  await expect(execute).toBeDisabled();
  await confirmation.fill("ADMIN_EXECUTE");
  await expect(execute).toBeEnabled();
  await execute.click();
  expect(mock.executeBodies[0]).toMatchObject({ sql, confirmation: "ADMIN_EXECUTE" });
  await expect(turn.getByText("ORA-00942: table or view does not exist").first()).toBeVisible();
  // 実行の確認は 1 回ごと。
  await expect(confirmation).toHaveValue("");

  await turn.getByTestId("db-admin-chat-analyze").click();
  await expect(turn.getByTestId("db-admin-chat-corrected-sql")).toHaveText(corrected);
  expect(mock.analyzeBodies[0]).toMatchObject({ sql, question: "在庫が 0 の行を消す" });
  await turn.getByTestId("db-admin-chat-use-corrected").click();

  const updated = page.getByTestId("db-admin-chat-turn").first();
  await expect(updated.getByTestId("db-admin-chat-sql")).toHaveText(corrected);
  await expect(updated.getByText("AI のエラー分析で修正した SQL です。")).toBeVisible();
  // 直した SQL も確認語からやり直す。
  await expect(updated.getByRole("textbox", { name: "実行確認語" })).toHaveValue("");
  await expect(updated.getByTestId("db-admin-chat-execute")).toBeDisabled();
  await updated.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await updated.getByTestId("db-admin-chat-execute").click();
  expect(mock.executeBodies[1]).toMatchObject({ sql: corrected, confirmation: "ADMIN_EXECUTE" });
  await expect(updated.getByText("確認語で実行済み")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("実行できない SQL は理由を出し、実行の操作を出さない", { tag: "@responsive" }, async ({ page }) => {
  const sql = "ALTER SYSTEM SET OPEN_CURSORS = 300";
  await setup(page, {
    responses: [
      generated(
        "カーソルの上限を変える",
        plan(sql, {
          blocked: true,
          blocked_reasons: ["ALTER SYSTEM（インスタンスの設定・セッションの強制終了など）は実行できません。"],
        }),
      ),
    ],
  });
  await page.goto("/db-admin-chat");
  await ask(page, "カーソルの上限を変える");
  const turn = page.getByTestId("db-admin-chat-turn").first();
  await expect(turn.getByText("この SQL は実行できません")).toBeVisible();
  await expect(turn.getByText("実行できない SQL")).toBeVisible();
  await expect(turn.getByTestId("db-admin-chat-execute")).toHaveCount(0);
  await expect(turn.getByRole("textbox", { name: "実行確認語" })).toHaveCount(0);
});

test("生成に失敗した質問は残して「再送信」を出す", { tag: "@responsive" }, async ({ page }) => {
  const mock = await setup(page, { responses: [] });
  await page.goto("/db-admin-chat");
  await ask(page, "データベースの版");
  const pending = page.getByTestId("db-admin-chat-pending-turn");
  await expect(pending.getByText("データベースの版")).toBeVisible();
  await expect(page.getByTestId("db-admin-chat-send-error")).toBeVisible();
  await page.getByRole("button", { name: "再送信" }).click();
  await expect.poll(() => mock.generateBodies.length).toBe(2);
});

test("DB 管理チャットの権限の無い利用者には出ず、画面も開けない", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: {
        data: {
          ...systemAdminMe,
          is_system_admin: false,
          role_codes: ["ADMIN_SQL_OPERATOR"],
          permissions: ["menu.admin_sql"],
        },
      },
    }),
  );
  await page.goto("/admin-sql");
  const sidebar = await openSidebarNav(page);
  const expand = sidebar.getByRole("button", { name: "データ準備 を展開" });
  if (await expand.isVisible()) await expand.click();
  await expect(sidebar.getByRole("link", { name: "管理 SQL を実行" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "DB 管理チャット" })).toHaveCount(0);
  await page.goto("/db-admin-chat");
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();
});
