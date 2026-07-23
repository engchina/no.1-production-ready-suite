import { expect, test, type Page, type Route } from "@playwright/test";

import { mockDatabaseGateReady } from "./_helpers/database-gate";

function envelope(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

async function fulfillJson(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(status >= 400 ? { detail: data } : envelope(data)),
  });
}

function auditRecord(id: number) {
  return {
    audit_id: id,
    actor_user_id: id === 12 ? "00000000-0000-0000-0000-000000000000" : `actor-${id}`,
    event_type: `EVENT_${id}`,
    target_type: "USER",
    target_id: `target-${id}`,
    outcome: id % 2 === 0 ? "SUCCESS" : "DENIED",
    detail: { id },
    request_id: `request-${id}`,
    client_ip: "127.0.0.1",
    created_at: `2026-07-${String(id).padStart(2, "0")}T03:00:00Z`,
  };
}

function hasDocumentHorizontalScroll(page: Page) {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1
  );
}

async function pageHeaderAction(page: Page, name: string) {
  const actions = page.getByTestId("security-audit-actions");
  const visibleButton = actions.getByRole("button", { name, exact: true });
  if (await visibleButton.isVisible()) return visibleButton;
  await actions.getByRole("button", { name: "その他の操作", exact: true }).click();
  return page.getByRole("menuitem", { name, exact: true });
}

test("監査ログは 10 件ずつページ移動し、直近 1 年を Excel 出力できる", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const records = Array.from({ length: 12 }, (_, index) => auditRecord(12 - index));
  const requestedPages: number[] = [];
  await page.route(/\/api\/security\/audit\/page(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const requestedPage = Number(url.searchParams.get("page") ?? "1");
    const pageSize = Number(url.searchParams.get("page_size") ?? "0");
    requestedPages.push(requestedPage);
    expect(pageSize).toBe(10);
    const offset = (requestedPage - 1) * pageSize;
    await fulfillJson(route, {
      items: records.slice(offset, offset + pageSize),
      page: requestedPage,
      page_size: pageSize,
      total: records.length,
      total_pages: 2,
    });
  });
  await page.route("**/api/security/audit/export.xlsx", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      headers: {
        "Content-Disposition":
          'attachment; filename="nl2sql_audit_logs_20250720-20260720.xlsx"',
      },
      body: "xlsx",
    })
  );

  await page.goto("/settings/security/audit");
  const table = page.getByTestId("security-audit-table");
  await expect(table.locator("tbody tr")).toHaveCount(10);
  await expect(table.locator("tbody tr").first()).toContainText("EVENT_12");
  const actorColumn = table.getByRole("columnheader", { name: "実行者" });
  await expect(actorColumn).toHaveCSS("min-width", "252px");
  await expect(table.locator("tbody tr").first().locator("td").nth(2)).toContainText(
    "00000000-0000-0000-0000-000000000000"
  );
  await expect(page.getByText("1-10 / 12 件", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "次へ" }).click();
  await expect(table.locator("tbody tr")).toHaveCount(2);
  await expect(table.locator("tbody tr").first()).toContainText("EVENT_2");
  await expect(page.getByText("11-12 / 12 件", { exact: true })).toBeVisible();

  await (await pageHeaderAction(page, "表示を更新")).click();
  await expect(page.getByText("最新の状態に更新しました。")).toBeVisible();
  await expect(table.locator("tbody tr")).toHaveCount(10);
  await expect(table.locator("tbody tr").first()).toContainText("EVENT_12");
  expect(requestedPages.filter((requestedPage) => requestedPage === 2)).toHaveLength(1);
  expect(requestedPages.at(-1)).toBe(1);

  const exportButton = await pageHeaderAction(page, "直近 1 年を Excel 出力");
  await exportButton.focus();
  await expect(exportButton).toBeFocused();
  const downloadPromise = page.waitForEvent("download");
  await exportButton.press("Enter");
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("nl2sql_audit_logs_20250720-20260720.xlsx");
  await expect(page.getByText("直近 1 年の監査ログのダウンロードを開始しました。"))
    .toBeVisible();
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});

test("監査ログは読込中の領域を予約し、空状態を表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let releaseRequest = () => {};
  const requestGate = new Promise<void>((resolve) => {
    releaseRequest = resolve;
  });
  await page.route(/\/api\/security\/audit\/page(?:\?.*)?$/, async (route) => {
    await requestGate;
    await fulfillJson(route, {
      items: [],
      page: 1,
      page_size: 10,
      total: 0,
      total_pages: 1,
    });
  });

  await page.goto("/settings/security/audit");
  await expect(page.getByTestId("security-audit-table").locator("tbody tr")).toHaveCount(3);
  releaseRequest();
  await expect(page.getByText("対象データはありません。", { exact: true })).toBeVisible();
  await expect(page.getByTestId("security-audit-pagination")).toHaveCount(0);
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});

test("監査ログの読込と Excel 出力に失敗した場合は再試行方法を示す", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let loadAttempts = 0;
  await page.route(/\/api\/security\/audit\/page(?:\?.*)?$/, async (route) => {
    loadAttempts += 1;
    if (loadAttempts <= 2) {
      await fulfillJson(route, "一時的な障害", 503);
      return;
    }
    await fulfillJson(route, {
      items: [],
      page: 1,
      page_size: 10,
      total: 0,
      total_pages: 1,
    });
  });
  await page.route("**/api/security/audit/export.xlsx", (route) =>
    fulfillJson(route, "Excel を作成できません。", 500)
  );

  await page.goto("/settings/security/audit");
  await expect(
    page.getByText(
      "監査ログを読み込めませんでした。接続状態と権限を確認して再試行してください。"
    )
  ).toBeVisible();
  const attemptsBeforeRetry = loadAttempts;
  await page.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByText("対象データはありません。", { exact: true })).toBeVisible();
  expect(loadAttempts).toBe(attemptsBeforeRetry + 1);

  await page.getByRole("button", { name: "直近 1 年を Excel 出力" }).click();
  await expect(
    page.getByText("Excel の作成に失敗しました。時間をおいて再度お試しください。")
  ).toBeVisible();
  expect(await hasDocumentHorizontalScroll(page)).toBe(false);
});
