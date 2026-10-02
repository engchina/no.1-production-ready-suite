/**
 * 一覧の読み込み中・縦スクロール・ページングを NL2SQL の基準にそろえたことの確認（#265）。
 *
 * - 読み込み中: 「〜を読み込んでいます」と経過時間（TimedLoadingState）＋ 内容の形の Skeleton。
 * - 読み込み中に「未設定」などの誤った警告を出さない。
 * - 一覧: 表頭の固定と、md 未満 5 行・md 以上 8 行で表の中の縦スクロール。
 * - ページング: 共通の Pagination（10 件/ページ）。ページ番号は作業状態に残り、5 秒ごとの再取得でも戻らない。
 */
import type { Page, Route } from "@playwright/test";

import { expect, MOCK_NOW, test, type MockApi } from "./fixtures/mock-api";

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800, visibleRows: 8 },
  { name: "mobile", width: 375, height: 812, visibleRows: 5 },
] as const;

/** 応答を `release()` まで止める route。止めている間の表示を確かめる。 */
async function holdResponses(page: Page, pattern: string) {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(pattern, async (route: Route) => {
    await gate;
    await route.fallback();
  });
  return () => release();
}

function seedAgents(mockApi: MockApi, count: number) {
  const base = mockApi.state.agents[0];
  for (let index = 1; index <= count; index += 1) {
    const id = `agent-${String(index).padStart(2, "0")}`;
    mockApi.state.agents.push({ ...base, id, name: `検証 Agent ${String(index).padStart(2, "0")}`, source: "runtime" });
  }
}

function seedRuns(mockApi: MockApi, count: number) {
  for (let index = 1; index <= count; index += 1) {
    const id = `run-${String(index).padStart(2, "0")}`;
    mockApi.state.runs.push({
      id,
      goal: `検証の実行 ${String(index).padStart(2, "0")}`,
      agent_id: "default",
      runtime_id: "legacy-native",
      status: "completed",
      steps: [],
      events: [],
      approvals: [],
      artifacts: [],
      pending_tool_calls: [],
      metadata: {},
      created_at: MOCK_NOW,
      updated_at: MOCK_NOW,
    });
  }
}

function seedAuditRecords(mockApi: MockApi, count: number) {
  for (let index = 1; index <= count; index += 1) {
    mockApi.state.auditRecords.push({
      run_id: `run-${index}`,
      run_goal: `監査の対象 ${String(index).padStart(2, "0")}`,
      run_status: "completed",
      run_created_at: MOCK_NOW,
      step_id: `step-${index}`,
      tool_name: "echo",
      status: "completed",
      approval_status: null,
      policy_decision: "allow",
      permission_level: "read",
      guardrail_warnings: [],
      duration_ms: 10,
      trace_id: `trace-${index}`,
      artifact_ids: [],
      error_code: null,
    });
  }
}

/** 表の中の縦スクロール領域が、表示行数ぶんの高さで止まり、中でスクロールできること。 */
async function expectScrollsInsideTable(page: Page, regionName: string, visibleRows: number) {
  const region = page.getByRole("region", { name: regionName });
  await expect(region).toBeVisible();
  const metrics = await region.evaluate((element) => {
    const header = element.querySelector("thead");
    const rows = Array.from(element.querySelectorAll("tbody tr"));
    return {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      headerHeight: header?.getBoundingClientRect().height ?? 0,
      rowHeight: rows[0]?.getBoundingClientRect().height ?? 0,
      rowCount: rows.length,
      stickyHeader: header ? getComputedStyle(header).position : "",
    };
  });
  expect(metrics.rowCount).toBe(10);
  expect(metrics.stickyHeader).toBe("sticky");
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  // 表頭 + N 行（行の最小高さ 3.5rem）の高さで止まる。1 行ぶんの誤差を許す。
  const expected = metrics.headerHeight + metrics.rowHeight * visibleRows;
  expect(Math.abs(metrics.clientHeight - expected)).toBeLessThan(metrics.rowHeight);
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

for (const viewport of VIEWPORTS) {
  test.describe(`一覧の読み込み中・縦スクロール・ページング (${viewport.name})`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("業務 Agent: 読み込み中は形の Skeleton と経過時間を出す", async ({ page, mockApi }) => {
      seedAgents(mockApi, 22);
      const releaseAgents = await holdResponses(page, "**/api/agents");
      await page.goto("/agents");

      const loading = page.getByTestId("query-loading");
      await expect(loading).toBeVisible();
      await expect(loading).toContainText("業務 Agent を読み込んでいます");
      await expect(loading).toContainText("経過時間");
      await expect(loading.locator('[data-skeleton="table"]')).toBeVisible();
      // 見出しは隠さない（messaging.md §3.7）。
      await expect(page.getByRole("heading", { name: "業務 Agent 一覧" })).toBeVisible();

      releaseAgents();
      await expect(page.getByTestId("agent-row-default")).toBeVisible();

      await expectScrollsInsideTable(page, "業務 Agent 一覧。スクロールできます。", viewport.visibleRows);
      const pager = page.getByTestId("agent-list-pagination");
      await expect(pager).toContainText("1 - 10 / 23 件");
      await expect(pager).toContainText("1 / 3 ページ");
      await expectNoHorizontalOverflow(page);
    });

    test("業務 Agent: ページはエディタとの往復と再読込で保つ", async ({ page, mockApi }) => {
      seedAgents(mockApi, 22);
      await page.goto("/agents");
      const pager = page.getByTestId("agent-list-pagination");
      await pager.getByRole("button", { name: "次へ" }).click();
      await expect(pager).toContainText("11 - 20 / 23 件");

      await page.getByRole("link", { name: /^検証 Agent 12 agent-12/ }).click();
      await expect(page).toHaveURL(/\/agents\?id=agent-12$/);
      // 375px ではヘッダーの操作がメニューに入るため、ブラウザの戻るで一覧へ戻る。
      await page.goBack();
      await expect(page).toHaveURL(/\/agents$/);
      await expect(pager).toContainText("11 - 20 / 23 件");

      await page.reload();
      await expect(pager).toContainText("11 - 20 / 23 件");
      await expect(page.getByRole("link", { name: /^検証 Agent 12 agent-12/ })).toBeVisible();
    });

    test("実行履歴: 再取得で行が増えてもページが戻らない", async ({ page, mockApi }) => {
      seedRuns(mockApi, 23);
      await page.goto("/runs");
      const pager = page.getByTestId("run-history-pagination");
      await expect(pager).toContainText("1 - 10 / 23 件");
      await expectScrollsInsideTable(page, "実行履歴。スクロールできます。", viewport.visibleRows);
      await pager.getByRole("button", { name: "次へ" }).click();
      await expect(pager).toContainText("11 - 20 / 23 件");

      // 5 秒ごとの再取得と同じく、一覧を取り直す（行も増える）。
      seedRuns(mockApi, 1);
      const refetched = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/runs");
      await page.getByRole("button", { name: "表示を更新" }).click();
      await refetched;
      await expect(pager).toContainText("11 - 20 / 24 件");
      await expect(pager).toContainText("2 / 3 ページ");
    });

    test("監査: API の offset でページを送り、読み込み中は表の形の Skeleton を出す", async ({ page, mockApi }) => {
      seedAuditRecords(mockApi, 25);
      const release = await holdResponses(page, "**/api/audit/tool-calls?**");
      await page.goto("/audit");
      const loading = page.getByTestId("query-loading");
      await expect(loading).toContainText("監査記録を読み込んでいます");
      await expect(loading.locator('[data-skeleton="table"]')).toBeVisible();
      release();

      const pager = page.getByTestId("audit-pagination");
      await expect(pager).toContainText("1 - 10 / 25 件");
      await expect.poll(() => mockApi.lastRequest("GET", "/api/audit/tool-calls")?.searchParams.get("limit")).toBe("10");
      await expectScrollsInsideTable(page, "監査レコード。スクロールできます。", viewport.visibleRows);

      await pager.getByRole("button", { name: "次へ" }).click();
      await expect(pager).toContainText("11 - 20 / 25 件");
      await expect
        .poll(() => mockApi.lastRequest("GET", "/api/audit/tool-calls")?.searchParams.get("offset"))
        .toBe("10");
      await expect(page.getByText("監査の対象 11")).toBeVisible();

      // ページも作業状態に残る。
      await page.reload();
      await expect(pager).toContainText("11 - 20 / 25 件");
      await expectNoHorizontalOverflow(page);
    });

    test("監査: 「フィルター適用」と「表示を更新」は押した側だけが回り、ページの切り替えではどちらも回さない（#819）", async ({ page, mockApi }) => {
      seedAuditRecords(mockApi, 25);
      await page.goto("/audit");
      const pager = page.getByTestId("audit-pagination");
      await expect(pager).toContainText("1 - 10 / 25 件");
      const apply = page.getByRole("button", { name: "フィルター適用", exact: true });
      const refresh = page.getByRole("button", { name: "表示を更新" });

      // フィルター適用: フィルター適用だけが回り、表示を更新は押せないだけ。
      let release = await holdResponses(page, "**/api/audit/tool-calls?**");
      await apply.click();
      await expect(apply).toHaveAttribute("aria-busy", "true");
      await expect(refresh).toBeDisabled();
      await expect(refresh).not.toHaveAttribute("aria-busy", /.*/);
      release();
      await expect(apply).not.toHaveAttribute("aria-busy", /.*/);

      // ページの切り替え（前のページを出したままの取り直し）では、どちらも回さない。
      release = await holdResponses(page, "**/api/audit/tool-calls?**");
      const nextPageRequested = page.waitForRequest(
        (request) =>
          new URL(request.url()).pathname === "/api/audit/tool-calls" &&
          new URL(request.url()).searchParams.get("offset") === "10"
      );
      await pager.getByRole("button", { name: "次へ" }).click();
      await nextPageRequested;
      await expect(apply).not.toHaveAttribute("aria-busy", /.*/);
      await expect(refresh).not.toHaveAttribute("aria-busy", /.*/);
      release();
      await expect(pager).toContainText("11 - 20 / 25 件");

      // 表示を更新: 表示を更新だけが回り、フィルター適用は押せないだけ。
      release = await holdResponses(page, "**/api/audit/tool-calls?**");
      await refresh.click();
      await expect(refresh).toHaveAttribute("aria-busy", "true");
      await expect(apply).toBeDisabled();
      await expect(apply).not.toHaveAttribute("aria-busy", /.*/);
      release();
      await expect(refresh).not.toHaveAttribute("aria-busy", /.*/);
      await expectNoHorizontalOverflow(page);
    });
  });
}
