// チャットで生成した SQL の実行（#1154）。実行は明示の操作で、結果は同じ吹き出しの SQL の下に、要約・
// 先頭の行のプレビュー（表の中で縦横スクロール）・「すべての行を見る」・「CSV をダウンロード」で出す。
// 各 test は playwright.config の desktop（1280px）と mobile-375 の両方で動く。
import { readFile } from "node:fs/promises";

import { expect, test, type Page } from "./_helpers/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { expectSingleSpinner } from "./_helpers/single-spinner";

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
const now = "2026-10-05T05:00:00Z";
const SQL = "SELECT * FROM APP.SALES";
const COLUMNS = [
  "SALE_ID",
  "CATEGORY",
  "AMOUNT",
  "NOTE",
  "CUSTOMER_NAME",
  "REGION",
  "CREATED_AT",
  "DESCRIPTION",
];

function rows(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    SALE_ID: index + 1,
    CATEGORY: `カテゴリ${(index % 4) + 1}`,
    AMOUNT: (index + 1) * 1000,
    NOTE: index === 0 ? null : `備考${index}`,
    CUSTOMER_NAME: `顧客 ${index + 1}`,
    REGION: ["東日本", "西日本"][index % 2],
    CREATED_AT: "2026-10-01T09:00:00",
    DESCRIPTION: `とても長い説明の文字列です。${"説明".repeat(40)}`,
  }));
}

function execution(rowCount: number, overrides: Record<string, unknown> = {}) {
  return {
    job_id: "chat-1",
    status: "done",
    executed_at: now,
    elapsed_ms: 800,
    executable_sql: SQL,
    results: {
      columns: COLUMNS,
      rows: rows(rowCount),
      total: rowCount,
      returned_count: rowCount,
      has_more: false,
      truncated: false,
      execution_context: "oracle_data_plane",
      vpd_context_enforced: false,
    },
    row_limit: 1000,
    max_cell_chars: 2000,
    cells_truncated: false,
    history_id: "history-1",
    ...overrides,
  };
}

interface Setup {
  turns: Record<string, unknown>[];
  executeRequests: string[];
  executeResponse: () => { status?: number; json: unknown };
  gate: Promise<void> | null;
}

async function setup(page: Page, options: { safe?: boolean; me?: Record<string, unknown> } = {}) {
  await mockDatabaseGateReady(page);
  if (options.me)
    await page.route("**/api/auth/me", (route) =>
      route.fulfill({ json: { data: { ...systemAdminMe, ...options.me } } }),
    );
  const state: Setup = {
    turns: [],
    executeRequests: [],
    executeResponse: () => ({ json: { data: execution(60) } }),
    gate: null,
  };
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({ json: { data: { items: [profile], total: 1, next_cursor: null } } }),
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
            ? { items: state.turns.length ? [conversation] : [], next_cursor: null }
            : { conversation, turns: state.turns },
      },
    });
  });
  await page.route("**/api/nl2sql/jobs", (route) => {
    const body = route.request().postDataJSON();
    const safe = options.safe ?? true;
    const id = `chat-${state.turns.length + 1}`;
    state.turns.push({
      job_id: id,
      question: body.question,
      engine: body.engine,
      conversation_id: "chat-1",
      generation_only: true,
      status: safe ? "done" : "error",
      error_code: safe ? undefined : "SQL_BLOCKED",
      error_message: safe ? undefined : "SELECT 以外の SQL は実行できません。",
      created_at: now,
      steps: [],
      result: {
        generated_sql: safe ? SQL : "DELETE FROM APP.SALES",
        original_question: body.question,
        explanation: "売上の明細です。",
        safety: { is_safe: safe },
      },
    });
    return route.fulfill({
      json: { data: { job_id: id, status: safe ? "done" : "error", created_at: now, steps: [] } },
    });
  });
  await page.route("**/api/nl2sql/jobs/*/execute", async (route) => {
    state.executeRequests.push(new URL(route.request().url()).pathname);
    if (state.gate) await state.gate;
    const response = state.executeResponse();
    return route.fulfill({ status: response.status ?? 200, json: response.json });
  });
  return state;
}

async function sendQuestion(page: Page, question = "売上の明細") {
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
  await composer.fill(question);
  await composer.press("Enter");
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
  return page.getByTestId("sql-chat-turn").last();
}

async function expectNoPageOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
}

test("実行は明示の操作で、実行中は押したボタンだけが回り、結果は吹き出しの中の表で縦横にスクロールする", async ({
  page,
}, testInfo) => {
  const state = await setup(page);
  let release!: () => void;
  state.gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  // 空の状態の案内は維持する（送信では実行しない）。
  await page.goto("/chat");
  await expect(page.getByText("SQL は自動で実行されません。", { exact: false })).toBeVisible();
  const turn = await sendQuestion(page);
  expect(state.executeRequests).toEqual([]);

  const run = turn.getByTestId("sql-chat-execute");
  await expect(run).toHaveAccessibleName("実行");
  await run.click();
  // 実行中: ラベルは変えず、アイコンがスピナーになる。経過時間は結果の領域の 1 か所。
  await expect(turn.getByTestId("sql-chat-execution-running")).toBeVisible();
  await expect(run).toHaveAccessibleName("実行");
  await expect(run).toHaveAttribute("aria-disabled", "true");
  await expectSingleSpinner(page, run);
  release();

  const region = turn.getByTestId("sql-chat-execution");
  await expect(region.getByTestId("sql-chat-result-summary")).toHaveText("60 行・8 列・0.8 秒");
  expect(state.executeRequests).toEqual(["/api/nl2sql/jobs/chat-1/execute"]);
  await expect(turn.getByText("安全検査済み・実行済み")).toBeVisible();
  await expect(run).toHaveAccessibleName("もう一度実行");

  // プレビューは先頭の 50 行。表の中で縦にスクロールし、吹き出し・ページを伸ばさない。
  const table = region.getByTestId("sql-chat-result-table");
  await expect(table.locator("tbody tr")).toHaveCount(50);
  await expect(region.getByTestId("sql-chat-result-preview-note")).toContainText("先頭の 50 行");
  const scroll = region.getByTestId("sql-chat-result-scroll");
  const box = await scroll.evaluate((element) => ({
    vertical: element.scrollHeight - element.clientHeight,
    horizontal: element.scrollWidth - element.clientWidth,
    height: element.getBoundingClientRect().height,
  }));
  expect(box.vertical).toBeGreaterThan(0);
  expect(box.horizontal).toBeGreaterThan(0);
  // 表頭は固定（表の中を縦にスクロールしても列名がスクロール領域の上端に残る）。
  await scroll.scrollIntoViewIfNeeded();
  await scroll.evaluate((element) => element.scrollTo({ top: 400 }));
  await expect
    .poll(() =>
      scroll.evaluate((element) => {
        const header = element.querySelector("thead th")!.getBoundingClientRect().top;
        return Math.abs(header - element.getBoundingClientRect().top);
      }),
    )
    .toBeLessThanOrEqual(2);
  // NULL は空と区別して「NULL」と出し、数値の列は右寄せ。
  await scroll.evaluate((element) => element.scrollTo({ top: 0 }));
  await expect(table.locator("tbody tr").first().getByText("NULL", { exact: true })).toBeVisible();
  await expect(table.locator("tbody tr").first().locator("td").nth(2)).toHaveClass(/text-right/);
  // 長い値は 1 行で省略し、title で全文を出す。
  const longCell = table.locator("tbody tr").first().locator("td").nth(7).locator("span");
  await expect(longCell).toHaveAttribute("title", /とても長い説明の文字列です。/);
  await expectNoPageOverflow(page);

  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await page.evaluate((dark) => document.documentElement.classList.toggle("dark", dark), colorScheme === "dark");
    await turn.screenshot({ path: testInfo.outputPath(`chat-execution-${colorScheme}.png`) });
  }
});

test("「すべての行を見る」は広いシートでページを送り、CSV は取得した行を書き出す", async ({
  page,
}, testInfo) => {
  await setup(page);
  const turn = await sendQuestion(page);
  await turn.getByTestId("sql-chat-execute").click();
  const region = turn.getByTestId("sql-chat-execution");
  await expect(region.getByTestId("sql-chat-result-summary")).toHaveText("60 行・8 列・0.8 秒");

  const viewAll = region.getByRole("button", { name: "すべての行を見る" });
  await viewAll.click();
  const sheet = page.getByRole("dialog", { name: "実行結果（60 行）" });
  await expect(sheet).toBeVisible();
  const viewport = page.viewportSize()!;
  const sheetBox = (await sheet.boundingBox())!;
  if (viewport.width < 640) expect(sheetBox.width).toBeGreaterThanOrEqual(viewport.width - 1);
  else expect(sheetBox.width).toBeGreaterThan(viewport.width * 0.6);
  const pagination = sheet.getByTestId("sql-chat-result-all-pagination");
  await expect(pagination).toContainText("1-10 / 60 件");
  await expect(sheet.getByTestId("sql-chat-result-all-table").locator("tbody tr")).toHaveCount(10);
  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("11-20 / 60 件");
  // 1 ページの行数を選べる（ページングで描く行を絞る。仮想化はしない）。
  await sheet.getByRole("combobox", { name: "1 ページの行数" }).click();
  await page.getByRole("option", { name: "50 行" }).click();
  await expect(pagination).toContainText("1-50 / 60 件");
  // 表頭固定・表の中のスクロール。シートの中の全文は折り返して読める。
  const allScroll = sheet.getByTestId("sql-chat-result-all-scroll");
  expect(await allScroll.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(0);
  await sheet.screenshot({ path: testInfo.outputPath("chat-execution-sheet.png") });

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    sheet.getByRole("button", { name: "CSV をダウンロード" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^nl2sql-chat-result-\d{8}-\d{6}\.csv$/);
  const csv = await readFile((await download.path())!, "utf8");
  const lines = csv.replace(/^\uFEFF/, "").trimEnd().split("\r\n");
  expect(lines[0]).toBe(COLUMNS.join(","));
  expect(lines).toHaveLength(61);
  expect(lines[1]).toContain("1,カテゴリ1,1000,,顧客 1");

  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(viewAll).toBeFocused();
});

test("上限で打ち切った結果は明示し、すべての行は「SELECT SQL を実行」で SQL を入れて開く", async ({
  page,
}) => {
  const state = await setup(page);
  state.executeResponse = () => ({
    json: {
      data: execution(1000, {
        elapsed_ms: 1200,
        results: {
          ...execution(1000).results,
          has_more: true,
          truncated: true,
        },
      }),
    },
  });
  const turn = await sendQuestion(page);
  await turn.getByTestId("sql-chat-execute").click();
  const region = turn.getByTestId("sql-chat-execution");
  await expect(region.getByTestId("sql-chat-result-summary")).toHaveText(
    "先頭の 1,000 行を取得しました（さらに行があります）・8 列・1.2 秒",
  );
  const truncated = region.getByTestId("sql-chat-result-truncated");
  await expect(truncated).toContainText("1 回に取得するのは先頭の 1,000 行までです。");
  await expect(truncated).toContainText("表示と CSV は取得した行だけです");
  await expect(truncated).toContainText("「SELECT SQL を実行」で取得件数上限を指定して実行してください");
  await expect(region.getByTestId("sql-chat-result-table").locator("tbody tr")).toHaveCount(50);
  await truncated.getByRole("link", { name: "SELECT SQL を実行で開く" }).click();
  await expect(page).toHaveURL(/\/direct-sql$/);
  await expect(page.locator("#direct-sql-input")).toHaveValue(SQL);
});

test("0 行は「該当する行はありません」、実行の失敗は吹き出しの中の danger の Banner で詳細を畳む", async ({
  page,
}) => {
  const state = await setup(page);
  state.executeResponse = () => ({ json: { data: execution(0) } });
  const turn = await sendQuestion(page);
  await turn.getByTestId("sql-chat-execute").click();
  const region = turn.getByTestId("sql-chat-execution");
  await expect(region.getByTestId("sql-chat-result-summary")).toHaveText(
    "該当する行はありません・8 列・0.8 秒",
  );
  await expect(region.getByTestId("sql-chat-result-table")).toHaveCount(0);
  await expect(region.getByRole("button", { name: "CSV をダウンロード" })).toHaveCount(0);

  state.executeResponse = () => ({
    json: {
      data: execution(0, {
        status: "error",
        results: { columns: [], rows: [], total: 0 },
        error_message:
          "生成した SQL の実行に失敗しました。生成した SQL と「詳細」の Oracle のエラーを確認し、質問を言い換えて実行し直してください。",
        error_code: "ORA-00942",
        error_detail: "SELECT の実行に失敗しました: ORA-00942: table or view does not exist",
      }),
    },
  });
  await turn.getByTestId("sql-chat-execute").click();
  const failure = region.getByTestId("sql-chat-execution-error");
  await expect(failure.getByRole("alert")).toContainText("生成した SQL の実行に失敗しました。");
  // 技術的な詳細（ORA のコードと元の文）は「詳細」に畳み、失敗なので開いて出す。
  await expect(failure.getByText("ORA-00942", { exact: true })).toBeVisible();
  await expect(failure.getByTestId("nl2sql-job-failure").locator("p").first()).not.toContainText("ORA-");

  // 実行の要求の失敗（生成の後に安全検査を通らなくなった等の 400）は API の失敗の Banner。
  state.executeResponse = () => ({
    status: 400,
    json: { detail: "安全検査を通っていない SQL は実行できません。" },
  });
  await turn.getByTestId("sql-chat-execute").click();
  await expect(region.getByTestId("sql-chat-execution-request-error")).toContainText(
    "安全検査を通っていない SQL は実行できません。",
  );
});

test("安全検査で遮断した SQL（DML）は実行できない", async ({
  page,
}) => {
  await setup(page, { safe: false });
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
  await composer.fill("売上を消して");
  await composer.press("Enter");
  const turn = page.getByTestId("sql-chat-turn").last();
  await expect(turn.getByText("安全検査でブロック")).toBeVisible();
  await expect(turn.getByTestId("sql-chat-execute")).toHaveCount(0);
  await expect(turn.getByTestId("sql-chat-execution")).toHaveCount(0);
});

test("実行の権限が無い利用者は実行できず、理由を吹き出しの中に出す", async ({ page }) => {
  await setup(page, {
    me: {
      is_system_admin: false,
      role_codes: ["CHAT"],
      permissions: ["menu.chat"],
      allowed_profile_ids: ["sales"],
    },
  });
  const turn = await sendQuestion(page);
  await expect(turn.getByTestId("sql-chat-execute")).toHaveCount(0);
  await expect(turn.getByText("SQL を実行するには「SELECT SQL 実行」の権限が必要です。", { exact: false })).toBeVisible();
});

test("会話を開き直したときは前回の実行の要約と「もう一度実行」を出す（行は保存しない）", async ({
  page,
}) => {
  const state = await setup(page);
  state.turns.push({
    job_id: "chat-1",
    question: "売上の明細",
    conversation_id: "chat-1",
    generation_only: true,
    status: "done",
    created_at: now,
    steps: [],
    result: {
      generated_sql: SQL,
      original_question: "売上の明細",
      explanation: "売上の明細です。",
      safety: { is_safe: true },
    },
    last_execution: {
      status: "done",
      executed_at: "2026-10-05T05:03:00Z",
      elapsed_ms: 800,
      row_count: 12,
      column_count: 8,
      has_more: false,
      history_id: "history-1",
    },
  });
  await page.goto("/chat");
  const history = page.getByRole("button", { name: "会話の履歴", exact: true });
  await history.click();
  await page.getByTestId("sql-chat-history").getByText("売上の明細", { exact: true }).click();
  const turn = page.getByTestId("sql-chat-turn");
  await expect(turn.getByTestId("sql-chat-last-execution")).toHaveText(
    "前回の実行（10/5 14:03）: 12 行・8 列。結果の行は保存していないため、見るにはもう一度実行してください。",
  );
  await expect(turn.getByText("安全検査済み・実行済み")).toBeVisible();
  const run = turn.getByTestId("sql-chat-execute");
  await expect(run).toHaveAccessibleName("もう一度実行");
  await run.click();
  await expect(turn.getByTestId("sql-chat-result-summary")).toHaveText("60 行・8 列・0.8 秒");
  await expect(turn.getByTestId("sql-chat-last-execution")).toHaveCount(0);
  expect(state.executeRequests).toEqual(["/api/nl2sql/jobs/chat-1/execute"]);
});
