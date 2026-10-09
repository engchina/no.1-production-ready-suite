import type { Page } from "@playwright/test";

import { ProgressLog } from "./fixtures/chat-progress-events";
import { expect, test, type MockApi } from "./fixtures/mock-api";

// #1160: チャットの回答の作成中に、処理の段階の配信（#1359。SSE の `/progress/stream` と polling の `/progress`）が
// 応答しない・失敗し続けても、画面を止めない。「接続を確認しています」を出して取り直し、完了・失敗の終端まで追う。

const THREAD_ID = `thread_${"c".repeat(32)}`;
const RUN_ID = "run-refresh";

function iso(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

function seedRunningRun(mockApi: MockApi) {
  const startedAt = iso(-3_000);
  const progress = new ProgressLog(RUN_ID).step("plan", "running", { startedAt });
  mockApi.state.runs.push({
    id: RUN_ID,
    goal: "契約の更新条件を調べて",
    agent_id: "default",
    runtime_id: "builtin",
    status: "running",
    steps: [],
    events: [
      { id: "ev-run", run_id: RUN_ID, type: "run.status_changed", message: "実行を開始しました。", payload: { status: "running" }, created_at: startedAt },
    ],
    approvals: [],
    artifacts: [],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: THREAD_ID,
    created_at: startedAt,
    updated_at: startedAt,
    progress_events: progress.events,
  });
  return { run: mockApi.state.runs.at(-1) as Record<string, unknown>, progress };
}

function complete(run: Record<string, unknown>, progress: ProgressLog) {
  run.status = "completed";
  progress.step("plan", "done").step("respond", "done").terminal("done");
  run.artifacts = [{ id: "answer-refresh", kind: "answer", name: "回答", content: { text: "契約は 1 年ごとに更新します。" } }];
  run.events = [
    ...(run.events as Record<string, unknown>[]),
    { id: "ev-done", run_id: RUN_ID, type: "run.completed", message: "実行を完了しました。", payload: {}, created_at: iso(0) },
  ];
}

/**
 * 処理の段階の配信（SSE と polling。`GET /api/runs/{id}/progress…`）を、応答しない・失敗させる。それ以外は
 * mock-api に任せる。会話の取得（回答・成果物）は止めない。
 */
async function controlProgressFetch(page: Page) {
  const control = { hang: 0, fail: 0, requests: 0 };
  await page.route(`**/api/runs/${RUN_ID}/progress**`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    control.requests += 1;
    if (control.hang > 0) {
      control.hang -= 1;
      return; // 応答しない（route を終えない）。
    }
    if (control.fail > 0) {
      control.fail -= 1;
      return route.fulfill({ status: 503, json: { detail: "一時的に応答できません。" } });
    }
    return route.fallback();
  });
  return control;
}

async function openThread(page: Page) {
  const viewport = page.viewportSize();
  await page.getByTestId("chat-history-toggle").click();
  if (viewport && viewport.width >= 1024) {
    await page.getByTestId("chat-history").getByRole("button", { name: /契約の更新条件を調べて/ }).click();
  } else {
    await page.getByRole("dialog", { name: "会話の履歴" }).getByText("契約の更新条件を調べて").click();
  }
}

for (const theme of ["light", "dark"] as const) {
  test(`段階の配信が応答しなくなったら「接続を確認しています」を出して取り直し、完了が出る（${theme}）`, async ({
    page,
    mockApi,
  }, testInfo) => {
    test.setTimeout(90_000);
    const { run, progress } = seedRunningRun(mockApi);
    const control = await controlProgressFetch(page);
    await useTheme(page, theme);
    await page.goto("/chat");
    await openThread(page);

    const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
    const current = turn.getByTestId("chat-progress-current");
    await expect(current).toContainText("考えています");
    // 応答を止める（SSE の張り直し・途絶えの後の SSE の張り直し・取り直しの 3 回）。修正前（#1160）は 1 回目の取得を
    // 待ったまま取り直しが止まった。
    control.hang = 3;
    const reconnecting = turn.getByTestId("chat-progress-reconnecting");
    await expect(reconnecting).toHaveText("接続を確認しています。", { timeout: 30_000 });
    await expect(current).toHaveAttribute("data-reconnecting", "true");
    await expect(turn.getByTestId("chat-progress-timer")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`progress-reconnecting-${theme}.png`) });
    complete(run, progress);
    await expect(turn.getByText("契約は 1 年ごとに更新します。")).toBeVisible({ timeout: 40_000 });
    await expect(turn.getByTestId("chat-progress")).toHaveAttribute("data-chat-progress-state", "done");
    await expect(reconnecting).toHaveCount(0);
    await expect(page.getByTestId("chat-send")).toHaveAccessibleName("送信");
  });

  test(`段階の配信の失敗が続いても backoff して再開し、失敗の終端が出る（${theme}）`, async ({ page, mockApi }) => {
    test.setTimeout(90_000);
    const { run, progress } = seedRunningRun(mockApi);
    const control = await controlProgressFetch(page);
    await useTheme(page, theme);
    await page.goto("/chat");
    await openThread(page);

    const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
    await expect(turn.getByTestId("chat-progress-current")).toContainText("考えています");
    control.fail = 6;
    await expect.poll(() => control.fail, { timeout: 60_000 }).toBe(0);
    run.status = "failed";
    progress.step("plan", "failed").terminal("failed");
    run.events = [
      ...(run.events as Record<string, unknown>[]),
      { id: "ev-failed", run_id: RUN_ID, type: "runtime.failed", message: "モデルの呼び出しに失敗しました（APIError）。", payload: {}, created_at: iso(0) },
    ];
    await expect(turn.getByTestId("chat-progress")).toHaveAttribute("data-chat-progress-state", "failed", { timeout: 30_000 });
    await expect(turn.getByText("モデルの呼び出しに失敗しました（APIError）。")).toBeVisible();
    await expect(turn.getByTestId("chat-progress-reconnecting")).toHaveCount(0);
    // 一時的な失敗で会話が消えて新しい会話に戻らない。
    await expect(turn).toBeVisible();
  });
}
