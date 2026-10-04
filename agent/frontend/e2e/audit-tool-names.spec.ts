import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { chooseSelectFieldOption } from "./fixtures/select-field";

// #983: 監査ログのツール名の絞り込みは、監査に記録されたツール（MCP 接続のツールを含む）から選べる。

function seedAuditRecord(mockApi: MockApi, index: number, toolName: string) {
  mockApi.state.auditRecords.push({
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
  for (const name of ["agent_skill_list", "echo", "nl2sql__nl2sql_query", "rag__rag_search"]) {
    await expect(listbox.locator(`[role="option"][data-value="${name}"]`)).toBeVisible();
  }
});
