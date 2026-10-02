import type { Page } from "@playwright/test";

import { MOCK_NOW, evaluationSummary, expect, test, type MockApi } from "./fixtures/mock-api";

// #776: 業務 Agent の品質評価（評価セットで実行し、LLM で判定して合格率とツールの選択を出す）。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

const SET = {
  id: "evset-seeded",
  agent_id: "default",
  name: "経理の評価",
  description: "月次の問い合わせ",
  cases: [
    { id: "expense-deadline", question: "経費精算の締め日はいつですか？", expected: "毎月 25 日", expected_tools: ["rag_search"] },
    { id: "sales-total", question: "今月の売上の合計はいくらですか？", expected: "合計金額（円）", expected_tools: ["nl2sql_query"] },
    { id: "register", question: "取引先を登録して", expected: "登録の手順", expected_tools: [] },
  ],
  created_by_user_uuid: "local",
  created_at: MOCK_NOW,
  updated_at: MOCK_NOW,
};

function seedSet(mockApi: MockApi) {
  mockApi.state.evaluationSets.push(structuredClone(SET));
}

function seedFinishedJobs(mockApi: MockApi) {
  seedSet(mockApi);
  const results = [
    {
      case: SET.cases[0],
      status: "judged",
      run_id: "run-eval-1",
      answer: "毎月 25 日です。",
      judgement: { verdict: "correct", score: 1, summary: "要点を満たしています。", missing_points: [] },
      tool_calls: ["rag__rag_search"],
      tool_selection_correct: true,
      error: null,
      duration_ms: 3200,
    },
    {
      case: SET.cases[1],
      status: "judged",
      run_id: "run-eval-2",
      answer: "分かりません。",
      judgement: {
        verdict: "incorrect",
        score: 0.2,
        summary: "金額を答えていません。",
        missing_points: ["今月の売上の合計金額"],
      },
      tool_calls: [],
      tool_selection_correct: false,
      error: null,
      duration_ms: 5100,
    },
    {
      case: SET.cases[2],
      status: "judged",
      run_id: "run-eval-3",
      answer: "取引先の登録の手順は次のとおりです。",
      judgement: { verdict: "correct", score: 0.9, summary: "手順を答えています。", missing_points: [] },
      tool_calls: ["eval776_register"],
      tool_selection_correct: null,
      error: null,
      duration_ms: 2100,
    },
  ];
  const previousResults = results.map((item) => ({
    ...item,
    judgement: { ...item.judgement, verdict: "incorrect", score: 0.2 },
  }));
  const job = (id: string, items: typeof results) => ({
    id,
    agent_id: "default",
    agent_name: "汎用業務 Agent",
    set_id: SET.id,
    set_name: SET.name,
    status: "completed",
    created_by_user_uuid: "local",
    results: items,
    error: null,
    summary: evaluationSummary(items),
    created_at: MOCK_NOW,
    started_at: MOCK_NOW,
    finished_at: MOCK_NOW,
    previous_job_id: null as string | null,
    previous_summary: null as unknown,
  });
  const latest = job("eval-latest", results);
  const previous = job("eval-previous", previousResults);
  latest.previous_job_id = previous.id;
  latest.previous_summary = previous.summary;
  mockApi.state.evaluations.push(latest, previous);
}

for (const viewport of VIEWPORTS) {
  test(`評価セットを作り、評価を始めると進み具合の後に結果を出す (${viewport.name})`, async ({ page, mockApi }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/evaluation");
    await expect(page.getByRole("heading", { name: "品質評価", level: 1 })).toBeVisible();
    await expect(page.getByText("この業務 Agent の評価セットはまだありません")).toBeVisible();

    await page.getByRole("button", { name: "評価セットを作成" }).first().click();
    await expect(page).toHaveURL(/\/evaluation\?id=new$/);
    // 必須の入力が無ければ保存しない。
    await page.getByRole("button", { name: "作成", exact: true }).first().click();
    await expect(page.getByText("名前を入力してください。")).toBeVisible();
    await expect(page.getByText("質問を入力してください。")).toBeVisible();
    expect(mockApi.lastRequest("POST", "/api/evaluation-sets")).toBeUndefined();

    await page.getByLabel("名前").fill("経理の評価");
    const first = page.getByTestId("evaluation-case-1");
    await first.getByLabel("質問").fill("経費精算の締め日はいつですか？");
    await first.getByLabel("期待する回答の要点").fill("毎月 25 日");
    await first.getByLabel("期待するツール").fill("rag_search");
    await page.getByRole("button", { name: "ケースを追加" }).click();
    const second = page.getByTestId("evaluation-case-2");
    await second.getByLabel("質問").fill("今月の売上の合計はいくらですか？");
    await second.getByLabel("期待する回答の要点").fill("合計金額（円）");
    await page.getByRole("button", { name: "作成", exact: true }).first().click();

    await expect(page.getByText("評価セットを作成しました")).toBeVisible();
    await expect(page).toHaveURL(/\/evaluation\?id=evset-1$/);
    expect(mockApi.lastRequest("POST", "/api/evaluation-sets")?.body).toEqual({
      agent_id: "default",
      name: "経理の評価",
      description: "",
      cases: [
        { question: "経費精算の締め日はいつですか？", expected: "毎月 25 日", expected_tools: ["rag_search"] },
        { question: "今月の売上の合計はいくらですか？", expected: "合計金額（円）", expected_tools: [] },
      ],
    });

    // 評価セットから評価を始める（対象の操作は概要の ObjectActionBar）。
    const actions = page.getByTestId("evaluation-set-actions");
    await expect(actions).toBeVisible();
    const startButton = actions.getByRole("button", { name: "評価を開始" });
    if (await startButton.count()) {
      await startButton.click();
    } else {
      await page.getByTestId("evaluation-set-actions-more").click();
      await page.getByRole("menuitem", { name: "評価を開始" }).click();
    }
    await expect(page.getByText("評価を開始しました")).toBeVisible();
    await expect(page).toHaveURL(/\/evaluation$/);
    expect(mockApi.lastRequest("POST", "/api/evaluations")?.body).toEqual({ set_id: "evset-1" });

    await expect(page.getByTestId("evaluation-progress")).toContainText("経理の評価 を評価しています（0 / 2 件）");
    const summary = page.getByTestId("evaluation-summary");
    await expect(summary).toContainText("50%");
    await expect(summary).toContainText("ツールの選択の正しさ");
    await expect(page.getByTestId("evaluation-progress")).toHaveCount(0);
    const results = page.getByRole("table", { name: "ケース別結果" });
    await expect(results.getByRole("row", { name: /case-1/ })).toContainText("期待どおり");
    await expect(results.getByRole("row", { name: /case-2/ })).toContainText("誤り");
    await expect(page.getByRole("table", { name: "評価セットの一覧" }).getByRole("row", { name: /経理の評価/ })).toContainText(
      "50%"
    );
    await expectNoHorizontalOverflow(page);
  });

  for (const theme of ["light", "dark"] as const) {
    test(`前回との比較・ツールの選択・ケースの詳細を出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      seedFinishedJobs(mockApi);
      await page.goto("/evaluation");

      const summary = page.getByTestId("evaluation-summary");
      await expect(summary).toContainText("67%");
      await expect(summary).toContainText("前回から +67 ポイント");
      await expect(summary).toContainText("50%");
      await expect(summary).toContainText("1 / 2 件");
      await expect(page.getByTestId("evaluation-previous")).toHaveText("差は、同じ評価セットの前回の評価との比較です。");
      const results = page.getByRole("table", { name: "ケース別結果" });
      await expect(results.getByRole("row", { name: /sales-total/ })).toContainText("期待と違う");
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`evaluation-${viewport.name}-${theme}.png`), fullPage: true });

      await results.getByRole("button", { name: /今月の売上の合計はいくらですか？/ }).click();
      const detail = page.getByRole("dialog", { name: "ケースの詳細" });
      await expect(detail).toContainText("合計金額（円）");
      await expect(detail).toContainText("金額を答えていません。");
      await expect(detail).toContainText("nl2sql_query");
      await expect(detail).toContainText("承認が要るツールを実行せずに");
      await expect(detail).toBeInViewport({ ratio: 1 });
      await page.screenshot({ path: testInfo.outputPath(`evaluation-detail-${viewport.name}-${theme}.png`) });
    });
  }
}

test("Excel から評価ケースを取り込み（確認して置き換え）、テンプレートと書き出しを取得できる", async ({ page, mockApi }, testInfo) => {
  seedSet(mockApi);
  await page.goto("/evaluation?id=evset-seeded");
  await expect(page.getByRole("heading", { name: "経理の評価", level: 1 })).toBeVisible();
  await expect(page.getByText("評価ケース（3 件）")).toBeVisible();

  await page.getByTestId("evaluation-set-file").setInputFiles({
    name: "cases.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("PK-mock"),
  });
  const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
  await expect(dialog.getByText("評価ケースを置き換えますか?")).toBeVisible();
  await dialog.getByRole("button", { name: "置き換える" }).click();
  await expect(page.getByText("Excel から 2 件を読み込みました。保存すると反映します。")).toBeVisible();
  await expect(page.getByText("評価ケース（2 件）")).toBeVisible();
  await expect(page.getByTestId("evaluation-case-1").getByLabel("質問")).toHaveValue("Excel の質問 1");
  await page.screenshot({ path: testInfo.outputPath("evaluation-set-editor.png"), fullPage: true });
  expect(mockApi.lastRequest("POST", "/api/evaluation-sets/parse-xlsx")).toBeDefined();

  // 保存していない変更があるときは評価を始めず、変更を破棄できる。
  await page.getByRole("button", { name: "変更を破棄" }).click();
  await expect(page.getByText("評価ケース（3 件）")).toBeVisible();

  await page.getByRole("button", { name: "テンプレートをダウンロード" }).click();
  await expect.poll(() => mockApi.lastRequest("GET", "/api/evaluation-sets/template.xlsx")).toBeDefined();
});

test("保存していない変更があるときは、一覧へ戻る前に確かめる。一覧から評価セットを削除できる", async ({ page, mockApi }) => {
  seedSet(mockApi);
  await page.goto("/evaluation?id=evset-seeded");
  await page.getByLabel("名前").fill("経理の評価（改）");
  await page.getByRole("button", { name: "一覧へ戻る" }).last().click();
  const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
  await expect(dialog.getByText("変更を破棄しますか")).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page.getByLabel("名前")).toHaveValue("経理の評価（改）");

  await page.getByRole("button", { name: "保存", exact: true }).first().click();
  await expect(page.getByText("評価セットを保存しました")).toBeVisible();
  await page.getByRole("button", { name: "一覧へ戻る" }).last().click();
  await expect(page).toHaveURL(/\/evaluation$/);

  await page.getByTestId("evaluation-set-row-actions-evset-seeded").click();
  await page.getByRole("menuitem", { name: "削除" }).click();
  await expect(dialog.getByText("「経理の評価（改）」を削除しますか?")).toBeVisible();
  await dialog.getByRole("button", { name: "削除", exact: true }).click();
  await expect(page.getByText("評価セットを削除しました")).toBeVisible();
  await expect(page.getByText("この業務 Agent の評価セットはまだありません")).toBeVisible();
});

test("読み込み中は経過時間を出す", async ({ page, mockApi }) => {
  seedFinishedJobs(mockApi);
  await page.route("**/api/evaluations/eval-latest", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fallback();
  });
  await page.goto("/evaluation");
  await expect(page.getByTestId("evaluation-loading")).toContainText("評価の結果を読み込んでいます");
  await expect(page.getByTestId("evaluation-summary")).toBeVisible();
});
