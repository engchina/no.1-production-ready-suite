import type { Page } from "@playwright/test";
import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { dbUser } from "./fixtures/auth";

// 日時はホストやブラウザの timezone ではなく JST で示す。
test.use({ timezoneId: "America/New_York" });

function seedApproval(mockApi: MockApi, id = "approval-layout", status = "pending") {
  const approval = {
    id,
    run_id: `run-${id}`,
    step_id: `step-${id}`,
    status,
    tool_call: { name: "nl2sql__nl2sql_query", arguments: { question: "売上を集計して", scope: "承認済みの国内取引" } },
    reason: "データの参照範囲を確認してください。",
    created_at: MOCK_NOW,
    decided_by: status === "pending" ? null : "reviewer.user",
    decided_at: status === "pending" ? null : MOCK_NOW,
  };
  mockApi.state.runs.push({
    id: approval.run_id,
    goal: `売上分析 ${id}`,
    agent_id: "default",
    runtime_id: "builtin",
    status: "waiting_approval",
    approvals: [approval],
    steps: [],
    events: [],
    artifacts: [],
    metadata: {},
    pending_tool_calls: [],
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
  return approval;
}

async function detailAction(page: Page, name: string) {
  const bar = page.getByTestId("approval-object-actions");
  await expect(bar).toBeVisible();
  const direct = bar.getByRole("button", { name, exact: true });
  if (await direct.count()) return direct.click();
  await bar.getByRole("button", { name: /その他の操作/ }).click();
  await page.getByRole("menuitem", { name, exact: true }).click();
}

test("全幅のキューから詳細を開き、URL と検索・選択行を戻る操作で保つ", async ({ page, mockApi }) => {
  seedApproval(mockApi);
  seedApproval(mockApi, "approval-other");
  await page.goto("/approvals");
  await expect(page.getByTestId("fixed-split-pane-approvals-list")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "承認の詳細" })).toHaveCount(0);
  await page.locator("#approval-search").fill("approval-layout");
  await expect(page.locator('a[data-approval-id="approval-other"]')).toHaveCount(0);
  const link = page.locator('a[data-approval-id="approval-layout"]');
  await expect(link).toHaveAttribute("href", "/approvals?id=approval-layout");
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/approvals\?id=approval-layout$/);
  const detail = page.getByRole("region", { name: "承認の詳細" });
  await expect(detail).toContainText("データの参照範囲を確認してください。");
  await expect(detail).toContainText("承認済みの国内取引");
  await expect(detail).toContainText("2026/06/28 09:00");
  await page.reload();
  await expect(detail).toContainText("売上分析 approval-layout");
  await page.goBack();
  await expect(page.locator("#approval-search")).toHaveValue("approval-layout");
  await expect(link).toBeFocused();
  await page.goForward();
  await expect(detail).toContainText("approval-layout");
  await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(link).toBeFocused();
  await expect(link).toHaveAttribute("aria-current", "true");
});

for (const approved of [true, false]) {
  test(`${approved ? "承認" : "却下"}の確認は対象を示し、判断後も同じ承認の記録を表示する`, async ({
    page,
    mockApi,
  }) => {
    seedApproval(mockApi);
    seedApproval(mockApi, "approval-next");
    const label = approved ? "承認" : "却下";
    await page.goto("/approvals?id=approval-layout");
    await detailAction(page, label);
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("売上分析 approval-layout");
    await expect(dialog).toContainText("承認 ID: approval-layout");
    await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
    expect(mockApi.lastRequest("POST", "/api/approvals/approval-layout/decision")).toBeUndefined();
    await detailAction(page, label);
    await dialog.getByRole("button", { name: label, exact: true }).click();
    await expect(page.getByTestId("approval-decision-record")).toContainText("local");
    await expect(page.getByTestId("approval-decision-record")).toContainText("2026/06/28 09:00");
    await expect(page).toHaveURL(/id=approval-layout$/);
    await expect(page.getByRole("region", { name: "承認の詳細" })).toContainText(approved ? "承認済み" : "却下済み");
    await expect(page.getByTestId("approval-object-actions")).toHaveCount(0);
    expect(mockApi.lastRequest("POST", "/api/approvals/approval-layout/decision")?.body).toEqual({ approved });
    await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
    await expect(page.locator('a[data-approval-id="approval-layout"]')).toHaveCount(0);
    await expect(page.locator('a[data-approval-id="approval-next"]')).toBeVisible();
    await expect(page.getByRole("heading", { name: "承認", level: 1 })).toBeFocused();
  });
}

test("判断中は押した操作だけを処理中にし、返却値で即時に判断済みへ更新する", async ({ page, mockApi }) => {
  seedApproval(mockApi);
  let releasePost!: () => void;
  let releaseGet!: () => void;
  const postGate = new Promise<void>((resolve) => {
    releasePost = resolve;
  });
  const getGate = new Promise<void>((resolve) => {
    releaseGet = resolve;
  });
  let pauseGet = false;
  await page.route("**/api/runs", async (route) => {
    if (pauseGet && route.request().method() === "GET") await getGate;
    return route.fallback();
  });
  await page.route("**/api/approvals/approval-layout/decision", async (route) => {
    await postGate;
    return route.fallback();
  });
  await page.goto("/approvals?id=approval-layout");
  await detailAction(page, "承認");
  await page.getByRole("alertdialog").getByRole("button", { name: "承認", exact: true }).click();
  try {
    const bar = page.getByTestId("approval-object-actions");
    const approve = bar.getByRole("button", { name: "承認", exact: true });
    await expect(approve).toHaveAttribute("aria-busy", "true");
    await expect(approve).toBeDisabled();
    await bar.getByRole("button", { name: /その他の操作/ }).click();
    await expect(page.getByRole("menuitem", { name: "却下", exact: true })).toBeDisabled();
    pauseGet = true;
    releasePost();
    await expect(page.getByTestId("approval-decision-record")).toBeVisible();
    await expect(bar).toHaveCount(0);
    expect(
      mockApi.requests.filter(
        (item) => item.method === "POST" && item.path === "/api/approvals/approval-layout/decision"
      )
    ).toHaveLength(1);
  } finally {
    releasePost();
    releaseGet();
  }
});

test("確認中に他の操作者が判断した承認には古い状態で送信しない", async ({ page, mockApi }) => {
  const approval = seedApproval(mockApi);
  await page.goto("/approvals?id=approval-layout");
  await detailAction(page, "承認");
  const requestCount = mockApi.requests.filter((item) => item.path === "/api/runs").length;
  approval.status = "approved";
  approval.decided_by = "another.reviewer";
  approval.decided_at = MOCK_NOW;
  await expect
    .poll(() => mockApi.requests.filter((item) => item.path === "/api/runs").length, { timeout: 8_000 })
    .toBeGreaterThan(requestCount);
  await expect(page.getByTestId("approval-decision-record")).toContainText("another.reviewer");
  await page.getByRole("alertdialog").getByRole("button", { name: "承認", exact: true }).click();
  await expect(
    page.getByText("この承認の状態が変わりました。最新の内容を確認してください。", { exact: true })
  ).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/approvals/approval-layout/decision")).toBeUndefined();
  await expect(page).toHaveURL(/id=approval-layout$/);
});

test("判断の失敗は理由を知らせ、同じ承認で再試行できる", async ({ page, mockApi }) => {
  seedApproval(mockApi);
  let fail = true;
  await page.route("**/api/approvals/approval-layout/decision", async (route) => {
    if (!fail) return route.fallback();
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["判断を保存できませんでした"], warning_messages: [] },
    });
  });
  await page.goto("/approvals?id=approval-layout");
  await detailAction(page, "承認");
  await page.getByRole("alertdialog").getByRole("button", { name: "承認", exact: true }).click();
  await expect(page.getByText("判断を保存できませんでした", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/id=approval-layout$/);
  fail = false;
  await detailAction(page, "承認");
  await page.getByRole("alertdialog").getByRole("button", { name: "承認", exact: true }).click();
  await expect(page.getByTestId("approval-decision-record")).toBeVisible();
});

test("判断の返却値が取消済みなら、承認成功と案内せず最新の記録を表示する", async ({ page, mockApi }) => {
  const approval = seedApproval(mockApi);
  await page.route("**/api/approvals/approval-layout/decision", async (route) => {
    approval.status = "cancelled";
    await route.fulfill({ json: { data: mockApi.state.runs[0], error_messages: [], warning_messages: [] } });
  });
  await page.goto("/approvals?id=approval-layout");
  await detailAction(page, "承認");
  await page.getByRole("alertdialog").getByRole("button", { name: "承認", exact: true }).click();
  await expect(
    page.getByText("この承認の状態が変わりました。最新の内容を確認してください。", { exact: true })
  ).toBeVisible();
  await expect(page.getByTestId("approval-decision-record")).toBeVisible();
  await expect(page.getByTestId("approval-object-actions")).toHaveCount(0);
  await expect(page).toHaveURL(/id=approval-layout$/);
});

test("古い判断記録の欠けた日時や操作者を捏造しない", async ({ page, mockApi }) => {
  const approval = seedApproval(mockApi, "approval-legacy", "approved");
  approval.decided_by = null;
  approval.decided_at = null;
  approval.created_at = "invalid-date";
  await page.goto("/approvals?id=approval-legacy");
  await expect(
    page.getByTestId("approval-decision-record").getByText("記録されていません", { exact: true })
  ).toHaveCount(2);
  await expect(
    page.getByRole("region", { name: "承認の詳細" }).getByText("記録されていません", { exact: true })
  ).toHaveCount(3);
});

test("閲覧だけの利用者には判断を出さず、判断済みの承認も URL で読める", async ({ page, mockApi }) => {
  seedApproval(mockApi);
  seedApproval(mockApi, "approval-completed", "approved");
  mockApi.setCurrentUser(
    dbUser({ permissions: ["menu.approvals", "agent.runs.view"], allowed_agent_ids: ["default"] })
  );
  await page.goto("/approvals?id=approval-layout");
  await expect(page.getByText("この承認を判断する権限がありません。内容の閲覧のみできます。")).toBeVisible();
  await expect(page.getByTestId("approval-object-actions")).toHaveCount(0);
  await page.goto("/approvals?id=approval-completed");
  await expect(page.getByTestId("approval-decision-record")).toContainText("reviewer.user");
  await expect(page.getByTestId("approval-object-actions")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "実行の結果と経過を確認" })).toHaveAttribute(
    "href",
    "/runs?id=run-approval-completed"
  );
});

test("存在しない承認と取得中・取得エラー・空のキューを区別する", async ({ page, mockApi }) => {
  seedApproval(mockApi);
  await page.goto("/approvals?id=missing-approval");
  await expect(page.getByText("対象が見つかりません")).toBeVisible();
  await expect(page.getByRole("region", { name: "承認の詳細" })).toHaveCount(0);
  let release!: () => void;
  let fail = true;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/runs", async (route) => {
    await gate;
    if (!fail) return route.fallback();
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["承認を取得できませんでした"], warning_messages: [] },
    });
  });
  await page.goto("/approvals");
  try {
    await expect(page.getByTestId("query-loading")).toBeVisible();
  } finally {
    release();
  }
  await expect(page.getByText("承認を取得できませんでした", { exact: true })).toBeVisible({ timeout: 15_000 });
  mockApi.state.runs = [];
  fail = false;
  await page.getByRole("button", { name: "再試行", exact: true }).click();
  await expect(page.getByText("保留中の承認はありません", { exact: true })).toBeVisible();
});

test("2ページ目から詳細を開いて戻ると、同じページと行に戻る", async ({ page, mockApi }) => {
  for (let index = 0; index < 23; index++) seedApproval(mockApi, `approval-${index}`);
  await page.goto("/approvals");
  await page.getByTestId("approval-list-pagination").getByRole("button", { name: "次へ", exact: true }).click();
  const link = page.locator('a[data-approval-id="approval-10"]');
  await link.click();
  await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
  await expect(page.getByTestId("approval-list-pagination")).toContainText("2 / 3");
  await expect(link).toBeFocused();
});

for (const viewport of [
  { width: 1920, height: 1080 },
  { width: 1280, height: 480 },
  { width: 375, height: 812 },
  { width: 812, height: 375 },
]) {
  test(`全幅の承認は明暗・文字拡大でもページ外へスクロールしない (${viewport.width}px)`, async ({
    page,
    mockApi,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (let index = 0; index < 14; index++) seedApproval(mockApi, `approval-${index}`);
    await page.goto("/approvals");
    await expect(page.locator('a[data-approval-id="approval-0"]')).toBeVisible();
    for (const theme of ["light", "dark"]) {
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      await page.screenshot({ path: testInfo.outputPath(`approvals-list-${theme}.png`) });
      await page.locator('a[data-approval-id="approval-0"]').click();
      const detail = page.getByRole("region", { name: "承認の詳細" });
      await expect(detail).toContainText("データの参照範囲を確認してください。");
      const bounds = () =>
        page.evaluate(() => ({
          width: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          height: document.documentElement.scrollHeight - document.documentElement.clientHeight,
          main: document.querySelector("main")!.clientWidth,
        }));
      const before = await bounds();
      expect(before.width).toBeLessThanOrEqual(0);
      expect(before.height).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`approvals-detail-${theme}.png`) });
      await page.evaluate(() => {
        document.documentElement.style.fontSize = "20px";
      });
      expect((await bounds()).width).toBeLessThanOrEqual(0);
      expect((await bounds()).height).toBeLessThanOrEqual(0);
      await page.evaluate(() => {
        document.documentElement.style.fontSize = "";
      });
      await page.getByRole("button", { name: "一覧へ戻る", exact: true }).click();
      expect((await bounds()).main).toBe(before.main);
    }
  });
}
