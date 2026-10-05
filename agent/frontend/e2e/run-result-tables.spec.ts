import type { Locator, Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #1158: チャットと実行履歴の詳細で、表の形のツールの結果・成果物・回答の Markdown の表を、NL2SQL のチャットと
// 同じ共通の結果の表（ChatResultTable。#1154）で出す。表でない結果は今までどおり（チャットは出さない・詳細は JSON）。

const THREAD_ID = `thread_${"b".repeat(32)}`;
const RUN_ID = "run-result-tables";
const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

const ROW_COUNT = 60;
const QUERY_OUTPUT = {
  job_id: "job-sales",
  status: "done",
  executable_sql: "select department, amount, note from sales",
  columns: ["DEPARTMENT", "AMOUNT", "NOTE"],
  rows: Array.from({ length: ROW_COUNT }, (_, index) => ({
    DEPARTMENT: `部門 ${index + 1}`,
    AMOUNT: (index + 1) * 100,
    // 1 行目の NOTE は NULL（空の文字列と区別して出す）。長い値は省略して全文を title に出す。
    NOTE: index === 0 ? null : index === 1 ? "" : `備考 ${index + 1} ${"長い説明の文。".repeat(index === 2 ? 20 : 1)}`,
  })),
  returned_count: ROW_COUNT,
  total: 1234,
  has_more: true,
  truncated: false,
};
const EMPTY_OUTPUT = { job_id: "job-empty", status: "done", columns: ["ID"], rows: [], total: 0, has_more: false };
const RAG_OUTPUT = { answer: "根拠付き回答", citations: [{ file_name: "規程.pdf", text: "条文" }] };
const ANSWER = [
  "部門別の売上をまとめました。",
  "",
  "| 部門 | 売上 |",
  "|---|---:|",
  "| 部門 1 | 100 |",
  "| 部門 2 | 200 |",
  "",
  "上位の部門は表のとおりです。",
].join("\n");

function toolStep(id: string, name: string, output: Record<string, unknown>) {
  return {
    id,
    run_id: RUN_ID,
    kind: "tool",
    status: "completed",
    tool_call: { name, arguments: {} },
    tool_result: {
      name,
      success: true,
      output,
      error_details: {},
      started_at: MOCK_NOW,
      completed_at: MOCK_NOW,
      duration_ms: 812,
      policy_decision: "allow",
      approval_required: false,
      guardrail_warnings: [],
      audit_metadata: {},
    },
    started_at: MOCK_NOW,
    completed_at: MOCK_NOW,
  };
}

function seedRun(mockApi: MockApi) {
  mockApi.state.runs.push({
    id: RUN_ID,
    goal: "部門別の売上は？",
    agent_id: "default",
    runtime_id: "builtin",
    status: "completed",
    steps: [
      toolStep("step-rag", "rag__rag_search", RAG_OUTPUT),
      toolStep("step-query", "nl2sql__nl2sql_query", QUERY_OUTPUT),
      toolStep("step-empty", "nl2sql__nl2sql_get_job", EMPTY_OUTPUT),
    ],
    events: [],
    approvals: [],
    artifacts: [
      {
        id: "artifact-query",
        name: "nl2sql__nl2sql_query:step-query",
        kind: "structured_table",
        content: QUERY_OUTPUT,
        created_at: MOCK_NOW,
      },
      { id: "artifact-raw", name: "中間結果", kind: "json", content: { rows: [1] }, created_at: MOCK_NOW },
      { id: "answer", name: "回答", kind: "answer", content: { text: ANSWER }, created_at: MOCK_NOW },
    ],
    pending_tool_calls: [],
    metadata: {},
    created_by_user_uuid: "local",
    thread_id: THREAD_ID,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function openThread(page: Page) {
  const viewport = page.viewportSize();
  await page.getByTestId("chat-history-toggle").click();
  if (viewport && viewport.width >= 1024) {
    await page.getByTestId("chat-history").getByRole("button", { name: /部門別の売上は？/ }).click();
  } else {
    await page.getByRole("dialog", { name: "会話の履歴" }).getByText("部門別の売上は？").click();
  }
}

/** 共通の結果の表（60 行・打ち切り）の振る舞い。チャットと実行履歴の詳細で同じであることを確かめる。 */
async function expectLargeResultTable(page: Page, table: Locator, testId: string) {
  await expect(table.getByTestId(`${testId}-summary`)).toContainText("全 1,234 行");
  await expect(table.getByTestId(`${testId}-summary`)).toContainText("3 列");
  // 上限での打ち切りを明示する。
  await expect(table.getByTestId(`${testId}-truncated`)).toBeVisible();
  // プレビューは表頭を固定し、表の中だけで縦にスクロールする（吹き出し・カードを伸ばさない）。
  const scroll = table.getByTestId(`${testId}-scroll`);
  await expect.poll(() => scroll.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(0);
  const header = scroll.locator("thead th").first();
  const headerTop = await header.evaluate((element) => element.getBoundingClientRect().top);
  await scroll.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
  await expect.poll(() => header.evaluate((element) => element.getBoundingClientRect().top)).toBeCloseTo(headerTop, 0);
  await scroll.evaluate((element) => element.scrollTo({ top: 0 }));
  // NULL は「NULL」で出し、空の文字列と区別する。
  const firstRow = scroll.locator("tbody tr").first();
  await expect(firstRow.getByText("NULL", { exact: true })).toBeVisible();
  // すべての行を見る（ページ送り）。閉じるとボタンへフォーカスを戻す。
  const viewAll = table.getByTestId(`${testId}-view-all`);
  await viewAll.click();
  const sheet = page.getByRole("dialog");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByTestId(`${testId}-all-pagination`)).toBeVisible();
  await expect(sheet.getByRole("button", { name: "CSV をダウンロード" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(viewAll).toBeFocused();
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`チャットで表の形のツールの結果と回答の表を共通の結果の表で出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      seedRun(mockApi);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/chat");
      await openThread(page);

      const turn = page.getByTestId(`chat-turn-${RUN_ID}`);
      // 回答の Markdown の表は表で出し、前後の文はそのまま出す。`|` の並んだ文を出さない。
      const answer = turn.getByTestId(`chat-answer-${RUN_ID}`);
      await expect(answer.getByText("部門別の売上をまとめました。")).toBeVisible();
      await expect(answer.getByText("上位の部門は表のとおりです。")).toBeVisible();
      const answerTable = answer.getByTestId(`chat-answer-${RUN_ID}-table-1`);
      await expect(answerTable.getByTestId(`chat-answer-${RUN_ID}-table-1-summary`)).toContainText("2 行");
      await expect(answerTable.getByRole("cell", { name: "部門 2" })).toBeVisible();
      await expect(turn).not.toContainText("|---|");

      // 表の形のツールの結果だけを表で出す（RAG の結果は表にしない）。
      await expect(turn.locator('section[data-testid^="chat-tool-table-"]')).toHaveCount(2);
      await expect(turn.getByTestId("chat-tool-table-step-rag")).toHaveCount(0);
      const query = turn.getByTestId("chat-tool-table-step-query");
      await expect(query).toContainText("ツール nl2sql__nl2sql_query の結果");
      await expectLargeResultTable(page, query, "chat-tool-table-step-query-result");

      // 0 行は「該当する行はありません」で、すべての行・CSV を出さない。
      const empty = turn.getByTestId("chat-tool-table-step-empty");
      await expect(empty.getByTestId("chat-tool-table-step-empty-result-summary")).toContainText("該当する行はありません");
      await expect(empty.getByTestId("chat-tool-table-step-empty-result-view-all")).toHaveCount(0);

      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`chat-${viewport.name}-${theme}.png`), fullPage: true });
    });

    test(`実行履歴の詳細で成果物・ツールの結果の表を共通の結果の表で出し、表でない結果は JSON のまま (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      seedRun(mockApi);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto(`/runs?id=${RUN_ID}`);

      // 結果: 回答の表・構造化データの成果物の表。表の成果物があるので「構造化データ」のカードは重ねない。
      await expect(page.getByTestId("run-answer-answer-table-1").getByRole("cell", { name: "部門 1" })).toBeVisible();
      const artifact = page.getByTestId("run-artifact-table-artifact-query");
      await expectLargeResultTable(page, artifact, "run-artifact-table-artifact-query");
      await expect(page.getByText("select department, amount, note from sales").first()).toBeVisible();
      await expect(page.getByTestId("run-structured-table")).toHaveCount(0);
      // 表でない成果物は JSON のまま。
      await expect(page.getByTestId("run-artifact-table-artifact-raw")).toHaveCount(0);
      await expect(page.locator("pre").filter({ hasText: /"rows": \[\s*1\s*\]/ })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`run-result-${viewport.name}-${theme}.png`), fullPage: true });

      // 実行の経過: 表の形のツールの結果は表、元の JSON は畳む。RAG の結果は JSON のまま。
      await page.getByRole("tab", { name: "実行の経過", exact: true }).click();
      const stepTable = page.getByTestId("run-step-table-step-query");
      await expect(stepTable.getByTestId("run-step-table-step-query-summary")).toContainText("全 1,234 行");
      await expect(page.getByTestId("run-step-table-step-empty-summary")).toContainText("該当する行はありません");
      await expect(page.getByTestId("run-step-table-step-rag")).toHaveCount(0);
      await expect(page.locator("pre").filter({ hasText: "根拠付き回答" })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`run-process-${viewport.name}-${theme}.png`), fullPage: true });
    });
  }
}

test("表の成果物が無い Run は、表の形のツールの結果を「構造化データ」のカードに出す（列名の文字列の配列も読む）", async ({ page, mockApi }) => {
  seedRun(mockApi);
  const run = mockApi.state.runs[0] as { artifacts: Record<string, unknown>[] };
  run.artifacts = run.artifacts.filter((artifact) => artifact.kind === "answer");
  await page.goto(`/runs?id=${RUN_ID}`);
  const card = page.getByTestId("run-structured-table");
  // 最後の表（0 行のジョブ）を出す。
  await expect(card.getByTestId("run-structured-table-summary")).toContainText("該当する行はありません");
});
