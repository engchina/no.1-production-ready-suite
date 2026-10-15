import type { Page } from "@playwright/test";
import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { dbUser } from "./fixtures/auth";
import { chooseSelectFieldOption, withListFilters } from "./fixtures/select-field";
import { jumpToPage, measureHorizontalOverflow } from "../../../platform/packages/ui/e2e/layout-checks";

function seedRun(
  mockApi: MockApi,
  id = "run-layout",
  status = "completed",
  metadata: Record<string, unknown> = {},
) {
  mockApi.state.runs.push({
    id,
    goal: `売上を調べる ${id}`,
    agent_id: "default",
    runtime_id: "builtin",
    status,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    metadata,
    pending_tool_calls: [],
    approvals: [],
    steps: [
      {
        id: "step-query",
        kind: "tool",
        status: "completed",
        tool_call: { name: "nl2sql__nl2sql_query", arguments: {} },
        tool_result: {
          name: "nl2sql__nl2sql_query",
          success: true,
          output: { answer: "売上の集計" },
          error_details: {},
          started_at: MOCK_NOW,
          completed_at: MOCK_NOW,
          duration_ms: 1200,
          policy_decision: "allow",
          approval_required: false,
          guardrail_warnings: [],
          audit_metadata: {},
        },
      },
    ],
    events: [{ id: "event-result", type: "run.completed", message: "実行を完了しました。", created_at: MOCK_NOW, payload: {} }],
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

const TOOL_STEP = "ツール nl2sql__nl2sql_query を呼びました";

// #1874: 一覧の「種類」（きっかけ）に、外部の MCP クライアントの Run は「MCP（キーの名前）」を出す。
test("一覧の種類で MCP の Run とキーの名前が分かる", { tag: "@responsive" }, async ({ page, mockApi }) => {
  seedRun(mockApi);
  seedRun(mockApi, "run-mcp", "completed", { source: "mcp", api_key_id: "0123456789abcdef", api_key_name: "経理の連携" });
  seedRun(mockApi, "run-auto", "completed", { automation_id: "auto-1" });
  await page.goto("/runs");
  await expect(page.getByTestId("run-history-row-run-mcp")).toContainText("MCP（経理の連携）");
  await expect(page.getByTestId("run-history-row-run-auto")).toContainText("自動実行");
  await expect(page.getByTestId("run-history-row-run-layout")).toContainText("画面");
});

// #1955: 列は 3 製品で同じ順（質問 → 状態 → 種類 → 実行者 → 開始日時 → 所要時間 → token → 操作）。状態は共通の値。
test("一覧は共通の列の順で、状態は共通の値と Agent の言い方で出す (desktop)", async ({ page, mockApi }) => {
  seedRun(mockApi);
  seedRun(mockApi, "run-waiting", "waiting_approval");
  await page.goto("/runs");
  const table = page.getByRole("table", { name: "実行履歴" });
  await expect(table.getByRole("columnheader")).toHaveText([
    "質問",
    "状態",
    "種類",
    "実行者",
    "開始日時",
    "所要時間",
    "token",
    "操作",
  ]);
  const waiting = page.getByTestId("run-history-row-run-waiting");
  await expect(waiting.locator("td [data-run-status]").first()).toHaveAttribute("data-run-status", "waiting");
  await expect(waiting.locator("td [data-run-status]").first()).toHaveText("承認待ち");
  const done = page.getByTestId("run-history-row-run-layout");
  await expect(done.locator("td [data-run-status]").first()).toHaveAttribute("data-run-status", "succeeded");
  // 開始日時は絶対時刻、所要時間は終わった実行だけ（実行中・待ちは「—」）。
  await expect(done.locator("time").last()).toHaveAttribute("datetime", MOCK_NOW);
  await expect(waiting).toContainText("—");
});

test("狭い画面では題名の下に状態・日時・所要時間をまとめ、横にあふれない", async ({ page, mockApi }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  seedRun(mockApi);
  await page.goto("/runs");
  const row = page.getByTestId("run-history-row-run-layout");
  await expect(row.locator("[data-run-history-compact-meta]")).toBeVisible();
  await expect(row.locator("[data-run-history-compact-meta] [data-run-status]")).toHaveAttribute("data-run-status", "succeeded");
  expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
});

// 絞り込みは検索 → 状態 → 種類 → 期間 → 実行者の FilterSelect（#1933・#1955）。backend の条件に写して送る。
test("状態・種類・期間で絞り込み、条件で 0 件なら条件をクリアできる", { tag: "@responsive" }, async ({ page, mockApi }) => {
  seedRun(mockApi);
  seedRun(mockApi, "run-waiting", "waiting_approval");
  seedRun(mockApi, "run-auto", "completed", { automation_id: "auto-1" });
  await page.goto("/runs");
  const toolbar = page.getByTestId("run-list-toolbar");
  await withListFilters(toolbar, async (scope) => {
    await chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-status"), "waiting");
  });
  await expect.poll(() => mockApi.lastRequest("GET", "/api/runs")?.searchParams.get("status")).toBe("waiting");
  await expect(page.locator("a[data-run-id]")).toHaveCount(1);
  await expect(page.locator('a[data-run-id="run-waiting"]')).toBeVisible();
  await withListFilters(toolbar, async (scope) => {
    await chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-status"), "all");
    await chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-kind"), "automation");
    await chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-period"), "24h");
  });
  await expect.poll(() => mockApi.lastRequest("GET", "/api/runs")?.searchParams.get("kind")).toBe("automation");
  expect(mockApi.lastRequest("GET", "/api/runs")?.searchParams.get("since")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  // MOCK_NOW は過去の日時なので、過去 24 時間には入らない（条件で 0 件）。
  await expect(page.getByText("条件に一致する実行がありません")).toBeVisible();
  await page.getByRole("button", { name: "条件をクリア", exact: true }).first().click();
  await expect(page.locator("a[data-run-id]")).toHaveCount(3);
  expect(mockApi.lastRequest("GET", "/api/runs")?.searchParams.get("kind")).toBeNull();
});

test("そもそも実行が無いときは空の状態を出す", { tag: "@responsive" }, async ({ page }) => {
  await page.goto("/runs");
  await expect(page.getByText("実行はまだありません")).toBeVisible();
  await expect(page.locator("a[data-run-id]")).toHaveCount(0);
});

// #875: 分割ペインではなく、一覧 → 全幅の詳細を URL で開く。
test("一覧から詳細を開き、戻る・再読込・進むで対象と条件を保持する", { tag: "@responsive" }, async ({ page, mockApi }) => {
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
  await expect(page.getByText("売上合計は100万円です。", { exact: true })).toBeVisible();
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

// #1955: タブをやめ、概要 → 回答 → 処理の経過 → 成果物 → 技術的な詳細 → 監査ログを縦に 1 枚で読む。
test("詳細は節を縦に並べ、処理の経過は Enter で開き、監査ログは開いた時だけ取得する", { tag: "@responsive" }, async ({ page, mockApi }) => {
  seedRun(mockApi);
  await page.goto("/runs?id=run-layout");
  await expect(page.getByRole("tab")).toHaveCount(0);
  const detail = page.locator("[data-run-detail]");
  await expect(detail).toBeVisible();
  const sections = await detail.evaluate((node) =>
    Array.from(node.children).map((child) => child.getAttribute("data-testid"))
  );
  expect(sections).toEqual([
    "run-summary",
    "run-answer-section",
    "run-timeline-section",
    // 管理者の評価（フィードバックの節。Agent 管理の権限）。
    "run-review-section",
    "run-technical-section",
    "run-audit-section",
  ]);
  await expect(page.getByTestId("run-summary").locator("[data-run-status]").first()).toHaveAttribute(
    "data-run-status",
    "succeeded"
  );
  // 処理の経過のツールの段は、行のボタンを Enter で開いて結果を見る。
  const step = page.getByTestId("run-timeline-section").getByRole("button", { name: new RegExp(TOOL_STEP) });
  await expect(step).toHaveAttribute("aria-expanded", "false");
  await step.focus();
  await page.keyboard.press("Enter");
  await expect(step).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByTestId("run-timeline-section").getByText("売上の集計")).toBeVisible();
  // 技術的な詳細・監査ログは既定で閉じる。監査ログは開いたときだけ読む。
  await expect(page.getByTestId("run-technical-section")).not.toHaveAttribute("open", "");
  expect(mockApi.lastRequest("GET", "/api/runs/run-layout/audit")).toBeUndefined();
  await page.getByTestId("run-audit-section").locator("summary").click();
  await expect.poll(() => mockApi.lastRequest("GET", "/api/runs/run-layout/audit")).toBeTruthy();
  await page.getByTestId("run-technical-section").locator("summary").click();
  await expect(page.getByRole("group", { name: "ストリーム方式" })).toBeVisible();
});

test("作成は独立画面で入力を確認し、成功した実行の詳細へ移る", { tag: "@responsive" }, async ({ page, mockApi }) => {
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

test("存在しない実行の URL は他の結果へ置換せず、一覧に戻れる", { tag: "@responsive" }, async ({ page, mockApi }) => {
  seedRun(mockApi);
  await page.goto("/runs?id=missing-run");
  await expect(page.getByText("対象が見つかりません")).toBeVisible();
  await expect(page.getByRole("region", { name: "実行の詳細" })).toHaveCount(0);
  await page.locator("header[data-page-header]").getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(page.locator('a[data-run-id="run-layout"]')).toBeVisible();
});

test("閲覧だけの利用者には作成・監査・実行の操作を出さない", { tag: "@responsive" }, async ({ page, mockApi }) => {
  mockApi.setCurrentUser(dbUser({ permissions: ["menu.runs", "agent.runs.view"], allowed_agent_ids: ["default"] }));
  seedRun(mockApi);
  await page.goto("/runs");
  await expect(page.locator('a[data-run-id="run-layout"]')).toBeVisible();
  await expect(page.getByRole("button", { name: "実行を作成", exact: true }).first()).toHaveCount(0);
  // 自分の実行だけを見る利用者には「実行者」の列と絞り込みを出さない（#1955）。
  await expect(page.getByRole("columnheader", { name: "実行者" })).toHaveCount(0);
  await expect(page.getByTestId("run-list-toolbar-executor")).toHaveCount(0);
  await page.locator('a[data-run-id="run-layout"]').click();
  await expect(page.getByTestId("run-audit-section")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "再実行", exact: true })).toHaveCount(0);
  expect(mockApi.lastRequest("GET", "/api/runs/run-layout/audit")).toBeUndefined();
  await page.goto("/runs?id=new");
  await expect(page.getByText("実行を作成する権限がありません。")).toBeVisible();
  await expect(page.locator("#run-goal")).toHaveCount(0);
});

test("取得中・取得失敗・再試行・空の履歴を区別する", { tag: "@responsive" }, async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = true;
  // 一覧はサーバー側のページング（`?limit=25&offset=0`。#1897）。
  await page.route(/\/api\/runs(\?.*)?$/, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await gate;
    await route.fulfill({
      status: fail ? 503 : 200,
      json: fail
        ? { data: null, error_messages: ["履歴を取得できません"], warning_messages: [] }
        : {
            data: { items: [], total: 0, limit: 25, offset: 0, has_next: false, unfiltered_total: 0 },
            error_messages: [],
            warning_messages: [],
          },
    });
  });
  await page.goto("/runs");
  try {
    // 読み込み中は「実行履歴を読み込んでいます」と経過時間 + 表の形の Skeleton。
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

test("回答を中間成果物より先に出し、失敗した実行は理由と処理の経過を同じ画面に出す", { tag: "@responsive" }, async ({ page, mockApi }) => {
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
  run.events = [{ id: "event-failed", type: "runtime.failed", message: "モデルに接続できませんでした", created_at: MOCK_NOW, payload: {} }];
  await page.reload();
  await expect(page.getByTestId("run-answer-section")).toHaveCount(0);
  await expect(page.getByTestId("run-summary").locator("[data-run-status]").first()).toHaveAttribute(
    "data-run-status",
    "failed"
  );
  await expect(page.getByTestId("run-summary").getByRole("alert")).toContainText("モデルに接続できませんでした");
  await expect(page.getByTestId("run-timeline-section").getByText(TOOL_STEP)).toBeVisible();
});

test("承認待ちの判断は概要の操作から行い、承認の記録も同じ画面に出す", { tag: "@responsive" }, async ({ page, mockApi }) => {
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
  const summary = page.getByTestId("run-summary");
  await expect(summary.locator("[data-run-status]").first()).toHaveAttribute("data-run-status", "waiting");
  await expect(summary.locator("[data-run-status]").first()).toHaveText("承認待ち");
  const bar = page.getByTestId("run-object-actions");
  await expect(bar.getByRole("button", { name: "承認", exact: true })).toBeVisible();
  await expect(page.getByTestId("run-approvals-section")).toContainText("nl2sql__nl2sql_query");
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
test("判断の返却値が判断済みでなければ、承認の成功と案内しない", { tag: "@responsive" }, async ({ page, mockApi }) => {
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

test("確認中に他の操作者が判断した承認には、古い状態で判断を送らない", { tag: "@responsive" }, async ({ page, mockApi }) => {
  const approval = seedPendingApproval(mockApi);
  await page.goto("/runs?id=run-layout");
  await detailAction(page, "却下");
  // 確認の間に他の操作者が判断した。画面は判断を送る前にその実行を読み直して確かめる（#1897）。
  approval.status = "approved";
  approval.decided_by = "another.reviewer";
  approval.decided_at = MOCK_NOW;
  await page.getByRole("alertdialog").getByRole("button", { name: "却下", exact: true }).click();
  await expect(page.getByText(CHANGED_DURING_REVIEW, { exact: true })).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/approvals/approval-layout/decision")).toBeUndefined();
});

test("一覧の2ページ目から詳細を開いて戻ると、同じページ・条件・行に戻る", { tag: "@responsive" }, async ({ page, mockApi }) => {
  for (let index = 0; index < 53; index++) seedRun(mockApi, `run-layout-${index}`);
  await page.goto("/runs");
  const pagination = page.getByTestId("run-history-pagination");
  // ページ番号を入れて移る（#1891）。3 ページ目へ移ってから、次へ・前へで 2 ページ目にする。
  await jumpToPage(pagination, 3);
  await expect(pagination).toContainText("3 / 3");
  await pagination.getByRole("button", { name: "前へ", exact: true }).click();
  await withListFilters(page.getByTestId("run-list-toolbar"), async (scope) => {
    await chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-status"), "succeeded");
  });
  // 条件を変えたら 1 ページ目へ戻す。
  await expect(pagination).toContainText("1 / 3");
  await pagination.getByRole("button", { name: "次へ", exact: true }).click();
  const link = page.locator('a[data-run-id="run-layout-25"]');
  await expect(link).toBeVisible();
  // ページはサーバー側で切り出す（1 ページ 25 件。全 Run を読まない。#1897・#1952）。
  const listQuery = mockApi.lastRequest("GET", "/api/runs")?.searchParams;
  expect([listQuery?.get("limit"), listQuery?.get("offset"), listQuery?.get("status")]).toEqual(["25", "25", "completed"]);
  await expect(page.locator("a[data-run-id]")).toHaveCount(25);
  await link.click();
  await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(pagination).toContainText("2 / 3");
  await expect(link).toBeFocused();
  await expect(link).toHaveAttribute("aria-current", "true");
  expect(mockApi.lastRequest("GET", "/api/runs")?.searchParams.get("status")).toBe("completed");
});

for (const viewport of [
  { width: 1920, height: 1080 },
  { width: 1280, height: 480 },
  { width: 375, height: 812 },
  { width: 812, height: 375 },
]) {
  test(`全幅の履歴と詳細は明暗・文字拡大でもページ外へスクロールしない (${viewport.width}px)`, { tag: "@responsive" }, async ({
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
      // 技術的な詳細を開いても本文の幅は変わらない。
      const technical = page.getByTestId("run-technical-section");
      await technical.locator("summary").click();
      expect((await checkBounds()).mainWidth).toBe(before.mainWidth);
      await page.evaluate(() => {
        document.documentElement.style.fontSize = "20px";
      });
      await technical.locator("summary").click();
      const expanded = await checkBounds();
      expect(expanded.width).toBeLessThanOrEqual(0);
      expect(expanded.height).toBeLessThanOrEqual(0);
      // 文字拡大は rem のサイドバーも拡大する。比較するのは同じ文字サイズでの節の開閉。
      await technical.locator("summary").click();
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
  test(`${path} を開いた直後の最初の Tab で「本文へスキップ」にフォーカスが当たる`, { tag: "@responsive" }, async ({ page, mockApi }) => {
    seedRun(mockApi);
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("main h1")).not.toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "本文へスキップ" })).toBeFocused();
  });
}
