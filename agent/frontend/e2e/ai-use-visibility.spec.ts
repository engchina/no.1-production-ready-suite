import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

/**
 * 「AI 活用」の下の機能のデータの見え方（#1889）。system_admin は全員の Run・会話を見られ、実行履歴に
 * 「利用者」の列、会話の履歴に利用者の表示名を出す。system_admin は他の利用者の会話でも、本人と同じく
 * 続きの質問ができ、持ち主以外が送った質問には送った人の表示名を添える（#1928）。
 * 持ち主での絞り込み（一般の利用者は自分の Run だけ・他人は 404）と、system_admin が操作した Run の利用者は
 * backend のテスト（test_security_scope.py・test_admin_operates_others.py）で確かめる。
 */

const OTHER_USER = "22222222-2222-2222-2222-222222222222";
const ADMIN_USER = "44444444-4444-4444-4444-444444444444";

function seedRun(
  mockApi: MockApi,
  id: string,
  goal: string,
  owner: string,
  thread: { id?: string; owner?: string } = {}
) {
  mockApi.state.runs.push({
    id,
    goal,
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [],
    events: [],
    approvals: [],
    artifacts: [{ id: `${id}-answer`, name: "回答", kind: "answer", created_at: MOCK_NOW, content: { text: `${goal}への回答` } }],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: owner,
    thread_id: thread.id ?? `thread_${id.padStart(32, "0")}`,
    thread_owner_user_uuid: thread.owner ?? owner,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

test.describe("「AI 活用」のデータの見え方（#1889）", { tag: "@responsive" }, () => {
  test("system_admin は他の利用者の会話を利用者つきで見て、続きの質問を送れる", async ({ page, mockApi }) => {
    mockApi.state.creatorDisplayNames[OTHER_USER] = "アリス";
    seedRun(mockApi, "run-alice", "アリスの質問", OTHER_USER);
    await page.goto("/chat");
    const toggle = page.getByTestId("chat-history-toggle");
    await expect(toggle).toBeEnabled();
    await toggle.click();
    const history = page.getByTestId("chat-history");
    await expect(history.getByText(/・アリス$/)).toBeVisible();

    await history.locator('[title="アリスの質問"]').click();
    await expect(page.getByTestId("chat-turn-run-alice")).toContainText("アリスの質問への回答");
    // 見るだけの案内は出さず、入力欄から続きの質問を送れる（#1928）。
    await expect(page.getByText(/ほかの利用者の会話です。/)).toHaveCount(0);
    // 持ち主が送った質問には送った人を添えない。
    await expect(page.getByTestId("chat-turn-run-alice").locator("[data-chat-sender]")).toHaveCount(0);
    await page.getByRole("textbox", { name: "質問" }).fill("続きの質問");
    await expect(page.getByTestId("chat-send")).toBeEnabled();
    await page.getByTestId("chat-send").click();

    // 送った質問と回答が同じ会話に出て、質問には送った system_admin の表示名を添える（色だけに頼らない文）。
    const sent = page.locator("article", { hasText: "「続きの質問」への回答です。" });
    await expect(sent).toBeVisible();
    await expect(sent.locator("[data-chat-sender]")).toHaveText("ローカル利用者 が送信");
    const continued = mockApi.state.runs.find((run) => run.goal === "続きの質問");
    // 同じ会話の続きで、Run は操作した system_admin（mock の現在の利用者）として作る。
    expect(continued?.thread_id).toBe(`thread_${"run-alice".padStart(32, "0")}`);
    expect(continued?.created_by_user_uuid).toBe("00000000-0000-0000-0000-000000000000");

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("会話の持ち主には、system_admin が送った質問に送った人の表示名を添える", async ({ page, mockApi }) => {
    mockApi.state.creatorDisplayNames[ADMIN_USER] = "管理 花子";
    const thread = { id: "thread_0000000000000000000000000000own1", owner: "local" };
    seedRun(mockApi, "run-own-1", "自分の質問", "local", thread);
    seedRun(mockApi, "run-admin-1", "管理者の質問", ADMIN_USER, thread);
    await page.goto("/chat");
    await page.getByTestId("chat-history-toggle").click();
    await page.getByTestId("chat-history").locator('[title="自分の質問"]').click();

    const byAdmin = page.getByTestId("chat-turn-run-admin-1");
    await expect(byAdmin).toContainText("管理者の質問への回答");
    await expect(byAdmin.locator("[data-chat-sender]")).toHaveText("管理 花子 が送信");
    await expect(page.getByTestId("chat-turn-run-own-1").locator("[data-chat-sender]")).toHaveCount(0);
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
