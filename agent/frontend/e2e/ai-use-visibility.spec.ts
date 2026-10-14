import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

/**
 * 「AI 活用」の下の機能のデータの見え方（#1889）。system_admin は全員の Run・会話を見られ、実行履歴に
 * 「利用者」の列、会話の履歴に利用者の表示名を出す。他の利用者の会話は見るだけ（続きの質問は本人だけ）。
 * 持ち主での絞り込み（一般の利用者は自分の Run だけ・他人は 404）は backend のテスト（test_security_scope.py）で確かめる。
 */

const OTHER_USER = "22222222-2222-2222-2222-222222222222";

function seedRun(mockApi: MockApi, id: string, goal: string, owner: string) {
  mockApi.state.runs.push({
    id,
    goal,
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: owner,
    thread_id: `thread_${id.padStart(32, "0")}`,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

test.describe("「AI 活用」のデータの見え方（#1889）", { tag: "@responsive" }, () => {
  test("system_admin は他の利用者の会話を利用者つきで見て、続きの質問は送れない", async ({ page, mockApi }) => {
    mockApi.state.creatorDisplayNames[OTHER_USER] = "アリス";
    seedRun(mockApi, "run-alice", "アリスの質問", OTHER_USER);
    await page.goto("/chat");
    const toggle = page.getByTestId("chat-history-toggle");
    await expect(toggle).toBeEnabled();
    await toggle.click();
    const history = page.getByTestId("chat-history");
    await expect(history.getByText(/・アリス$/)).toBeVisible();

    await history.locator('[title="アリスの質問"]').click();
    await expect(page.getByText(/ほかの利用者の会話です。/)).toBeVisible();
    await page.getByRole("textbox", { name: "質問" }).fill("続きの質問");
    await expect(page.getByTestId("chat-send")).toBeDisabled();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("system_admin の実行履歴には「利用者」の列を出す", async ({ page, mockApi }) => {
    mockApi.state.creatorDisplayNames[OTHER_USER] = "アリス";
    seedRun(mockApi, "run-alice-history", "アリスの実行", OTHER_USER);
    seedRun(mockApi, "run-gone-history", "削除済みの人の実行", "33333333-3333-3333-3333-333333333333");
    await page.goto("/runs");
    const table = page.getByRole("table", { name: "実行履歴" });
    await expect(table.getByRole("columnheader", { name: "利用者" })).toBeVisible();
    await expect(page.getByTestId("run-row-run-alice-history")).toContainText("アリス");
    await expect(page.getByTestId("run-row-run-gone-history")).toContainText("（削除済みのユーザー）");
  });
});
