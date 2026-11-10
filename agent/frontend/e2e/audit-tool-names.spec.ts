import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";
import { DATABASE_READ_FAILED_MESSAGE, failDatabaseReads } from "./fixtures/read-failure";
import { chooseSelectFieldOption } from "./fixtures/select-field";

// #983: 監査ログのツール名の絞り込みは、監査に記録されたツール（MCP 接続のツールを含む）から選べる。

function seedAuditRecord(
  mockApi: MockApi,
  index: number,
  toolName: string,
  origin: { run_source?: string; api_key_name?: string | null } = {},
) {
  mockApi.state.auditRecords.push({
    ...origin,
    run_id: `run-${index}`,
    run_goal: `監査の対象 ${index}`,
    run_status: "completed",
    run_created_at: MOCK_NOW,
    step_id: `step-${index}`,
    tool_name: toolName,
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

// #1874: 監査のきっかけの列に、外部の MCP クライアントの Run は「MCP（キーの名前）」を出す。
test("きっかけの列で MCP の Run とキーの名前が分かる", async ({ page, mockApi }) => {
  seedAuditRecord(mockApi, 1, "rag__rag_search", { run_source: "mcp", api_key_name: "会社の PC の Claude Code" });
  seedAuditRecord(mockApi, 2, "echo");
  await page.goto("/audit");
  const table = page.getByRole("table", { name: "監査レコード" });
  await expect(table.getByRole("columnheader", { name: "きっかけ" })).toBeVisible();
  await expect(table.getByRole("row").filter({ hasText: "監査の対象 1" })).toContainText("MCP（会社の PC の Claude Code）");
  await expect(table.getByRole("row").filter({ hasText: "監査の対象 2" })).toContainText("画面");
});

test("MCP 接続のツールをツール名で選んで絞り込める", async ({ page, mockApi }) => {
  seedAuditRecord(mockApi, 1, "rag__rag_search");
  seedAuditRecord(mockApi, 2, "echo");
  seedAuditRecord(mockApi, 3, "nl2sql__nl2sql_query");
  await page.goto("/audit");
  const table = page.getByRole("table", { name: "監査レコード" });
  await expect(table.getByRole("row")).toHaveCount(4);

  await chooseSelectFieldOption(page.locator("#audit-tool-name"), "rag__rag_search");
  await page.getByRole("button", { name: "フィルター適用" }).click();

  await expect
    .poll(() => mockApi.lastRequest("GET", "/api/audit/tool-calls")?.searchParams.get("tool_name"))
    .toBe("rag__rag_search");
  await expect(table.getByRole("row")).toHaveCount(2);
  await expect(table).toContainText("監査の対象 1");
  await expect(table).not.toContainText("監査の対象 2");

  // 絞り込んだ後も、ほかのツール（MCP 接続・登録済み）を選び直せる。
  await page.locator("#audit-tool-name").click();
  const listbox = page.locator(`[id="${await page.locator("#audit-tool-name").getAttribute("aria-controls")}"]`);
  for (const name of ["echo", "nl2sql__nl2sql_query", "rag__rag_search"]) {
    await expect(listbox.locator(`[role="option"][data-value="${name}"]`)).toBeVisible();
  }
});

// #4222: backend は監査の記録を読めないとき、手元の実行だけの記録を 200 で返さず、失敗として返す。
for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
]) {
  test(`監査の記録を読み取れないときは 0 件と出さず、失敗を出して再試行で回復する (${viewport.name})`, async ({ page, mockApi }, testInfo) => {
    seedAuditRecord(mockApi, 1, "echo");
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const reads = await failDatabaseReads(page, "/api/audit/tool-calls");
    await page.goto("/audit");

    // 自動の再試行（1・2 秒）の後に失敗を出す。
    const failure = page.locator('[data-failure-kind="other"]');
    await expect(failure.getByRole("alert")).toContainText(DATABASE_READ_FAILED_MESSAGE, { timeout: 15_000 });
    await expect(page.getByText("条件に一致する監査レコードはありません")).toHaveCount(0);
    await expect(page.getByTestId("audit-list-toolbar")).toHaveCount(0);
    // 絞り込みは読めているのでそのまま出す。
    await expect(page.getByTestId("audit-filters")).toBeVisible();
    await expectNoPageHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`audit-read-failed-${viewport.name}.png`) });

    reads.recover();
    await failure.getByRole("button", { name: "再試行", exact: true }).click();
    await expect(failure).toHaveCount(0);
    await expect(page.getByRole("table", { name: "監査レコード" })).toContainText("監査の対象 1");
  });
}
