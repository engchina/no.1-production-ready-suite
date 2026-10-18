// DB 管理チャット（#2264）。質問から Oracle の SQL を作り、生成した SQL を必ず見せる。SELECT は「実行」で、
// それ以外は実行確認語を入力したときだけ実行する。画面の骨格・会話の履歴・入力欄・送信中の往復・SQL の表示・
// 結果の表・処理の経過は「チャット」と同じ共通の部品で描く。会話はサーバーが保存し、チャットと同じ会話の
// 履歴のパネル（開き直し・名前の変更・削除・新しい会話）を持つ（#2319）。
// LLM・Oracle・会話の保存は page.route の決定論の mock に置き換える（表・列の名前は合成のもの）。
// 各 test は playwright.config の desktop（1280px）と mobile-375 の両方で動く。
import { expect, test, type Page, type Request, type Route } from "./_helpers/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { openSidebarNav } from "./_helpers/sidebar-nav";

const timing = { created_at: "2026-10-18T00:00:00Z", finished_at: "2026-10-18T00:00:01Z", elapsed_ms: 120 };

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

type Plan = ReturnType<typeof plan>;

interface Generated {
  plan: Plan;
  explanation?: string;
}

function generated(planData: Plan, explanation = "説明の文です。"): Generated {
  return { plan: planData, explanation };
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

type Json = Record<string, unknown>;

interface StoredTurn extends Json {
  turn_id: string;
  question: string;
  plan: Plan;
  revision: number;
  corrected: boolean;
  last_execution: Json | null;
}

interface StoredConversation {
  id: string;
  title: string;
  created_at: string;
  owned: boolean;
  actor_display_name: string;
  turns: StoredTurn[];
}

interface ChatMock {
  generateBodies: Json[];
  executeBodies: Json[];
  analyzeBodies: Json[];
  planBodies: Json[];
  conversations: StoredConversation[];
}

function storedTurn(turnId: string, question: string, next: Generated): StoredTurn {
  return {
    turn_id: turnId,
    question,
    created_at: "2026-10-18T00:00:00Z",
    generated_at: "2026-10-18T00:00:02Z",
    explanation: next.explanation ?? "",
    warnings: [],
    context_tables: [],
    model: "test-text-model",
    plan: next.plan,
    revision: 0,
    corrected: false,
    last_execution: null,
  };
}

/** 保存した会話（開き直しの確かめに使う）。 */
function savedConversation(
  id: string,
  title: string,
  turns: StoredTurn[],
  overrides: Partial<StoredConversation> = {},
): StoredConversation {
  return { id, title, created_at: "2026-10-17T09:30:00Z", owned: true, actor_display_name: "", turns, ...overrides };
}

/** 会話の保存・生成・実行・分析を、サーバーと同じ契約の mock にする。 */
async function setup(
  page: Page,
  {
    responses = [],
    execute,
    analyze,
    conversations = [],
  }: {
    responses?: Generated[];
    execute?: (body: Json, turn: StoredTurn) => Json;
    analyze?: unknown;
    conversations?: StoredConversation[];
  } = {},
): Promise<ChatMock> {
  await mockDatabaseGateReady(page);
  const mock: ChatMock = {
    generateBodies: [],
    executeBodies: [],
    analyzeBodies: [],
    planBodies: [],
    conversations: [...conversations],
  };
  const body = (request: Request) => JSON.parse(request.postData() ?? "{}") as Json;
  const summary = (item: StoredConversation) => ({
    id: item.id,
    title: item.title,
    created_at: item.created_at,
    owned: item.owned,
    actor_display_name: item.actor_display_name,
  });
  const find = (id: string) => mock.conversations.find((item) => item.id === id);
  await page.route("**/api/nl2sql/db-admin-chat/conversations**", (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/conversations")[1]!.split("/").filter(Boolean).map(decodeURIComponent);
    if (parts.length === 0) {
      const offset = Number(url.searchParams.get("offset") ?? "0");
      const limit = Number(url.searchParams.get("limit") ?? "25");
      const items = mock.conversations.slice(offset, offset + limit).map(summary);
      return route.fulfill({
        json: {
          data: { items, total: mock.conversations.length, limit, offset, has_next: offset + limit < mock.conversations.length },
        },
      });
    }
    const conversation = find(parts[0]!);
    if (!conversation) return route.fulfill({ status: 404, json: { detail: "会話が見つかりません。" } });
    if (parts[1] === "turns" && parts[3] === "plan") {
      const update = body(request);
      mock.planBodies.push(update);
      const turn = conversation.turns.find((item) => item.turn_id === parts[2])!;
      Object.assign(turn, {
        plan: plan(String(update.sql)),
        revision: turn.revision + 1,
        corrected: true,
        last_execution: null,
      });
      return route.fulfill({ json: { data: turn } });
    }
    if (request.method() === "PATCH") {
      conversation.title = String(body(request).title);
      return route.fulfill({ json: { data: summary(conversation) } });
    }
    if (request.method() === "DELETE") {
      mock.conversations = mock.conversations.filter((item) => item.id !== conversation.id);
      return route.fulfill({ json: { data: null } });
    }
    return route.fulfill({ json: { data: { conversation: summary(conversation), turns: conversation.turns } } });
  });
  await page.route("**/api/nl2sql/db-admin-chat/generate", (route) => {
    const request = body(route.request());
    mock.generateBodies.push(request);
    const next = responses.shift();
    if (!next) return route.fulfill({ status: 502, json: { detail: "SQL の生成に失敗しました。" } });
    const turnId = String(request.client_turn_id);
    const id = String(request.conversation_id || turnId);
    let conversation = find(id);
    if (!conversation) {
      conversation = savedConversation(id, String(request.question), [], { created_at: "2026-10-18T00:00:00Z" });
      mock.conversations.unshift(conversation);
    }
    const turn = storedTurn(turnId, String(request.question), next);
    conversation.turns.push(turn);
    return route.fulfill({ json: { data: { conversation: summary(conversation), turn } } });
  });
  await page.route("**/api/nl2sql/db-admin-chat/execute", (route) => {
    const request = body(route.request());
    mock.executeBodies.push(request);
    const conversation = find(String(request.conversation_id))!;
    const turn = conversation.turns.find((item) => item.turn_id === request.turn_id)!;
    const data = execute ? execute(request, turn) : selectResult;
    const statements = (data.statements as Json[]) ?? [];
    const succeeded = statements.every((item) => item.status === "success" || item.status === "executed");
    const select = data.select_result as Json | undefined;
    turn.last_execution = {
      executed_at: "2026-10-18T00:00:01Z",
      revision: turn.revision,
      confirmation_given: Boolean(request.confirmation),
      succeeded,
      row_count: select ? select.returned_count : null,
      column_count: select ? (select.columns as unknown[]).length : null,
      has_more: false,
      outcome: succeeded ? "成功" : "失敗",
      result: { ...data, select_result: null },
    };
    return route.fulfill({ json: { data } });
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

function historyToggle(page: Page) {
  return page.getByRole("button", { name: "会話の履歴", exact: true });
}

/** 会話の履歴を開いて会話を選ぶ（desktop は左のパネル、狭い画面はシート。チャットと同じ）。 */
async function openFromHistory(page: Page, title: string) {
  const toggle = historyToggle(page);
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await page.getByTestId("db-admin-chat-history").getByText(title, { exact: true }).click();
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test("DB 管理チャットは「データ準備」の「管理 SQL を実行」の上にあり、チャットと同じ会話の履歴の開閉ボタンを持つ", { tag: "@responsive" }, async ({ page }) => {
  await setup(page);
  await page.goto("/db-admin-chat");
  await expect(page.getByRole("heading", { level: 1, name: "DB 管理チャット" })).toBeVisible();
  await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
  // 会話の履歴の開閉はアイコンだけのボタンで、会話の欄の上端の行の左端にある（チャットと同じ。#889）。
  const toggle = historyToggle(page);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toHaveAttribute("aria-controls", "db-admin-chat-history");
  await expect(toggle).toHaveText("");
  const toggleBox = (await toggle.boundingBox())!;
  const newButtonBox = (await page.getByRole("button", { name: "新しい会話", exact: true }).boundingBox())!;
  const panelBox = (await page.getByTestId("db-admin-chat-panel").boundingBox())!;
  expect(toggleBox.x - panelBox.x).toBeLessThan(24);
  expect(toggleBox.x).toBeLessThan(newButtonBox.x);
  await toggle.click();
  await expect(page.getByTestId("db-admin-chat-history").getByText("会話はまだありません")).toBeVisible();
  // 狭い画面の履歴はシート（Esc で閉じて開閉ボタンへ戻る）、desktop は左のパネル（もう一度押して閉じる）。
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await page.keyboard.press("Escape");
    await expect(toggle).toBeFocused();
  } else {
    await toggle.click();
  }
  await expect(toggle).toHaveAttribute("aria-expanded", "false");

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

test("SELECT は「実行」を押すと実行し、結果を表で出す。続きの質問は同じ会話に送り、前の往復は送らない", { tag: "@responsive" }, async ({ page }) => {
  const mock = await setup(page, {
    responses: [
      generated(plan("SELECT BANNER_FULL FROM V$VERSION")),
      generated(plan("SELECT TABLESPACE_NAME, USED_PERCENT FROM DBA_TABLESPACE_USAGE_METRICS")),
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
  // SQL は送らず、ターンに保存した SQL を実行する（#2319）。
  const conversationId = String(mock.generateBodies[0]!.client_turn_id);
  expect(mock.generateBodies[0]).toMatchObject({ question: "データベースの版", conversation_id: "" });
  expect(mock.executeBodies[0]).toEqual({
    conversation_id: conversationId,
    turn_id: conversationId,
    confirmation: "",
    row_limit: expect.any(Number),
  });

  await ask(page, "表領域の使用状況");
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(2);
  // 続きの質問は同じ会話に送り、前の往復はサーバーが保存した会話から復元する。
  expect(mock.generateBodies[1]).toMatchObject({ question: "表領域の使用状況", conversation_id: conversationId });
  expect(mock.generateBodies[1]).not.toHaveProperty("history");
  await expect(page.getByTestId("db-admin-chat-panel").getByText("データベースの版", { exact: true }).first()).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("会話は履歴から開き直せ、SQL・分類・前回の実行の要約が戻る。「新しい会話」は空から始める", { tag: "@responsive" }, async ({ page }) => {
  const selectTurn = storedTurn("turn-a1", "データベースの版", generated(plan("SELECT BANNER_FULL FROM V$VERSION")));
  selectTurn.last_execution = {
    executed_at: "2026-10-17T09:31:00Z",
    revision: 0,
    confirmation_given: false,
    succeeded: true,
    row_count: 1,
    column_count: 1,
    has_more: false,
    outcome: "成功（1 行）",
    result: { ...selectResult, select_result: null },
  };
  const deleteSql = "DELETE FROM APP.TEMP_ITEMS WHERE QTY = 0";
  const mutationTurn = storedTurn("turn-a2", "在庫が 0 の行を消す", generated(plan(deleteSql)));
  mutationTurn.last_execution = {
    executed_at: "2026-10-17T09:32:00Z",
    revision: 0,
    confirmation_given: true,
    succeeded: true,
    row_count: null,
    column_count: null,
    has_more: false,
    outcome: "成功",
    result: mutationResult(deleteSql),
  };
  const mock = await setup(page, {
    responses: [generated(plan("SELECT 1 FROM DUAL"))],
    conversations: [
      savedConversation("conversation-a", "会話 A", [selectTurn, mutationTurn]),
      savedConversation("conversation-b", "会話 B", []),
    ],
  });
  await page.goto("/db-admin-chat");
  await openFromHistory(page, "会話 A");
  const turns = page.getByTestId("db-admin-chat-turn");
  await expect(turns).toHaveCount(2);
  // 結果の行は保存しないので、SELECT は前回の実行の要約と「もう一度実行」（チャットと同じ）。
  await expect(turns.nth(0).getByTestId("db-admin-chat-last-execution")).toContainText("1 行・1 列");
  await expect(turns.nth(0).getByText("読み取り専用・実行済み")).toBeVisible();
  await expect(turns.nth(0).getByTestId("db-admin-chat-execute")).toHaveText("もう一度実行");
  // 更新系は文ごとの状態・影響行数・commit。確認語は保存しないので空からやり直す。
  await expect(turns.nth(1).getByText("確認語で実行済み")).toBeVisible();
  await expect(turns.nth(1).getByText("影響件数 3 件", { exact: true })).toBeVisible();
  await expect(turns.nth(1).getByRole("textbox", { name: "実行確認語" })).toHaveValue("");
  // 開いている会話の名前を会話の欄の上端に出す。
  if ((page.viewportSize()?.width ?? 0) >= 1024) await historyToggle(page).click();
  else await expect(page.getByRole("dialog", { name: "会話の履歴" })).toHaveCount(0);
  await expect(page.getByTestId("db-admin-chat-panel").getByText("会話 A", { exact: true }).first()).toBeVisible();

  // 再読み込みしても開いていた会話に戻る（会話の ID は作業状態に残す）。
  await page.reload();
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(2);

  // 「もう一度実行」も保存した SQL を実行する。
  await page.getByTestId("db-admin-chat-turn").nth(0).getByTestId("db-admin-chat-execute").click();
  await expect(page.getByTestId("db-admin-chat-result")).toBeVisible();
  expect(mock.executeBodies.at(-1)).toMatchObject({ conversation_id: "conversation-a", turn_id: "turn-a1" });

  // 開いた会話への続きの質問は、その会話に送る。
  await ask(page, "続きの質問");
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(3);
  expect(mock.generateBodies.at(-1)).toMatchObject({ conversation_id: "conversation-a" });

  await page.getByRole("button", { name: "新しい会話", exact: true }).click();
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(0);
  await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

/** 会話の欄の末尾からの距離（px）。 */
function distanceFromBottom(page: Page) {
  return page
    .getByTestId("db-admin-chat-conversation")
    .evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
}

// 往復の多い会話でも、チャットと同じく本文は画面の高さに収め、会話の欄の中だけをスクロールする（#2332）。
// 入力欄は会話のカードの下端、履歴のパネルは左に固定。375px はチャットと同じく会話のカードの高さを決めて
// その中をスクロールし、ページは縦に流れる。長い SQL と結果の表は会話の欄の中に収める。
for (const width of [1280, 1920, 375]) {
  test(`往復の多い会話は会話の欄の中でスクロールし、入力欄を画面内に保つ (#2332, ${width}px)`, async ({ page }, testInfo) => {
    const longSql = `SELECT ${Array.from({ length: 40 }, (_, index) => `COLUMN_${index + 1}`).join(", ")} FROM APP.SAMPLE_ITEMS`;
    const turns = Array.from({ length: 12 }, (_, index) =>
      storedTurn(
        `turn-many-${index + 1}`,
        `質問 ${index + 1}`,
        generated(plan(index % 3 === 0 ? longSql : `SELECT ${index + 1} FROM DUAL`)),
      ),
    );
    const wideColumns = Array.from({ length: 16 }, (_, index) => `COLUMN_${index + 1}`);
    await setup(page, {
      responses: [generated(plan(longSql))],
      conversations: [savedConversation("conversation-many", "会話 M", turns)],
      execute: () => ({
        ...selectResult,
        select_result: {
          ...selectResult.select_result,
          columns: wideColumns,
          rows: [Object.fromEntries(wideColumns.map((column) => [column, `${column}_VALUE_LONG_TEXT`]))],
        },
      }),
    });
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/db-admin-chat");
    await openFromHistory(page, "会話 M");
    const log = page.getByTestId("db-admin-chat-conversation");
    await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(12);
    // 会話を開き直したら会話の欄の末尾を出す（チャットと同じ）。
    await expect.poll(() => distanceFromBottom(page)).toBeLessThanOrEqual(2);
    // 会話の欄の中にスクロールがある。
    expect(await log.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeGreaterThan(0);

    const composer = page.locator("#db-admin-chat-composer");
    if (width >= 1024) {
      // ページも本文（#pr-main）も縦にスクロールしない。
      expect(
        await page.evaluate(() => document.scrollingElement!.scrollHeight - window.innerHeight),
      ).toBeLessThanOrEqual(0);
      expect(await page.locator("#pr-main").evaluate((el) => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1);
      // 入力欄は会話のカードの下端にあり、スクロールせずに画面内に見える。
      await expect(composer).toBeInViewport();
      const panel = (await page.getByTestId("db-admin-chat-panel").boundingBox())!;
      const composerRegion = (await page.getByTestId("db-admin-chat-composer-region").boundingBox())!;
      expect(Math.abs(panel.y + panel.height - (composerRegion.y + composerRegion.height))).toBeLessThanOrEqual(2);
      expect(panel.y + panel.height).toBeLessThanOrEqual(900);
      // 履歴のパネルは左に開いたまま、会話の欄を上へスクロールしても動かない。
      const history = page.getByTestId("db-admin-chat-history");
      await expect(history).toBeVisible();
      const historyBefore = (await history.boundingBox())!;
      await log.evaluate((el) => el.scrollTo({ top: 0, behavior: "auto" }));
      await expect.poll(() => log.evaluate((el) => el.scrollTop)).toBe(0);
      const historyAfter = (await history.boundingBox())!;
      expect(historyAfter.y).toBe(historyBefore.y);
      await expect(composer).toBeInViewport();
      await page.screenshot({ path: testInfo.outputPath(`db-admin-chat-many-${width}.png`) });
      // 新しい往復を送ると、上を読んでいても末尾へ移る（送った本人の操作なので末尾へ追う）。
      await ask(page, "続きの質問");
      await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(13);
      await expect.poll(() => distanceFromBottom(page)).toBeLessThanOrEqual(2);
      await expect(composer).toBeInViewport();
    } else {
      // 375px はチャットと同じ形: 履歴はシートで閉じ、会話のカードは画面の高さ以内で、その中をスクロールする。
      await expect(page.getByRole("dialog", { name: "会話の履歴" })).toHaveCount(0);
      const panel = (await page.getByTestId("db-admin-chat-panel").boundingBox())!;
      expect(panel.height).toBeLessThanOrEqual(900);
      await composer.focus();
      await expect(composer).toBeInViewport();
      await ask(page, "続きの質問");
      await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(13);
      await expect.poll(() => distanceFromBottom(page)).toBeLessThanOrEqual(2);
    }

    // 長い SQL と横に広い結果の表は会話の欄の中で横にスクロールし、ページを横に広げない。
    const last = page.getByTestId("db-admin-chat-turn").last();
    await last.getByTestId("db-admin-chat-execute").click();
    await expect(last.getByTestId("db-admin-chat-result")).toBeVisible();
    const logBox = (await log.boundingBox())!;
    for (const box of [
      (await last.getByTestId("db-admin-chat-sql").boundingBox())!,
      (await last.getByTestId("db-admin-chat-result").boundingBox())!,
    ]) {
      expect(box.x).toBeGreaterThanOrEqual(logBox.x - 1);
      expect(box.x + box.width).toBeLessThanOrEqual(logBox.x + logBox.width + 1);
    }
    await expectNoHorizontalOverflow(page);
    if (width >= 1024) {
      expect(
        await page.evaluate(() => document.scrollingElement!.scrollHeight - window.innerHeight),
      ).toBeLessThanOrEqual(0);
      await expect(composer).toBeInViewport();
    }
  });
}

test("会話の名前の変更と削除はチャットと同じ操作でできる", { tag: "@responsive" }, async ({ page }) => {
  const mock = await setup(page, {
    conversations: [
      savedConversation("conversation-a", "会話 A", [
        storedTurn("turn-a1", "データベースの版", generated(plan("SELECT 1 FROM DUAL"))),
      ]),
    ],
  });
  await page.goto("/db-admin-chat");
  await openFromHistory(page, "会話 A");
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(1);
  const toggle = historyToggle(page);
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  const history = page.getByTestId("db-admin-chat-history");
  await history.getByRole("button", { name: "「会話 A」の名前を変更" }).click();
  const title = history.getByRole("textbox", { name: "会話名" });
  await title.fill("会話 A2");
  await title.press("Enter");
  await expect(history.getByText("会話 A2", { exact: true })).toBeVisible();
  expect(mock.conversations[0]!.title).toBe("会話 A2");

  await history.getByRole("button", { name: "「会話 A2」を削除" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "削除", exact: true }).click();
  await expect(history.getByText("会話はまだありません")).toBeVisible();
  expect(mock.conversations).toHaveLength(0);
  // 開いていた会話を消したら、新しい会話に戻る。
  if ((page.viewportSize()?.width ?? 0) < 1024) await page.keyboard.press("Escape");
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(0);
  await expect(page.getByText("質問を入力して会話を始めます")).toBeVisible();
});

test("system_admin が見る他の利用者の会話には、日時に利用者の名前を添え、入力欄の上に案内を出す", { tag: "@responsive" }, async ({ page }) => {
  await setup(page, {
    conversations: [
      savedConversation(
        "conversation-x",
        "会話 X",
        [storedTurn("turn-x1", "データベースの版", generated(plan("SELECT 1 FROM DUAL")))],
        { owned: false, actor_display_name: "利用者 B" },
      ),
    ],
  });
  await page.goto("/db-admin-chat");
  await historyToggle(page).click();
  await expect(page.getByTestId("db-admin-chat-history").getByText("利用者 B", { exact: false })).toBeVisible();
  await page.getByTestId("db-admin-chat-history").getByText("会話 X", { exact: true }).click();
  await expect(page.getByTestId("db-admin-chat-turn")).toHaveCount(1);
  await expect(page.getByText("この会話の持ち主にも、回答と途中の結果が見えます")).toBeVisible();
});

test("SELECT 以外は実行確認語を入れたときだけ実行し、失敗は AI の分析で直した SQL を確認からやり直す", { tag: "@responsive" }, async ({ page }) => {
  const sql = "DELETE FROM APP.TEMP_ITEMS WHERE QTY = 0";
  const corrected = "DELETE FROM APP.TEMP_ITEMS_V2 WHERE QTY = 0";
  let calls = 0;
  const mock = await setup(page, {
    responses: [generated(plan(sql, { implicit_commit: false }))],
    execute: (_request, turn) => {
      calls += 1;
      return mutationResult(turn.plan.sql, calls > 1);
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
  expect(mock.executeBodies[0]).toMatchObject({ confirmation: "ADMIN_EXECUTE" });
  expect(mock.executeBodies[0]).not.toHaveProperty("sql");
  await expect(turn.getByText("ORA-00942: table or view does not exist").first()).toBeVisible();
  // 実行の確認は 1 回ごと。
  await expect(confirmation).toHaveValue("");

  await turn.getByTestId("db-admin-chat-analyze").click();
  await expect(turn.getByTestId("db-admin-chat-corrected-sql")).toHaveText(corrected);
  expect(mock.analyzeBodies[0]).toMatchObject({ sql, question: "在庫が 0 の行を消す" });
  await turn.getByTestId("db-admin-chat-use-corrected").click();
  // 直した SQL はサーバーがターンの SQL を置き換えて分類し直す。
  await expect.poll(() => mock.planBodies).toEqual([{ sql: corrected }]);

  const updated = page.getByTestId("db-admin-chat-turn").first();
  await expect(updated.getByTestId("db-admin-chat-sql")).toHaveText(corrected);
  await expect(updated.getByText("AI のエラー分析で修正した SQL です。")).toBeVisible();
  // 直した SQL も確認語からやり直す。
  await expect(updated.getByRole("textbox", { name: "実行確認語" })).toHaveValue("");
  await expect(updated.getByTestId("db-admin-chat-execute")).toBeDisabled();
  await updated.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await updated.getByTestId("db-admin-chat-execute").click();
  expect(mock.executeBodies[1]).toMatchObject({ confirmation: "ADMIN_EXECUTE" });
  await expect(updated.getByText("確認語で実行済み")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("実行できない SQL は理由を出し、実行の操作を出さない", { tag: "@responsive" }, async ({ page }) => {
  const sql = "ALTER SYSTEM SET OPEN_CURSORS = 300";
  await setup(page, {
    responses: [
      generated(
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

test("生成に失敗した質問は残して、同じターンの ID で「再送信」する", { tag: "@responsive" }, async ({ page }) => {
  const mock = await setup(page);
  await page.goto("/db-admin-chat");
  await ask(page, "データベースの版");
  const pending = page.getByTestId("db-admin-chat-pending-turn");
  await expect(pending.getByText("データベースの版")).toBeVisible();
  await expect(page.getByTestId("db-admin-chat-send-error")).toBeVisible();
  await page.getByRole("button", { name: "再送信" }).click();
  await expect.poll(() => mock.generateBodies.length).toBe(2);
  // 応答が届かなかっただけならサーバーは保存したターンを返す（二重に生成しない）。
  expect(mock.generateBodies[1]!.client_turn_id).toBe(mock.generateBodies[0]!.client_turn_id);
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
