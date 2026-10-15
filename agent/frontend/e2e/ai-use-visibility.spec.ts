import { dbUser } from "./fixtures/auth";
import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { chooseSelectFieldOption, withListFilters } from "./fixtures/select-field";

/**
 * 「AI 活用」の下の機能のデータの見え方（#1889）。system_admin は全員の Run・会話を見られ、実行履歴に
 * 「利用者」の列、会話の履歴に利用者の表示名を出す。system_admin は他の利用者の会話でも、本人と同じく
 * 続きの質問ができ、持ち主以外が送った質問には送った人の表示名を添える（#1928）。
 * 持ち主での絞り込み（一般の利用者は自分の Run だけ・他人は 404）と、system_admin が操作した Run の利用者は
 * backend のテスト（test_security_scope.py・test_admin_operates_others.py）で確かめる。
 *
 * #1954: system_admin が他の利用者の会話を開くと、送る前に「持ち主にも見える」と案内する。持ち主の画面では、
 * system_admin が実行して止まった Run に待っている相手を出し（操作は出さない）、自分で質問し直せる。実行履歴は
 * 「実行者」の列・「〈名前〉が実行」・実行者の絞り込みを出す。
 */

const OTHER_USER = "22222222-2222-2222-2222-222222222222";
const ADMIN_USER = "44444444-4444-4444-4444-444444444444";

function seedRun(
  mockApi: MockApi,
  id: string,
  goal: string,
  owner: string,
  thread: { id?: string; owner?: string } = {},
  waitingApproval = false
) {
  mockApi.state.runs.push({
    id,
    goal,
    agent_id: "default",
    runtime_id: "builtin",
    status: waitingApproval ? "waiting_approval" : "completed",
    steps: [],
    events: [],
    approvals: waitingApproval
      ? [
          {
            id: `${id}-approval`,
            run_id: id,
            step_id: `${id}-step`,
            tool_call: { name: "nl2sql__nl2sql_query", arguments: { question: "売上" } },
            status: "pending",
            reason: "承認が必要です。",
            created_at: MOCK_NOW,
            decided_by: null,
            decided_at: null,
          },
        ]
      : [],
    artifacts: waitingApproval
      ? []
      : [{ id: `${id}-answer`, name: "回答", kind: "answer", created_at: MOCK_NOW, content: { text: `${goal}への回答` } }],
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

  test("system_admin の実行履歴には「実行者」の列を出す", async ({ page, mockApi, isMobile }) => {
    mockApi.state.creatorDisplayNames[OTHER_USER] = "アリス";
    seedRun(mockApi, "run-alice-history", "アリスの実行", OTHER_USER);
    seedRun(mockApi, "run-gone-history", "削除済みの人の実行", "33333333-3333-3333-3333-333333333333");
    await page.goto("/runs");
    const table = page.getByRole("table", { name: "実行履歴" });
    // 狭い画面は列の代わりに題名の下の 1 行に実行者を出す（#1955）。
    if (!isMobile) await expect(table.getByRole("columnheader", { name: "実行者" })).toBeVisible();
    await expect(page.getByTestId("run-row-run-alice-history")).toContainText("アリス");
    await expect(page.getByTestId("run-row-run-gone-history")).toContainText("（削除済みのユーザー）");
  });

  test("system_admin が他の利用者の会話を開いたときだけ、送る前に持ち主にも見えることを案内する", async ({
    page,
    mockApi,
    isMobile,
  }) => {
    mockApi.state.creatorDisplayNames[OTHER_USER] = "アリス";
    seedRun(mockApi, "run-alice-notice", "アリスの会話", OTHER_USER);
    seedRun(mockApi, "run-own-notice", "自分の会話", "local");
    const notice = page.locator("[data-chat-composer-notice]");
    await page.goto("/chat");
    await expect(page.getByRole("textbox", { name: "質問" })).toBeVisible();
    // 新しい会話では出さない。
    await expect(notice).toHaveCount(0);

    await page.getByTestId("chat-history-toggle").click();
    await page.getByTestId("chat-history").locator('[title="アリスの会話"]').click();
    await expect(page.getByTestId("chat-turn-run-alice-notice")).toBeVisible();
    await expect(notice).toHaveText("この会話の持ち主にも、回答と途中の結果が見えます");
    // 入力欄の説明に結び、読み上げでも分かる。
    const describedBy = await page.getByRole("textbox", { name: "質問" }).getAttribute("aria-describedby");
    const noticeId = await notice.getAttribute("id");
    expect(noticeId && describedBy?.split(" ")).toContain(noticeId);

    // 自分の会話では出さない（desktop の履歴は開いたまま、375px はシートを開き直す）。
    if (isMobile) {
      // シートは会話を選ぶと閉じる（閉じ終わってから開き直す）。
      await expect(page.getByTestId("chat-history")).toBeHidden();
      await page.getByTestId("chat-history-toggle").click();
    }
    await page.getByTestId("chat-history").locator('[title="自分の会話"]').click();
    await expect(page.getByTestId("chat-turn-run-own-notice")).toBeVisible();
    await expect(notice).toHaveCount(0);
  });

  test("持ち主には system_admin が実行して止まった往復の待っている相手を出し、自分で質問し直せる", async ({
    page,
    mockApi,
  }) => {
    mockApi.state.creatorDisplayNames[ADMIN_USER] = "管理 花子";
    mockApi.setCurrentUser(
      dbUser({
        permissions: ["menu.chat", "menu.runs", "agent.runs.operate", "agent.approvals.decide"],
        allowed_agent_ids: ["default"],
      })
    );
    const thread = { id: "thread_0000000000000000000000000000wait", owner: "local" };
    seedRun(mockApi, "run-own-wait", "自分の質問", "local", thread);
    seedRun(mockApi, "run-admin-wait", "管理者の質問", ADMIN_USER, thread, true);
    await page.goto("/chat");
    await page.getByTestId("chat-history-toggle").click();
    await page.getByTestId("chat-history").locator('[title="自分の質問"]').click();

    const turn = page.getByTestId("chat-turn-run-admin-wait");
    const waiting = page.getByTestId("chat-waiting-for-executor-run-admin-wait");
    await expect(waiting).toContainText("システム管理者（管理 花子）の承認・回答を待っています");
    // 承認の操作は出さない（backend も 404。#1928）。持ち主自身の会話なので送る前の案内も出さない。
    await expect(turn.getByRole("button", { name: "承認して実行" })).toHaveCount(0);
    await expect(page.locator("[data-chat-composer-notice]")).toHaveCount(0);

    // 自分で質問し直す: 止まった往復を止め、同じ質問を同じ会話で自分の権限の新しい実行として送る。
    await waiting.getByRole("button", { name: "自分で質問し直す" }).click();
    await expect(page.locator("article", { hasText: "「管理者の質問」への回答です。" })).toBeVisible();
    await expect(waiting).toHaveCount(0);
    const stopped = mockApi.state.runs.find((run) => run.id === "run-admin-wait");
    expect(stopped?.status).toBe("cancelled");
    const reasked = mockApi.state.runs.filter((run) => run.goal === "管理者の質問" && run.id !== "run-admin-wait");
    expect(reasked).toHaveLength(1);
    expect(reasked[0].thread_id).toBe(thread.id);
    expect(reasked[0].created_by_user_uuid).toBe("local");

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("持ち主の実行履歴に実行者の列と絞り込み、詳細に実行者と会話の持ち主を出す", async ({
    page,
    mockApi,
    isMobile,
  }) => {
    mockApi.state.creatorDisplayNames[ADMIN_USER] = "管理 花子";
    mockApi.setCurrentUser(
      dbUser({
        permissions: ["menu.chat", "menu.runs", "agent.runs.operate", "agent.approvals.decide"],
        allowed_agent_ids: ["default"],
      })
    );
    const thread = { id: "thread_0000000000000000000000000000hist", owner: "local" };
    seedRun(mockApi, "run-own-hist", "自分の実行", "local", thread);
    seedRun(mockApi, "run-admin-hist", "管理者の実行", ADMIN_USER, thread, true);
    await page.goto("/runs");

    // 自分の会話で他の人（system_admin）が実行した Run があるので、「実行者」の列と絞り込みを出す（#1955）。
    const table = page.getByRole("table", { name: "実行履歴" });
    if (!isMobile) await expect(table.getByRole("columnheader", { name: "実行者" })).toBeVisible();
    const byAdmin = page.getByTestId("run-row-run-admin-hist");
    await expect(byAdmin).toContainText("管理 花子");
    await expect(page.getByTestId("run-row-run-own-hist")).toContainText("自分");

    // 実行者の絞り込み（サーバー側。件数も同じ条件）。
    const toolbar = page.getByTestId("run-list-toolbar");
    await withListFilters(toolbar, (scope) => chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-executor"), "others"));
    await expect(page.getByTestId("run-row-run-own-hist")).toHaveCount(0);
    await expect(byAdmin).toBeVisible();
    expect(mockApi.lastRequest("GET", "/api/runs")?.searchParams.get("executor")).toBe("others");
    await withListFilters(toolbar, (scope) => chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-executor"), "self"));
    await expect(byAdmin).toHaveCount(0);
    await expect(page.getByTestId("run-row-run-own-hist")).toBeVisible();
    expect(mockApi.lastRequest("GET", "/api/runs")?.searchParams.get("executor")).toBe("mine");
    await withListFilters(toolbar, (scope) => chooseSelectFieldOption(scope.getByTestId("run-list-toolbar-executor"), "all"));

    // 待っている system_admin の実行は、持ち主には承認・再開を出さない。
    await page.getByTestId("run-row-actions-run-admin-hist").click();
    await expect(page.getByRole("menuitem", { name: "承認" })).toHaveCount(0);
    await page.keyboard.press("Escape");

    await byAdmin.locator('a[data-run-id="run-admin-hist"]').click();
    // 概要の事実（実行者と違うときだけ会話の持ち主。#1954・#1955）。
    const summary = page.getByTestId("run-summary");
    await expect(summary.locator('[data-run-fact="executor"] dd')).toHaveText("管理 花子");
    await expect(summary.locator('[data-run-fact="owner"] dd')).toHaveText("自分");
    await expect(page.getByTestId("run-waiting-for-executor")).toContainText(
      "システム管理者（管理 花子）の承認・回答を待っています"
    );
  });
});
