import { expect, test, type Page } from "./_helpers/test";
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
  [key: string]: unknown;
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
      engine: body.engine,
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
    const composer = page.getByRole("textbox", { name: "質問", exact: true });
    await composer.fill("カテゴリ別売上");
    await composer.press("Enter");
    await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
    await composer.fill("多い順にして");
    await page.getByTestId("sql-chat-send").click();
    await expect(page.getByTestId("sql-chat-turn")).toHaveCount(2);
    await expect(page.locator("pre").last()).toContainText(
      "ORDER BY SUM(AMOUNT) DESC",
    );
    // 生成の prompt には公開版のオントロジーの文脈を使い、画面に出さない生成後の接地確認は
    // 求めない（#1172）。
    expect(state.requests[0]).toMatchObject({
      generation_only: true,
      profile_id: "sales",
      previous_job_id: null,
      use_ontology_context: true,
      include_ontology_grounding: false,
    });
    expect(state.requests[1]).toMatchObject({
      generation_only: true,
      previous_job_id: "chat-1",
      question: "多い順にして",
      use_ontology_context: true,
      include_ontology_grounding: false,
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
test("生成中は停止でき、失敗時も送った質問を会話に残して再送信できる", async ({ page }) => {
  const state = await setup(page);
  state.pending = true;
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("カテゴリ別売上");
  await composer.press("Enter");
  const send = page.getByTestId("sql-chat-send");
  await expect(send).toHaveAccessibleName("停止");
  await composer.fill("次の条件");
  await composer.press("Enter");
  expect(state.requests).toHaveLength(1);
  await send.click();
  await expect(page.getByText("SQL の生成を停止しました")).toBeVisible();
  // 停止しても送った質問は会話に残る（#907）。
  await expect(
    page.getByTestId("sql-chat-turn").getByText("カテゴリ別売上", { exact: true }),
  ).toBeVisible();
  await expect(send).toHaveAccessibleName("送信");
  await expect(composer).toHaveValue("次の条件");
  state.failSend = true;
  await send.click();
  // 送った質問は会話の欄に残し、入力欄は空のまま（#907）。理由と「再送信」は質問の直下に出す。
  const pendingTurn = page.getByTestId("sql-chat-pending-turn");
  await expect(pendingTurn.locator('[data-status="failed"]')).toContainText(
    "次の条件",
  );
  await expect(pendingTurn).toContainText("送信できませんでした");
  await expect(
    pendingTurn.getByTestId("sql-chat-send-error"),
  ).toContainText("モデルへ接続できません。");
  await expect(composer).toHaveValue("");
  state.failSend = false;
  state.pending = false;
  await pendingTurn.getByRole("button", { name: "再送信" }).click();
  await expect(pendingTurn).toHaveCount(0);
  await expect(page.getByTestId("sql-chat-turn")).toHaveCount(2);
  await expect(page.getByTestId("sql-chat-send-error")).toHaveCount(0);
  expect(state.requests.at(-1)).toMatchObject({ question: "次の条件" });
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
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
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
  // 送信前に決めた job ID で取り直しを試みる。送った質問は会話の欄に残す（#907）。
  const clientJobId = String(state.requests[0]?.client_job_id ?? "");
  expect(clientJobId).toMatch(/^[0-9a-f-]{36}$/u);
  expect(recovered).toEqual([`/api/nl2sql/jobs/${clientJobId}`]);
  await expect(composer).toHaveValue("");
  await expect(page.getByTestId("sql-chat-send")).toHaveAccessibleName("送信");
  // 結果は送った質問の直下に、会話の欄の幅で出す（messaging.md §10.1 のチャットの扱い。#907）。
  const question = page
    .getByTestId("sql-chat-pending-turn")
    .locator('[data-status="failed"]');
  await expect(question).toContainText("select * from employee");
  await expect(failure.getByRole("button", { name: "再送信" })).toBeVisible();
  const questionBox = (await question.boundingBox())!;
  const failureBox = (await failure.boundingBox())!;
  const regionBox = (await page
    .getByTestId("sql-chat-conversation")
    .boundingBox())!;
  expect(failureBox.y).toBeGreaterThan(questionBox.y + questionBox.height - 1);
  expect(regionBox.width - failureBox.width).toBeLessThan(48);
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
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
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
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
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
  // 送った質問は会話の欄に残し、入力欄は空のまま（#907）。
  await expect(
    page.getByTestId("sql-chat-pending-turn").locator('[data-status="failed"]'),
  ).toContainText("select * from employee");
  await expect(composer).toHaveValue("");
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
    .getByRole("textbox", { name: "質問", exact: true })
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
    page.getByRole("textbox", { name: "質問", exact: true }),
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
  isMobile,
}) => {
  const state = await setup(page);
  await page.goto("/chat");
  const row = page.getByTestId("sql-chat-engine-row");
  const engine = page.getByRole("combobox", { name: "生成方法", exact: true });
  await expect(row).toContainText("生成方法");
  await expectSelectFieldValue(engine, "select_ai");
  // 選んだ生成方法の説明は常設せず、ラベルの横の info アイコンから出す（#901）。
  const description = page.getByTestId("sql-chat-engine-description");
  const info = row.getByRole("button", { name: "生成方法の説明", exact: true });
  await expect(description).toBeHidden();
  await expect(info).toHaveAccessibleDescription(/Oracle Select AI/);
  // マウスはポインタを乗せると出し、離すと閉じる。タッチ端末（ホバーが無い）はタップで出し、外側のタップで閉じる。
  if (isMobile) await info.tap();
  else await info.hover();
  await expect(description).toBeVisible();
  await expect(description).toContainText("Oracle Select AI");
  if (isMobile) await row.getByText("生成方法", { exact: true }).first().tap();
  else await page.mouse.move(0, 0);
  await expect(description).toBeHidden();
  // 入力欄の直上の行（RAG のチャットの「回答するモデル」と同じ位置。#890）。
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  const rowBox = (await row.boundingBox())!;
  const composerBox = (await composer.boundingBox())!;
  expect(rowBox.y + rowBox.height).toBeLessThanOrEqual(composerBox.y);
  expect(composerBox.y - (rowBox.y + rowBox.height)).toBeLessThan(24);
  // 選択欄にも、選んだ生成方法の説明を結び付ける（閉じている吹き出しの文を読む）。
  await expect(engine).toHaveAccessibleDescription(/Oracle Select AI/);
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
  // キーボード: 選択欄から Shift+Tab で info アイコンへ戻るとフォーカスで出し、Escape で閉じる（フォーカスは動かさない）。
  await expect(engine).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(info).toBeFocused();
  // タッチ端末ではフォーカスだけでは出さないので、Enter で開く（マウス・キーボードの環境はフォーカスで出る）。
  if (isMobile) await page.keyboard.press("Enter");
  await expect(description).toBeVisible();
  await expect(description).toContainText(
    "Select AI Agent が SQL 生成ツールを呼び出して",
  );
  await page.keyboard.press("Escape");
  await expect(description).toBeHidden();
  await expect(info).toBeFocused();
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

// #907: 送った質問は、ジョブの投入の応答を待たずにすぐ会話の欄の末尾へ出す（楽観的な表示）。
/** ジョブの投入を止めておき、好きなときに setup の応答へ流す。 */
async function gateJobSubmit(page: Page) {
  let open: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  await page.route("**/api/nl2sql/jobs", async (route) => {
    await opened;
    await route.fallback();
  });
  return open;
}

async function expectConversationScrolledToEnd(page: Page) {
  await expect
    .poll(() =>
      page
        .getByTestId("sql-chat-conversation")
        .evaluate(
          (element) =>
            element.scrollHeight - element.clientHeight - element.scrollTop,
        ),
    )
    .toBeLessThanOrEqual(2);
}

test("送った質問はジョブの投入の応答を待たずに会話の欄へ出る（新しい会話。#907）", async ({
  page,
}, testInfo) => {
  await setup(page);
  const release = await gateJobSubmit(page);
  await page.goto("/chat");
  const empty = page.getByText("どのような SQL を生成しますか？");
  await expect(empty).toBeVisible();
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("カテゴリ別売上");
  await composer.press("Enter");

  const pendingTurn = page.getByTestId("sql-chat-pending-turn");
  await expect(pendingTurn.locator('[data-status="sending"]')).toHaveText(
    "カテゴリ別売上",
  );
  await expect(
    // 投入の応答を待つ間は、送信の段階を今の段階として出す（#1145）。
    pendingTurn.getByTestId("sql-chat-progress-current"),
  ).toContainText("質問を送信しています");
  await expect(
    pendingTurn.getByTestId("sql-chat-progress-timer"),
  ).toBeVisible();
  await expect(empty).toHaveCount(0);
  await expect(page.getByRole("log", { name: "会話" })).toContainText(
    "カテゴリ別売上",
  );
  await expect(composer).toHaveValue("");
  await expect(composer).toBeFocused();
  // 処理中の表示は回答の場所の 1 つだけ（messaging.md §3.7）。
  await expect(page.locator("svg.animate-spin:visible")).toHaveCount(1);
  await expect(pendingTurn.locator("svg.animate-spin:visible")).toHaveCount(1);
  await expectConversationScrolledToEnd(page);
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
    await page.getByTestId("sql-chat-panel").screenshot({
      path: testInfo.outputPath(`chat-sending-${colorScheme}.png`),
    });
  }

  // 投入できたら、同じ位置のジョブの表示に置き換える（質問を二重に出さない）。
  release();
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
  await expect(pendingTurn).toHaveCount(0);
  await expect(page.getByTestId("sql-chat-turn")).toHaveCount(1);
  await expect(
    page
      .getByTestId("sql-chat-conversation")
      .getByText("カテゴリ別売上", { exact: true }),
  ).toHaveCount(1);
});

test("続きの会話でも、送った質問は投入の応答を待たずに末尾へ出る（#907）", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("カテゴリ別売上");
  await composer.press("Enter");
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();

  const release = await gateJobSubmit(page);
  await composer.fill("多い順にして");
  await composer.press("Enter");
  const pendingTurn = page.getByTestId("sql-chat-pending-turn");
  await expect(pendingTurn.locator('[data-status="sending"]')).toHaveText(
    "多い順にして",
  );
  await expect(composer).toHaveValue("");
  const text = await page.getByTestId("sql-chat-conversation").innerText();
  expect(text.indexOf("カテゴリ別売上")).toBeGreaterThanOrEqual(0);
  expect(text.indexOf("カテゴリ別売上")).toBeLessThan(
    text.indexOf("多い順にして"),
  );
  await expect(page.locator("svg.animate-spin:visible")).toHaveCount(1);
  await expectConversationScrolledToEnd(page);

  release();
  await expect(page.getByTestId("sql-chat-turn")).toHaveCount(2);
  await expect(pendingTurn).toHaveCount(0);
});

/**
 * 応答だけが届かないジョブの投入（backend はジョブを作り終える）。job ID は画面が決めた client_job_id で、
 * 同じ ID の再送は作成済みのジョブを返す（backend の `_replay_client_job` と同じ）。
 */
async function loseJobSubmitResponse(
  page: Page,
  state: Awaited<ReturnType<typeof setup>>,
) {
  const control = { lose: true };
  await page.route("**/api/nl2sql/jobs", (route) => {
    const body = route.request().postDataJSON();
    state.requests.push(body);
    const id = String(body.client_job_id);
    if (!state.turns.some((turn) => turn.job_id === id))
      state.turns.push({
        job_id: id,
        question: body.question,
        status: "done",
        created_at: now,
        steps: [],
        result: {
          generated_sql: "SELECT CATEGORY FROM APP.SALES",
          original_question: body.question,
          explanation: "",
          safety: { is_safe: true },
        },
      });
    if (control.lose) return route.abort("failed");
    return route.fulfill({
      json: { data: { job_id: id, status: "done", created_at: now, steps: [] } },
    });
  });
  // 取り直しも届かない（通信が戻る前）。
  await page.route("**/api/nl2sql/jobs/*", (route) => route.abort("failed"));
  return control;
}

test("応答が届かなかった送信のジョブが会話に入っていれば、質問を二重に出さず再送信も出さない (#900 / #907)", async ({
  page,
}) => {
  const state = await setup(page);
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("カテゴリ別売上");
  await composer.press("Enter");
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();

  await loseJobSubmitResponse(page, state);
  await composer.fill("多い順にして");
  await composer.press("Enter");
  // 失敗の後に取り直した会話に、作成済みのジョブが入る。送った質問は 1 回だけ出す。
  await expect(page.getByTestId("sql-chat-turn")).toHaveCount(2);
  await expect(page.getByTestId("sql-chat-pending-turn")).toHaveCount(0);
  await expect(
    page
      .getByTestId("sql-chat-conversation")
      .getByText("多い順にして", { exact: true }),
  ).toHaveCount(1);
  await expect(page.getByRole("button", { name: "再送信" })).toHaveCount(0);
  expect(state.requests).toHaveLength(2);
  expect(state.turns).toHaveLength(2);
});

test("応答が届かなかった送信の再送信は同じ job ID で送り、作成済みのジョブを二重に生成しない (#900 / #907)", async ({
  page,
}) => {
  const state = await setup(page);
  const control = await loseJobSubmitResponse(page, state);
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("カテゴリ別売上");
  await composer.press("Enter");
  const failure = page.getByTestId("sql-chat-send-error");
  await expect(failure).toBeVisible();

  control.lose = false;
  await failure.getByRole("button", { name: "再送信" }).click();
  await expect(page.getByText("安全検査済み・未実行")).toBeVisible();
  await expect(page.getByTestId("sql-chat-pending-turn")).toHaveCount(0);
  await expect(page.getByTestId("sql-chat-turn")).toHaveCount(1);
  expect(state.requests).toHaveLength(2);
  expect(state.requests[1]).toMatchObject({
    question: "カテゴリ別売上",
    client_job_id: state.requests[0].client_job_id,
  });
  expect(state.turns).toHaveLength(1);
});

test("会話の履歴と会話の読み込み中は、文言と経過時間を出し内容の形の Skeleton で覆う", async ({
  page,
}) => {
  const state = await setup(page);
  state.turns.push({
    job_id: "chat-1",
    question: "カテゴリ別売上",
    status: "done",
    created_at: now,
    steps: [],
    result: {
      generated_sql: "SELECT CATEGORY FROM APP.SALES",
      original_question: "カテゴリ別売上",
      explanation: "",
      safety: { is_safe: true },
    },
  });
  let release: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/nl2sql/chats**", async (route) => {
    await opened;
    await route.fallback();
  });
  await page.goto("/chat");
  await page.getByRole("button", { name: "会話の履歴", exact: true }).click();
  const historyLoading = page.getByTestId("sql-chat-history-loading");
  await expect(historyLoading).toContainText("会話の履歴を読み込んでいます");
  await expect(historyLoading.locator(".animate-pulse").first()).toBeVisible();
  release();
  await page
    .getByTestId("sql-chat-history")
    .getByRole("button", { name: /カテゴリ別売上/ })
    .click();
  await expect(page.getByTestId("sql-chat-turn")).toHaveCount(1);

  // 再読込で開いている会話を読み込む間（会話の内容の取得を止める）。
  let releaseConversation: () => void = () => undefined;
  const conversationOpened = new Promise<void>((resolve) => {
    releaseConversation = resolve;
  });
  await page.route("**/api/nl2sql/chats/*", async (route) => {
    await conversationOpened;
    await route.fallback();
  });
  await page.reload();
  const conversationLoading = page.getByTestId("sql-chat-conversation-loading");
  await expect(conversationLoading).toContainText("会話を読み込んでいます");
  await expect(
    conversationLoading.locator(".animate-pulse").first(),
  ).toBeVisible();
  releaseConversation();
  await expect(page.getByTestId("sql-chat-turn")).toHaveCount(1);
  await expect(conversationLoading).toHaveCount(0);
});

// #1145: 回答の場所に backend の処理の段階を出す（3 製品共通の ChatProgress）。
const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();
const pendingSteps = () =>
  ["prepare_context", "generate_sql", "safety_check", "execute_sql", "format_results"].map(
    (stage) => ({ stage, status: "pending" }),
  );

async function applyColorScheme(page: Page, colorScheme: "light" | "dark") {
  await page.emulateMedia({ colorScheme });
  await page.evaluate(
    (dark) => document.documentElement.classList.toggle("dark", dark),
    colorScheme === "dark",
  );
}

for (const width of [1280, 375]) {
  test(`回答の場所に開始待ち・実行中の段階を出し、完了後は「処理の経過」に畳む (#1145, ${width}px)`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = await setup(page);
    state.pending = true;
    await page.goto("/chat");
    const composer = page.getByRole("textbox", { name: "質問", exact: true });
    await composer.fill("select * from employee");
    await composer.press("Enter");
    const turn = page.getByTestId("sql-chat-turn");
    const progress = turn.getByTestId("sql-chat-progress");
    const current = progress.getByTestId("sql-chat-progress-current");

    await expect.poll(() => state.turns.length).toBe(1);
    // worker が始めていない間は「処理の開始を待っています」（生成ではなく開始で待っていることが分かる）。
    Object.assign(state.turns[0], {
      status: "pending",
      created_at: iso(2_000),
      steps: pendingSteps(),
    });
    await expect(current).toHaveAttribute("data-step-id", "queue");
    await expect(current).toContainText("処理の開始を待っています");
    await expect(progress.getByRole("status")).toHaveText("処理の開始を待っています");

    // 実行中: 今の段階 1 行（補足に生成方法）と、完了した段階の畳んだ見出し。遅延の案内は今の段階に付く。
    Object.assign(state.turns[0], {
      status: "running",
      created_at: iso(20_000),
      started_at: iso(19_000),
      steps: [
        { stage: "prepare_context", status: "done", started_at: iso(19_000), finished_at: iso(18_000) },
        { stage: "generate_sql", status: "running", started_at: iso(18_000) },
        { stage: "safety_check", status: "pending" },
        { stage: "execute_sql", status: "skipped" },
        { stage: "format_results", status: "skipped" },
      ],
    });
    await expect(current).toHaveAttribute("data-step-id", "generate_sql");
    await expect(current).toContainText("SQL を生成しています（Select AI）");
    await expect(progress.getByTestId("sql-chat-progress-slow")).toHaveText(
      "通常より時間がかかっています。",
    );
    await expect(progress.getByRole("status")).toHaveText("SQL を生成しています");
    const completed = progress.getByTestId("sql-chat-progress-completed");
    await expect(completed).toHaveText("2 ステップ完了");
    // 動くスピナーは今の段階の 1 つだけ（messaging.md §3.7）。
    await expect(page.locator("svg.animate-spin:visible")).toHaveCount(1);
    await completed.click();
    const prepared = progress.getByTestId("sql-chat-progress-step-prepare_context");
    await expect(prepared).toContainText("質問と対象の表を準備しました");
    await expect(prepared).toContainText("1.0 秒");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    ).toBeLessThanOrEqual(0);
    for (const colorScheme of ["light", "dark"] as const) {
      await applyColorScheme(page, colorScheme);
      await turn.screenshot({
        path: testInfo.outputPath(`chat-progress-running-${colorScheme}.png`),
      });
    }
    await applyColorScheme(page, "light");

    // 完了: 回答の上に「処理の経過（N ステップ・M 秒）」の 1 行に畳む（既定は閉じる）。
    Object.assign(state.turns[0], {
      status: "done",
      finished_at: iso(0),
      steps: [
        { stage: "prepare_context", status: "done", started_at: iso(19_000), finished_at: iso(18_000) },
        { stage: "generate_sql", status: "done", started_at: iso(18_000), finished_at: iso(1_000) },
        { stage: "safety_check", status: "done", started_at: iso(1_000), finished_at: iso(0) },
        { stage: "execute_sql", status: "skipped" },
        { stage: "format_results", status: "skipped" },
      ],
      result: {
        generated_sql: "SELECT * FROM APP.EMPLOYEE",
        original_question: "select * from employee",
        explanation: "",
        safety: { is_safe: true, referenced_tables: ["APP.EMPLOYEE"] },
      },
    });
    await expect(turn.getByText("安全検査済み・未実行")).toBeVisible();
    const summary = progress.getByTestId("sql-chat-progress-summary");
    await expect(summary).toHaveText(/^処理の経過（4 ステップ・2\d 秒）$/);
    await expect(progress).toHaveAttribute("data-chat-progress-state", "done");
    await expect(progress.locator("details")).not.toHaveAttribute("open", /.*/);
    await expect(page.locator("svg.animate-spin:visible")).toHaveCount(0);
    await summary.click();
    await expect(progress.getByTestId("sql-chat-progress-step-safety_check")).toContainText(
      "SQL の安全性を確認しました（APP.EMPLOYEE）",
    );
    for (const colorScheme of ["light", "dark"] as const) {
      await applyColorScheme(page, colorScheme);
      await turn.screenshot({
        path: testInfo.outputPath(`chat-progress-done-${colorScheme}.png`),
      });
    }
  });
}

test("生成に失敗した段階は「処理の経過」を開いて失敗を文字とアイコンで出す (#1145)", async ({
  page,
}, testInfo) => {
  const state = await setup(page);
  state.pending = true;
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("select * from employee");
  await composer.press("Enter");
  const turn = page.getByTestId("sql-chat-turn");
  await expect(turn.getByTestId("sql-chat-progress-current")).toBeVisible();
  Object.assign(state.turns[0], {
    status: "error",
    created_at: iso(6_000),
    started_at: iso(5_000),
    finished_at: iso(0),
    error_message: "Select AI で SQL を生成できませんでした。",
    steps: [
      { stage: "prepare_context", status: "done", started_at: iso(5_000), finished_at: iso(4_000) },
      { stage: "generate_sql", status: "error", started_at: iso(4_000), finished_at: iso(0) },
      { stage: "safety_check", status: "pending" },
      { stage: "execute_sql", status: "pending" },
      { stage: "format_results", status: "pending" },
    ],
  });
  const progress = turn.getByTestId("sql-chat-progress");
  await expect(progress).toHaveAttribute("data-chat-progress-state", "failed");
  await expect(progress.locator("details")).toHaveAttribute("open", "");
  const failed = progress.getByTestId("sql-chat-progress-step-generate_sql");
  await expect(failed).toHaveAttribute("data-status", "failed");
  await expect(failed).toContainText("SQL を生成できませんでした");
  await expect(failed).toContainText("失敗");
  await expect(progress.getByTestId("sql-chat-progress-step-safety_check")).toContainText(
    "未実行",
  );
  await expect(turn.getByText("Select AI で SQL を生成できませんでした。")).toBeVisible();
  await applyColorScheme(page, "dark");
  await turn.screenshot({ path: testInfo.outputPath("chat-progress-failed-dark.png") });
});

test("上を読んでいる間に回答が届いても引き戻さず「最新のメッセージへ」を出し、押すと末尾へ戻る (#1161)", async ({
  page,
}) => {
  const state = await setup(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  const log = page.getByRole("log", { name: "会話" });
  const distanceFromBottom = () =>
    log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
  // 会話の欄がスクロールするだけの往復を作る。
  for (const question of ["売上", "地域別", "月別", "商品別", "前年比"]) {
    await composer.fill(question);
    await composer.press("Enter");
    await expect(page.getByTestId("sql-chat-turn")).toHaveCount(state.turns.length);
  }
  state.pending = true;
  await composer.fill("担当者別");
  await composer.press("Enter");
  await expect(page.getByTestId("sql-chat-send")).toHaveAccessibleName("停止");
  // 送った直後は末尾にいる。
  await expect.poll(distanceFromBottom).toBeLessThan(48);
  const latest = page.getByTestId("sql-chat-latest");
  await expect(latest).toHaveCount(0);

  // 上を読んでいる間に回答が届く（ポーリングで状態が変わる）。
  await log.evaluate((el) => el.scrollTo({ top: 0 }));
  const running = state.turns.at(-1)!;
  Object.assign(running, {
    status: "done",
    result: {
      generated_sql: "SELECT OWNER, SUM(AMOUNT) FROM APP.SALES GROUP BY OWNER",
      original_question: running.question,
      explanation: "担当者ごとの売上合計です。",
      safety: { is_safe: true },
    },
  });
  await expect(page.getByTestId("sql-chat-send")).toHaveAccessibleName("送信");
  await expect(latest).toBeVisible();
  await expect(latest).toHaveAccessibleName("最新のメッセージへ");
  // 引き戻さない。
  expect(await log.evaluate((el) => el.scrollTop)).toBeLessThan(48);

  await latest.click();
  await expect.poll(distanceFromBottom).toBeLessThan(48);
  await expect(latest).toHaveCount(0);
});
