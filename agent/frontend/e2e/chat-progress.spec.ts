import type { Page } from "@playwright/test";

import { findIconTextMisalignment } from "../../../platform/packages/ui/e2e/layout-checks";

import { ProgressLog } from "./fixtures/chat-progress-events";
import { expect, test, type MockApi } from "./fixtures/mock-api";
import { expectProgressTimerMonotonic, startProgressTimerSampler } from "./fixtures/progress-timer";
import { expectSpinnerStable } from "./fixtures/spinner-stability";

// #1147: チャットの回答の場所に、Run の処理の段階（考えている・ツールの呼び出し・承認待ち・回答の作成）を
// 共有の ChatProgress（3 製品共通。#1145）で出す。段階は backend が記録したイベント（#1359）で、実行中は SSE で受け取り、
// 承認待ちの間も途絶えずに進むことを確かめる。

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

async function openThread(page: Page) {
  const viewport = page.viewportSize();
  await page.getByTestId("chat-history-toggle").click();
  if (viewport && viewport.width >= 1024) {
    await page.getByTestId("chat-history").getByRole("button", { name: /^契約の更新条件を調べて/ }).click();
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
  test(`終わったツールの段は、段の終わりのイベントの詳細で、後の段の実行中から開ける（#2373） (${viewport.name})`, async ({ page, mockApi }) => {
    const { progress: log } = seedRunningRun(mockApi);
    // Run の step はまだ取り直していない（steps は空）。詳細は段のイベントだけで届く。
    log
      .step("plan", "done")
      .step("tool:rag__rag_search", "running", { params: { tool: "rag__rag_search", title: "ナレッジベースの検索" } })
      .step("tool:rag__rag_search", "done", {
        details: {
          fields: [{ label: "ツール", value: "rag__rag_search", mono: true }],
          code: [{ label: "引数", language: "json", text: JSON.stringify({ query: "合成の語" }, null, 2) }],
        },
      })
      .step("respond", "running");
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/chat");
    await openThread(page);

    const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
    const current = turn.getByTestId("chat-progress-current");
    await expect(current).toHaveAttribute("data-step-id", "respond");
    const toggle = turn.getByTestId("chat-progress-step-tool:rag__rag_search-toggle");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    const details = turn.getByTestId("chat-progress-step-tool:rag__rag_search-details");
    await expect(details.locator("dd").first()).toHaveText("rag__rag_search");
    await expect(details.getByLabel("引数")).toContainText("合成の語");
    // 後の段は実行中のまま。
    await expect(current).toHaveAttribute("data-step-id", "respond");
    await expectNoHorizontalOverflow(page);
  });

  test(`処理の経過のツールの段は表示名で出し、開くと内部名と引数・結果の JSON を出す（詳細の無い段は開かない。#2294 / #2324） (${viewport.name})`, async ({ page, mockApi }, testInfo) => {
    const { run, progress: log } = seedRunningRun(mockApi);
    run.status = "completed";
    run.steps = [
      toolStep("step-search", "rag__rag_search", "completed", {
        tool_result: {
          name: "rag__rag_search",
          success: true,
          output: { answer: "合成の回答 A", sources: [{ title: "文書 A" }] },
          duration_ms: 800,
        },
      }),
    ];
    // ツールの表示名（MCP の title）は backend が params の title に入れる（#2324）。
    log
      .step("plan", "done")
      .step("tool:rag__rag_search", "running", { params: { tool: "rag__rag_search", title: "ナレッジベースの検索" } })
      .step("tool:rag__rag_search", "done")
      .step("respond", "running")
      .step("respond", "done")
      .terminal("done");
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/chat");
    await openThread(page);

    const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
    await expect(turn.getByTestId("chat-progress")).toHaveAttribute("data-chat-progress-state", "done");
    // 使ったツールは処理の経過のツールの段で見る（「使ったツール」の一覧は出さない。#2324）。
    await expect(turn.getByText(/使ったツール/)).toHaveCount(0);
    await turn.getByTestId("chat-progress-summary").click();
    await expect(turn.getByTestId("chat-progress-step-tool:rag__rag_search")).toContainText(
      "「ナレッジベースの検索」を実行しました"
    );
    // 詳細のある段（ツール）だけに開閉のボタン。既定で閉じる。
    await expect(turn.getByTestId("chat-progress-step-plan-toggle")).toHaveCount(0);
    const toggle = turn.getByTestId("chat-progress-step-tool:rag__rag_search-toggle");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(turn.getByTestId("chat-progress-step-tool:rag__rag_search-details")).toBeHidden();
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    const details = turn.getByTestId("chat-progress-step-tool:rag__rag_search-details");
    await expect(details).toBeVisible();
    // 段の名前は表示名なので、調べる用の内部名を詳細の先頭に出す。
    await expect(details.locator("dt").first()).toHaveText("ツール");
    await expect(details.locator("dd").first()).toHaveText("rag__rag_search");
    await expect(details.getByLabel("引数")).toContainText('"query": "契約"');
    await expect(details.getByLabel("結果")).toContainText("合成の回答 A");
    // 文字の横のアイコン（状態・開閉の印）は、文字の 1 行目の中心にそろえる（#2371）。
    expect(await findIconTextMisalignment(page, { root: `[data-testid="chat-turn-${RUN_ID}"]` })).toEqual([]);
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`progress-tool-details-${viewport.name}.png`) });
    await toggle.click();
    await expect(details).toBeHidden();
  });

  for (const theme of ["light", "dark"] as const) {
    test(`回答の作成中は今の段階を 1 行で出し、ツール・承認待ち・完了へ進む (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      const { run, progress: log } = seedRunningRun(mockApi);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/chat");
      // #1176: 段階が進む間、右上の経過時間（Run の作成からの通算）が減らないことを記録する。
      await startProgressTimerSampler(page, "chat-progress");
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
      log.step("plan", "done").step("tool:rag__rag_search", "running", { params: { tool: "rag__rag_search" } });
      await expect(current).toHaveAttribute("data-step-id", "tool:rag__rag_search");
      await expect(current).toContainText("ツール rag__rag_search を呼んでいます");
      await expect(turn.getByTestId("chat-progress-running")).toContainText("処理中（1 ステップ完了）");
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
      // backend の記録と同じ: ツールの後に回答の作成を始め、承認を求めたら進め方の検討の完了に変える。
      log
        .step("tool:rag__rag_search", "done")
        .step("respond", "running")
        .step("respond", "done", { kind: "plan" })
        .step("approval_wait", "running", { params: { tools: "nl2sql__execute" } })
        .step("tool:nl2sql__execute", "pending", { params: { tool: "nl2sql__execute" } });
      await expect(current).toHaveAttribute("data-step-id", "approval_wait");
      await expect(current).toContainText("承認を待っています（nl2sql__execute）");
      await expect(turn.getByRole("button", { name: "承認して実行" })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`progress-approval-${viewport.name}-${theme}.png`) });
      // 考えている・ツール・承認待ちの段階を通る間、経過時間は減らない（段階ごとに 0 に戻さない）。
      // 段階のイベントは SSE ですぐ届くので、記録（100ms ごと）が承認待ちを 1 回は拾うまで待ってから確かめる。
      await expect
        .poll(() =>
          page.evaluate(() =>
            ((window as unknown as { __progressTimerSamples?: string[] }).__progressTimerSamples ?? []).some((sample) =>
              sample.startsWith("approval_wait|")
            )
          )
        )
        .toBe(true);
      await expectProgressTimerMonotonic(page, 3);

      // 4. 承認後に実行して完了。回答の上に「処理の経過」を閉じて出す（実行中は開く。#2096 / #2121）。承認待ちの間も段階の配信
      // （SSE）は続くので、開き直さなくても終端が届き、会話を取り直して回答を出す（#1359）。
      run.status = "completed";
      run.steps = [
        toolStep("step-search", "rag__rag_search", "completed"),
        toolStep("step-exec", "nl2sql__execute", "completed", { approval_id: "approval-progress" }),
      ];
      run.approvals = [{ ...(run.approvals as Record<string, unknown>[])[0], status: "approved", decided_at: iso(-200) }];
      run.artifacts = [{ id: "answer-progress", kind: "answer", name: "回答", content: { text: "契約は 1 年ごとに更新します。" } }];
      log
        .step("approval_wait", "done")
        .step("tool:nl2sql__execute", "running")
        .step("tool:nl2sql__execute", "done")
        .step("respond#2", "running")
        .step("respond#2", "done")
        .terminal("done");
      run.events = [
        ...(run.events as Record<string, unknown>[]),
        { id: "ev-done", run_id: RUN_ID, type: "run.completed", message: "実行を完了しました。", payload: {}, created_at: iso(0) },
      ];
      await expect(progress).toHaveAttribute("data-chat-progress-state", "done");
      await expect(current).toHaveCount(0);
      const summary = turn.getByTestId("chat-progress-summary");
      await expect(summary).toContainText("処理の経過（6 ステップ・");
      // 完了したら一覧を閉じ（#2121。RAG と同じ）、押すと開く。
      await expect(turn.getByTestId("chat-progress-step-respond#2")).toBeHidden();
      await expect(turn.getByText("契約は 1 年ごとに更新します。")).toBeVisible();
      // 処理の経過は回答の本文より上。使ったツールは処理の経過のツールの段で見る（一覧を重ねて出さない。#2324）。
      await expect(turn.getByText(/使ったツール/)).toHaveCount(0);
      const [summaryBox, answerBox] = await Promise.all([
        summary.boundingBox(),
        turn.getByText("契約は 1 年ごとに更新します。").boundingBox(),
      ]);
      expect(summaryBox!.y).toBeLessThan(answerBox!.y);
      await summary.click();
      await expect(turn.getByTestId("chat-progress-step-respond#2")).toBeVisible();
      // 開いた一覧は実行の詳細と同じ RunTimeline（時間の線つき。狭い置き場所は線を隠し、所要時間の文字だけ。#2265）。
      const bars = progress.locator("[data-run-timeline-bar]");
      await expect(bars).toHaveCount(6);
      if (viewport.width >= 1280) await expect(bars.first()).toBeVisible();
      else await expect(bars.first()).toBeHidden();
      await expect(turn.getByTestId("chat-progress-step-tool:nl2sql__execute")).toContainText("ツール nl2sql__execute を呼びました");
      await expect(turn.getByTestId("chat-progress-step-respond#2")).toHaveAttribute("data-status", "done");
      // 回答の作成の後にツールを呼んだ段階は、進め方の検討の完了として残る（#1358）。
      await expect(turn.getByTestId("chat-progress-step-respond")).toContainText("進め方を決めました");
      // 終端が届いたら、承認待ちの後も会話を取り直して回答を出す（開き直さない）。
      expect(mockApi.requests.some((request) => request.path === `/api/runs/${RUN_ID}/progress/stream`)).toBe(true);
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`progress-done-${viewport.name}-${theme}.png`) });
    });

    test(`失敗した回答は止まった段階を開いて出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      const { run, progress: log } = seedRunningRun(mockApi);
      run.status = "failed";
      run.steps = [toolStep("step-search", "rag__rag_search", "completed")];
      log
        .step("plan", "done")
        .step("tool:rag__rag_search", "running", { params: { tool: "rag__rag_search" } })
        .step("tool:rag__rag_search", "done")
        .step("respond", "running")
        .step("respond", "failed")
        .terminal("failed");
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
