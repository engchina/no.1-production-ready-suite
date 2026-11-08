import { MOCK_NOW, expect, test } from "./fixtures/mock-api";

const OUT = "../../tmp/list-scroll/shots";

test("shot: run timeline collapsed", async ({ page, mockApi }, testInfo) => {
  const steps = Array.from({ length: 30 }, (_, index) => ({
    id: `step-${index + 1}`,
    kind: "tool",
    status: "completed",
    tool_call: { name: "nl2sql__nl2sql_query", arguments: { q: index } },
    tool_result: {
      name: "nl2sql__nl2sql_query", success: true, output: { answer: `結果 ${index + 1}` }, error_details: {},
      started_at: MOCK_NOW, completed_at: MOCK_NOW, duration_ms: 1000 + index, policy_decision: "allow",
      approval_required: false, guardrail_warnings: [], audit_metadata: {},
    },
  }));
  mockApi.state.runs.push({
    id: "run-shot", goal: "合成の目的", agent_id: "default", runtime_id: "builtin", status: "completed",
    created_at: MOCK_NOW, updated_at: MOCK_NOW, metadata: {}, pending_tool_calls: [], approvals: [], steps,
    events: [], artifacts: [],
  });
  await page.goto("/runs?id=run-shot");
  await page.getByTestId("run-timeline-section-summary").click();
  await expect(page.getByTestId("run-timeline-section").getByRole("button", { name: /すべて表示/ })).toBeVisible();
  await page.getByTestId("run-timeline-section").screenshot({ path: `${OUT}/pr1-agent-timeline-${testInfo.project.name}.png` });
});

test("shot: api key agent select", async ({ page, mockApi }, testInfo) => {
  for (let index = 0; index < 40; index += 1) {
    mockApi.state.agents.push({ ...(mockApi.state.agents[0] as object), id: `agent-${index}`, name: `業務 Agent ${index + 1}` });
  }
  await page.goto("/account");
  await page.getByTestId("api-key-agent-scope-selected").check();
  await page.getByRole("combobox", { name: /使う業務 Agent|業務 Agent/ }).first().click();
  await page.getByTestId("api-key-agent-options").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/pr1-agent-apikey-${testInfo.project.name}.png`, fullPage: false });
});
