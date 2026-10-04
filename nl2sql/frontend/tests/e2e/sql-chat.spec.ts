import { expect, test, type Page } from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { openSidebarNav, closeSidebarNav } from "./_helpers/sidebar-nav";
import {
  chooseSelectFieldOption,
  expectSelectFieldValue,
} from "./_helpers/select-field";
import { expectedControlHeight } from "./_helpers/control-height";

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
    await expect(history).toHaveAttribute("aria-controls", "sql-chat-history");
    await expect(page.getByTestId("sql-chat-history")).not.toBeVisible();
    // 会話の履歴の開閉はアイコンだけのボタンで、会話の欄の上端の行の左端にある（#889）。
    await expect(history).toHaveText("");
    const toggleBox = (await history.boundingBox())!;
    const newButtonBox = (await page
      .getByRole("button", { name: "新しい会話", exact: true })
      .boundingBox())!;
    const panelBox = (await page.getByTestId("sql-chat-panel").boundingBox())!;
    expect(toggleBox.x - panelBox.x).toBeLessThan(24);
    expect(toggleBox.x).toBeLessThan(newButtonBox.x);
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
/**
 * ジョブの投入の上限（120 秒）だけを短くする。ほかの要求の上限は変えない（#900）。
 */
async function shortenJobSubmitTimeout(page: Page) {
  await page.addInitScript(() => {
    const original = AbortSignal.timeout.bind(AbortSignal);
    AbortSignal.timeout = (ms: number) => original(ms === 120_000 ? 300 : ms);
  });
}

/** 応答を返さないジョブの投入。テストの終わりに abort して後始末する。 */
async function holdJobSubmit(page: Page, state: { requests: Record<string, unknown>[] }) {
  const held: import("@playwright/test").Route[] = [];
  await page.route("**/api/nl2sql/jobs", (route) => {
    state.requests.push(route.request().postDataJSON());
    held.push(route);
  });
  return async () => {
    await Promise.all(held.map((route) => route.abort().catch(() => undefined)));
  };
}

test("送信の応答が上限までに届かないと、英語の signal timed out ではなく日本語の案内と詳細を出す (#900)", async ({
  page,
}, testInfo) => {
  const state = await setup(page);
  await shortenJobSubmitTimeout(page);
  const release = await holdJobSubmit(page, state);
  const recovered: string[] = [];
  // backend はジョブを作り終えていない（取り直しは 404）。
  await page.route("**/api/nl2sql/jobs/*", (route) => {
    recovered.push(new URL(route.request().url()).pathname);
    return route.fulfill({
      status: 404,
      json: { error: "指定されたジョブが見つかりません。" },
    });
  });
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
  await composer.fill("select * from employee");
  await composer.press("Enter");
  const failure = page.getByTestId("sql-chat-send-error");
  await expect(failure).toBeVisible();
  const banner = failure.getByRole("alert");
  await expect(banner).toContainText(
    "送信の応答が 120 秒以内に返りませんでした。",
  );
  await expect(banner).toContainText(
    "少し待ってから会話の履歴を確かめ、見当たらなければもう一度送信してください。",
  );
  // ブラウザの英語の文は「詳細」にだけ出す（失敗なので開いて出す）。
  const details = failure.locator("details");
  await expect(details).toHaveAttribute("open", "");
  await expect(details).toContainText("POST /api/nl2sql/jobs");
  await expect(details).toContainText("120 秒");
  await expect(details).toContainText("TimeoutError");
  await expect(failure.locator("p").first()).not.toContainText("signal");
  // 送信前に決めた job ID で取り直しを試み、草稿は残す。
  const clientJobId = String(state.requests[0]?.client_job_id ?? "");
  expect(clientJobId).toMatch(/^[0-9a-f-]{36}$/u);
  expect(recovered).toEqual([`/api/nl2sql/jobs/${clientJobId}`]);
  await expect(composer).toHaveValue("select * from employee");
  await expect(page.getByTestId("sql-chat-send")).toHaveAccessibleName("送信");
  // 結果は入力欄の行の直下に、会話の欄の幅で出す（messaging.md §10.1）。
  const composerBox = (await composer.boundingBox())!;
  const failureBox = (await failure.boundingBox())!;
  const regionBox = (await page
    .getByTestId("sql-chat-composer-region")
    .boundingBox())!;
  expect(failureBox.y).toBeGreaterThan(composerBox.y + composerBox.height - 1);
  expect(regionBox.width - failureBox.width).toBeLessThan(32);
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await page.evaluate(
      (dark) => document.documentElement.classList.toggle("dark", dark),
      colorScheme === "dark",
    );
    await failure.screenshot({
      path: testInfo.outputPath(`chat-send-timeout-${colorScheme}.png`),
    });
  }
  await release();
});

test("送信の応答が届かなくても、作成済みのジョブを取り直して生成の結果を表示する (#900)", async ({
  page,
}) => {
  const state = await setup(page);
  await shortenJobSubmitTimeout(page);
  const release = await holdJobSubmit(page, state);
  await page.route("**/api/nl2sql/jobs/*", (route) => {
    const jobId = new URL(route.request().url()).pathname.split("/").at(-1)!;
    const turn: Turn = {
      job_id: jobId,
      question: "select * from employee",
      status: "done",
      created_at: now,
      steps: [],
      result: {
        generated_sql: "SELECT * FROM APP.EMPLOYEE",
        original_question: "select * from employee",
        explanation: "",
        safety: { is_safe: true },
      },
    };
    if (!state.turns.some((item) => item.job_id === jobId))
      state.turns.push(turn);
    return route.fulfill({ json: { data: turn } });
  });
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
  await composer.fill("select * from employee");
  await composer.press("Enter");
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
  await expect(page.locator("pre").last()).toContainText(
    "SELECT * FROM APP.EMPLOYEE",
  );
  await expect(page.getByTestId("sql-chat-send-error")).toHaveCount(0);
  await expect(composer).toHaveValue("");
  expect(state.requests).toHaveLength(1);
  await release();
});

test("サーバーに接続できないときは英語の Failed to fetch ではなく日本語で案内する (#900)", async ({
  page,
}) => {
  const state = await setup(page);
  await page.route("**/api/nl2sql/jobs", (route) => {
    state.requests.push(route.request().postDataJSON());
    return route.abort("failed");
  });
  await page.route("**/api/nl2sql/jobs/*", (route) => route.abort("failed"));
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
  await composer.fill("select * from employee");
  await composer.press("Enter");
  const failure = page.getByTestId("sql-chat-send-error");
  await expect(failure.getByRole("alert")).toContainText(
    "サーバーに接続できませんでした。",
  );
  await expect(failure.getByRole("alert")).toContainText(
    "ネットワークの接続とサーバーの起動状態を確かめてから、もう一度実行してください。",
  );
  await expect(failure.locator("details")).toContainText("POST /api/nl2sql/jobs");
  await expect(failure.locator("p").first()).not.toContainText("Failed to fetch");
  await expect(composer).toHaveValue("select * from employee");
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

test("利用できるプロファイルがないと会話の欄を出さず送信できない", async ({
  page,
}) => {
  const state = await setup(page);
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({
      json: { data: { items: [], total: 0, next_cursor: null } },
    }),
  );
  await page.goto("/chat");
  // RAG・Agent のチャットと同じく、上部のカードに空の状態だけを出す（#891）。
  await expect(
    page.getByText("利用できる業務プロファイルがありません", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("#sql-chat-profile")).toHaveCount(0);
  await expect(page.getByTestId("sql-chat-panel")).toHaveCount(0);
  await expect(
    page.getByRole("textbox", { name: "クエリ", exact: true }),
  ).toHaveCount(0);
  expect(state.requests).toHaveLength(0);
});

test("業務プロファイルの欄は RAG の検索・回答プロファイルと同じ形で、読み込み中は欄の形を出す", async ({
  page,
}) => {
  await setup(page);
  let release!: () => void;
  const released = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/nl2sql/profiles/search**", async (route) => {
    await released;
    await route.fulfill({
      json: { data: { items: [profile], total: 1, next_cursor: null } },
    });
  });
  await page.goto("/chat");
  // 読み込み中: ラベルと欄の形の Skeleton と経過時間（#891）。
  const loading = page.getByTestId("sql-chat-profiles-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toContainText("業務プロファイルを読み込んでいます");
  release();
  await expect(page.getByTestId("sql-chat-profiles-loading")).toHaveCount(0);
  const field = page.locator("#sql-chat-profile");
  await expect(field).toBeVisible();
  await expect(field).toContainText("売上分析");
  // 必須の表示・先頭の検索アイコン・カードの幅いっぱい（#635 / #891）。
  await expect(page.getByText("必須", { exact: true }).first()).toBeVisible();
  await expect(field.locator("svg").first()).toBeVisible();
  const card = (await page
    .locator(".rounded-lg")
    .filter({ has: field })
    .first()
    .boundingBox())!;
  const fieldBox = (await field.boundingBox())!;
  expect(card.width - fieldBox.width).toBeLessThan(64);
  expect(fieldBox.height).toBe(await expectedControlHeight(page, "md"));
  await field.click();
  await expect(page.getByRole("option", { name: /売上分析/ })).toContainText(
    "表・ビュー 1 件",
  );
  await page.keyboard.press("Escape");
  await expect(field).toBeFocused();
});

test("生成方法は入力欄の直上で選び、Select AI Agent も送れる", async ({
  page,
}) => {
  const state = await setup(page);
  await page.goto("/chat");
  const row = page.getByTestId("sql-chat-engine-row");
  const engine = page.getByRole("combobox", { name: "生成方法", exact: true });
  await expect(row).toContainText("生成方法");
  await expectSelectFieldValue(engine, "select_ai");
  await expect(page.getByTestId("sql-chat-engine-description")).toContainText(
    "Oracle Select AI",
  );
  // 入力欄の直上の行（RAG のチャットの「回答するモデル」と同じ位置。#890）。
  const composer = page.getByRole("textbox", { name: "クエリ", exact: true });
  const rowBox = (await row.boundingBox())!;
  const composerBox = (await composer.boundingBox())!;
  expect(rowBox.y + rowBox.height).toBeLessThanOrEqual(composerBox.y);
  expect(composerBox.y - (rowBox.y + rowBox.height)).toBeLessThan(24);
  await expect(engine).toHaveAttribute("aria-describedby", /.+/);
  expect((await engine.boundingBox())!.height).toBe(
    await expectedControlHeight(page, "sm"),
  );
  await engine.click();
  await expect(page.getByRole("option")).toHaveText([
    "Select AI",
    "Select AI Agent",
    "Enterprise AI",
  ]);
  await page.keyboard.press("Escape");
  await chooseSelectFieldOption(engine, "select_ai_agent");
  await expect(page.getByTestId("sql-chat-engine-description")).toContainText(
    "Select AI Agent",
  );
  await composer.fill("カテゴリ別売上");
  await composer.press("Enter");
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
  expect(state.requests[0]).toMatchObject({
    engine: "select_ai_agent",
    generation_only: true,
  });
  // 生成方法は作業状態に残る。
  await page.reload();
  await expectSelectFieldValue(
    page.getByRole("combobox", { name: "生成方法", exact: true }),
    "select_ai_agent",
  );
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
});
