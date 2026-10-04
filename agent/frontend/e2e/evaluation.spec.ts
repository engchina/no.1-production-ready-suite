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
    agent_version: 1 as number | "draft" | null,
    previous_job_id: null as string | null,
    previous_summary: null as unknown,
    previous_agent_version: null as number | "draft" | null,
    previous_created_at: null as string | null,
  });
  const latest = job("eval-latest", results);
  const previous = job("eval-previous", previousResults);
  latest.agent_version = "draft";
  latest.previous_job_id = previous.id;
  latest.previous_summary = previous.summary;
  latest.previous_agent_version = previous.agent_version;
  latest.previous_created_at = previous.created_at;
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
        { question: "経費精算の締め日はいつですか？", expected: "毎月 25 日", expected_tools: ["rag_search"], source_run_id: null },
        { question: "今月の売上の合計はいくらですか？", expected: "合計金額（円）", expected_tools: [], source_run_id: null },
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
    // 公開していない変更の無い業務 Agent は、既定で公開中の版を評価する（#810）。
    expect(mockApi.lastRequest("POST", "/api/evaluations")?.body).toEqual({ set_id: "evset-1", agent_version: "published" });

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
      // どの版どうしの比較かを出す（#810）。
      await expect(page.getByTestId("evaluation-previous")).toContainText("同じ評価ケースで完了した前回の評価（v1・");
      await expect(page.getByText("経理の評価（汎用業務 Agent・下書き）")).toBeVisible();
      await expect(
        page.getByRole("table", { name: "最近の評価" }).getByRole("row").nth(1)
      ).toContainText("下書き");
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

for (const viewport of VIEWPORTS) {
  test(`最近の評価は 365 日残し、サーバー側でページングする (${viewport.name})`, async ({ page, mockApi }) => {
    // #794: 評価の履歴は件数（旧 50 件）ではなく期間で残し、一覧は offset / limit で取得する。
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    seedFinishedJobs(mockApi);
    const [latest] = mockApi.state.evaluations;
    for (let index = 0; index < 12; index += 1) {
      mockApi.state.evaluations.push({ ...latest, id: `eval-old-${index}`, previous_job_id: null, previous_summary: null });
    }
    await page.goto("/evaluation");

    await expect(page.getByText("新しい順。終わった評価は 365 日残します。")).toBeVisible();
    const pager = page.getByTestId("evaluation-jobs-pagination");
    await expect(pager).toContainText("1 - 10 / 14 件");
    await pager.getByRole("button", { name: "次へ" }).click();
    await expect(pager).toContainText("11 - 14 / 14 件");
    const request = mockApi.lastRequest("GET", "/api/evaluations");
    expect([request?.searchParams.get("offset"), request?.searchParams.get("limit")]).toEqual(["10", "10"]);
    // 表示している評価は最新の評価のまま（2 ページ目を見ても変わらない）。
    await expect(page.getByTestId("evaluation-summary")).toContainText("67%");
    await expectNoHorizontalOverflow(page);
  });
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

// ---- #810: 評価する版・テンプレートの評価ケース ----------------------------------------------------

function draftAgent(mockApi: MockApi) {
  const agent = mockApi.state.agents.find((item) => item.id === "default");
  if (!agent) throw new Error("default agent missing");
  agent.unpublished_changes = true;
  return agent;
}

for (const viewport of VIEWPORTS) {
  test(`評価する版を選んで始め、版を一覧と概要に出す (${viewport.name})`, async ({ page, mockApi }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    seedSet(mockApi);
    draftAgent(mockApi);
    await page.goto("/evaluation");

    // 公開していない変更があれば、既定は下書き。公開中の版も選べる。
    const version = page.getByTestId("evaluation-version");
    await expect(version).toContainText("下書き（公開していない変更あり）");
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`evaluation-version-${viewport.name}.png`), fullPage: true });
    await version.click();
    await page.getByRole("option", { name: "公開中の版（v1）" }).click();
    await expect(version).toContainText("公開中の版（v1）");

    await page.getByTestId("evaluation-set-row-actions-evset-seeded").click();
    await page.getByRole("menuitem", { name: "評価を開始" }).click();
    await expect(page.getByText("評価を開始しました")).toBeVisible();
    expect(mockApi.lastRequest("POST", "/api/evaluations")?.body).toEqual({
      set_id: "evset-seeded",
      agent_version: "published",
    });
    await expect(page.getByTestId("evaluation-summary")).toBeVisible();
    await expect(page.getByText("経理の評価（汎用業務 Agent・v1）")).toBeVisible();
    await expect(page.getByRole("table", { name: "最近の評価" }).getByRole("row").nth(1)).toContainText("v1");
  });
}

test("評価セットのエディタでも評価する版を選べる", async ({ page, mockApi }) => {
  seedSet(mockApi);
  draftAgent(mockApi);
  await page.goto("/evaluation?id=evset-seeded");
  await expect(page.getByTestId("evaluation-set-version")).toContainText("下書き（公開していない変更あり）");
  const actions = page.getByTestId("evaluation-set-actions");
  const startButton = actions.getByRole("button", { name: "評価を開始" });
  if (await startButton.count()) {
    await startButton.click();
  } else {
    await page.getByTestId("evaluation-set-actions-more").click();
    await page.getByRole("menuitem", { name: "評価を開始" }).click();
  }
  await expect(page.getByText("評価を開始しました")).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/evaluations")?.body).toEqual({ set_id: "evset-seeded", agent_version: "draft" });
});

test("公開した版の無い業務 Agent は下書きだけを選べる", async ({ page, mockApi }) => {
  seedSet(mockApi);
  const agent = draftAgent(mockApi);
  agent.published_version = null;
  agent.versions = [];
  await page.goto("/evaluation");
  const version = page.getByTestId("evaluation-version");
  await expect(version).toContainText("下書き（公開していない変更あり）");
  await expect(page.getByText("公開した版が無いため、下書きで評価します。")).toBeVisible();
  await version.click();
  await expect(page.getByRole("option")).toHaveCount(1);
});

for (const viewport of VIEWPORTS) {
  test(`テンプレートから作った業務 Agent は、評価セットが無ければテンプレートの評価ケースで作れる (${viewport.name})`, async ({
    page,
    mockApi,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const agent = mockApi.state.agents.find((item) => item.id === "default");
    if (!agent) throw new Error("default agent missing");
    agent.template_id = "internal-policy-helpdesk";
    await page.goto("/evaluation");
    await expect(page.getByText("この業務 Agent は業種テンプレートから作りました。")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`evaluation-from-template-${viewport.name}.png`), fullPage: true });
    await page.getByTestId("evaluation-sets-from-template").click();
    await expect(page.getByText("評価セット「社内規程の問い合わせ（テンプレート）」を作りました")).toBeVisible();
    expect(mockApi.lastRequest("POST", "/api/evaluation-sets/from-template")?.body).toEqual({ agent_id: "default" });
    await expect(
      page.getByRole("table", { name: "評価セットの一覧" }).getByRole("row", { name: /社内規程の問い合わせ（テンプレート）/ })
    ).toContainText("1");
  });
}

// #965: ケースの削除と追加・別のページの未入力・前の評価の表示中の最新の評価の終わり。
test("ケースを削除して足しても保存でき、ID の重複は欄で知らせる", async ({ page, mockApi }) => {
  mockApi.state.evaluationSets.push({
    ...structuredClone(SET),
    id: "evset-auto",
    cases: [1, 2, 3].map((number) => ({
      id: `case-${number}`,
      question: `質問 ${number}`,
      expected: "要点",
      expected_tools: [],
    })),
  });
  await page.goto("/evaluation?id=evset-auto");
  await page.getByRole("button", { name: "ケース 1 を削除" }).click();
  await page.getByRole("button", { name: "ケースを追加" }).click();
  const added = page.getByTestId("evaluation-case-3");
  // 空のまま保存すると付く ID（残ったケースの case-3 と重ならない）を placeholder に出す。
  await expect(added.getByLabel("ケース ID")).toHaveAttribute("placeholder", "case-4");
  await added.getByLabel("質問").fill("質問 4");
  await added.getByLabel("期待する回答の要点").fill("要点");

  // 明示した ID の重複は、送らずに欄で知らせる。
  await page.getByTestId("evaluation-case-1").getByLabel("ケース ID").fill("case-3");
  await page.getByRole("button", { name: "保存", exact: true }).first().click();
  await expect(page.getByText("ほかのケースと同じ ID です。")).toHaveCount(2);
  expect(mockApi.requests.filter((request) => request.method === "PUT")).toHaveLength(0);

  await page.getByTestId("evaluation-case-1").getByLabel("ケース ID").fill("case-2");
  await page.getByRole("button", { name: "保存", exact: true }).first().click();
  await expect(page.getByText("評価セットを保存しました")).toBeVisible();
  const saved = mockApi.state.evaluationSets.find((item) => item.id === "evset-auto");
  expect((saved?.cases as { id: string }[]).map((item) => item.id)).toEqual(["case-2", "case-3", "case-4"]);
});

test("別のページのケースが未入力なら、保存でそのページへ移ってエラーを出す", async ({ page, mockApi }) => {
  mockApi.state.evaluationSets.push({
    ...structuredClone(SET),
    id: "evset-many",
    cases: Array.from({ length: 11 }, (_, index) => ({
      id: `case-${index + 1}`,
      question: `質問 ${index + 1}`,
      expected: "要点",
      expected_tools: [],
    })),
  });
  await page.goto("/evaluation?id=evset-many");
  await page.getByTestId("evaluation-case-1").getByLabel("質問").fill("");
  const pager = page.getByTestId("evaluation-cases-pagination");
  await pager.getByRole("button", { name: "次へ" }).click();
  await expect(page.getByTestId("evaluation-case-11")).toBeVisible();

  await page.getByRole("button", { name: "保存", exact: true }).first().click();
  await expect(page.getByTestId("evaluation-case-1")).toContainText("質問を入力してください。");
  await expect(pager).toContainText("1 - 10 / 11 件");
  expect(mockApi.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
});

test("前の評価を表示している間に最新の評価が終わると、評価を開始できるようになる", async ({ page, mockApi }) => {
  seedFinishedJobs(mockApi);
  const [latest] = mockApi.state.evaluations;
  mockApi.state.evaluations.unshift({ ...structuredClone(latest), id: "eval-running", status: "running", finished_at: null });
  // mock は詳細の取得の回数で評価を終わらせるため、この評価は詳細を取り直しても終わらないようにする。
  mockApi.state.evaluationPollsUntilDone = 1_000;
  await page.goto("/evaluation");
  await page.getByTestId("evaluation-row-actions-eval-previous").click();
  await page.getByRole("menuitem", { name: "結果を表示" }).click();
  const busy = page.getByText("ほかの評価を実行しています。終わってから始めてください。");
  await expect(busy).toBeVisible();

  mockApi.state.evaluations[0].status = "completed";
  mockApi.state.evaluations[0].finished_at = MOCK_NOW;
  await expect(busy).toBeHidden();
  await page.getByTestId("evaluation-set-row-actions-evset-seeded").click();
  await expect(page.getByTestId("evaluation-set-start-evset-seeded")).toBeEnabled();
});
