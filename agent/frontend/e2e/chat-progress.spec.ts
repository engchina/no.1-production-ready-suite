import type { Page } from "@playwright/test";

import { expect, test, type MockApi } from "./fixtures/mock-api";
import { expectSpinnerStable } from "./fixtures/spinner-stability";

// #1147: チャットの回答の場所に、Run の処理の段階（考えている・ツールの呼び出し・承認待ち・回答の作成）を
// 共有の ChatProgress（3 製品共通。#1145）で出す。会話の取り直し（polling）で段階が進むことを確かめる。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
] as const;

const THREAD_ID = `thread_${"b".repeat(32)}`;
const RUN_ID = "run-progress";

function iso(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

function seedRunningRun(mockApi: MockApi) {
  const startedAt = iso(-3_000);
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
  });
  return mockApi.state.runs.at(-1) as Record<string, unknown>;
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

function toolStep(id: string, name: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    run_id: RUN_ID,
    kind: "tool",
    status,
    tool_call: { name, arguments: { query: "契約" }, trace_id: `call-${id}` },
    started_at: iso(-2_000),
    completed_at: status === "completed" || status === "failed" ? iso(-1_000) : null,
    ...extra,
  };
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`回答の作成中は今の段階を 1 行で出し、ツール・承認待ち・完了へ進む (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      const run = seedRunningRun(mockApi);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/chat");
      await openThread(page);

      const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
      const progress = turn.getByTestId("chat-progress");
      const current = turn.getByTestId("chat-progress-current");
      // 1. モデルが考えている。
      await expect(progress).toHaveAttribute("data-chat-progress-state", "running");
      await expect(current).toHaveAttribute("data-step-id", "plan");
      await expect(current).toContainText("考えています");
      await expect(turn.getByTestId("chat-progress-timer")).toBeVisible();
      // 動くスピナーは今の段階の 1 つだけ。回転しても見た目の重心も箱・行も動かない（#1180）。
      await expect(turn.locator("svg.animate-spin:visible")).toHaveCount(1);
      await expectSpinnerStable(current.locator(".pr-spinner"));
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`progress-plan-${viewport.name}-${theme}.png`) });

      // 2. ツールを呼んでいる（取り直しで進む）。完了した段階は畳んだ見出しに数える。
      run.steps = [toolStep("step-search", "rag__rag_search", "running", { completed_at: null })];
      await expect(current).toHaveAttribute("data-step-id", "tool:rag__rag_search");
      await expect(current).toContainText("ツール rag__rag_search を呼んでいます");
      await expect(turn.getByTestId("chat-progress-completed")).toHaveText("1 ステップ完了");
      await page.screenshot({ path: testInfo.outputPath(`progress-tool-${viewport.name}-${theme}.png`) });

      // 3. 承認待ち。段階は承認待ちで、承認の Banner も出る。
      run.status = "waiting_approval";
      run.steps = [
        toolStep("step-search", "rag__rag_search", "completed"),
        toolStep("step-exec", "nl2sql__execute", "waiting_approval", { approval_id: "approval-progress" }),
      ];
      run.approvals = [
        {
          id: "approval-progress",
          run_id: RUN_ID,
          step_id: "step-exec",
          tool_call: { name: "nl2sql__execute", arguments: { sql: "select 1" } },
          status: "pending",
          reason: "承認が必要です。",
          created_at: iso(-500),
        },
      ];
      await expect(current).toHaveAttribute("data-step-id", "approval_wait");
      await expect(current).toContainText("承認を待っています（nl2sql__execute）");
      await expect(turn.getByRole("button", { name: "承認して実行" })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`progress-approval-${viewport.name}-${theme}.png`) });

      // 4. 承認後に実行して完了。回答の上に「処理の経過」の 1 行に畳む（既定は閉じる）。
      run.status = "completed";
      run.steps = [
        toolStep("step-search", "rag__rag_search", "completed"),
        toolStep("step-exec", "nl2sql__execute", "completed", { approval_id: "approval-progress" }),
      ];
      run.approvals = [{ ...(run.approvals as Record<string, unknown>[])[0], status: "approved", decided_at: iso(-200) }];
      run.artifacts = [{ id: "answer-progress", kind: "answer", name: "回答", content: { text: "契約は 1 年ごとに更新します。" } }];
      run.events = [
        ...(run.events as Record<string, unknown>[]),
        { id: "ev-done", run_id: RUN_ID, type: "run.completed", message: "実行を完了しました。", payload: {}, created_at: iso(0) },
      ];
      // 承認待ちの間は会話を取り直さない（承認の操作で取り直す）ので、開き直して完了を読む。
      await page.reload();
      await expect(progress).toHaveAttribute("data-chat-progress-state", "done");
      await expect(current).toHaveCount(0);
      const summary = turn.getByTestId("chat-progress-summary");
      await expect(summary).toContainText("処理の経過（5 ステップ・");
      // 既定は閉じる（段階の一覧は見えない）。
      await expect(turn.getByTestId("chat-progress-step-respond")).toBeHidden();
      await expect(turn.getByText("契約は 1 年ごとに更新します。")).toBeVisible();
      await summary.click();
      await expect(turn.getByTestId("chat-progress-step-tool:nl2sql__execute")).toContainText("ツール nl2sql__execute を呼びました");
      await expect(turn.getByTestId("chat-progress-step-respond")).toHaveAttribute("data-status", "done");
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`progress-done-${viewport.name}-${theme}.png`) });
    });

    test(`失敗した回答は止まった段階を開いて出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      const run = seedRunningRun(mockApi);
      run.status = "failed";
      run.steps = [toolStep("step-search", "rag__rag_search", "completed")];
      run.events = [
        ...(run.events as Record<string, unknown>[]),
        { id: "ev-failed", run_id: RUN_ID, type: "runtime.failed", message: "モデルの呼び出しに失敗しました（APIError）。", payload: {}, created_at: iso(0) },
      ];
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/chat");
      await openThread(page);

      const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
      await expect(turn.getByTestId("chat-progress")).toHaveAttribute("data-chat-progress-state", "failed");
      // 失敗があれば最初から開いて出す。
      const failed = turn.getByTestId("chat-progress-step-respond");
      await expect(failed).toBeVisible();
      await expect(failed).toHaveAttribute("data-status", "failed");
      await expect(failed).toContainText("回答を作れませんでした");
      await expect(failed).toContainText("失敗");
      // 原因は段階ではなく、回答の場所の Banner に出す。
      await expect(turn.getByText("モデルの呼び出しに失敗しました（APIError）。")).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`progress-failed-${viewport.name}-${theme}.png`) });
    });
  }
}
