import type { Page } from "@playwright/test";
import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { dbUser } from "./fixtures/auth";

function seedRun(mockApi: MockApi, id = "run-layout", status = "completed") {
  mockApi.state.runs.push({
    id,
    goal: `売上を調べる ${id}`,
    agent_id: "default",
    runtime_id: "builtin",
    status,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    metadata: {},
    pending_tool_calls: [],
    approvals: [],
    steps: [
      {
        id: "step-query",
        kind: "tool",
        status: "completed",
        tool_call: { name: "nl2sql__nl2sql_query", arguments: {} },
      },
    ],
    events: [{ id: "event-result", type: "run.completed", created_at: MOCK_NOW, payload: {} }],
    artifacts: [
      {
        id: "answer-layout",
        kind: "answer",
        name: "回答",
        content: { text: "売上合計は100万円です。" },
        created_at: MOCK_NOW,
      },
    ],
  });
}

// #875: 分割ペインではなく、一覧 → 全幅の結果を URL で開く。
test("一覧から結果を開き、戻る・再読込・進むで対象と検索を保持する", async ({ page, mockApi }) => {
  seedRun(mockApi);
  seedRun(mockApi, "run-other");
  await page.goto("/runs");
  await expect(page.locator("#run-goal")).toHaveCount(0);
  await expect(page.getByTestId("fixed-split-pane-runs-list")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "実行の詳細" })).toHaveCount(0);
  await page.locator("#run-search").fill("run-layout");
  const link = page.locator('a[data-run-id="run-layout"]');
  await expect(page.locator('a[data-run-id="run-other"]')).toHaveCount(0);
  await expect(link).toHaveAttribute("href", "/runs?id=run-layout");
  await link.click();
  await expect(page).toHaveURL(/\/runs\?id=run-layout$/);
  await expect(page.getByRole("tab", { name: "結果", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("売上合計は100万円です。", { exact: true })).toBeVisible();
  await expect(page.getByText("nl2sql__nl2sql_query", { exact: true })).toBeHidden();
  await page.reload();
  await expect(page.getByText("売上合計は100万円です。", { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/runs$/);
  await expect(page.locator("#run-search")).toHaveValue("run-layout");
  await expect(link).toBeFocused();
  await expect(link).toHaveAttribute("aria-current", "true");
  await page.goForward();
  await expect(page.getByRole("region", { name: "実行の詳細" })).toContainText("run-layout");
  await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(page.locator("#run-search")).toHaveValue("run-layout");
  await expect(link).toBeFocused();
});

test("結果・経過・監査のタブをキーボードで選び、監査は開いた時だけ取得する", async ({ page, mockApi }) => {
  seedRun(mockApi);
  await page.goto("/runs?id=run-layout");
  const result = page.getByRole("tab", { name: "結果", exact: true });
  await expect(result).toHaveAttribute("aria-selected", "true");
  expect(mockApi.lastRequest("GET", "/api/runs/run-layout/audit")).toBeUndefined();
  await result.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "実行の経過", exact: true })).toBeFocused();
  await expect(page.getByText("nl2sql__nl2sql_query", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "ストリーム方式" })).toBeHidden();
  expect(mockApi.lastRequest("GET", "/api/runs/run-layout/audit")).toBeUndefined();
  await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: "監査ログ", exact: true })).toBeFocused();
  await expect.poll(() => mockApi.lastRequest("GET", "/api/runs/run-layout/audit")).toBeTruthy();
  await page.keyboard.press("Home");
  await expect(result).toBeFocused();
  await expect(page.getByText("売上合計は100万円です。", { exact: true })).toBeVisible();
});

test("作成は独立画面で入力を確認し、成功した実行の詳細へ移る", async ({ page, mockApi }) => {
  await page.goto("/runs");
  await page.getByRole("button", { name: "実行を作成", exact: true }).click();
  await expect(page).toHaveURL(/\/runs\?id=new$/);
  await expect(page.getByRole("table", { name: "実行履歴" })).toHaveCount(0);
  const goal = page.locator("#run-goal");
  await goal.fill("");
  await page.getByTestId("run-create-submit").click();
  await expect(goal).toHaveAttribute("aria-invalid", "true");
  await expect(goal).toBeFocused();
  expect(mockApi.lastRequest("POST", "/api/runs")).toBeUndefined();
  await goal.fill("売上の推移を調べて");
  await goal.press("Control+Enter");
  await expect(page).toHaveURL(/\/runs\?id=run-/);
  await expect(page.getByRole("region", { name: "実行の詳細" })).toContainText("売上の推移を調べて");
  await expect(page.getByText("対象が見つかりません")).toHaveCount(0);
  expect(mockApi.lastRequest("POST", "/api/runs")?.body).toMatchObject({
    agent_id: "default",
    goal: "売上の推移を調べて",
  });
});

test("存在しない実行の URL は他の結果へ置換せず、一覧に戻れる", async ({ page, mockApi }) => {
  seedRun(mockApi);
  await page.goto("/runs?id=missing-run");
  await expect(page.getByText("対象が見つかりません")).toBeVisible();
  await expect(page.getByRole("region", { name: "実行の詳細" })).toHaveCount(0);
  await page.locator("header[data-page-header]").getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(page.locator('a[data-run-id="run-layout"]')).toBeVisible();
});

test("閲覧だけの利用者には作成・監査・実行の操作を出さない", async ({ page, mockApi }) => {
  mockApi.setCurrentUser(dbUser({ permissions: ["menu.runs", "agent.runs.view"], allowed_agent_ids: ["default"] }));
  seedRun(mockApi);
  await page.goto("/runs");
  await expect(page.locator('a[data-run-id="run-layout"]')).toBeVisible();
  await expect(page.getByRole("button", { name: "実行を作成", exact: true })).toHaveCount(0);
  await page.locator('a[data-run-id="run-layout"]').click();
  await expect(page.getByRole("tab", { name: "監査ログ", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "再実行", exact: true })).toHaveCount(0);
  expect(mockApi.lastRequest("GET", "/api/runs/run-layout/audit")).toBeUndefined();
  await page.goto("/runs?id=new");
  await expect(page.getByText("実行を作成する権限がありません。")).toBeVisible();
  await expect(page.locator("#run-goal")).toHaveCount(0);
});

test("取得中・取得失敗・再試行・空の履歴を区別する", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = true;
  await page.route("**/api/runs", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await gate;
    await route.fulfill({
      status: fail ? 503 : 200,
      json: fail
        ? { data: null, error_messages: ["履歴を取得できません"], warning_messages: [] }
        : { data: { runs: [] }, error_messages: [], warning_messages: [] },
    });
  });
  await page.goto("/runs");
  try {
    await expect(page.getByTestId("query-loading")).toBeVisible();
  } finally {
    release();
  }
  await expect(page.getByText("履歴を取得できません", { exact: true })).toBeVisible({ timeout: 15_000 });
  fail = false;
  await page.getByRole("button", { name: "再試行", exact: true }).click();
  await expect(page.getByTestId("query-loading")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "実行を作成", exact: true })).toBeVisible();
  await expect(page.locator("a[data-run-id]")).toHaveCount(0);
});

test("回答を中間成果物より先に出し、失敗時には経過への導線を出す", async ({ page, mockApi }) => {
  seedRun(mockApi);
  const run = mockApi.state.runs[0];
  (run.artifacts as Record<string, unknown>[]).unshift({
    id: "raw-result",
    name: "中間結果",
    kind: "json",
    content: { rows: [1] },
    created_at: MOCK_NOW,
  });
  await page.goto("/runs?id=run-layout");
  const answerTop = await page
    .getByText("売上合計は100万円です。", { exact: true })
    .evaluate((node) => node.getBoundingClientRect().top);
  const rawTop = await page.getByText("中間結果", { exact: true }).evaluate((node) => node.getBoundingClientRect().top);
  expect(answerTop).toBeLessThan(rawTop);
  run.status = "failed";
  run.artifacts = [];
  await page.reload();
  await expect(page.getByText("成果物はまだありません")).toBeVisible();
  await page.getByRole("button", { name: "実行の経過", exact: true }).click();
  await expect(page.getByText("nl2sql__nl2sql_query", { exact: true })).toBeVisible();
});

test("承認待ちの判断は結果と経過のどちらでも見つかる", async ({ page, mockApi }) => {
  seedRun(mockApi, "run-layout", "waiting_approval");
  mockApi.state.runs[0].approvals = [
    {
      id: "approval-layout",
      run_id: "run-layout",
      step_id: "step-query",
      status: "pending",
      tool_call: { name: "nl2sql__nl2sql_query", arguments: {} },
      reason: "承認が必要",
      created_at: MOCK_NOW,
    },
  ];
  await page.goto("/runs?id=run-layout");
  const bar = page.getByTestId("run-object-actions");
  await expect(bar.getByRole("button", { name: "承認", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "実行の経過", exact: true }).click();
  await expect(bar.getByRole("button", { name: "承認", exact: true })).toBeVisible();
});

function seedPendingApproval(mockApi: MockApi) {
  seedRun(mockApi, "run-layout", "waiting_approval");
  const approval: Record<string, unknown> = {
    id: "approval-layout",
    run_id: "run-layout",
    step_id: "step-query",
    status: "pending",
    tool_call: { name: "nl2sql__nl2sql_query", arguments: {} },
    reason: "承認が必要",
    created_at: MOCK_NOW,
  };
  mockApi.state.runs[0].approvals = [approval];
  return approval;
}

const CHANGED_DURING_REVIEW = "この承認の状態が変わりました。最新の内容を確認してください。";

/** 詳細の操作（狭い幅では「その他の操作」に入る）を押す。 */
async function detailAction(page: Page, name: string) {
  const bar = page.getByTestId("run-object-actions");
  await expect(bar).toBeVisible();
  const direct = bar.getByRole("button", { name, exact: true });
  if (await direct.count()) return direct.click();
  await bar.getByRole("button", { name: /その他の操作/ }).click();
  await page.getByRole("menuitem", { name, exact: true }).click();
}

// #919: 承認の画面（#877）と同じく、判断の結果は返却値で確かめる。
test("判断の返却値が判断済みでなければ、承認の成功と案内しない", async ({ page, mockApi }) => {
  const approval = seedPendingApproval(mockApi);
  await page.route("**/api/approvals/approval-layout/decision", async (route) => {
    // 送る前に実行が取り消された（backend は 200 で、承認を cancelled にした Run を返す）。
    approval.status = "cancelled";
    mockApi.state.runs[0].status = "cancelled";
    await route.fulfill({ json: { data: mockApi.state.runs[0], error_messages: [], warning_messages: [] } });
  });
  await page.goto("/runs?id=run-layout");
  await detailAction(page, "承認");
  await page.getByRole("alertdialog").getByRole("button", { name: "承認", exact: true }).click();
  await expect(page.getByText(CHANGED_DURING_REVIEW, { exact: true })).toBeVisible();
  await expect(page.getByText("ツールの実行を承認しました", { exact: true })).toHaveCount(0);
  // 返却値（取消済み）を反映し、承認待ちの表示を消す。
  await expect(page.getByText("承認待ち", { exact: true })).toHaveCount(0);
});

test("確認中に他の操作者が判断した承認には、古い状態で判断を送らない", async ({ page, mockApi }) => {
  const approval = seedPendingApproval(mockApi);
  await page.goto("/runs?id=run-layout");
  await detailAction(page, "却下");
  const requestCount = mockApi.requests.filter((item) => item.path === "/api/runs").length;
  approval.status = "approved";
  approval.decided_by = "another.reviewer";
  approval.decided_at = MOCK_NOW;
  await expect
    .poll(() => mockApi.requests.filter((item) => item.path === "/api/runs").length, { timeout: 8_000 })
    .toBeGreaterThan(requestCount);
  await page.getByRole("alertdialog").getByRole("button", { name: "却下", exact: true }).click();
  await expect(page.getByText(CHANGED_DURING_REVIEW, { exact: true })).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/approvals/approval-layout/decision")).toBeUndefined();
});

test("一覧の2ページ目から詳細を開いて戻ると、同じページと行に戻る", async ({ page, mockApi }) => {
  for (let index = 0; index < 23; index++) seedRun(mockApi, `run-layout-${index}`);
  await page.goto("/runs");
  await page.getByTestId("run-history-pagination").getByRole("button", { name: "次へ", exact: true }).click();
  const link = page.locator('a[data-run-id="run-layout-10"]');
  await expect(link).toBeVisible();
  await link.click();
  await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(page.getByTestId("run-history-pagination")).toContainText("2 / 3");
  await expect(link).toBeFocused();
});

for (const viewport of [
  { width: 1920, height: 1080 },
  { width: 1280, height: 480 },
  { width: 375, height: 812 },
  { width: 812, height: 375 },
]) {
  test(`全幅の履歴と詳細は明暗・文字拡大でもページ外へスクロールしない (${viewport.width}px)`, async ({
    page,
    mockApi,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (let index = 0; index < 14; index++) seedRun(mockApi, `run-layout-${index}`);
    await page.goto("/runs");
    await expect(page.locator('a[data-run-id="run-layout-0"]')).toBeVisible();
    for (const theme of ["light", "dark"]) {
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      await page.screenshot({ path: testInfo.outputPath(`runs-list-${theme}.png`) });
      await page.locator('a[data-run-id="run-layout-0"]').click();
      await expect(page.getByText("売上合計は100万円です。", { exact: true })).toBeVisible();
      const checkBounds = () =>
        page.evaluate(() => ({
          width: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          height: document.documentElement.scrollHeight - document.documentElement.clientHeight,
          mainWidth: document.querySelector("main")!.clientWidth,
        }));
      const before = await checkBounds();
      expect(before.width).toBeLessThanOrEqual(0);
      expect(before.height).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`runs-detail-${theme}.png`) });
      await page.getByRole("tab", { name: "実行の経過", exact: true }).click();
      expect((await checkBounds()).mainWidth).toBe(before.mainWidth);
      await page.evaluate(() => {
        document.documentElement.style.fontSize = "20px";
      });
      await page.getByRole("tab", { name: "実行の経過", exact: true }).click();
      const expanded = await checkBounds();
      expect(expanded.width).toBeLessThanOrEqual(0);
      expect(expanded.height).toBeLessThanOrEqual(0);
      // 文字拡大は rem のサイドバーも拡大する。比較するのは同じ文字サイズでのタブの切替。
      await page.getByRole("tab", { name: "結果", exact: true }).click();
      expect((await checkBounds()).mainWidth).toBe(expanded.mainWidth);
      await page.evaluate(() => {
        document.documentElement.style.fontSize = "";
      });
      await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
    }
  });
}

// #1122: 開いた直後はフォーカスを動かさない。最初の Tab は「本文へスキップ」（一覧 ↔ 詳細の切り替えだけ見出し・行へ移す）。
for (const path of ["/runs", "/runs?id=run-layout", "/runs?id=new"]) {
  test(`${path} を開いた直後の最初の Tab で「本文へスキップ」にフォーカスが当たる`, async ({ page, mockApi }) => {
    seedRun(mockApi);
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("main h1")).not.toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "本文へスキップ" })).toBeFocused();
  });
}
